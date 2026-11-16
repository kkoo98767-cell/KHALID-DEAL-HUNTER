"""Real public-store integration scan; no AI calls or Telegram messages."""
import json
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
from main import fetch_store
from config import STORES
from database import save_and_evaluate, get_archive_stats, checkpoint


def scan(store):
    try:
        items = fetch_store(store, "wireless mouse")
        return store, items, None
    except Exception as exc:
        return store, [], f"{type(exc).__name__}: {exc}"


def main():
    report = {"before": get_archive_stats(), "stores": {}}
    with ThreadPoolExecutor(max_workers=4) as pool:
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
