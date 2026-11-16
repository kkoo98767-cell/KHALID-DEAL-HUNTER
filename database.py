import sqlite3
from pathlib import Path
from statistics import median
from datetime import datetime, timezone
import math
import json
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
    for name in ("product_id", "brand", "category", "currency", "evaluation"):
        if name not in columns:
            db.execute(f"ALTER TABLE products ADD COLUMN {name} TEXT")
    db.execute("CREATE TABLE IF NOT EXISTS alert_history (id INTEGER PRIMARY KEY, product_key TEXT, price REAL, status TEXT, sent_at TEXT DEFAULT CURRENT_TIMESTAMP)")
    db.execute("CREATE INDEX IF NOT EXISTS idx_alert_history ON alert_history(product_key,id DESC)")
    db.execute("CREATE TABLE IF NOT EXISTS ai_cache (cache_key TEXT PRIMARY KEY, result TEXT NOT NULL, created_at TEXT DEFAULT CURRENT_TIMESTAMP)")
    db.execute("CREATE TABLE IF NOT EXISTS discovery_history (id INTEGER PRIMARY KEY, store TEXT, query TEXT, searched_at TEXT DEFAULT CURRENT_TIMESTAMP)")
    db.execute("CREATE TABLE IF NOT EXISTS product_aliases (canonical_url TEXT PRIMARY KEY, product_key TEXT NOT NULL)")
    if db.execute("PRAGMA user_version").fetchone()[0] < 3:
        for key,url in db.execute("SELECT product_key,url FROM products").fetchall():
            db.execute("INSERT OR IGNORE INTO product_aliases VALUES(?,?)", (canonical_url(url),key))
        db.execute("PRAGMA user_version=3")
    db.commit()
    return db


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
        "SELECT price, checked_at FROM price_history WHERE product_key=? ORDER BY id DESC LIMIT 1",
        (key,)
    ).fetchone()

    should_record = True
    if latest and round(float(latest[0]), 2) == round(float(item["price"]), 2):
        recent = db.execute(
            "SELECT 1 FROM price_history WHERE product_key=? "
            "AND checked_at >= datetime('now', ? ) LIMIT 1",
            (key, f"-{int(minimum_sample_minutes)} minutes")
        ).fetchone()
        should_record = recent is None

    observations = db.execute(
        "SELECT price, checked_at FROM price_history WHERE product_key=? AND checked_at >= datetime('now', '-90 days') ORDER BY checked_at",
        (key,)
    ).fetchall()
    previous = [r[0] for r in observations]

    if should_record:
        db.execute(
            "INSERT INTO price_history(product_key,price) VALUES(?,?)",
            (key, item["price"])
        )
    for field in ("product_id", "brand", "category", "currency"):
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
    # One daily median gives busy scan days no extra voting weight.
    days = {}
    for price, stamp in observations:
        if math.isfinite(price) and price > 0:
            days.setdefault(stamp[:10], []).append(price)
    daily = sorted((day, median(values)) for day, values in days.items())
    result = {"status": "INSUFFICIENT_HISTORY", "baseline": None, "drop_percent": None,
              "history_days": len(daily), "previous_min": min((v for _,v in daily), default=None)}
    if len(daily) < MIN_HISTORY_POINTS:
        return result
    coverage = (datetime.fromisoformat(daily[-1][0])-datetime.fromisoformat(daily[0][0])).days
    stale = (datetime.now(timezone.utc).date()-datetime.fromisoformat(daily[-1][0]).date()).days > 7
    if coverage < MIN_HISTORY_DAYS or stale:
        return result
    values = [v for _, v in daily]
    center = median(values)
    mad = median(abs(v-center) for v in values)
    clean = [v for v in values if abs(v-center) <= max(center*.2, 3*mad)]
    if len(clean) < MIN_HISTORY_POINTS:
        return result
    baseline = min(median(clean), median(values[-7:]))
    drop = (baseline-current)/baseline*100
    result.update(baseline=round(baseline,2), drop_percent=round(drop,2))
    if current < baseline*.1 or current > baseline*5:
        result["status"] = "DATA_ANOMALY"
    else:
        result["status"] = "VERIFIED_DEAL" if drop >= MIN_DROP_PERCENT else ("PRICE_INCREASED" if drop < -1 else "PRICE_NORMAL")
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
    row = db.execute("SELECT price,status, sent_at >= datetime('now', ?) FROM alert_history WHERE product_key=? ORDER BY id DESC LIMIT 1", (f"-{int(cooldown_hours)} hours", _product_key(item))).fetchone()
    legacy = db.execute("SELECT 1 FROM sent_alerts WHERE product_key=? AND sent_at >= datetime('now', ?)", (_product_key(item),f"-{int(cooldown_hours)} hours")).fetchone()
    db.close()
    if row:
        price,status,recent = row
        return status == item.get("status") and abs(price-item["price"])/price < .05
    return legacy is not None


def record_alert(item):
    db = connect()
    with db:
        db.execute("INSERT INTO alert_history(product_key,price,status) VALUES(?,?,?)", (_product_key(item),item["price"],item.get("status")))
        db.execute("INSERT INTO sent_alerts(product_key,last_price,sent_at) VALUES(?,?,CURRENT_TIMESTAMP) ON CONFLICT(product_key) DO UPDATE SET last_price=excluded.last_price,sent_at=CURRENT_TIMESTAMP", (_product_key(item),item["price"]))
    db.close()


def checkpoint():
    db = connect()
    db.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    db.close()
