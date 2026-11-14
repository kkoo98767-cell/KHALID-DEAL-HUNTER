import random
import requests
from urllib.parse import quote_plus

from config import STORES, REQUEST_TIMEOUT, MAX_PRODUCTS_PER_SEARCH
from discovery import choose_search
from scrapers.common import parse_jsonld_products, enrich_product_details
from database import save_and_evaluate, connect
from ai_analyzer import analyze_deal
from telegram_bot import send_deal

REQUEST_HEADERS = [
    {
        "User-Agent": (
            "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
        ),
        "Accept-Language": "ar-SA,ar;q=0.9,en-US;q=0.8,en;q=0.7",
    },
    {
        "User-Agent": (
            "Mozilla/5.0 (Linux; Android 13; Pixel 7) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/124.0.0.0 Mobile Safari/537.36"
        ),
        "Accept-Language": "ar-SA,ar;q=0.9,en;q=0.8",
    },
]


def fetch_store(store, query):
    url = STORES[store].format(query=quote_plus(query))
    last_error = None

    # Try a desktop browser request first, then a mobile browser request
    # if the first response is blocked or contains no parsable products.
    for attempt, headers in enumerate(REQUEST_HEADERS, start=1):
        try:
            response = requests.get(
                url,
                headers=headers,
                timeout=REQUEST_TIMEOUT,
                allow_redirects=True,
            )
            response.raise_for_status()
            items = parse_jsonld_products(
                response.text, response.url, store, limit=MAX_PRODUCTS_PER_SEARCH
            )
            if items:
                print(f"{store}: extracted products using request profile {attempt}")
                return items
            print(f"{store}: no products parsed with request profile {attempt}")
        except Exception as exc:
            last_error = exc
            print(f"{store}: request profile {attempt} failed: {type(exc).__name__}: {exc}")

    if last_error:
        raise last_error
    return []



def main():
    db = connect()
    db.close()

    searches = random.randint(1, 3)
    stores_seen = set()
    total = alerts = failures = ai_skips = 0

    for _ in range(searches):
        store, query = choose_search()
        if store in stores_seen:
            continue
        stores_seen.add(store)
        print(f"Searching {store}: {query}")

        try:
            items = fetch_store(store, query)
            print(f"Parsed {len(items)} structured products")
            for item in items:
                total += 1
                deal = save_and_evaluate(item)

                # Gemini is called only for candidates that already passed
                # the Python price-history rules (3 prior prices, 25% drop).
                if not deal:
                    continue

                # Optional product-page enrichment: review/rating/purchase details.
                # Any block or layout change is non-fatal; the deal can still be evaluated.
                deal = enrich_product_details(deal)

                try:
                    ai_result = analyze_deal(deal)
                except Exception as exc:
                    ai_skips += 1
                    print(f"AI evaluation failed; alert skipped: {type(exc).__name__}: {exc}")
                    continue

                if not ai_result["worth_alerting"]:
                    ai_skips += 1
                    print(f"Gemini rejected candidate: {ai_result.get('reason', 'No reason')}")
                    continue

                deal["ai_reason"] = ai_result.get("reason", "")
                deal["ai_message"] = ai_result.get("telegram_message", "")
                send_deal(deal)
                alerts += 1

        except Exception as exc:
            failures += 1
            print(f"Store/search failed: {store} / {query}: {exc}")

    print(
        f"Finished. products={total}, alerts={alerts}, "
        f"ai_skips={ai_skips}, failures={failures}"
    )


if __name__ == "__main__":
    main()
