import os
import random

from config import CORE_QUERIES, DISCOVERY_QUERIES, STORES


def choose_search(store=None):
    """
    Keep each store on the same query for four consecutive workflow runs.
    This lets the price database observe the same products repeatedly,
    while rotating through a weighted mix of core and discovery searches.
    """
    if store not in STORES:
        store = random.choice(list(STORES.keys()))

    run_number = max(1, int(os.getenv("GITHUB_RUN_NUMBER", "1")))
    cycle = (run_number - 1) // 4
    store_index = list(STORES.keys()).index(store)

    # A 70/30 weighted pool, traversed deterministically per store.
    weighted_queries = (CORE_QUERIES * 7) + (DISCOVERY_QUERIES * 3)
    query_index = (cycle * 4 + store_index * 11) % len(weighted_queries)
    return store, weighted_queries[query_index]
