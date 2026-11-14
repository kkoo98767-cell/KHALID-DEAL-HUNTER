import json
import re
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "ar-SA,ar;q=0.9,en-US;q=0.8,en;q=0.7",
}

PRICE_KEYS = (
    "price", "salePrice", "finalPrice", "offerPrice", "currentPrice",
    "lowPrice", "sellingPrice", "formattedPrice",
)
TITLE_KEYS = ("name", "title", "productName", "displayName")
URL_KEYS = ("url", "productUrl", "canonicalUrl", "link")
CARD_SELECTORS = {
    "Amazon.sa": [
        'div[data-component-type="s-search-result"]',
        'div[data-asin][data-index]',
    ],
    "Noon": [
        '[data-qa="product-card"]', '[data-testid="product-card"]',
        'a[href*="/p/"]',
    ],
    "Extra": [
        ".product-item", ".product-tile", '[data-product-id]',
        '[class*="productCard"]',
    ],
    "Jarir": [
        ".product-item", ".product-item-info", '[data-product-id]',
    ],
}
REVIEW_SELECTORS = [
    '[data-hook="review-body"]',
    '[data-testid*="review"]',
    '[class*="review-text"]',
    '[class*="reviewText"]',
    '.review-item .content',
    '.review-content',
]


def _number(value):
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).strip().replace(",", "").replace("٬", "")
    match = re.search(r"(?<!\d)(\d+(?:\.\d{1,2})?)(?!\d)", text)
    if not match:
        return None
    try:
        return float(match.group(1))
    except ValueError:
        return None


def _walk_json(obj):
    if isinstance(obj, list):
        for value in obj:
            yield from _walk_json(value)
    elif isinstance(obj, dict):
        typ = obj.get("@type", "")
        if typ == "Product" or (isinstance(typ, list) and "Product" in typ):
            yield obj
        for value in obj.values():
            if isinstance(value, (dict, list)):
                yield from _walk_json(value)


def _first_offer(product):
    offers = product.get("offers", {})
    if isinstance(offers, list):
        return offers[0] if offers else {}
    if isinstance(offers, dict) and "offers" in offers:
        nested = offers["offers"]
        return nested[0] if isinstance(nested, list) and nested else nested
    return offers if isinstance(offers, dict) else {}


def _rating_data(product):
    rating = product.get("aggregateRating", {}) or {}
    if isinstance(rating, list):
        rating = rating[0] if rating else {}
    if not isinstance(rating, dict):
        rating = {}
    value = _number(rating.get("ratingValue") or rating.get("rating"))
    count = _number(
        rating.get("reviewCount") or rating.get("ratingCount")
        or product.get("reviewCount") or product.get("ratingCount")
    )
    return (value or 0, int(count or 0))


def _normalise_product(title, price, url, store, base_url, rating=0, reviews=0):
    if not title or price is None or price <= 0 or not url:
        return None
    absolute_url = urljoin(base_url, str(url).strip())
    if not absolute_url.startswith(("http://", "https://")):
        return None
    return {
        "store": store,
        "title": re.sub(r"\s+", " ", str(title)).strip()[:300],
        "url": absolute_url,
        "price": round(float(price), 2),
        "rating": float(rating or 0),
        "reviews": int(reviews or 0),
        "purchase_count_text": "",
        "review_snippets": [],
        "data_sources": ["structured"] if rating or reviews else ["structured"],
    }


def _products_from_jsonld(soup, base_url, store, limit):
    found = []
    for script in soup.select('script[type="application/ld+json"]'):
        raw = script.string or script.get_text()
        try:
            data = json.loads(raw)
        except Exception:
            continue
        for product in _walk_json(data):
            offer = _first_offer(product)
            price = _number(
                offer.get("price") or offer.get("lowPrice")
                or product.get("price")
            )
            url = product.get("url") or offer.get("url")
            rating, reviews = _rating_data(product)
            item = _normalise_product(
                product.get("name"), price, url, store, base_url, rating, reviews
            )
            if item:
                found.append(item)
                if len(found) >= limit:
                    return found
    return found


