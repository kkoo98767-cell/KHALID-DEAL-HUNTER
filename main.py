import os
import random
import time
import requests
import json
import uuid
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from urllib.parse import quote_plus, urljoin, urlparse
from bs4 import BeautifulSoup

from config import (STORES, MAX_PRODUCTS_PER_SEARCH, MAX_SEARCH_PAGES, MAX_PRODUCTS_PER_STORE,
                    QUERIES_PER_STORE, TRACKED_PRODUCTS_PER_STORE, REQUEST_GAP_SECONDS, MIN_DROP_PERCENT)
from discovery import choose_search, choose_queries
from scrapers.common import parse_jsonld_products, canonical_url, product_identifier, reliable_title
from database import save_and_evaluate, connect, was_recently_alerted, record_alert, get_archive_stats, checkpoint
from database import store_cooldown, set_store_cooldown
from config import TRACKED_JARIR_PRODUCTS
from ai_analyzer import analyze_deal, fallback_result
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

TRANSIENT_STATUS = {408, 425, 500, 502, 503, 504}
MAX_RETRY_WAIT = 12


def _search_url_variants(store, query):
    """Use each store's native search route plus a conservative URL fallback."""
    encoded = quote_plus(query)
    primary = STORES[store].format(query=encoded)
    variants = [primary]
    if store == "Amazon.sa":
        variants.append(f"https://www.amazon.sa/s?k={encoded}&language=en_AE")
    return list(dict.fromkeys(variants))


def _looks_like_challenge(html):
    sample = html[:250_000].lower()
    markers = (
        "captcha", "verify you are human", "robot check",
        "access denied", "unusual traffic", "challenge-platform", "down for maintenance",
        "cf-chl-", "px-captcha", "enable javascript and cookies",
        "to discuss automated access to amazon data", "api-services-support@amazon.com",
        "click the button below to continue shopping",
    )
    return any(marker in sample for marker in markers)


class StoreReadError(RuntimeError):
    """Carries already validated products when a later page fails."""
    def __init__(self,message,products=None,blocked=False):
        super().__init__(message)
        self.products=products or []
        self.blocked=blocked


class EmptyStoreResults(StoreReadError):
    """HTTP success did not yield a usable, validated price observation."""


def _blocked_exception(exc):
    if getattr(exc,"blocked",False):
        return True
    response=getattr(exc,"response",None)
    return response is not None and response.status_code in {401,403,429}


def _retry_wait(value):
    if not value:
        return 0
    if value.isdigit():
        return int(value)
    try:
        target = parsedate_to_datetime(value)
        if target.tzinfo is None:
            target = target.replace(tzinfo=timezone.utc)
        return max(0, (target - datetime.now(timezone.utc)).total_seconds())
    except (TypeError, ValueError, OverflowError):
        return 0


def _fetch_search_page(session, url):
    for attempt in range(3):
        try:
            response = session.get(url, headers=REQUEST_HEADERS[0], timeout=(5, 18))
        except (requests.Timeout, requests.ConnectionError):
            if attempt == 2:
                raise
            time.sleep(2 ** attempt)
            continue
        status = response.status_code
        if status in {401, 403, 429}:
            raise StoreReadError(f"Store refused requests: HTTP {status}", blocked=True)
        if (status == 200 or status in TRANSIENT_STATUS) and _looks_like_challenge(response.text):
            raise StoreReadError("Store challenge page; access protection respected", blocked=True)
        if status not in TRANSIENT_STATUS:
            return response
        wait = _retry_wait(response.headers.get("Retry-After"))
        if wait > MAX_RETRY_WAIT or (wait and attempt == 2):
            raise StoreReadError(f"Store returned HTTP {status} and requested a cooldown", blocked=True)
        if attempt == 2:
            return response
        time.sleep(max(wait, 3 * (2 ** attempt) + random.uniform(0, 1)))


