import sqlite3
from pathlib import Path
from statistics import median

from config import DB_PATH, MIN_HISTORY_POINTS, MIN_DROP_PERCENT


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
    db.commit()
    return db


def _product_key(item):
    return item["url"].split("?")[0].rstrip("/")


def save_and_evaluate(item, minimum_sample_minutes=60):
    """
    Archive every valid product. Avoid filling the history with identical
    15-minute readings: unchanged prices are sampled at most once per hour,
    while a changed price is recorded immediately.
    """
    db = connect()
    key = _product_key(item)
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

    previous = [r[0] for r in db.execute(
        "SELECT price FROM price_history WHERE product_key=? ORDER BY id DESC LIMIT 12",
        (key,)
    ).fetchall()]

    if should_record:
        db.execute(
            "INSERT INTO price_history(product_key,price) VALUES(?,?)",
            (key, item["price"])
        )
    db.commit()
    db.close()

    item["_is_new_product"] = is_new
    item["_history_points_before"] = len(previous)
    item["_price_recorded"] = should_record

    if len(previous) < MIN_HISTORY_POINTS:
        return None

    baseline = median(previous)
    if baseline <= 0 or item["price"] >= baseline:
        return None

    drop = (baseline - item["price"]) / baseline * 100
    if drop >= MIN_DROP_PERCENT:
        item["baseline"] = round(baseline, 2)
        item["drop_percent"] = round(drop)
        return item
    return None


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
    key = _product_key(item)
    db = connect()
    row = db.execute(
        "SELECT 1 FROM sent_alerts WHERE product_key=? "
        "AND sent_at >= datetime('now', ?)",
        (key, f"-{int(cooldown_hours)} hours")
    ).fetchone()
    db.close()
    return row is not None


def record_alert(item):
    """Record only after Telegram confirms the message was accepted."""
    key = _product_key(item)
    db = connect()
    db.execute(
        "INSERT INTO sent_alerts(product_key,last_price,sent_at) "
        "VALUES(?,?,CURRENT_TIMESTAMP) "
        "ON CONFLICT(product_key) DO UPDATE SET "
        "last_price=excluded.last_price, sent_at=CURRENT_TIMESTAMP",
        (key, item["price"])
    )
    db.commit()
    db.close()
