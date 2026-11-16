"""Read-only archive projection shared by the API and static dashboard."""
import json
from datetime import datetime, timezone
from pathlib import Path
from database import connect, historical_assessment
from scrapers.common import reliable_title
from urllib.parse import urlparse


def snapshot():
    db = connect()
    products = []
    for key,store,title,url,brand,category,evaluation in db.execute("SELECT product_key,store,title,url,brand,category,evaluation FROM products ORDER BY last_seen DESC"):
        history = db.execute("SELECT price,checked_at FROM price_history WHERE product_key=? ORDER BY checked_at,id", (key,)).fetchall()
        if not history:
            continue
        price = history[-1][0]
        assessment = historical_assessment(history[:-1],price)
        if not reliable_title(title) or urlparse(url).hostname in {"sponsored-ads.amazon.sa", "aax-eu-zaz.amazon.sa"}:
            assessment.update(status="DATA_ANOMALY",baseline=None,drop_percent=None)
        try:
            analysis = json.loads(evaluation) if evaluation else None
        except (ValueError,TypeError):
            analysis = None
        products.append(dict(id=key,store=store,title=title,url=url,brand=brand,category=category,price=price,currency="SAR",last_seen=history[-1][1],history=[dict(price=p,date=d) for p,d in history[-90:]],analysis=analysis,**assessment))
    observations = db.execute("SELECT COUNT(*) FROM price_history").fetchone()[0]
    db.close()
    report_path = Path("scan-results.json")
    scan = json.loads(report_path.read_text(encoding="utf-8")) if report_path.exists() else None
    return dict(generated_at=datetime.now(timezone.utc).isoformat(),stats=dict(products=len(products),observations=observations,verified=sum(p["status"]=="VERIFIED_DEAL" for p in products),sufficient=sum(p["baseline"] is not None for p in products)),scan=scan,products=products)


def export(path="data/dashboard.json"):
    target = Path(path)
    temp = target.with_suffix(".tmp")
    temp.write_text(json.dumps(snapshot(),ensure_ascii=False,indent=2),encoding="utf-8")
    temp.replace(target)


if __name__ == "__main__":
    export()
