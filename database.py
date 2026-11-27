import sqlite3
from pathlib import Path
from statistics import median
from datetime import datetime, timezone
import math
import json
import os
import unicodedata
from scrapers.common import canonical_url

from config import DB_PATH, MIN_HISTORY_POINTS, MIN_DROP_PERCENT, MIN_HISTORY_DAYS


def connect():
    Path(DB_PATH).parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(DB_PATH, timeout=30)
    db.execute("PRAGMA journal_mode=WAL")
    db.execute("PRAGMA busy_timeout=30000")
    db.execute("""
        CREATE TABLE IF NOT EXISTS products (
            product_key TEXT PRIMARY KEY,
            store TEXT NOT NULL,
            title TEXT NOT NULL,
            url TEXT NOT NULL,
            first_seen TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            last_seen TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
    """)
    db.execute("""
        CREATE TABLE IF NOT EXISTS price_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            product_key TEXT NOT NULL,
            price REAL NOT NULL,
            checked_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
    """)
    db.execute("""
        CREATE TABLE IF NOT EXISTS sent_alerts (
            product_key TEXT PRIMARY KEY,
            last_price REAL NOT NULL,
            sent_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
    """)
    db.execute("CREATE INDEX IF NOT EXISTS idx_price_history_product_time ON price_history(product_key, id DESC)")
    columns = {r[1] for r in db.execute("PRAGMA table_info(products)")}
    for name in ("product_id", "brand", "category", "currency", "evaluation", "gtin", "normalized_name", "metadata"):
        if name not in columns:
            db.execute(f"ALTER TABLE products ADD COLUMN {name} TEXT")
    history_columns = {r[1] for r in db.execute("PRAGMA table_info(price_history)")}
    if "validated" not in history_columns:
        db.execute("ALTER TABLE price_history ADD COLUMN validated INTEGER NOT NULL DEFAULT 0")
    if "scan_id" not in history_columns:
        db.execute("ALTER TABLE price_history ADD COLUMN scan_id TEXT")
    if "store_original_price" not in history_columns:
        db.execute("ALTER TABLE price_history ADD COLUMN store_original_price REAL")
    db.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_history_scan ON price_history(product_key,scan_id) WHERE scan_id IS NOT NULL")
    db.execute("CREATE INDEX IF NOT EXISTS idx_product_gtin ON products(gtin)")
    db.execute("CREATE TABLE IF NOT EXISTS alert_history (id INTEGER PRIMARY KEY, product_key TEXT, price REAL, status TEXT, sent_at TEXT DEFAULT CURRENT_TIMESTAMP)")
    db.execute("CREATE INDEX IF NOT EXISTS idx_alert_history ON alert_history(product_key,id DESC)")
    db.execute("CREATE TABLE IF NOT EXISTS ai_cache (cache_key TEXT PRIMARY KEY, result TEXT NOT NULL, created_at TEXT DEFAULT CURRENT_TIMESTAMP)")
    db.execute("CREATE TABLE IF NOT EXISTS discovery_history (id INTEGER PRIMARY KEY, store TEXT, query TEXT, searched_at TEXT DEFAULT CURRENT_TIMESTAMP)")
    db.execute("CREATE TABLE IF NOT EXISTS product_aliases (canonical_url TEXT PRIMARY KEY, product_key TEXT NOT NULL)")
    db.execute("CREATE TABLE IF NOT EXISTS store_health (store TEXT PRIMARY KEY, retry_after REAL NOT NULL, reason TEXT NOT NULL)")
    if db.execute("PRAGMA user_version").fetchone()[0] < 5:
        for key,url in db.execute("SELECT product_key,url FROM products").fetchall():
            db.execute("INSERT OR IGNORE INTO product_aliases VALUES(?,?)", (canonical_url(url),key))
    if db.execute("PRAGMA user_version").fetchone()[0] < 5:
        db.execute("PRAGMA user_version=5")
    db.commit()
    return db


def store_cooldown(store):
    db = connect()
    try:
        row = db.execute("SELECT retry_after,reason FROM store_health WHERE store=?", (store,)).fetchone()
    finally:
        db.close()
    return dict(retry_after=row[0], reason=row[1]) if row else None


def set_store_cooldown(store, retry_after, reason):
    db = connect()
    try:
        with db:
            db.execute("INSERT OR REPLACE INTO store_health VALUES(?,?,?)", (store, retry_after, reason))
    finally:
        db.close()


def _product_key(item):
    return item.get("_product_key") or canonical_url(item["url"])


