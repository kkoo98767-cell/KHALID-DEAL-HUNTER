
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

    # Avoid calling a first-seen product a genuine discount.
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
