import os
import random

from database import connect

from config import CORE_QUERIES, DISCOVERY_QUERIES, STORES


def choose_search(store=None):
    """
    Rotate search intent on every workflow run instead of repeating the same
    query four times. Each store receives a different query in each run.
    """
    stores = list(STORES.keys())
    if store not in STORES:
        store = random.choice(stores)

    db = connect()
    queries = (CORE_QUERIES[:18] + ["laptop", "printer", "tablet", "هاتف ذكي", "شاشة ألعاب"]) if store in {"Extra", "Jarir"} else CORE_QUERIES + DISCOVERY_QUERIES
    counts = dict(db.execute("SELECT query,COUNT(*) FROM discovery_history WHERE store=? GROUP BY query", (store,)).fetchall())
    query = min(queries, key=lambda q: counts.get(q, 0))
    with db:
        db.execute("INSERT INTO discovery_history(store,query) VALUES(?,?)", (store,query))
    db.close()
    return store, query