def save_and_evaluate(item, minimum_sample_minutes=60):
    """
    Archive every valid product. Avoid filling the history with identical
    15-minute readings: unchanged prices are sampled at most once per hour,
    while a changed price is recorded immediately.
    """
    if not isinstance(item.get("price"), (int, float)) or isinstance(item["price"], bool) or not math.isfinite(item["price"]) or item["price"] <= 0:
        raise ValueError("Invalid product price")
    if not item.get("title") or not item.get("store") or not item.get("url", "").startswith(("https://", "http://")):
        raise ValueError("Invalid product identity")
    db = connect()
    key = _product_key(item)
    alias = db.execute("SELECT product_key FROM product_aliases WHERE canonical_url=?", (key,)).fetchone()
    if alias:
        key = alias[0]
    legacy_key = item["url"].split("?")[0].rstrip("/")
    if not db.execute("SELECT 1 FROM products WHERE product_key=?", (key,)).fetchone() and db.execute("SELECT 1 FROM products WHERE product_key=?", (legacy_key,)).fetchone():
        key = legacy_key
    item["_product_key"] = key
    row = db.execute(
        "SELECT product_key FROM products WHERE product_key=?", (key,)
    ).fetchone()
    is_new = row is None

    if is_new:
        db.execute(
            "INSERT INTO products(product_key,store,title,url) VALUES(?,?,?,?)",
            (key, item["store"], item["title"], item["url"])
        )
    else:
        db.execute(
            "UPDATE products SET title=?, store=?, url=?, last_seen=CURRENT_TIMESTAMP WHERE product_key=?",
            (item["title"], item["store"], item["url"], key)
        )

    latest = db.execute(
        "SELECT price, checked_at, validated FROM price_history WHERE product_key=? ORDER BY id DESC LIMIT 1",
        (key,)
    ).fetchone()

    should_record = True
    if latest and latest[2] and round(float(latest[0]), 2) == round(float(item["price"]), 2):
        recent = db.execute(
            "SELECT 1 FROM price_history WHERE product_key=? "
            "AND checked_at >= datetime('now', ? ) LIMIT 1",
            (key, f"-{int(minimum_sample_minutes)} minutes")
        ).fetchone()
        should_record = recent is None

    observations = db.execute(
        "SELECT price, checked_at FROM price_history WHERE product_key=? AND validated=1 AND checked_at >= datetime('now', '-90 days') ORDER BY checked_at",
        (key,)
    ).fetchall()
    previous = [r[0] for r in observations]

    if should_record:
        cursor = db.execute(
            "INSERT OR IGNORE INTO price_history(product_key,price,scan_id,store_original_price,validated) VALUES(?,?,?,?,1)",
            (key, item["price"],item.get("_scan_id"),item.get("original_price"))
        )
        should_record = cursor.rowcount > 0
    item["normalized_name"] = " ".join(unicodedata.normalize("NFKC",item["title"]).casefold().split())
    stored = db.execute("SELECT metadata FROM products WHERE product_key=?", (key,)).fetchone()
    try:
        metadata = json.loads(stored[0]) if stored and stored[0] else {}
    except ValueError:
        metadata = {}
    metadata.update({k:item[k] for k in ("rating","reviews","description","specifications","data_sources","model","condition","color","capacity","size","image_url") if item.get(k)})
    # Current offer evidence may change while the unchanged price is sampled hourly.
    # Keep it separate from immutable historical observations and never replace the
    # accepted price on a duplicate scan with a conflicting second reading.
    if should_record or (latest and latest[2] and round(float(latest[0]),2)==round(float(item["price"]),2)):
        original = item.get("original_price")
        original = float(original) if isinstance(original,(int,float)) and not isinstance(original,bool) and math.isfinite(original) and original>item["price"] else None
        metadata["current_offer"] = {"price":round(float(item["price"]),2),"original_price":original,"seen_at":datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S"),"validated":True,"shipping_sar":item.get("shipping_sar"),"data_sources":item.get("data_sources",[])}
    item["metadata"] = json.dumps(metadata,ensure_ascii=False)
    for field in ("product_id", "brand", "category", "currency", "gtin", "normalized_name", "metadata"):
        if item.get(field):
            db.execute(f"UPDATE products SET {field}=? WHERE product_key=?", (str(item[field]), key))
    db.execute("INSERT OR IGNORE INTO product_aliases VALUES(?,?)", (canonical_url(item["url"]),key))
    db.commit()
    db.close()

    item["_is_new_product"] = is_new
    item["_history_points_before"] = len(previous)
    item["_price_recorded"] = should_record

    assessment = historical_assessment(observations, item["price"])
    item.update(assessment)
    item["history_status"] = assessment["status"]
    if assessment["status"] == "INSUFFICIENT_HISTORY":
        advertised = item.get("original_price")
        if advertised and advertised > item["price"] and (advertised-item["price"])/advertised*100 >= MIN_DROP_PERCENT:
            item["status"] = "POTENTIAL_DEAL"
        elif is_new:
            item["status"] = "NEW_PRODUCT"
    return item if item["status"] in {"POTENTIAL_DEAL", "VERIFIED_DEAL"} else None


def historical_assessment(observations, current):
    """Qualify positive historical savings independently of the advertised 25% signal."""
    days = {}
    valid_observations = 0
    today = datetime.now(timezone.utc).date()
    for price, stamp in observations:
        try:
            observed_day = datetime.fromisoformat(stamp.replace("Z","+00:00")).date()
        except (ValueError,TypeError,AttributeError):
            continue
        if isinstance(price,(int,float)) and not isinstance(price,bool) and math.isfinite(price) and price>0 and observed_day<=today:
            days.setdefault(observed_day.isoformat(),[]).append(price)
            valid_observations += 1
    daily = sorted((day,median(values)) for day,values in days.items())
    coverage = (datetime.fromisoformat(daily[-1][0])-datetime.fromisoformat(daily[0][0])).days if daily else 0
    result = {"status":"INSUFFICIENT_HISTORY","baseline":None,"drop_percent":None,
              "history_days":len(daily),"history_observations":valid_observations,
              "history_coverage_days":coverage,"history_required_days":MIN_HISTORY_POINTS,
              "history_required_coverage_days":MIN_HISTORY_DAYS,
              "previous_min":min((v for _,v in daily),default=None)}
    if not isinstance(current,(int,float)) or isinstance(current,bool) or not math.isfinite(current) or current<=0:
        result["status"]="DATA_ANOMALY"
        return result
    if len(daily)<MIN_HISTORY_POINTS or coverage<MIN_HISTORY_DAYS:
        return result
    if (today-datetime.fromisoformat(daily[-1][0]).date()).days>7:
        return result
    values=[v for _,v in daily]
    center=median(values)
    mad=median(abs(v-center) for v in values)
    clean=[(day,v) for day,v in daily if abs(v-center)<=max(center*.2,3*mad)]
    clean_coverage=(datetime.fromisoformat(clean[-1][0])-datetime.fromisoformat(clean[0][0])).days if clean else 0
    if len(clean)<MIN_HISTORY_POINTS or clean_coverage<MIN_HISTORY_DAYS:
        return result
    baseline=round(min(median(v for _,v in clean),median(values[-7:])),2)
    drop=(baseline-round(current,2))/baseline*100
    result.update(baseline=baseline,drop_percent=round(drop,2),history_qualified_days=len(clean))
    if current<baseline*.1 or current>baseline*5:
        result["status"]="DATA_ANOMALY"
    elif round(current,2)<baseline:
        result["status"]="VERIFIED_DEAL"
    else:
        result["status"]="PRICE_INCREASED" if round(current,2)>baseline else "PRICE_NORMAL"
    return result


def get_archive_stats():
    db = connect()
    products = db.execute("SELECT COUNT(*) FROM products").fetchone()[0]
    observations = db.execute("SELECT COUNT(*) FROM price_history").fetchone()[0]
    stores = db.execute("SELECT COUNT(DISTINCT store) FROM products").fetchone()[0]
    db.close()
    return {
        "products": int(products),
        "price_observations": int(observations),
        "stores_with_products": int(stores),
    }


def was_recently_alerted(item, cooldown_hours=72):
    db = connect()
    key = _product_key(item)
    try:
        row = db.execute("SELECT last_price FROM sent_alerts WHERE product_key=?", (key,)).fetchone()
        if row is None:
            row = db.execute("SELECT price FROM alert_history WHERE product_key=? ORDER BY id DESC LIMIT 1", (key,)).fetchone()
    finally:
        db.close()
    if not row or row[0] is None:
        return False
    item["last_alert_price"] = row[0]
    return round(item["price"], 2) >= round(row[0], 2)


def record_alert(item):
    record_alerts([item])


def record_alerts(items):
    db = connect()
    with db:
        for item in items:
            db.execute("INSERT INTO alert_history(product_key,price,status) VALUES(?,?,?)", (_product_key(item),item["price"],item.get("status")))
            db.execute("INSERT INTO sent_alerts(product_key,last_price,sent_at) VALUES(?,?,CURRENT_TIMESTAMP) ON CONFLICT(product_key) DO UPDATE SET last_price=excluded.last_price,sent_at=CURRENT_TIMESTAMP", (_product_key(item),item["price"]))
    db.close()


def checkpoint():
    db = connect()
    db.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    db.close()
