"""Persistent, diverse category discovery for the two active stores."""
import random
from database import connect
from config import CORE_QUERIES, DISCOVERY_QUERIES, STORES, QUERIES_PER_STORE

JARIR_QUERIES = CORE_QUERIES[:18] + [
    "laptop", "printer", "tablet", "هاتف ذكي", "شاشة ألعاب", "bluetooth speaker",
    "gaming chair", "smart watch", "router", "airpods", "monitor", "camera",
    "عروض كمبيوتر", "عروض جوالات", "عروض سماعات", "laptop accessories",
]
AMAZON_QUERIES = CORE_QUERIES + DISCOVERY_QUERIES + [
    "عروض سماعات", "عروض شواحن", "عروض اجهزة منزلية", "خصومات اكسسوارات كمبيوتر",
    "portable monitor", "wireless earbuds", "smart watch", "wifi router",
]


def choose_search(store=None):
    if store is None:
        store=random.choice(list(STORES))
    if store not in STORES:
        raise ValueError("Store is not enabled for monitoring")
    pool=JARIR_QUERIES if store=="Jarir" else AMAZON_QUERIES
    db=connect()
    counts=dict(db.execute("SELECT query,COUNT(*) FROM discovery_history WHERE store=? GROUP BY query",(store,)))
    query=min(pool,key=lambda q:counts.get(q,0))
    with db:
        db.execute("INSERT INTO discovery_history(store,query) VALUES(?,?)",(store,query))
    db.close()
    return store,query


def choose_queries(store,count=QUERIES_PER_STORE):
    """Every run covers several different intents and records each choice."""
    selected=[]
    for _ in range(count):
        query=choose_search(store)[1]
        if query not in selected:
            selected.append(query)
    return selected
