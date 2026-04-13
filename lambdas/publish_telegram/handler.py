# lambdas/publish_telegram/handler.py
import json
import logging
import os
import time
from datetime import datetime, timezone
from html import escape as html_escape

import boto3
import requests

logger = logging.getLogger()
logger.setLevel(os.environ.get("LOG_LEVEL", "INFO"))

TABLE_NAME = os.environ["DYNAMODB_TABLE"]
CHANNEL_ID = os.environ["TELEGRAM_CHANNEL_ID"]

dynamodb = boto3.resource("dynamodb")
table = dynamodb.Table(TABLE_NAME)

CATEGORY_MAP = {
    "NEW_PRODUCT": {"emoji": "🚀", "label": "NEW PRODUCT"},
    "MODEL_UPDATE": {"emoji": "⚡", "label": "MODEL UPDATE"},
    "METHODOLOGY": {"emoji": "🧠", "label": "METHODOLOGY"},
    "MARKET_NEWS": {"emoji": "📊", "label": "MARKET NEWS"},
    "USE_CASE": {"emoji": "💡", "label": "USE CASE"},
    "VIDEO_EXPLAINER": {"emoji": "🎬", "label": "VIDEO"},
    "UNCATEGORIZED": {"emoji": "📌", "label": "NEWS"},
}

SOURCE_MAP = {
    "arxiv": {"icon": "📄", "name": "ArXiv"},
    "producthunt": {"icon": "🐱", "name": "Product Hunt"},
    "github": {"icon": "⚙️", "name": "GitHub"},
    "youtube": {"icon": "▶️", "name": "YouTube"},
    "instagram_reels": {"icon": "📸", "name": "Instagram"},
    "tiktok": {"icon": "🎵", "name": "TikTok"},
}


def _source_meta(source: str) -> dict:
    if source in SOURCE_MAP:
        return SOURCE_MAP[source]
    if source.startswith("rss_"):
        name = source.replace("rss_", "").replace("_", " ").title()
        return {"icon": "📰", "name": name}
    return {"icon": "📡", "name": source or "feed"}


def build_card(item: dict) -> str:
    cat = item.get("category", "MARKET_NEWS")
    source = item.get("source", "")
    url = (item.get("url") or "").strip()
    title = (item.get("title") or "")[:100]
    summary = (item.get("summary_es") or "")[:280]
    score_raw = item.get("relevance_score", 0)
    try:
        score = int(score_raw)
    except (TypeError, ValueError):
        score = 0

    cat_info = CATEGORY_MAP.get(cat, {"emoji": "📌", "label": "NEWS"})
    src_info = _source_meta(source)
    now_str = datetime.now(timezone.utc).strftime("%Y-%m-%d · %H:%M UTC")

    t_esc = html_escape(title, quote=False)
    s_esc = html_escape(summary, quote=False)
    lbl_esc = html_escape(cat_info["label"], quote=False)
    src_esc = html_escape(src_info["name"], quote=False)

    pre_header = html_escape(f"Selección IA · readout\n{now_str}", quote=False)
    parts = [
        "<b>◉ PULSO IA</b>",
        f"<pre>{pre_header}</pre>",
        "",
        f"{cat_info['emoji']}  <b><code>{lbl_esc}</code></b>",
        "",
        f"<b>{t_esc}</b>",
        "",
        s_esc,
        "",
        f"{src_info['icon']} <i>{src_esc}</i>  ·  relevancia <code>{score}</code>",
    ]
    if url:
        parts.extend(
            [
                "",
                f"<a href=\"{html_escape(url, quote=True)}\">Leer más ↗</a>",
            ]
        )

    return "\n".join(parts)


def _plain_fallback(item: dict) -> str:
    cat_info = CATEGORY_MAP.get(item.get("category", ""), {"emoji": "📌", "label": "NEWS"})
    return (
        f"◉ PULSO IA\n"
        f"{cat_info['emoji']} {cat_info['label']}\n\n"
        f"{(item.get('title') or '')[:100]}\n\n"
        f"{(item.get('summary_es') or '')[:280]}\n\n"
        f"{item.get('url', '')}"
    )


def get_bot_token() -> str:
    ssm = boto3.client("ssm", region_name=os.environ.get("AWS_REGION", "us-east-1"))
    return ssm.get_parameter(
        Name="/pulso-ia/telegram-bot-token", WithDecryption=True
    )["Parameter"]["Value"]


def send_card(
    token: str,
    item: dict,
    keyboard: dict | None = None,
    attempt: int = 0,
) -> dict | None:
    payload = {
        "chat_id": CHANNEL_ID,
        "text": build_card(item),
        "parse_mode": "HTML",
        "disable_web_page_preview": False,
    }
    if keyboard:
        payload["reply_markup"] = keyboard

    resp = requests.post(
        f"https://api.telegram.org/bot{token}/sendMessage",
        json=payload,
        timeout=15,
    )

    if resp.status_code == 429 and attempt < 3:
        retry_after = resp.json().get("parameters", {}).get("retry_after", 5)
        time.sleep(retry_after)
        return send_card(token, item, keyboard, attempt + 1)

    if not resp.ok:
        logger.error("Telegram HTML error %s: %s", resp.status_code, resp.text[:300])
        if resp.status_code == 400 and "parse" in resp.text.lower() and attempt == 0:
            logger.warning("HTML parse error — retrying as plain text")
            resp2 = requests.post(
                f"https://api.telegram.org/bot{token}/sendMessage",
                json={
                    "chat_id": CHANNEL_ID,
                    "text": _plain_fallback(item),
                    "disable_web_page_preview": False,
                    **({"reply_markup": keyboard} if keyboard else {}),
                },
                timeout=15,
            )
            return resp2.json().get("result") if resp2.ok else None
        return None

    return resp.json().get("result")


def handler(event, context):
    relevant_items = event.get("relevant_items", [])
    if not relevant_items:
        logger.info("No relevant items to publish")
        return {"published": 0, "sent": 0, "failed": 0, "total": 0}

    token = get_bot_token()
    sent = 0
    failed = 0

    for item in relevant_items:
        try:
            result = send_card(token, item)
            if result:
                table.update_item(
                    Key={"item_id": item["item_id"]},
                    UpdateExpression="SET telegram_sent = :t, telegram_message_id = :mid",
                    ExpressionAttributeValues={":t": "true", ":mid": result.get("message_id")},
                )
                sent += 1
            else:
                failed += 1
            time.sleep(1)
        except Exception as e:
            failed += 1
            logger.error(json.dumps({"error": str(e), "item_id": item.get("item_id")}))

    return {"published": sent, "sent": sent, "failed": failed, "total": len(relevant_items)}
