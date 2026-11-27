"""Persistent, diverse category discovery for the two active stores."""
import random
from database import connect
from config import CORE_QUERIES, DISCOVERY_QUERIES, STORES, QUERIES_PER_STORE

# Both stores rotate through the same interests, with independent search history.
PERSONAL_QUERIES = [
    "gaming PC", "IEM earphones", "USB C multiport 100W charger", "fresh daily office fragrance men",
    "omega 3", "iPhone accessories", "gaming controller",
    "NVIDIA RTX graphics card", "TWS earbuds", "USB C cable", "Dior Sauvage eau de toilette",
    "vitamin D3", "iPad accessories", "Fortnite controller accessories",
    "Intel Core i5 processor", "ANC noise cancelling headphones", "Lightning cable",
    "clean aquatic fragrance men", "magnesium glycinate", "CPU GPU temperature display",
    "Call of Duty gaming headset", "Intel Core i7 processor", "over ear headphones",
    "USB adapter", "Versace Pour Homme", "multivitamins minerals",
    "USB hub", "gaming gear", "motherboard", "gaming headset", "UGREEN USB C charger",
    "fresh citrus fragrance men", "energy focus supplements", "PC accessories",
    "DDR5 RGB RAM", "NVMe SSD", "PC power supply PSU", "CPU cooler", "PC case",
    "QHD 240Hz IPS gaming monitor", "iHerb California Gold Nutrition",
    "LG UltraGear 27GR83Q", "RTX 3080 Ti", "FIFINE microphone", "PS5 DualSense accessories",
    "Philips electric shaver", "gaming mouse", "mechanical keyboard", "Baseus GaN charger",
]
SEARCH_POOL = list(dict.fromkeys(PERSONAL_QUERIES + CORE_QUERIES + DISCOVERY_QUERIES))


def choose_search(store=None):
    if store is None:
        store=random.choice(list(STORES))
    if store not in STORES:
        raise ValueError("Store is not enabled for monitoring")
    pool=SEARCH_POOL
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
