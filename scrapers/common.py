import json
import re
import math
from urllib.parse import urljoin, urlparse, urlunparse, parse_qsl, urlencode

import requests
from bs4 import BeautifulSoup

PRICE_KEYS = (
    "salePrice", "finalPrice", "offerPrice", "currentPrice", "price",
    "lowPrice", "sellingPrice", "formattedPrice", "sale_price", "final_price",
    "current_price", "price_value", "amount", "value", "specialPrice",
    "special_price", "discountPrice", "discount_price", "priceValue",
    "displayPrice", "display_price", "minPrice", "min_price",
)
TITLE_KEYS = ("name", "title", "productName", "displayName", "product_name", "product_title", "display_name", "productTitle", "itemName", "item_name")
URL_KEYS = ("url", "productUrl", "canonicalUrl", "link", "product_url", "canonical_url", "productLink", "product_link", "pdpUrl", "productPageUrl", "product_page_url", "pdp_url", "href")
CARD_SELECTORS = {
    "Amazon.sa": [
        'div[data-component-type="s-search-result"]', 'div[data-asin][data-index]',
        '[data-cy="asin-faceout-container"]',
    ],
    "Noon": [
        '[data-qa="product-card"]', '[data-testid="product-card"]',
        '[class*="productContainer"]', '[class*="ProductCard"]',
        'a[href*="/p/"]',
    ],
    "Extra": [
        ".product-item", ".product-tile", '[data-product-id]',
        '[class*="productCard"]', '[class*="product-card"]',
        '[data-testid*="product"]', '[class*="product-item"]',
    ],
    "Jarir": [
        ".product-item", ".product-item-info", '[data-product-id]',
        '[class*="product-card"]', '[data-testid*="product"]',
        '[class*="product-item"]', '[data-product-sku]',
    ],
}
REVIEW_SELECTORS = [
    '[data-hook="review-body"]', '[data-testid*="review"]',
    '[class*="review-text"]', '[class*="reviewText"]',
    '.review-item .content', '.review-content',
]


def _number(value):
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value) if math.isfinite(value) else None
    if isinstance(value, dict):
        for key in ("value", "amount", "price", "currentPrice", "salePrice", "sellingPrice", "finalPrice", "formattedValue"):
            if key in value:
                parsed = _number(value[key])
                if parsed is not None:
                    return parsed
        return None
    text = str(value).strip()
    if re.search(r"-\s*[0-9٠-٩۰-۹]", text):
        return None
    # Normalise Arabic-Indic and Persian digits and separators.
    digit_map = str.maketrans("٠١٢٣٤٥٦٧٨٩۰۱۲۳۴۵۶۷۸۹", "01234567890123456789")
    text = text.translate(digit_map).replace("٬", ",").replace("٫", ".")
    text = re.sub(r"(?<=\d)[,](?=\d{3}(?:\D|$))", "", text)
    match = re.search(r"(?<!\d)(\d+(?:\.\d{1,2})?)(?!\d)", text)
    try:
        return float(match.group(1)) if match else None
    except (ValueError, TypeError):
        return None


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


def _walk_json(obj):
    for obj in _walk_dicts(obj):
        typ = obj.get("@type", "")
        types = typ if isinstance(typ, list) else [typ]
        if any(str(value).rstrip("/").split("/")[-1].lower() == "product" for value in types):
            yield obj


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
    count = _number(rating.get("reviewCount") or rating.get("ratingCount")
                    or product.get("reviewCount") or product.get("ratingCount"))
    return value or 0, int(count or 0)


def _normalise_product(title, price, url, store, base_url, rating=0, reviews=0, source="unknown"):
    if not title or price is None or price <= 0 or not url:
        return None
    title = re.sub(r"\s+", " ", str(title)).strip()
    if not reliable_title(title) or title.lower() in {"home", "search", "products", "المنتجات"}:
        return None
    absolute_url = canonical_url(urljoin(base_url, str(url).strip()))
    parsed = urlparse(absolute_url)
    if parsed.scheme not in ("http", "https") or not parsed.netloc or parsed.hostname != urlparse(base_url).hostname:
        return None
    if any(x in parsed.path.lower() for x in ("/search", "/customer/account", "/checkout")):
        return None
    return {
        "store": store, "title": title[:300], "url": absolute_url,
        "product_id": product_identifier(absolute_url), "currency": "SAR",
        "price": round(float(price), 2), "rating": float(rating or 0),
        "reviews": int(reviews or 0), "purchase_count_text": "",
        "review_snippets": [], "data_sources": [source],
    }


