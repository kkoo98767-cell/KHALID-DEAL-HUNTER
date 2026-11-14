import os
import random
import time
import requests
from urllib.parse import quote_plus

from config import STORES, REQUEST_TIMEOUT, MAX_PRODUCTS_PER_SEARCH
from discovery import choose_search
from scrapers.common import parse_jsonld_products, enrich_product_details
from database import save_and_evaluate, connect, was_recently_alerted, record_alert, get_archive_stats
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
        "access denied", "unusual traffic", "challenge-platform",
        "cf-chl-", "px-captcha", "enable javascript and cookies",
    )
    return any(marker in sample for marker in markers)


def fetch_store(store, query):
    urls = _search_url_variants(store, query)
    last_error = None
    valid_response_seen = False
    request_timeout = min(REQUEST_TIMEOUT, 18)

    with requests.Session() as session:
        for url_index, url in enumerate(urls, start=1):
            for profile_number, headers in enumerate(REQUEST_HEADERS, start=1):
                for attempt in range(1, 3):
                    try:
                        response = session.get(
                            url, headers=headers, timeout=request_timeout,
                            allow_redirects=True,
                        )
                        if response.status_code in TRANSIENT_STATUS:
                            raise requests.HTTPError(
                                f"Transient HTTP {response.status_code}",
                                response=response,
                            )
                        response.raise_for_status()
                        valid_response_seen = True
                        last_error = None
                        html = response.text or ""
                        if not html:
                            print(f"{store}: empty response body (URL {url_index}, profile {profile_number})")
                            break

                        blocked = _looks_like_challenge(html)
                        items = parse_jsonld_products(
                            html, response.url, store,
                            limit=MAX_PRODUCTS_PER_SEARCH,
                        )
                        source_counts = {}
                        for item in items:
                            for source in item.get("data_sources", []):
                                source_counts[source] = source_counts.get(source, 0) + 1
                        print(
                            f"{store}: HTTP {response.status_code}, {len(response.content)} bytes, "
                            f"products={len(items)}, extraction={source_counts or 'none'}, "
                            f"challenge={blocked}, URL variant={url_index}, "
                            f"profile={profile_number}, attempt={attempt}"
                        )
                        if items:
                            return items
                        break
                    except Exception as exc:
                        last_error = exc
                        print(
                            f"{store}: URL variant {url_index}, profile {profile_number}, "
                            f"attempt {attempt} failed: {type(exc).__name__}: {exc}"
                        )
                        if attempt == 1:
                            time.sleep(1.5)
    if last_error and not valid_response_seen:
        raise last_error
    return []


def main():
    provider_test = os.getenv("AI_PROVIDER_TEST", "none").strip().lower()
    is_ai_test = provider_test in {"gemini", "groq"}

    send_startup_status(
        run_number=os.getenv("GITHUB_RUN_NUMBER"),
        run_id=os.getenv("GITHUB_RUN_ID"),
        mode="ai_test" if is_ai_test else "hunt",
    )
    print("Telegram startup heartbeat sent successfully.")

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
                print(f"Could not send AI test failure to Telegram: {type(telegram_exc).__name__}: {telegram_exc}")
            raise

    db = connect()
    db.close()

    stores = list(STORES.keys())
    random.shuffle(stores)

    total = alerts = failures = ai_skips = 0
    price_candidates = cooldown_skips = telegram_errors = 0
    empty_stores = 0
    newly_archived = 0
    print(f"Starting scan across {len(stores)} stores")

    for store in stores:
        _, query = choose_search(store)
        print(f"Searching {store}: {query}")
        try:
            items = fetch_store(store, query)
            print(f"{store}: parsed {len(items)} products")
            if not items:
                empty_stores += 1

            for item in items:
                total += 1
                deal = save_and_evaluate(item)
                if item.get("_is_new_product"):
                    newly_archived += 1
                if not deal:
                    continue

                price_candidates += 1
                print(
                    f"PRICE CANDIDATE: {deal['title']} | "
                    f"price={deal['price']} SAR | baseline={deal['baseline']} SAR | "
                    f"drop={deal['drop_percent']}%"
                )
                if was_recently_alerted(deal):
                    cooldown_skips += 1
                    print(f"Skipping repeat alert within cooldown: {deal['title']}")
                    continue

                deal = enrich_product_details(deal)
                try:
                    ai_result = analyze_deal(deal)
                except Exception as exc:
                    ai_skips += 1
                    print(f"AI evaluation failed; alert skipped: {type(exc).__name__}: {exc}")
                    continue

                if not ai_result["worth_alerting"]:
                    ai_skips += 1
                    print(
                        f"{ai_result.get('provider', 'AI')} rejected candidate: "
                        f"{ai_result.get('reason', 'No reason')}"
                    )
                    continue

                deal["ai_provider"] = ai_result.get("provider", "AI")
                deal["ai_reason"] = ai_result.get("reason", "")
                deal["ai_message"] = ai_result.get("telegram_message", "")
                try:
                    send_deal(deal)
                except Exception as exc:
                    telegram_errors += 1
                    print(f"TELEGRAM SEND FAILED for {deal['title']}: {type(exc).__name__}: {exc}")
                    continue

                record_alert(deal)
                alerts += 1
                print(f"TELEGRAM ALERT SENT: {deal['title']}")

        except Exception as exc:
            failures += 1
            print(f"Store/search failed: {store} / {query}: {type(exc).__name__}: {exc}")

    archive_stats = get_archive_stats()
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
        )
        print("Telegram archive summary sent successfully.")
    except Exception as exc:
        print(f"Telegram archive summary failed: {type(exc).__name__}: {exc}")
        telegram_errors += 1

    # A green workflow with zero extracted products is not a healthy scan.
    if total == 0:
        raise RuntimeError(
            "No products were extracted from any store. "
            "Check store access, anti-bot pages, and scraper selectors."
        )


if __name__ == "__main__":
    main()
