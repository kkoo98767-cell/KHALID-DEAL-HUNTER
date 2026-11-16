import os
import random
import time
import requests
import json
import uuid
from pathlib import Path
from urllib.parse import quote_plus, urljoin, urlparse
from bs4 import BeautifulSoup

from config import STORES, REQUEST_TIMEOUT, MAX_PRODUCTS_PER_SEARCH
from discovery import choose_search
from scrapers.common import parse_jsonld_products, enrich_product_details
from database import save_and_evaluate, connect, was_recently_alerted, record_alert, get_archive_stats, checkpoint
from ai_analyzer import analyze_deal, test_ai_provider
from telegram_bot import send_deal, send_startup_status, send_ai_test_status, send_scan_summary

REQUEST_HEADERS = [
    {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
        "Accept-Language": "ar-SA,ar;q=0.9,en-US;q=0.8,en;q=0.7",
        "Cache-Control": "no-cache",
    },
    {
        "User-Agent": "Mozilla/5.0 (Linux; Android 14; Pixel 8) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Mobile Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "ar-SA,ar;q=0.9,en;q=0.8",
        "Cache-Control": "no-cache",
    },
]

TRANSIENT_STATUS = {408, 425, 429, 500, 502, 503, 504}


def _search_url_variants(store, query):
    """Use each store's native search route plus a conservative URL fallback."""
    encoded = quote_plus(query)
    primary = STORES[store].format(query=encoded)
    variants = [primary]
    if store == "Noon":
        variants.append(f"https://www.noon.com/saudi-en/search?q={encoded}")
    elif store == "Extra":
        variants.append(f"https://www.extra.com/en-sa/search?text={encoded}")
    elif store == "Jarir":
        variants.append(f"https://www.jarir.com/sa-en/search/?q={encoded}")
    elif store == "Amazon.sa":
        variants.append(f"https://www.amazon.sa/s?k={encoded}&language=en_AE")
    return list(dict.fromkeys(variants))


def _looks_like_challenge(html):
    sample = html[:250_000].lower()
    markers = (
        "captcha", "verify you are human", "robot check",
        "access denied", "unusual traffic", "challenge-platform", "down for maintenance",
        "cf-chl-", "px-captcha", "enable javascript and cookies",
    )
    return any(marker in sample for marker in markers)


def fetch_store(store, query):
    items, seen = [], set()
    # Stop on access denial/challenges. Never rotate fingerprints to evade them.
    with requests.Session() as session:
        urls = _search_url_variants(store, query)
        pages = 0
        for url in urls:
            pages += 1
            if pages > 3:
                break
            for attempt in range(2):
                try:
                    response = session.get(url, headers=REQUEST_HEADERS[0], timeout=(5,18))
                except (requests.Timeout, requests.ConnectionError):
                    if attempt == 1:
                        raise
                    time.sleep(1)
                    continue
                if response.status_code in {401,403}:
                    raise RuntimeError(f"Store access denied: HTTP {response.status_code}")
                if response.status_code in TRANSIENT_STATUS and attempt == 0:
                    time.sleep(min(5, max(1, int(response.headers.get("Retry-After", "1")) if response.headers.get("Retry-After", "1").isdigit() else 1)))
                    continue
                if response.status_code == 404:
                    break
                response.raise_for_status()
                if _looks_like_challenge(response.text):
                    raise RuntimeError("Store challenge page; access protection respected")
                parsed = parse_jsonld_products(response.text, response.url, store, limit=MAX_PRODUCTS_PER_SEARCH)
                if not parsed and store == "Jarir":
                    from scrapers.jarir import discover
                    parsed = discover(session,response.text,query,REQUEST_HEADERS[0],MAX_PRODUCTS_PER_SEARCH)
                print(f"{store}: HTTP {response.status_code}, bytes={len(response.content)}, validated_products={len(parsed)}")
                for item in parsed:
                    if item["url"] not in seen:
                        seen.add(item["url"])
                        items.append(item)
                if len(items) >= MAX_PRODUCTS_PER_SEARCH:
                    return items[:MAX_PRODUCTS_PER_SEARCH]
                next_link = BeautifulSoup(response.text, "html.parser").select_one('a[rel="next"], a.s-pagination-next[href]')
                if next_link and next_link.get("href"):
                    next_url = urljoin(response.url, next_link["href"])
                    if urlparse(next_url).hostname == urlparse(response.url).hostname and next_url not in urls:
                        urls.append(next_url)
                if parsed:
                    # Drop alternate search routes after a successful native page.
                    urls[:] = [url] + [u for u in urls if u == locals().get("next_url")]

                break
    return items


