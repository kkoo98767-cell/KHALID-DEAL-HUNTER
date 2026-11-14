
import random
from config import CORE_QUERIES, DISCOVERY_QUERIES, STORES

def choose_search():
    # 70% core interests, 30% exploratory discovery.
    pool = CORE_QUERIES if random.random() < 0.70 else DISCOVERY_QUERIES
    query = random.choice(pool)
    store = random.choice(list(STORES.keys()))
    return store, query
