"""Real public-store integration scan; no AI calls or Telegram messages."""
import json
import os
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
from main import collect_store
from discovery import choose_queries
from config import STORES, QUERIES_PER_STORE
from database import save_and_evaluate, get_archive_stats, checkpoint


def scan(store):
    try:
        queries=choose_queries(store,max(1,min(QUERIES_PER_STORE,int(os.getenv("INTEGRATION_QUERIES_PER_STORE","2")))))
        items,report=collect_store(store,queries)
        return store,items,report.get("error") or ("EmptyStoreResults" if not items else None)
    except Exception as exc:
        return store, [], f"{type(exc).__name__}: {exc}"


def main():
    report = {"before": get_archive_stats(), "stores": {}}
    with ThreadPoolExecutor(max_workers=len(STORES)) as pool:
        for store, items, error in pool.map(scan, STORES):
            for item in items:
                save_and_evaluate(item)
            report["stores"][store] = {"products": len(items), "error": error, "sample": items[:2]}
    report["after"] = get_archive_stats()
    checkpoint()
    Path("integration-results.json").write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding="utf-8")
    print(json.dumps(report,ensure_ascii=False,indent=2))


if __name__ == "__main__":
    main()