def fetch_store(store, query):
    if store not in STORES:
        raise ValueError("Store is not enabled for monitoring")
    items,seen=[],set()
    variants=_search_url_variants(store,query)
    pending=[variants.pop(0)]
    visited=set()
    with requests.Session() as session:
        try:
            while pending and len(visited)<MAX_SEARCH_PAGES and len(items)<MAX_PRODUCTS_PER_SEARCH:
                url=pending.pop(0)
                if url in visited:
                    continue
                visited.add(url)
                response=_fetch_search_page(session,url)
                if response.status_code==404:
                    if not items and variants:
                        pending.append(variants.pop(0))
                    continue
                response.raise_for_status()
                remaining=MAX_PRODUCTS_PER_SEARCH-len(items)
                parsed=parse_jsonld_products(response.text,response.url,store,limit=remaining)
                if not parsed and store=="Jarir":
                    from scrapers.jarir import discover
                    parsed=discover(session,response.text,query,REQUEST_HEADERS[0],remaining)
                print(f"{store}: HTTP {response.status_code}, bytes={len(response.content)}, validated_products={len(parsed)}")
                for item in parsed:
                    key=canonical_url(item["url"])
                    if key not in seen:
                        seen.add(key);items.append(item)
                next_link=BeautifulSoup(response.text,"html.parser").select_one('a[rel="next"], a.s-pagination-next[href]')
                if next_link and next_link.get("href"):
                    next_url=urljoin(response.url,next_link["href"])
                    if urlparse(next_url).hostname==urlparse(STORES[store]).hostname and next_url not in visited:
                        pending.append(next_url)
                if not items and not pending and variants:
                    pending.append(variants.pop(0))
                if pending:
                    time.sleep(REQUEST_GAP_SECONDS)
        except Exception as exc:
            partial=items+getattr(exc,"products",[])
            if partial or isinstance(exc,StoreReadError):
                unique={canonical_url(p["url"]):p for p in partial}
                raise StoreReadError(str(exc) if isinstance(exc,StoreReadError) else f"{store} source failed: {type(exc).__name__}",list(unique.values()),_blocked_exception(exc)) from None
            raise
    if not items:
        raise EmptyStoreResults("Public search yielded no validated products; results may be empty or unreadable")
    return items[:MAX_PRODUCTS_PER_SEARCH]


def merge_products(items):
    """Conflicting observations are held back instead of inflating deal counts."""
    unique={};conflicts=set()
    for item in items:
        key=canonical_url(item["url"])
        if key in unique:
            previous=unique[key]
            if abs(previous["price"]-item["price"])>max(.01,previous["price"]*.01):
                conflicts.add(key)
            for field in ("image_url","original_price","brand","category"):
                if not previous.get(field) and item.get(field):
                    previous[field]=item[field]
            continue
        unique[key]=item
    return [p for k,p in unique.items() if k not in conflicts],len(conflicts)


def advertised_drop(item):
    from deal_evidence import savings
    return savings(item.get("original_price"),item.get("price"))["percent"] or 0


def collect_store(store,queries=None):
    if store not in STORES:
        raise ValueError("Store is not enabled for monitoring")
    cooldown = store_cooldown(store)
    if cooldown and cooldown["retry_after"] > time.time():
        return [], dict(products=0, queries_checked=0, queries_requested=0,
                        error="StoreCooldown", query_errors=0, conflicting_products=0,
                        retry_after=cooldown["retry_after"], reason=cooldown["reason"], blocked=True)
    queries=queries or choose_queries(store)
    items=[];errors=[];checked=0;blocked=False
    for query in queries:
        if len({canonical_url(p["url"]) for p in items})>=MAX_PRODUCTS_PER_STORE:
            break
        checked+=1
        print(f"Searching {store}: {query}")
        try:
            batch=fetch_store(store,query)
            if not batch:
                raise EmptyStoreResults("Public search yielded no validated products")
            items.extend(batch)
        except Exception as exc:
            items.extend(getattr(exc,"products",[]))
            errors.append(type(exc).__name__)
            print(f"Store/search failed: {store} / {query}: {type(exc).__name__}: {exc}")
            blocked = _blocked_exception(exc)
            if blocked or len(errors)>=2:
                break
        if checked<len(queries):
            time.sleep(REQUEST_GAP_SECONDS)
    items,conflicts=merge_products(items)
    items.sort(key=lambda p:advertised_drop(p),reverse=True)
    retry_after = time.time() + (21600 if blocked else 1800) if errors else 0
    set_store_cooldown(store, retry_after, errors[0] if errors else "")
    return items[:MAX_PRODUCTS_PER_STORE],dict(products=min(len(items),MAX_PRODUCTS_PER_STORE),queries_checked=checked,queries_requested=len(queries),error=errors[0] if errors else None,query_errors=len(errors),conflicting_products=conflicts,blocked=blocked,retry_after=retry_after)


