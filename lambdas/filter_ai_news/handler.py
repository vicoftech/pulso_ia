# lambdas/filter_ai_news/handler.py
import boto3
import json
import logging
import os
import sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../../shared"))
from dynamo import batch_save_items
from models import ProcessedNewsItem
from datetime import datetime, timezone

logger = logging.getLogger()
logger.setLevel(os.environ.get("LOG_LEVEL", "INFO"))

BEDROCK_MODEL = os.environ.get("BEDROCK_MODEL_ID", "amazon.nova-lite-v1:0")
THRESHOLD = int(os.environ.get("RELEVANCE_THRESHOLD", "60"))
BATCH_SIZE = int(os.environ.get("BATCH_SIZE", "20"))
bedrock = boto3.client("bedrock-runtime")

SYSTEM_PROMPT = """You are the AI curator for "Pulso IA", a premium Telegram channel.
Analyze news items and classify them strictly. Only truly relevant AI news passes.

Categories:
- NEW_PRODUCT: New AI tool, service, or product launch
- MODEL_UPDATE: New model release, version update, or new features
- METHODOLOGY: New techniques, papers, or approaches relevant to AI practitioners
- MARKET_NEWS: Acquisitions, funding, major launches, regulatory news, significant failures
- USE_CASE: Novel applications of AI in specific domains

Respond ONLY with a valid JSON array. No markdown, no explanation, no preamble."""

def classify_batch(items: list) -> list:
    simplified = [{"item_id": i["item_id"], "title": i["title"],
                   "raw_content": i.get("raw_content", "")[:300]} for i in items]
    user_prompt = (
        f"Analyze and return a JSON array (same order as input):\n{json.dumps(simplified)}\n\n"
        'Return format per item:\n'
        '{"item_id":"...","is_ai_related":true/false,"is_relevant":true/false,'
        '"category":"NEW_PRODUCT|MODEL_UPDATE|METHODOLOGY|MARKET_NEWS|USE_CASE|null",'
        '"relevance_score":0-100,'
        '"summary_es":"resumen en espanol, maximo 280 caracteres, directo y sin rodeos"}'
    )
    response = bedrock.converse(
        modelId=BEDROCK_MODEL,
        system=[{"text": SYSTEM_PROMPT}],
        messages=[{
            "role": "user",
            "content": [{"text": user_prompt}]
        }],
        inferenceConfig={"maxTokens": 4096}
    )
    text = response["output"]["message"]["content"][0]["text"].strip()
    if text.startswith("```"):
        text = text.split("```")[1]
        if text.startswith("json"):
            text = text[4:]
    return json.loads(text)

def handler(event, context):
    items = event.get("items", [])
    if not items:
        return {"relevant_items": [], "total_processed": 0, "total_relevant": 0}

    now = datetime.now(timezone.utc).isoformat()
    all_results, relevant_items, by_category = [], [], {}

    for i in range(0, len(items), BATCH_SIZE):
        batch = items[i:i + BATCH_SIZE]
        try:
            classifications = classify_batch(batch)
        except Exception as e:
            logger.error(json.dumps({"action": "bedrock_error", "error": str(e)}))
            classifications = [{"item_id": it["item_id"], "is_ai_related": False,
                                "is_relevant": False, "category": None,
                                "relevance_score": 0, "summary_es": ""} for it in batch]

        item_map = {it["item_id"]: it for it in batch}
        for cl in classifications:
            raw = item_map.get(cl["item_id"], {})
            processed = ProcessedNewsItem(
                item_id=cl["item_id"],
                source=raw.get("source", ""),
                title=raw.get("title", ""),
                url=raw.get("url", ""),
                summary_es=cl.get("summary_es", ""),
                category=cl.get("category") or "UNCATEGORIZED",
                published_at=raw.get("published_at", now),
                processed_at=now,
                is_relevant=cl.get("is_relevant", False),
                relevance_score=cl.get("relevance_score", 0)
            )
            all_results.append(processed)
            if processed.is_relevant and processed.relevance_score >= THRESHOLD:
                relevant_items.append(processed.__dict__)
                cat = processed.category
                by_category[cat] = by_category.get(cat, 0) + 1

    batch_save_items(all_results)
    logger.info(json.dumps({
        "action": "filter_done",
        "total_processed": len(all_results),
        "total_relevant": len(relevant_items),
        "by_category": by_category
    }))

    return {
        "relevant_items": relevant_items,
        "total_processed": len(all_results),
        "total_relevant": len(relevant_items),
        "by_category": by_category
    }
