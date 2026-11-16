"""Read-only archive projection shared by the API and static dashboard."""
import json
from datetime import datetime, timezone
from pathlib import Path
from database import connect, historical_assessment
from scrapers.common import reliable_title
from urllib.parse import urlparse
from deal_evidence import evaluate, matching_offers
from collections import defaultdict
from scrapers.common import product_identifier


def snapshot():
    db = connect()
    products = []
    histories = defaultdict(list)
    for key,price,stamp,original,validated in db.execute("SELECT h.product_key,h.price,h.checked_at,h.store_original_price,h.validated FROM price_history h JOIN (SELECT product_key,MAX(id) AS latest FROM price_history GROUP BY product_key) last ON last.product_key=h.product_key WHERE h.checked_at >= datetime('now','-90 days') OR h.id=last.latest ORDER BY h.checked_at,h.id"):
        histories[key].append((price,stamp,original,validated))
    for key,store,title,url,brand,category,evaluation,pid,gtin,metadata in db.execute("SELECT product_key,store,title,url,brand,category,evaluation,product_id,gtin,metadata FROM products ORDER BY last_seen DESC"):
        history = histories[key]
        if not history:
            continue
        price = history[-1][0]
        assessment = historical_assessment([(p,d) for p,d,_,valid in history[:-1] if valid],price)
        if not reliable_title(title) or (store=="Amazon.sa" and not product_identifier(url)) or urlparse(url).hostname in {"sponsored-ads.amazon.sa", "aax-eu-zaz.amazon.sa"}:
            assessment.update(status="DATA_ANOMALY",baseline=None,drop_percent=None)
        try:
            analysis = json.loads(evaluation) if evaluation else None
        except (ValueError,TypeError):
            analysis = None
        product = dict(id=key,store=store,title=title,url=url,brand=brand,category=category,price=price,currency="SAR",last_seen=history[-1][1],history=[dict(price=p,date=d) for p,d,_,_ in history[-90:]],analysis=analysis,product_id=pid or product_identifier(url),gtin=gtin,store_original_price=history[-1][2],**assessment)
        try:
            meta=json.loads(metadata) if metadata else {}
        except (ValueError,TypeError):
            meta={}
        product.update({k:meta[k] for k in ("model","color","capacity","condition","size") if meta.get(k)})
        products.append(product)
    by_gtin = defaultdict(list)
    for product in products:
        if product.get("gtin"):
            by_gtin[product["gtin"]].append(product)
    for product in products:
        product["evidence"]=evaluate(product,matching_offers(product,by_gtin.get(product.get("gtin"),[])))
    observations = db.execute("SELECT COUNT(*) FROM price_history").fetchone()[0]
    db.close()
    report_path = Path("scan-results.json")
    scan = json.loads(report_path.read_text(encoding="utf-8")) if report_path.exists() else None
    return dict(schema_version=2,refresh_seconds=60,score_methodology={"scale":"0–100: evidence strength, not purchase quality or probability", "components":{"identity":10,"freshness":20,"history":50,"cross_store":20},"insufficient_or_stale_cap":39},generated_at=datetime.now(timezone.utc).isoformat(),stats=dict(products=len(products),observations=observations,verified=sum(p["evidence"]["verified_deal"] for p in products),sufficient=sum(p["baseline"] is not None for p in products)),scan=scan,products=products)


def export(path="data/dashboard.json"):
    target = Path(path)
    temp = target.with_suffix(".tmp")
    temp.write_text(json.dumps(snapshot(),ensure_ascii=False,indent=2),encoding="utf-8")
    temp.replace(target)


if __name__ == "__main__":
    export()
