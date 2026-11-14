import os
import random

from config import CORE_QUERIES, DISCOVERY_QUERIES, STORES


def choose_search(store=None):
    """
    Keep each store on the same query for four consecutive workflow runs.
    This gives the price database repeated observations before rotating searches.
    The sequence preserves a 70/30 split between core and discovery searches.
    """
    if store not in STORES:
        store = random.choice(list(STORES.keys()))

    run_number = max(1, int(os.getenv("GITHUB_RUN_NUMBER", "1")))
    cycle = (run_number - 1) // 4
    store_index = list(STORES.keys()).index(store)
    sequence = cycle * len(STORES) + store_index

    slot = sequence % 10
    batch = sequence // 10
    if slot < 7:
        query_index = (batch * 7 + slot) % len(CORE_QUERIES)
        query = CORE_QUERIES[query_index]
    else:
        query_index = (batch * 3 + slot - 7) % len(DISCOVERY_QUERIES)
        query = DISCOVERY_QUERIES[query_index]

    return store, query