def _products_from_embedded_json(soup, base_url, store, limit):
    found = []
    for script in soup.find_all("script"):
        raw = script.string or script.get_text()
        if not raw or len(raw) > 2_000_000:
            continue
        script_id = (script.get("id") or "").lower()
        script_type = (script.get("type") or "").lower()
        if not (
            "json" in script_type
            or script_id in ("__next_data__", "__nuxt_data__")
            or "__next_data__" in raw[:500]
            or "__apollo_state__" in raw[:500]
        ):
            continue
        candidate = raw.strip()
        if candidate.startswith("<!--"):
            continue
        try:
            data = json.loads(candidate)
        except Exception:
            continue
        for obj in _walk_dicts(data):
            title = next((obj.get(k) for k in TITLE_KEYS if obj.get(k)), None)
            price_value = next((obj.get(k) for k in PRICE_KEYS if obj.get(k) is not None), None)
            url = next((obj.get(k) for k in URL_KEYS if obj.get(k)), None)
            if not title or price_value is None or not url:
                continue
            price = _number(price_value)
            rating = _number(obj.get("rating") or obj.get("ratingValue")) or 0
            reviews = _number(obj.get("reviewCount") or obj.get("ratingsCount") or obj.get("reviewsCount")) or 0
            item = _normalise_product(title, price, url, store, base_url, rating, reviews)
            if item:
                item["data_sources"] = ["embedded_json"]
                found.append(item)
                if len(found) >= limit:
                    return found
    return found


def _walk_dicts(obj):
    if isinstance(obj, dict):
        yield obj
        for value in obj.values():
            if isinstance(value, (dict, list)):
                yield from _walk_dicts(value)
    elif isinstance(obj, list):
        for value in obj:
            if isinstance(value, (dict, list)):
                yield from _walk_dicts(value)


def _text_price(card):
    # Prefer explicit current-price attributes over generic price containers.
    priority_selectors = [
        '[data-price-amount]',
        '[itemprop="price"]',
        '[data-qa="price"]',
        '[data-testid="price"]',
        '.a-price:not(.a-text-price) .a-offscreen',
    ]
    for selector in priority_selectors:
        node = card.select_one(selector)
        if not node:
            continue
        value = node.get("content") or node.get("data-price-amount") or node.get_text(" ", strip=True)
        price = _number(value)
        if price and price > 0:
            return price

    # Generic store-specific price classes; skip old/struck-through prices.
    for node in card.select('[class*="price"], [data-qa*="price"], [data-testid*="price"]'):
        classes = " ".join(node.get("class", [])).lower()
        if any(marker in classes for marker in ("old", "was", "strike", "original", "list-price")):
            continue
        if node.find(["del", "s", "strike"]):
            continue
        value = node.get("content") or node.get("data-price-amount") or node.get_text(" ", strip=True)
        price = _number(value)
        if price and price > 0:
            return price

    # Last fallback: only accept a number explicitly paired with a currency.
    text = card.get_text(" ", strip=True)
    matches = re.findall(
        r"(?:SAR|ر\.?س\.?|ر.س|﷼)\s*([0-9][0-9,]*(?:\.[0-9]{1,2})?)"
        r"|([0-9][0-9,]*(?:\.[0-9]{1,2})?)\s*(?:SAR|ر\.?س\.?|﷼)",
        text,
        flags=re.I,
    )
    for match in matches:
        price = _number(next((part for part in match if part), None))
        if price and price > 0:
            return price
    return None


def _products_from_cards(soup, base_url, store, limit):
    found, seen_cards = [], set()
    selectors = CARD_SELECTORS.get(store, [])
    for selector in selectors:
        try:
            cards = soup.select(selector)
        except Exception:
            continue
        for card in cards:
            title_node = card.select_one(
                'h2 a, h3 a, [class*="title"] a, [class*="name"] a, '
                '[data-qa*="title"], [itemprop="name"], a[href]'
            )
            if not title_node:
                continue
            title = title_node.get("title") or title_node.get("aria-label") or title_node.get_text(" ", strip=True)
            link_node = title_node if title_node.name == "a" else title_node.find_parent("a")
            if not link_node:
                link_node = card.select_one("a[href]")
            href = link_node.get("href") if link_node else None
            price = _text_price(card)
            if not title or not href or not price:
                continue
            key = urljoin(base_url, href).split("?")[0]
            if key in seen_cards:
                continue
            seen_cards.add(key)

            rating = 0
            reviews = 0
            rating_node = card.select_one('[aria-label*="out of"], [aria-label*="من 5"], [class*="rating"]')
            if rating_node:
                rating_text = rating_node.get("aria-label") or rating_node.get_text(" ", strip=True)
                rating = _number(rating_text) or 0
            review_node = card.select_one(
                '[class*="review"], [class*="ratingCount"], [aria-label*="ratings"]'
            )
            if review_node:
                reviews = int(_number(review_node.get_text(" ", strip=True)) or 0)

            item = _normalise_product(title, price, href, store, base_url, rating, reviews)
            if item:
                item["data_sources"] = ["html_card"]
                found.append(item)
                if len(found) >= limit:
                    return found
    return found


