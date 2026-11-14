
import random
import requests
from urllib.parse import quote_plus
from config import STORES, REQUEST_TIMEOUT, MAX_PRODUCTS_PER_SEARCH
from discovery import choose_search
from scrapers.common import parse_jsonld_products
from database import save_and_evaluate, connect
from telegram_bot import send_deal

HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; KhalidDealHunter/1.0; +https://github.com/)"
}

def fetch_store(store, query):
    url = STORES[store].format(query=quote_plus(query))
    response = requests.get(url, headers=HEADERS, timeout=REQUEST_TIMEOUT)
    response.raise_for_status()
    return parse_jsonld_products(
        response.text, response.url, store, limit=MAX_PRODUCTS_PER_SEARCH
    )

def main():
    # Create the database even if no store returns products on this run.
    db = connect()
    db.close()

    # A small random batch each run, to reduce repeated traffic.
    searches = random.randint(1, 3)
    stores_seen = set()
    total = alerts = failures = 0

    for _ in range(searches):
        store, query = choose_search()
        # Avoid hitting the same store more than once in this run.
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
                if deal:
                    send_deal(deal)
                    alerts += 1
        except Exception as exc:
            failures += 1
            print(f"Store/search failed: {store} / {query}: {exc}")

    print(f"Finished. products={total}, alerts={alerts}, failures={failures}")

if __name__ == "__main__":
    main()
