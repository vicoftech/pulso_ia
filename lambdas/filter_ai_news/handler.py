# lambdas/filter_ai_news/handler.py
import boto3
import json
import logging
import os
import sys

_pkg = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _pkg)
_shared = os.path.normpath(os.path.join(_pkg, "..", "..", "shared"))
if os.path.isdir(_shared):
    sys.path.insert(0, _shared)

from dynamo import batch_get_telegram_sent, batch_save_items
from models import ProcessedNewsItem
from datetime import datetime, timezone

logger = logging.getLogger()
logger.setLevel(os.environ.get("LOG_LEVEL", "INFO"))

BEDROCK_MODEL = os.environ.get("BEDROCK_MODEL_ID", "amazon.nova-lite-v1:0")
THRESHOLD = int(os.environ.get("RELEVANCE_THRESHOLD", "60"))
BATCH_SIZE = int(os.environ.get("BATCH_SIZE", "20"))
bedrock = boto3.client("bedrock-runtime")

SUBCATEGORY_CATALOG = {
    "NEW_PRODUCT": [
        "Productivity Tools",
        "Developer Tools",
        "Enterprise SaaS",
        "Creative Tools",
        "Education",
        "Security",
        "Otros",
    ],
    "MODEL_UPDATE": [
        "New Model Release",
        "Capability Upgrade",
        "Pricing Update",
        "Context Window",
        "Multimodal Update",
        "Open Source Release",
        "Otros",
    ],
    "METHODOLOGY": [
        "Research Paper",
        "Benchmark",
        "Training Technique",
        "Inference Optimization",
        "Evaluation Method",
        "Alignment and Safety",
        "Otros",
    ],
    "MARKET_NEWS": [
        "Funding",
        "M&A",
        "Partnership",
        "Regulation",
        "Legal",
        "Ecosystem Shift",
        "Otros",
    ],
    "USE_CASE": [
        "Healthcare",
        "Finance",
        "Retail and E-commerce",
        "Manufacturing",
        "Public Sector",
        "Customer Support",
        "Otros",
    ],
    "UNCATEGORIZED": ["Otros"],
}

SYSTEM_PROMPT = """You are the AI curator for "Pulso IA", a premium Telegram channel.
Analyze news items and classify them strictly. Only truly relevant AI news passes.

Categories:
- NEW_PRODUCT: New AI tool, service, or product launch
- MODEL_UPDATE: New model release, version update, or new features
- METHODOLOGY: New techniques, papers, or approaches relevant to AI practitioners
- MARKET_NEWS: Acquisitions, funding, major launches, regulatory news, significant failures
- USE_CASE: Novel applications of AI in specific domains

Subcategory catalog (must pick one from the selected category, otherwise use "Otros"):
- NEW_PRODUCT: Productivity Tools, Developer Tools, Enterprise SaaS, Creative Tools, Education, Security, Otros
- MODEL_UPDATE: New Model Release, Capability Upgrade, Pricing Update, Context Window, Multimodal Update, Open Source Release, Otros
- METHODOLOGY: Research Paper, Benchmark, Training Technique, Inference Optimization, Evaluation Method, Alignment and Safety, Otros
- MARKET_NEWS: Funding, M&A, Partnership, Regulation, Legal, Ecosystem Shift, Otros
- USE_CASE: Healthcare, Finance, Retail and E-commerce, Manufacturing, Public Sector, Customer Support, Otros

Respond ONLY with a valid JSON array. No markdown, no explanation, no preamble."""


def _normalize_subcategory(category: str, subcategory: str | None) -> str:
    allowed = SUBCATEGORY_CATALOG.get(category, SUBCATEGORY_CATALOG["UNCATEGORIZED"])
    raw = (subcategory or "").strip()
    if not raw:
        return "Otros"
    for opt in allowed:
        if raw.lower() == opt.lower():
            return opt
    return "Otros"

def classify_batch(items: list) -> list:
    simplified = [{"item_id": i["item_id"], "title": i["title"],
                   "raw_content": i.get("raw_content", "")[:300]} for i in items]
    user_prompt = (
        f"Analyze and return a JSON array (same order as input):\n{json.dumps(simplified)}\n\n"
        'Return format per item:\n'
        '{"item_id":"...","is_ai_related":true/false,"is_relevant":true/false,'
        '"category":"NEW_PRODUCT|MODEL_UPDATE|METHODOLOGY|MARKET_NEWS|USE_CASE|null",'
        '"subcategory":"short specific subcategory in English (2-4 words) or null",'
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
        preserved_sent = batch_get_telegram_sent([it["item_id"] for it in batch])
        for cl in classifications:
            raw = item_map.get(cl["item_id"], {})
            is_rel = cl.get("is_relevant", False)
            rscore = cl.get("relevance_score", 0)
            queued = is_rel and rscore >= THRESHOLD
            iu = raw.get("image_url")
            if not (isinstance(iu, str) and iu.strip()):
                iu = None
            else:
                iu = iu.strip()[:2000]
            processed = ProcessedNewsItem(
                item_id=cl["item_id"],
                source=raw.get("source", ""),
                title=raw.get("title", ""),
                url=raw.get("url", ""),
                summary_es=cl.get("summary_es", ""),
                category=cl.get("category") or "UNCATEGORIZED",
                published_at=raw.get("published_at", now),
                processed_at=now,
                is_relevant=is_rel,
                relevance_score=rscore,
                subcategory=_normalize_subcategory(
                    cl.get("category") or "UNCATEGORIZED",
                    cl.get("subcategory"),
                ),
                image_url=iu,
                outbox_key="1" if queued else None,
            )
            prev = preserved_sent.get(cl["item_id"], "false")
            if prev in ("queued", "true"):
                processed.telegram_sent = prev
            all_results.append(processed)
            if queued:
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
