import sqlite3
from pathlib import Path
from statistics import median

from config import DB_PATH, MIN_HISTORY_POINTS, MIN_DROP_PERCENT


def connect():
    Path(DB_PATH).parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(DB_PATH)
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
    db.commit()
    return db


def save_and_evaluate(item):
    db = connect()
    key = item["url"].split("?")[0].rstrip("/")
    row = db.execute(
        "SELECT product_key FROM products WHERE product_key=?", (key,)
    ).fetchone()
    if not row:
        db.execute(
            "INSERT INTO products(product_key,store,title,url) VALUES(?,?,?,?)",
            (key, item["store"], item["title"], item["url"])
        )
    else:
        db.execute(
            "UPDATE products SET title=?, url=?, last_seen=CURRENT_TIMESTAMP WHERE product_key=?",
            (item["title"], item["url"], key)
        )

    previous = [r[0] for r in db.execute(
        "SELECT price FROM price_history WHERE product_key=? ORDER BY id DESC LIMIT 12",
        (key,)
    ).fetchall()]
    db.execute(
        "INSERT INTO price_history(product_key,price) VALUES(?,?)",
        (key, item["price"])
    )
    db.commit()
    db.close()

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


def was_recently_alerted(item, cooldown_hours=72):
    """Prevent repeated alerts for the same product within 72 hours."""
    key = item["url"].split("?")[0].rstrip("/")
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
    key = item["url"].split("?")[0].rstrip("/")
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