def refresh_tracked(store,seen):
    """Revisit more prior deal candidates, using Jarir's public batch catalog."""
    if not TRACKED_PRODUCTS_PER_STORE:
        return []
    refresh_limit = TRACKED_JARIR_PRODUCTS if store == "Jarir" else TRACKED_PRODUCTS_PER_STORE
    db=connect()
    rows=db.execute("SELECT p.url,p.product_id,p.title FROM products p JOIN price_history h ON h.id=(SELECT MAX(id) FROM price_history WHERE product_key=p.product_key) WHERE p.store=? ORDER BY CASE WHEN h.store_original_price>0 AND (h.store_original_price-h.price)/h.store_original_price*100>=? THEN 0 ELSE 1 END,p.last_seen LIMIT ?",(store,MIN_DROP_PERCENT,refresh_limit*3)).fetchall()
    db.close()
    rows=[(url,pid or product_identifier(url)) for url,pid,title in rows if canonical_url(url) not in seen and reliable_title(title) and urlparse(url).hostname==urlparse(STORES[store]).hostname][:refresh_limit]
    if store=="Jarir":
        import re
        from scrapers.jarir import fetch_catalog
        skus=list(dict.fromkeys(pid for _,pid in rows if pid and re.fullmatch(r"[0-9]{3,12}",pid)))
        if not skus:
            return []
        try:
            with requests.Session() as session:
                return fetch_catalog(session,skus,REQUEST_HEADERS[0])
        except Exception as exc:
            print(f"{store}: tracked refresh failed: {type(exc).__name__}")
            return getattr(exc,"products",[])
    items=[]
    for url,pid in rows:
        if not pid:
            continue
        try:
            response=requests.get(url,headers=REQUEST_HEADERS[0],timeout=(5,12))
            if response.status_code in {401,403,429} or _looks_like_challenge(response.text):
                print(f"{store}: tracked refresh blocked; stopped")
                break
            response.raise_for_status()
            for item in parse_jsonld_products(response.text,response.url,store):
                if canonical_url(item["url"])==canonical_url(url):
                    items.append(item)
        except requests.RequestException as exc:
            print(f"{store}: tracked refresh failed: {type(exc).__name__}")
        time.sleep(REQUEST_GAP_SECONDS)
    return merge_products(items)[0]


