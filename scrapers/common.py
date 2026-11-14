
import json
from urllib.parse import urljoin
from bs4 import BeautifulSoup

def _walk_json(obj):
    if isinstance(obj, list):
        for x in obj:
            yield from _walk_json(x)
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

def parse_jsonld_products(html, base_url, store, limit=20):
    soup = BeautifulSoup(html, "html.parser")
    found, seen = [], set()

    for script in soup.select('script[type="application/ld+json"]'):
        raw = script.string or script.get_text()
        try:
            data = json.loads(raw)
        except Exception:
            continue

        for product in _walk_json(data):
            title = product.get("name")
            offer = _first_offer(product)
            price = offer.get("price") or offer.get("lowPrice")
            url = product.get("url") or offer.get("url")
            if not title or not price or not url:
                continue
            try:
                price = float(str(price).replace(",", "").replace("SAR", "").strip())
            except Exception:
                continue
            if price <= 0:
                continue
            url = urljoin(base_url, url)
            if url in seen:
                continue
            seen.add(url)

            rating = product.get("aggregateRating", {}) or {}
            try:
                rating_value = float(rating.get("ratingValue", 0) or 0)
            except Exception:
                rating_value = 0
            try:
                reviews = int(float(rating.get("reviewCount", rating.get("ratingCount", 0)) or 0))
            except Exception:
                reviews = 0

            found.append({
                "store": store, "title": str(title).strip(),
                "url": url, "price": price,
                "rating": rating_value, "reviews": reviews,
            })
            if len(found) >= limit:
                return found
    return found