def refresh_tracked(store, seen):
    """Revisit oldest products so rotating discovery does not starve history."""
    db = connect()
    rows = db.execute("SELECT url FROM products WHERE store=? ORDER BY last_seen LIMIT 3", (store,)).fetchall()
    db.close()
    items = []
    for (url,) in rows:
        if url in seen or urlparse(url).hostname != urlparse(STORES[store]).hostname:
            continue
        try:
            response = requests.get(url, headers=REQUEST_HEADERS[0], timeout=(5,12))
            if response.status_code in {401,403,429} or _looks_like_challenge(response.text):
                print(f"{store}: tracked refresh blocked; stopped")
                break
            response.raise_for_status()
            for item in parse_jsonld_products(response.text,response.url,store):
                if item["url"] == url and item["url"] not in seen:
                    seen.add(item["url"])
                    items.append(item)
        except requests.RequestException as exc:
            print(f"{store}: tracked refresh failed: {type(exc).__name__}")
    return items


def main():
    provider_test = os.getenv("AI_PROVIDER_TEST", "none").strip().lower()
    is_ai_test = provider_test in {"gemini", "groq"}

    startup_failed = False
    try:
        send_startup_status(
        run_number=os.getenv("GITHUB_RUN_NUMBER"),
        run_id=os.getenv("GITHUB_RUN_ID"),
        mode="ai_test" if is_ai_test else "hunt",
    )
    except Exception as exc:
        startup_failed = True
        print(f"Telegram startup failed: {type(exc).__name__}")

    if is_ai_test:
        provider_name = "Gemini" if provider_test == "gemini" else "Groq"
        try:
            result = test_ai_provider(provider_test)
            print(f"AI PROVIDER TEST PASSED: {result}")
            send_ai_test_status(
                result["provider"], True,
                f"النموذج: {result['model']} — {result['reason']}",
                os.getenv("GITHUB_RUN_NUMBER"),
            )
            print(f"Telegram AI test success message sent for {provider_name}.")
            return
        except Exception as exc:
            error_detail = f"{type(exc).__name__}: {exc}"
            print(f"AI PROVIDER TEST FAILED: {error_detail}")
            try:
                send_ai_test_status(
                    provider_name, False, error_detail,
                    os.getenv("GITHUB_RUN_NUMBER"),
                )
                print("Telegram AI test failure message sent.")
            except Exception as telegram_exc:
                print(f"Could not send AI test failure to Telegram: {type(telegram_exc).__name__}")
            raise

    db = connect()
    db.close()

    scan_id = os.getenv("GITHUB_RUN_ID") or uuid.uuid4().hex
    stores = list(STORES.keys())
    random.shuffle(stores)

    total = alerts = failures = ai_skips = 0
    price_candidates = cooldown_skips = 0
    telegram_errors = int(startup_failed)
    empty_stores = 0
    newly_archived = 0
    potential = verified = sufficient = 0
    providers = set()
    store_results = {}
    print(f"Starting scan across {len(stores)} stores")

    for store in stores:
        _, query = choose_search(store)
        print(f"Searching {store}: {query}")
        try:
            items = fetch_store(store, query)
            print(f"{store}: parsed {len(items)} products")
            store_results[store] = {"products": len(items), "error": None}
            if not items:
                empty_stores += 1
            else:
                items.extend(refresh_tracked(store, {i["url"] for i in items}))

            for item in items:
                total += 1
                try:
                    item["_scan_id"] = scan_id
                    deal = save_and_evaluate(item)
                except ValueError as exc:
                    print(f"Invalid product skipped: {exc}")
                    continue
                ai_result = analyze_deal(item)
                providers.add(ai_result.get("provider", "Fallback"))
                if item.get("_is_new_product"):
                    newly_archived += 1
                potential += item.get("status") == "POTENTIAL_DEAL"
                verified += item.get("status") == "VERIFIED_DEAL"
                sufficient += item.get("baseline") is not None
                db = connect()
                prior = db.execute("SELECT status FROM alert_history WHERE product_key=? ORDER BY id DESC LIMIT 1", (item["_product_key"],)).fetchone()
                db.close()
                notify_new = os.getenv("NOTIFY_NEW_PRODUCTS", "false").lower() == "true"
                if not deal and not (prior and (prior[0] == "VERIFIED_DEAL" or item.get("status") != prior[0])) and not (notify_new and item.get("_is_new_product")):
                    continue
                deal = item

                price_candidates += 1
                print(
                    f"PRICE CANDIDATE: {deal['title']} | "
                    f"price={deal['price']} SAR | baseline={deal.get('baseline')} SAR | "
                    f"drop={deal.get('drop_percent')}%"
                )
                if was_recently_alerted(deal):
                    cooldown_skips += 1
                    print(f"Skipping repeat alert within cooldown: {deal['title']}")
                    continue

                deal["ai_provider"] = ai_result.get("provider", "AI")
                deal["ai_reason"] = ai_result.get("reason", "")
                deal["ai_message"] = ai_result.get("telegram_message", "")
                try:
                    send_deal(deal)
                except Exception as exc:
                    telegram_errors += 1
                    print(f"TELEGRAM SEND FAILED: {type(exc).__name__}")
                    continue

                record_alert(deal)
                alerts += 1
                print(f"TELEGRAM ALERT SENT: {deal['title']}")

        except Exception as exc:
            failures += 1
            store_results[store] = {"products": 0, "error": type(exc).__name__}
            print(f"Store/search failed: {store} / {query}: {type(exc).__name__}: {exc}")

    from ai_analyzer import evaluate_archive_backlog
    providers.update(evaluate_archive_backlog())
    archive_stats = get_archive_stats()
    from dashboard import snapshot
    sufficient = snapshot()["stats"]["sufficient"]
    scan_result = dict(scan_number=os.getenv("GITHUB_RUN_NUMBER"),stores=store_results,products=total,new_products=newly_archived,potential_deals=potential,verified_deals=verified,sufficient_history=sufficient,store_errors=failures,empty_stores=empty_stores,telegram_errors=telegram_errors,ai_providers=sorted(providers),archive=archive_stats)
    Path("scan-results.json").write_text(json.dumps(scan_result,ensure_ascii=False,indent=2),encoding="utf-8")
    summary_ai = analyze_deal({"title":"ملخص نشاط مراقبة الأسعار", "status":"SCAN_SUMMARY", "description": json.dumps(scan_result,ensure_ascii=False)})
    print(
        f"Finished. stores_checked={len(stores)}, products={total}, "
        f"newly_archived={newly_archived}, archive_total={archive_stats['products']}, "
        f"price_observations={archive_stats['price_observations']}, "
        f"stores_with_products={archive_stats['stores_with_products']}, "
        f"empty_stores={empty_stores}, price_candidates={price_candidates}, "
        f"cooldown_skips={cooldown_skips}, ai_skips={ai_skips}, "
        f"telegram_errors={telegram_errors}, alerts={alerts}, failures={failures}, "
        f"no_price_signal={max(0, total - price_candidates)}"
    )
    try:
        send_scan_summary(
            run_number=os.getenv("GITHUB_RUN_NUMBER"),
            products_seen=total,
            new_products=newly_archived,
            failures=failures,
            empty_stores=empty_stores,
            archive_stats=archive_stats,
            stores_checked=len(stores), successful_stores=len(stores)-failures-empty_stores,
            potential_deals=potential, verified_deals=verified, sufficient_history=sufficient,
            providers=", ".join(sorted(providers)) or "غير مستخدم",
            comment=summary_ai.get("telegram_message", ""),
        )
        print("Telegram archive summary sent successfully.")
    except Exception as exc:
        print(f"Telegram archive summary failed: {type(exc).__name__}")
        telegram_errors += 1

    from dashboard import export
    export()
    checkpoint()
    # A green workflow with zero extracted products is not a healthy scan.
    if failures or telegram_errors or empty_stores:
        raise RuntimeError(f"Degraded scan: store_errors={failures}, empty_stores={empty_stores}, telegram_errors={telegram_errors}")
    if total == 0:
        raise RuntimeError(
            "No products were extracted from any store. "
            "Check store access, anti-bot pages, and scraper selectors."
        )


if __name__ == "__main__":
    main()
