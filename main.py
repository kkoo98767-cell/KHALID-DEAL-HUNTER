import random
import time
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
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
        ),
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
        "Accept-Language": "ar-SA,ar;q=0.9,en-US;q=0.8,en;q=0.7",
        "Cache-Control": "no-cache",
    },
    {
        "User-Agent": (
            "Mozilla/5.0 (Linux; Android 14; Pixel 8) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/131.0.0.0 Mobile Safari/537.36"
        ),
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "ar-SA,ar;q=0.9,en;q=0.8",
    },
]


def fetch_store(store, query):
    url = STORES[store].format(query=quote_plus(query))
    last_error = None

    # Try desktop and mobile browser profiles. Retry transient blocks/server errors.
    for profile_number, headers in enumerate(REQUEST_HEADERS, start=1):
        for attempt in range(1, 3):
            try:
                response = requests.get(
                    url,
                    headers=headers,
                    timeout=REQUEST_TIMEOUT,
                    allow_redirects=True,
                )
                if response.status_code in (429, 500, 502, 503, 504):
                    raise requests.HTTPError(
                        f"Transient HTTP {response.status_code}", response=response
                    )
                response.raise_for_status()

                items = parse_jsonld_products(
                    response.text, response.url, store, limit=MAX_PRODUCTS_PER_SEARCH
                )
                print(
                    f"{store}: HTTP {response.status_code}, "
                    f"{len(response.content)} bytes, parsed {len(items)} products "
                    f"(profile {profile_number}, attempt {attempt})"
                )
                if items:
                    return items
                # A successful but empty page can be a bot-check or changed layout.
                break
            except Exception as exc:
                last_error = exc
                print(
                    f"{store}: profile {profile_number}, attempt {attempt} failed: "
                    f"{type(exc).__name__}: {exc}"
                )
                if attempt < 2:
                    time.sleep(attempt * 2)

    if last_error:
        raise last_error
    return []


def main():
    db = connect()
    db.close()

    # Check all four stores on every run instead of randomly checking only 1–3.
    stores = list(STORES.keys())
    random.shuffle(stores)

    total = alerts = failures = ai_skips = 0
    print(f"Starting scan across {len(stores)} stores")

    for store in stores:
        _, query = choose_search()
        print(f"Searching {store}: {query}")

        try:
            items = fetch_store(store, query)
            print(f"{store}: parsed {len(items)} products")

            for item in items:
                total += 1
                deal = save_and_evaluate(item)
                if not deal:
                    continue

                # Optional product-page details; missing reviews/purchase signals
                # never block an otherwise valid candidate.
                deal = enrich_product_details(deal)

                try:
                    ai_result = analyze_deal(deal)
                except Exception as exc:
                    ai_skips += 1
                    print(
                        f"AI evaluation failed; alert skipped: "
                        f"{type(exc).__name__}: {exc}"
                    )
                    continue

                if not ai_result["worth_alerting"]:
                    ai_skips += 1
                    print(
                        f"Gemini rejected candidate: "
                        f"{ai_result.get('reason', 'No reason')}"
                    )
                    continue

                deal["ai_reason"] = ai_result.get("reason", "")
                deal["ai_message"] = ai_result.get("telegram_message", "")
                send_deal(deal)
                alerts += 1

        except Exception as exc:
            failures += 1
            print(f"Store/search failed: {store} / {query}: {type(exc).__name__}: {exc}")

    print(
        f"Finished. stores_checked={len(stores)}, products={total}, "
        f"alerts={alerts}, ai_skips={ai_skips}, failures={failures}"
    )


if __name__ == "__main__":
    main()
