import os
import random

from config import CORE_QUERIES, DISCOVERY_QUERIES, STORES


def choose_search(store=None):
    """
    Rotate search intent on every workflow run instead of repeating the same
    query four times. Each store receives a different query in each run.
    """
    stores = list(STORES.keys())
    if store not in STORES:
        store = random.choice(stores)

    run_number = max(1, int(os.getenv("GITHUB_RUN_NUMBER", "1")))
    store_index = stores.index(store)
    sequence = (run_number - 1) * len(stores) + store_index

    # Preserve an approximate 70/30 split, but rotate on every scan.
    slot = sequence % 10
    batch = sequence // 10
    if slot < 7:
        index = (batch * 7 + slot) % len(CORE_QUERIES)
        query = CORE_QUERIES[index]
    else:
        index = (batch * 3 + slot - 7) % len(DISCOVERY_QUERIES)
        query = DISCOVERY_QUERIES[index]

    return store, query
