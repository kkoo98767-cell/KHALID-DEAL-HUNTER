"""Read-only, active-store projection; archived rows are never deleted."""
import json
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse
from config import STORES, MIN_DROP_PERCENT
from database import connect, historical_assessment
from deal_evidence import evaluate, matching_offers, age_hours
from scrapers.common import reliable_title, product_identifier, canonical_url


def _metadata(raw):
    try:
        result=json.loads(raw) if raw else {}
        return result if isinstance(result,dict) else {}
    except (ValueError,TypeError):
        return {}


def _image_url(raw):
    if not isinstance(raw,str):
        return None
    parsed=urlparse(raw)
    host=(parsed.hostname or "").lower()
    allowed=("media-amazon.com","images-amazon.com","ssl-images-amazon.com","amazon.sa","jarir.com","jarirbookstore.com")
    if parsed.scheme=="https" and not parsed.username and not parsed.password and any(host==h or host.endswith("."+h) for h in allowed):
        return raw
    return None


def _identity(store,url):
    parsed=urlparse(url)
    host=(parsed.hostname or "").lower()
    expected=urlparse(STORES[store]).hostname or ""
    expected=expected.removeprefix("www.")
    if parsed.scheme!="https" or host.removeprefix("www.")!=expected:
        return None
    identifier=product_identifier(url)
    if not identifier:
        return None
    return store,identifier


def _scan_scope(scan):
    if not isinstance(scan,dict):
        return None
    result=dict(scan)
    result["stores"]={name:value for name,value in scan.get("stores",{}).items() if name in STORES}
    result["store_errors"]=sum(bool(v.get("error")) for v in result["stores"].values())
    result["empty_stores"]=sum(not v.get("error") and not v.get("products") for v in result["stores"].values())
    return result