def _merge_products(groups, limit):
    merged, by_url = [], {}
    for group in groups:
        for item in group:
            key = item["url"].split("?")[0].rstrip("/")
            if key in by_url:
                existing = by_url[key]
                if not existing.get("rating") and item.get("rating"):
                    existing["rating"] = item["rating"]
                if not existing.get("reviews") and item.get("reviews"):
                    existing["reviews"] = item["reviews"]
                existing["data_sources"] = list(set(existing["data_sources"] + item["data_sources"]))
                continue
            by_url[key] = item
            merged.append(item)
            if len(merged) >= limit:
                return merged
    return merged


def parse_jsonld_products(html, base_url, store, limit=20):
    """Backward-compatible parser with structured, embedded JSON and HTML fallbacks."""
    soup = BeautifulSoup(html, "html.parser")
    return _merge_products(
        [
            _products_from_jsonld(soup, base_url, store, limit),
            _products_from_embedded_json(soup, base_url, store, limit),
            _products_from_cards(soup, base_url, store, limit),
        ],
        limit,
    )


def _extract_purchase_text(text):
    amount = r"[\d,.]+\s*[KkMm]?"
    patterns = [
        rf"({amount}\+?\s*(?:bought|purchased)\s+(?:in the past month|last month|this month))",
        rf"((?:bought|purchased)\s+by\s+{amount}\+?\s+(?:customers|people))",
        rf"(اشتراه\s+{amount}\+?\s+(?:شخص|عميل|مستخدم)\s*(?:خلال الشهر الماضي)?)",
        rf"({amount}\+?\s*(?:عملية شراء|مشتري)\s*(?:خلال الشهر الماضي)?)",
    ]
    for pattern in patterns:
        match = re.search(pattern, text, flags=re.I)
        if match:
            return re.sub(r"\s+", " ", match.group(1)).strip()[:120]
    return ""


def enrich_product_details(item, timeout=12):
    """Best-effort optional detail-page extraction; never fails the deal if blocked."""
    url = item.get("url")
    if not url:
        return item
    try:
        response = requests.get(
            url,
            headers=HEADERS,
            timeout=timeout,
            allow_redirects=True,
        )
        response.raise_for_status()
        soup = BeautifulSoup(response.text, "html.parser")
    except Exception as exc:
        item["detail_fetch_status"] = type(exc).__name__
        return item

    # Structured product data may carry ratings and review totals.
    for script in soup.select('script[type="application/ld+json"]'):
        try:
            data = json.loads(script.string or script.get_text())
        except Exception:
            continue
        for product in _walk_json(data):
            rating, reviews = _rating_data(product)
            if not item.get("rating") and rating:
                item["rating"] = rating
            if not item.get("reviews") and reviews:
                item["reviews"] = reviews

    # Fallback to visible microdata/meta fields when JSON-LD is absent.
    if not item.get("rating"):
        rating_node = soup.select_one('[itemprop="ratingValue"], [data-hook="rating-out-of-text"]')
        if rating_node:
            item["rating"] = _number(
                rating_node.get("content") or rating_node.get_text(" ", strip=True)
            ) or 0
    if not item.get("reviews"):
        count_node = soup.select_one(
            '[itemprop="reviewCount"], [itemprop="ratingCount"], '
            '[data-hook="total-review-count"]'
        )
        if count_node:
            item["reviews"] = int(_number(
                count_node.get("content") or count_node.get_text(" ", strip=True)
            ) or 0)

    # Read only short, visibly rendered review excerpts; do not send full review pages.
    snippets = []
    for selector in REVIEW_SELECTORS:
        for node in soup.select(selector):
            value = re.sub(r"\s+", " ", node.get_text(" ", strip=True))
            if len(value) >= 25 and value not in snippets:
                snippets.append(value[:350])
            if len(snippets) >= 3:
                break
        if len(snippets) >= 3:
            break

    page_text = soup.get_text(" ", strip=True)
    purchase_text = _extract_purchase_text(page_text)
    item["review_snippets"] = snippets
    item["purchase_count_text"] = purchase_text
    item["detail_fetch_status"] = "ok"
    if snippets:
        item.setdefault("data_sources", []).append("visible_reviews")
    if purchase_text:
        item.setdefault("data_sources", []).append("visible_purchase_signal")
    return item
