# lambdas/fetch_sources/handler.py
import json
import logging
import os
import sys
from concurrent.futures import ThreadPoolExecutor
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../../shared"))

from sources import SOURCE_REGISTRY
from dynamo import batch_get_existing_ids

logger = logging.getLogger()
logger.setLevel(os.environ.get("LOG_LEVEL", "INFO"))
MAX_ITEMS = 50

def handler(event, context):
    sources_input = event.get("sources", list(SOURCE_REGISTRY.keys()))
    lookback_hours = event.get("lookback_hours", 1)
    if event.get("initial_run"):
        lookback_hours = 240

    logger.info(json.dumps({
        "action": "fetch_start",
        "sources": sources_input,
        "lookback_hours": lookback_hours
    }))

    all_items = []
    with ThreadPoolExecutor(max_workers=4) as ex:
        futures = {
            ex.submit(SOURCE_REGISTRY[s]().fetch, lookback_hours): s
            for s in sources_input if s in SOURCE_REGISTRY
        }
        for future, source_name in futures.items():
            try:
                items = future.result()
                all_items.extend(items)
                logger.info(json.dumps({"source": source_name, "fetched": len(items)}))
            except Exception as e:
                logger.error(json.dumps({"source": source_name, "error": str(e)}))

    all_ids = [i.item_id for i in all_items]
    existing_ids = batch_get_existing_ids(all_ids) if all_ids else set()
    new_items = [i for i in all_items if i.item_id not in existing_ids][:MAX_ITEMS]

    by_source = {}
    for item in new_items:
        by_source[item.source] = by_source.get(item.source, 0) + 1

    logger.info(json.dumps({
        "action": "fetch_done",
        "total_fetched": len(all_items),
        "total_new": len(new_items),
        "by_source": by_source
    }))

    return {
        "items": [i.__dict__ for i in new_items],
        "count": len(new_items),
        "by_source": by_source
    }