def _json_objects(soup):
    """Read JSON scripts even when a store does not label them as application/ld+json."""
    for script in soup.find_all("script"):
        raw = script.string or script.get_text()
        if not raw or len(raw) > 5_000_000:
            continue
        raw = raw.strip()
        if not raw or raw in ("{}", "[]"):
            continue
        candidates = [raw]
        # Some storefronts wrap serialized state in an assignment or HTML comment.
        for marker in ("JSON.parse(", "window.__INITIAL_STATE__ =", "window.__PRELOADED_STATE__ ="):
            pos = raw.find(marker)
            if pos >= 0:
                fragment = raw[pos + len(marker):].strip().rstrip(";)")
                if fragment.startswith(('"', "'")):
                    try:
                        fragment = json.loads(fragment)
                    except Exception:
                        pass
                if isinstance(fragment, str):
                    candidates.append(fragment)
        for candidate in candidates:
            try:
                yield json.loads(candidate)
            except Exception:
                continue


def _products_from_json(soup, base_url, store, limit):
    found = []
    for data in _json_objects(soup):
        # Product schema objects and generic embedded product records.
        for obj in _walk_dicts(data):
            is_product = "product" in str(obj.get("@type", "")).lower()
            title = next((obj.get(k) for k in TITLE_KEYS if obj.get(k)), None)
            offer = _first_offer(obj) if is_product else {}
            price_value = (offer.get("price") or offer.get("lowPrice")
                           or next((obj.get(k) for k in PRICE_KEYS if obj.get(k) is not None), None))
            if store == "Noon" and obj.get("url_key") and obj.get("sku") and re.fullmatch(r"[A-Za-z0-9_-]+", str(obj["sku"])):
                obj = dict(obj, productUrl=f"/saudi-en/{str(obj['url_key']).strip('/')}/{obj['sku']}/p/")
            url = (obj.get("url") or offer.get("url")
                   or next((obj.get(k) for k in URL_KEYS if obj.get(k)), None))
            currency = offer.get("priceCurrency") or obj.get("priceCurrency") or (price_value.get("currency") if isinstance(price_value, dict) else None)
            if offer.get("highPrice") and _number(offer.get("highPrice")) != _number(offer.get("lowPrice")):
                continue
            if currency and currency != "SAR":
                continue
            if is_product and not url:
                canonical = soup.select_one('link[rel="canonical"]')
                url = canonical.get("href") if canonical else base_url
            if not title or price_value is None or not url:
                continue
            price = _number(price_value)
            rating, reviews = _rating_data(obj)
            if not rating:
                rating = _number(obj.get("rating") or obj.get("ratingValue")) or 0
            if not reviews:
                reviews = int(_number(obj.get("reviewCount") or obj.get("ratingsCount") or obj.get("reviewsCount")) or 0)
            item = _normalise_product(title, price, url, store, base_url, rating, reviews, "embedded_json")
            if item:
                for field, keys in {"brand": ("brand",), "category": ("category",), "product_id": ("sku", "productId", "product_id"), "gtin": ("gtin13", "gtin", "gtin14")}.items():
                    val = next((obj.get(k) for k in keys if obj.get(k)), None)
                    if isinstance(val, dict):
                        val = val.get("name")
                    if isinstance(val, (str,int)):
                        item[field] = str(val)
                original = _number(obj.get("oldPrice") or obj.get("originalPrice") or obj.get("listPrice"))
                if original and original > item["price"]:
                    item["original_price"] = original
                found.append(item)
                if len(found) >= limit:
                    return found
    return found


def _text_price(card):
    selectors = [
        '.a-price:not(.a-text-price) .a-offscreen', '[data-price-amount]', '[data-price]', '[itemprop="price"]',
        '[data-qa*="price"]', '[data-testid*="price"]',
        '[class*="price"]', '[class*="Price"]', '[id*="price"]',
        '.a-price:not(.a-text-price) .a-offscreen',
        '[class*="selling-price"]', '[class*="sale-price"]',
        '[class*="final-price"]', '[class*="current-price"]',
    ]
    for selector in selectors:
        node = card.select_one(selector)
        if not node:
            continue
        classes = " ".join(node.get("class", [])).lower()
        if any(word in classes for word in ("old", "was", "strike", "original", "list-price", "crossed")):
            continue
        if node.find(["del", "s", "strike"]) or node.find_parent(["del", "s", "strike"]):
            continue
        value = node.get("content") or node.get("data-price-amount") or node.get("data-price") or node.get_text(" ", strip=True)
        price = _number(value)
        if price and 0.5 <= price <= 2_000_000:
            return price
    for node in card.select('[class*="price"], [data-qa*="price"], [data-testid*="price"]'):
        classes = " ".join(node.get("class", [])).lower()
        if any(word in classes for word in ("old", "was", "strike", "original", "list-price", "crossed")):
            continue
        if node.find(["del", "s", "strike"]) or node.find_parent(["del", "s", "strike"]):
            continue
        price = _number(node.get("content") or node.get_text(" ", strip=True))
        if price and 0.5 <= price <= 2_000_000:
            return price
    return None