def main():
    if os.getenv("AI_PROVIDER_TEST","none").strip().lower() in {"gemini","groq"}:
        from ai_connection_test import main as provider_check
        return provider_check()
    startup_failed=False
    try:
        send_startup_status(run_number=os.getenv("GITHUB_RUN_NUMBER"),run_id=os.getenv("GITHUB_RUN_ID"),mode="hunt")
    except Exception as exc:
        startup_failed=True
        print(f"Telegram startup failed: {type(exc).__name__}")
    db=connect();db.close()
    scan_id=os.getenv("GITHUB_RUN_ID") or uuid.uuid4().hex
    stores=list(STORES);random.shuffle(stores)
    total=alerts=failures=empty_stores=newly_archived=potential=verified=processing_errors=0
    telegram_errors=int(startup_failed)
    providers=set();store_results={};archived=[];scan_seen=set()
    for store in stores:
        try:
            items,report=collect_store(store)
            if items and report["error"] is None:
                items.extend(refresh_tracked(store,{canonical_url(p["url"]) for p in items}))
            items,conflicts=merge_products(items)
            report["conflicting_products"]+=conflicts
            report["observed_products"]=len(items)
            store_results[store]=report
            failures+=bool(report["error"])
            empty_stores+=not items and not report["error"]
            # Archive the entire validated batch before any AI or Telegram work.
            for item in items:
                key=(store,canonical_url(item["url"]))
                if key in scan_seen:
                    continue
                scan_seen.add(key)
                try:
                    item["_scan_id"]=scan_id
                    save_and_evaluate(item)
                except Exception as exc:
                    processing_errors+=1
                    print(f"Product archive failed: {store}: {type(exc).__name__}")
                    continue
                total+=1;newly_archived+=bool(item.get("_is_new_product"))
                potential+=item.get("status")=="POTENTIAL_DEAL"
                verified+=item.get("status")=="VERIFIED_DEAL"
                archived.append(item)
        except Exception as exc:
            failures+=1
            store_results[store]=dict(products=0,error=type(exc).__name__)
            print(f"Store failed: {store}: {type(exc).__name__}: {exc}")
    archived.sort(key=lambda p:(p.get("status")=="VERIFIED_DEAL",p.get("status")=="POTENTIAL_DEAL",advertised_drop(p)),reverse=True)
    unverified_alerts=0
    alert_limit=max(0,int(os.getenv("MAX_UNVERIFIED_ALERTS","12")))
    for item in archived:
        try:
            ai_result=analyze_deal(item)
        except Exception as exc:
            print(f"AI unavailable: {type(exc).__name__}")
            ai_result=fallback_result()
        providers.add(ai_result.get("provider","Fallback"))
        db=connect();prior=db.execute("SELECT status FROM alert_history WHERE product_key=? ORDER BY id DESC LIMIT 1",(item["_product_key"],)).fetchone();db.close()
        notify_new=os.getenv("NOTIFY_NEW_PRODUCTS","false").lower()=="true"
        should_notify=item.get("status") in {"POTENTIAL_DEAL","VERIFIED_DEAL"} or (prior and item.get("status")!=prior[0]) or (notify_new and item.get("_is_new_product"))
        if not should_notify or was_recently_alerted(item):
            continue
        if item.get("status")=="POTENTIAL_DEAL" and unverified_alerts>=alert_limit:
            continue
        item.update(ai_provider=ai_result.get("provider","Fallback"),ai_reason=ai_result.get("reason",""),ai_message=ai_result.get("telegram_message",""))
        try:
            send_deal(item);record_alert(item)
            alerts+=1
            unverified_alerts+=item.get("status")=="POTENTIAL_DEAL"
            print(f"TELEGRAM ALERT SENT: {item['title']}")
        except Exception as exc:
            telegram_errors+=1
            print(f"TELEGRAM SEND FAILED: {type(exc).__name__}")
    from ai_analyzer import evaluate_archive_backlog
    try:
        providers.update(evaluate_archive_backlog())
    except Exception as exc:
        print(f"Archive AI backlog failed: {type(exc).__name__}")
    archive_stats=get_archive_stats()
    from dashboard import snapshot,export
    sufficient=snapshot()["stats"]["sufficient"]
    scan_result=dict(scan_number=os.getenv("GITHUB_RUN_NUMBER"),stores=store_results,products=total,new_products=newly_archived,potential_deals=potential,verified_deals=verified,sufficient_history=sufficient,store_errors=failures,empty_stores=empty_stores,telegram_errors=telegram_errors,processing_errors=processing_errors,alerts_sent=alerts,ai_providers=sorted(providers),archive=archive_stats)
    scan_result["status"] = "UNAVAILABLE" if not total else "PARTIAL" if failures or empty_stores else "COMPLETE"
    coverage_status = scan_result["status"]
    Path("scan-results.json").write_text(json.dumps(scan_result,ensure_ascii=False,indent=2),encoding="utf-8")
    try:
        summary_ai=analyze_deal(dict(title="ملخص نشاط مراقبة الأسعار",status="SCAN_SUMMARY",description=json.dumps(scan_result,ensure_ascii=False)))
    except Exception:
        summary_ai=dict(telegram_message="تم حفظ نتائج الفحص الفعلية؛ التعليق الآلي غير متاح حالياً.")
    try:
        send_scan_summary(run_number=os.getenv("GITHUB_RUN_NUMBER"),products_seen=total,new_products=newly_archived,failures=failures,empty_stores=empty_stores,archive_stats=archive_stats,coverage_status=coverage_status,stores_checked=len(stores),successful_stores=sum(bool(s.get("observed_products")) for s in store_results.values()),potential_deals=potential,verified_deals=verified,sufficient_history=sufficient,providers=", ".join(sorted(providers)) or "غير مستخدم",comment=summary_ai.get("telegram_message",""))
        print("Telegram archive summary sent successfully.")
    except Exception as exc:
        telegram_errors+=1
        print(f"Telegram archive summary failed: {type(exc).__name__}")
    scan_result["telegram_errors"]=telegram_errors
    if telegram_errors or processing_errors:
        scan_result["status"] = "FAILED"
    Path("scan-results.json").write_text(json.dumps(scan_result,ensure_ascii=False,indent=2),encoding="utf-8")
    export();checkpoint()
    print("Finished. "+json.dumps(scan_result,ensure_ascii=False))
    if failures or empty_stores:
        print(f"::warning::Partial store coverage: store_errors={failures}, empty_stores={empty_stores}. See scan-results.json for source details.")
    if telegram_errors or processing_errors or not total:
        raise RuntimeError(f"Degraded scan: store_errors={failures},empty_stores={empty_stores},telegram_errors={telegram_errors},archive_errors={processing_errors}")


if __name__=="__main__":
    main()