def snapshot():
    db=connect()
    histories=defaultdict(list)
    for key,price,stamp,original,validated,observation_id in db.execute("SELECT h.product_key,h.price,h.checked_at,h.store_original_price,h.validated,h.id FROM price_history h JOIN (SELECT product_key,MAX(id) AS latest FROM price_history GROUP BY product_key) last ON last.product_key=h.product_key WHERE h.checked_at >= datetime('now','-90 days') OR h.id=last.latest ORDER BY h.checked_at,h.id"):
        histories[key].append((price,stamp,original,bool(validated),observation_id))
    active_observations=0
    active_rows=0
    grouped=defaultdict(list)
    rows=db.execute("SELECT product_key,store,title,url,brand,category,evaluation,product_id,gtin,metadata,first_seen,last_seen FROM products ORDER BY last_seen DESC").fetchall()
    for row in rows:
        key,store,title,url,*_=row
        if store not in STORES:
            continue
        active_rows+=1
        active_observations+=len(histories[key])
        identity=_identity(store,url)
        if not identity or not reliable_title(title) or not histories[key]:
            continue
        grouped[identity].append(row)
    products=[]
    for identity,members in grouped.items():
        # Canonical duplicate rows remain in SQLite. Their exact same-product
        # observations share one feed listing, and never gain extra daily votes.
        members.sort(key=lambda row:(histories[row[0]][-1][1],histories[row[0]][-1][4],histories[row[0]][-1][3],row[-1]),reverse=True)
        key,store,title,url,brand,category,evaluation,pid,gtin,metadata,first_seen,last_seen=members[0]
        history=sorted({observation for row in members for observation in histories[row[0]]},key=lambda v:(v[1],v[4]))
        price,stamp,original,validated,_=history[-1]
        meta=_metadata(metadata)
        offer=meta.get("current_offer",{})
        # A separately observed, unchanged current offer can have a newer
        # crossed-out price than the hourly price sample. Never infer it.
        if validated and isinstance(offer,dict) and offer.get("validated") and offer.get("price")==price and str(offer.get("seen_at", ""))>=stamp:
            original=offer.get("original_price")
            stamp=offer["seen_at"]
            validated=True
        shipping = offer.get("shipping_sar") if isinstance(offer,dict) and offer.get("seen_at")==stamp and offer.get("price")==price else None
        previous=[(p,d) for p,d,_,valid,_ in history[:-1] if valid]
        assessment=historical_assessment(previous,price)
        if assessment["status"]=="INSUFFICIENT_HISTORY" and original and original>price and (original-price)/original*100>=MIN_DROP_PERCENT:
            assessment["status"]="POTENTIAL_DEAL"
        analysis=_metadata(evaluation) or None
        product=dict(shipping_sar=shipping,id=key,store=store,title=title,url=canonical_url(url),brand=brand,category=category,price=price,currency="SAR",last_seen=stamp,first_seen=min(row[-2] for row in members),history=[dict(price=p,date=d,validated=valid) for p,d,_,valid,_ in history[-90:]],analysis=analysis,product_id=pid or identity[1],gtin=gtin,store_original_price=original,latest_validated=validated,data_sources=meta.get("data_sources",[]),image_url=_image_url(meta.get("image_url")),**assessment)
        product.update({k:meta[k] for k in ("model","color","capacity","condition","size") if meta.get(k)})
        recent_prices = [p for p,d,_,valid,_ in history if valid and age_hours(d)<=24*30]
        product["lowest_observed_30d"] = min(recent_prices) if recent_prices else None
        product["history_summary"]={"observations":len(history),"validated_observations":sum(v[3] for v in history),"observed_days":assessment["history_days"],"coverage_days":assessment["history_coverage_days"],"required_days":assessment["history_required_days"],"required_coverage_days":assessment["history_required_coverage_days"],"sufficient":assessment["baseline"] is not None,"latest_validated":validated,"first_observed":history[0][1],"last_observed":stamp}
        products.append(product)
    by_gtin=defaultdict(list)
    for product in products:
        if product.get("gtin"):
            by_gtin[product["gtin"]].append(product)
    for product in products:
        evidence=evaluate(product,matching_offers(product,by_gtin.get(product.get("gtin"),[])))
        product["evidence"]=evidence
        product["featured"]=evidence["featured"]
        product["deal_type"]="ANOMALY" if product["status"]=="DATA_ANOMALY" else "STALE" if age_hours(product["last_seen"])>24 else "VERIFIED" if evidence["verified_deal"] else "POTENTIAL" if product["featured"] else "MONITORING"
        if evidence["verified_deal"]:
            product["display_discount_percent"]=evidence["historical_savings"]["percent"]
            product["display_discount_source"]="HISTORICAL"
        elif evidence["advertised_savings"]["percent"] is not None:
            product["display_discount_percent"]=evidence["advertised_savings"]["percent"]
            product["display_discount_source"]="ADVERTISED"
        else:
            product["display_discount_percent"]=None
            product["display_discount_source"]="NONE"
    observations=db.execute("SELECT COUNT(*) FROM price_history").fetchone()[0]
    archive_products=db.execute("SELECT COUNT(*) FROM products").fetchone()[0]
    # Full count is deliberately independent of the 90-day chart window.
    placeholders=",".join("?" for _ in STORES)
    active_observations=db.execute(f"SELECT COUNT(*) FROM price_history h JOIN products p ON p.product_key=h.product_key WHERE p.store IN ({placeholders})",tuple(STORES)).fetchone()[0] if STORES else 0
    db.close()
    report_path=Path("scan-results.json")
    try:
        scan=_scan_scope(json.loads(report_path.read_text(encoding="utf-8"))) if report_path.exists() else None
    except (ValueError,OSError):
        scan=None
    store_stats={store:{"products":sum(p["store"]==store for p in products),"featured_25":sum(p["store"]==store and p["featured"] for p in products),"verified":sum(p["store"]==store and p["evidence"]["verified_deal"] for p in products)} for store in STORES}
    from discovery import SEARCH_POOL
    from config import PREFERRED_TERMS
    return dict(interest_terms=SEARCH_POOL + PREFERRED_TERMS,schema_version=3,active_stores=list(STORES),refresh_seconds=60,discovery_threshold_percent=MIN_DROP_PERCENT,score_methodology={"scale":"0–100: evidence strength, not purchase quality or probability","components":{"identity":10,"freshness":20,"history":50,"cross_store":20},"insufficient_or_stale_cap":39},generated_at=datetime.now(timezone.utc).isoformat(),stats=dict(products=len(products),observations=observations,active_observations=active_observations,verified=sum(p["evidence"]["verified_deal"] for p in products),potential=sum(p["deal_type"]=="POTENTIAL" for p in products),featured_25=sum(p["featured"] for p in products),sufficient=sum(p["baseline"] is not None for p in products),archive_products=archive_products,hidden_legacy_products=active_rows-len(products)),store_stats=store_stats,scan=scan,products=products)


def export(path="data/dashboard.json"):
    target=Path(path)
    temp=target.with_suffix(".tmp")
    temp.write_text(json.dumps(snapshot(),ensure_ascii=False,indent=2),encoding="utf-8")
    temp.replace(target)


if __name__=="__main__":
    export()