def _rating_from_card(card):
    rating = reviews = 0
    node = card.select_one('[aria-label*="out of"], [aria-label*="من 5"], [class*="rating"]')
    if node:
        rating = _number(node.get("aria-label") or node.get_text(" ", strip=True)) or 0
        if rating > 5:
            rating = 0
    count = card.select_one('[class*="review"], [class*="ratingCount"], [aria-label*="ratings"]')
    if count:
        reviews = int(_number(count.get("aria-label") or count.get_text(" ", strip=True)) or 0)
    return rating, reviews


def _products_from_cards(soup, base_url, store, limit):
    found, seen = [], set()
    selectors = CARD_SELECTORS.get(store, []) + [
        '[data-product-id]', '[data-product-sku]', '[itemtype*="Product"]',
        'li[class*="product"]', 'article[class*="product"]',
        'div[class*="product-card"]', 'div[class*="productCard"]',
        'div[class*="ProductCard"]', 'div[class*="product-tile"]',
        'div[class*="productTile"]', 'div[class*="ProductTile"]',
        'li[class*="item"]', 'article[class*="item"]',
        'article[class*="tile"]', 'div[class*="tile"]',
        'article[class*="Tile"]', 'div[class*="Tile"]',
    ]
    for selector in selectors:
        try:
            cards = soup.select(selector)
        except Exception:
            continue
        for card in cards:
            # Skip page-level wrappers; they can contain unrelated prices and links.
            if len(card.select("a[href]")) > 8 or len(card.get_text(" ", strip=True)) > 5000:
                continue
            title_node = card.select_one(
                '[itemprop="name"], h2 a, a h2, h2, h3 a, [class*="title"] a, '
                '[class*="name"] a, [data-qa*="title"], a[href]'
            )
            if not title_node:
                continue
            title = title_node.get("title") or title_node.get("aria-label") or title_node.get_text(" ", strip=True)
            link = title_node if title_node.name == "a" else title_node.find_parent("a")
            if not link:
                link = card.select_one("a[href]")
            href = link.get("href") if link else None
            price = _text_price(card)
            if not title or not href or not price:
                continue
            key = urljoin(base_url, href).split("?")[0].rstrip("/")
            if key in seen:
                continue
            seen.add(key)
            rating, reviews = _rating_from_card(card)
            item = _normalise_product(title, price, href, store, base_url, rating, reviews, "html_card")
            if item:
                found.append(item)
                if len(found) >= limit:
                    return found
    return found


def _products_from_meta(soup, base_url, store):
    title = None
    for selector in ('meta[property="og:title"]', 'meta[name="title"]'):
        node = soup.select_one(selector)
        if node and node.get("content"):
            title = node["content"]
            break
    price = None
    for selector in ('meta[property="product:price:amount"]', 'meta[property="og:price:amount"]', 'meta[itemprop="price"]'):
        node = soup.select_one(selector)
        if node:
            price = _number(node.get("content"))
            if price:
                break
    canonical = soup.select_one('link[rel="canonical"]')
    url = canonical.get("href") if canonical else base_url
    item = _normalise_product(title, price, url, store, base_url, source="meta_tags")
    return [item] if item else []


def _products_from_product_links(soup, base_url, store, limit):
    """Fallback for storefronts whose cards have no stable CSS class."""
    found, seen = [], set()
    selectors = ('a[href*="/dp/"]', 'a[href*="/p/"]', 'a[href*="/product/"]',
                 'a[href*="/products/"]', 'a[href*=".html"]',
                 'a[href*="/catalog/"]', 'a[href*="/item/"]')
    anchors = []
    for selector in selectors:
        try:
            anchors.extend(soup.select(selector))
        except Exception:
            pass
    for anchor in anchors:
        href = anchor.get("href")
        title = anchor.get("title") or anchor.get("aria-label") or anchor.get_text(" ", strip=True)
        if not href or len(title.strip()) < 4:
            continue
        parent = anchor
        for _ in range(5):
            if parent.parent is None:
                break
            parent = parent.parent
            product_links = parent.select(
                'a[href*="/p/"], a[href*="/dp/"], a[href*="/product/"], a[href*="/products/"]'
            )
            if len(product_links) > 4:
                break
            price = _text_price(parent)
            if price and len({canonical_url(urljoin(base_url,a.get("href"))) for a in product_links}) <= 1:
                rating, reviews = _rating_from_card(parent)
                heading = parent.select_one('h2, h3, [itemprop="name"], [class*="product-title"]')
                if heading:
                    title = heading.get_text(" ",strip=True)
                item = _normalise_product(title, price, href, store, base_url, rating, reviews, "product_link_ancestor")
                if item:
                    key = item["url"].split("?")[0].rstrip("/")
                    if key not in seen:
                        seen.add(key)
                        found.append(item)
                break
        if len(found) >= limit:
            break
    return found


