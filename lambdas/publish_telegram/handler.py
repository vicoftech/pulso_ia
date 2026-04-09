# lambdas/publish_telegram/handler.py
import boto3
import json
import logging
import os
import time
import requests
import sys

_pkg = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _pkg)
_shared = os.path.normpath(os.path.join(_pkg, "..", "..", "shared"))
if os.path.isdir(_shared):
    sys.path.insert(0, _shared)

from dynamo import mark_as_sent

logger = logging.getLogger()
logger.setLevel(os.environ.get("LOG_LEVEL", "INFO"))
CHANNEL_ID = os.environ["TELEGRAM_CHANNEL_ID"]

CATEGORY_META = {
    "NEW_PRODUCT":  {"emoji": "🚀", "tag": "#NuevoProducto"},
    "MODEL_UPDATE": {"emoji": "⚡", "tag": "#ActualizaciónIA"},
    "METHODOLOGY":  {"emoji": "🧠", "tag": "#MetodologíaIA"},
    "MARKET_NEWS":  {"emoji": "📊", "tag": "#NoticiasIA"},
    "USE_CASE":     {"emoji": "💡", "tag": "#CasosDeUso"},
}
SOURCE_TAGS = {
    "arxiv": "#ArXiv",
    "producthunt": "#ProductHunt",
    "github": "#GitHub",
}

def get_bot_token() -> str:
    ssm = boto3.client("ssm")
    return ssm.get_parameter(
        Name="/pulso-ia/telegram-bot-token", WithDecryption=True
    )["Parameter"]["Value"]

def escape_md2(text: str) -> str:
    special = r"_*[]()~`>#+-=|{}.!"
    return "".join(f"\\{c}" if c in special else c for c in text)

def send_message(token: str, text: str, max_retries=3):
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    for attempt in range(max_retries):
        resp = requests.post(url, json={
            "chat_id": CHANNEL_ID,
            "text": text,
            "parse_mode": "MarkdownV2",
            "disable_web_page_preview": False
        }, timeout=10)
        data = resp.json()
        if data.get("ok"):
            return True
        if resp.status_code == 429:
            retry_after = data.get("parameters", {}).get("retry_after", 5)
            time.sleep(retry_after)
        else:
            logger.error(json.dumps({"telegram_error": data, "attempt": attempt}))
            time.sleep(2 ** attempt)
    return False

def format_message(item: dict) -> str:
    cat = item.get("category", "USE_CASE")
    meta = CATEGORY_META.get(cat, {"emoji": "📌", "tag": "#IA"})
    source = item.get("source", "")
    source_tag = SOURCE_TAGS.get(source, "#Blog") if not source.startswith("rss_") else "#Blog"
    title   = escape_md2(item.get("title", "")[:100])
    summary = escape_md2(item.get("summary_es", ""))
    url     = item.get("url", "")
    return (
        f"{meta['emoji']} *{title}*\n\n"
        f"{summary}\n\n"
        f"🔗 [Leer más]({url})\n"
        f"{escape_md2(meta['tag'])} {escape_md2(source_tag)}"
    )

def handler(event, context):
    token = get_bot_token()
    items = event.get("relevant_items", [])
    sent, failed = 0, 0
    for item in items:
        try:
            ok = send_message(token, format_message(item))
            if ok:
                mark_as_sent(item["item_id"])
                sent += 1
            else:
                failed += 1
            time.sleep(1)
        except Exception as e:
            failed += 1
            logger.error(json.dumps({"error": str(e), "item_id": item.get("item_id")}))
    return {"sent": sent, "failed": failed, "total": len(items)}