def _merge_products(groups, limit):
    merged, by_url = [], {}
    for group in groups:
        for item in group:
            key = item["url"].split("?")[0].rstrip("/")
            if key in by_url:
                existing = by_url[key]
                for field in ("rating", "reviews"):
                    if not existing.get(field) and item.get(field):
                        existing[field] = item[field]
                existing["data_sources"] = list(dict.fromkeys(existing["data_sources"] + item["data_sources"]))
                continue
            by_url[key] = item
            merged.append(item)
            if len(merged) >= limit:
                return merged
    return merged


def parse_jsonld_products(html, base_url, store, limit=20):
    """Multi-strategy parser: JSON-LD/embedded state, store cards, metadata and product links."""
    soup = BeautifulSoup(html, "html.parser")
    groups = [
        _products_from_json(soup, base_url, store, limit),
        _products_from_cards(soup, base_url, store, limit),
        _products_from_product_links(soup, base_url, store, limit),
        _products_from_meta(soup, base_url, store),
    ]
    products = _merge_products(groups, limit)
    by_url = {}
    conflicts = set()
    for group in groups:
        for item in group:
            key = item["url"]
            if key in by_url and abs(by_url[key]-item["price"]) > max(.01,by_url[key]*.01):
                conflicts.add(key)
            by_url[key] = item["price"]
    return [item for item in products if item["url"] not in conflicts]


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
    """Optional product-page enrichment. Ratings/reviews never gate an alert."""
    url = item.get("url")
    if not url:
        return item
    try:
        response = requests.get(
            url,
            headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/131.0 Safari/537.36",
                     "Accept-Language": "ar-SA,ar;q=0.9,en-US;q=0.8,en;q=0.7"},
            timeout=timeout, allow_redirects=True,
        )
        response.raise_for_status()
        soup = BeautifulSoup(response.text, "html.parser")
    except Exception as exc:
        item["detail_fetch_status"] = type(exc).__name__
        return item

    for data in _json_objects(soup):
        for product in _walk_json(data):
            rating, reviews = _rating_data(product)
            if not item.get("rating") and rating:
                item["rating"] = rating
            if not item.get("reviews") and reviews:
                item["reviews"] = reviews

    if not item.get("rating"):
        node = soup.select_one('[itemprop="ratingValue"], [data-hook="rating-out-of-text"], [aria-label*="out of 5"]')
        if node:
            rating = _number(node.get("content") or node.get("aria-label") or node.get_text(" ", strip=True))
            item["rating"] = rating if rating and rating <= 5 else 0
    if not item.get("reviews"):
        node = soup.select_one('[itemprop="reviewCount"], [itemprop="ratingCount"], [data-hook="total-review-count"]')
        if node:
            item["reviews"] = int(_number(node.get("content") or node.get_text(" ", strip=True)) or 0)

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
    item["review_snippets"] = snippets
    item["purchase_count_text"] = _extract_purchase_text(soup.get_text(" ", strip=True))
    item["detail_fetch_status"] = "ok"
    if snippets:
        item.setdefault("data_sources", []).append("visible_reviews")
    if item["purchase_count_text"]:
        item.setdefault("data_sources", []).append("visible_purchase_signal")
    return item


def canonical_url(url):
    parsed = urlparse(url)
    path = parsed.path.rstrip("/")
    asin = re.search(r"/(?:dp|gp/product)/([A-Z0-9]{10})(?:/|$)", path, re.I)
    if parsed.hostname and parsed.hostname.endswith("amazon.sa") and asin:
        path = "/dp/" + asin.group(1).upper()
    query = [(k,v) for k,v in parse_qsl(parsed.query) if not (k.lower().startswith("utm_") or k.lower() in {"ref", "tag", "qid", "sr", "pf_rd_p", "pf_rd_r"})]
    return urlunparse((parsed.scheme,parsed.netloc.lower(),path,"",urlencode(query),""))


def product_identifier(url):
    for pattern in (r"/dp/([A-Z0-9]{10})", r"/([A-Z0-9]+)/(?:p|P)$", r"/p/(\d+)", r"-(\d+)\.html$"):
        match = re.search(pattern,url)
        if match:
            return match.group(1)
    return None


def reliable_title(title):
    if len(str(title).strip()) < 3:
        return False
    if re.search(r"^(?:SAR|Limited time deal|No featured offers|الخيارات:|الشراء لمرة|تم نسخ معلومات|This advertisement)",str(title).strip(),re.I):
        return False
    stripped = re.sub(r"[\d\W_]+|ريال|SAR|List|Price|Was|سعر|القائمه", "", str(title),flags=re.I)
    return len(stripped) >= 3
