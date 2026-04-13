# lambdas/publish_telegram/handler.py
import json
import logging
import os
import sys
import time
from datetime import datetime, timezone
from html import escape as html_escape

import boto3
import requests

_pkg = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _pkg)
_shared = os.path.normpath(os.path.join(_pkg, "..", "..", "shared"))
if os.path.isdir(_shared):
    sys.path.insert(0, _shared)

from dynamo import mark_as_queued, mark_as_sent, query_by_telegram_status

logger = logging.getLogger()
logger.setLevel(os.environ.get("LOG_LEVEL", "INFO"))

CHANNEL_ID = os.environ["TELEGRAM_CHANNEL_ID"]

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


def _published_at_sort_key(item: dict) -> str:
    return (item.get("published_at") or "")[:32]


def build_publication_queue(relevant_items: list[dict]) -> list[dict]:
    """
    Orden: primero ítems ya en cola (telegram_sent=queued), luego los nuevos del barrido,
    cada bloque ordenado por published_at ascendente (la menos reciente = más antigua primero).
    """
    backlog = query_by_telegram_status("queued")
    backlog_ids = {x["item_id"] for x in backlog}
    backlog_sorted = sorted(backlog, key=_published_at_sort_key)

    new_items = [dict(i) for i in relevant_items if i.get("item_id") not in backlog_ids]
    new_sorted = sorted(new_items, key=_published_at_sort_key)

    return backlog_sorted + new_sorted


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
    relevant_items = event.get("relevant_items") or []

    combined = build_publication_queue(relevant_items)
    if not combined:
        logger.info("Empty publication queue (no queued items and no new relevant)")
        return {
            "published": 0,
            "sent": 0,
            "failed": 0,
            "total_new_in_run": len(relevant_items),
            "queued_remaining": 0,
        }

    to_send = combined[0]
    remainder = combined[1:]
    queued_remaining = len(remainder)

    token = get_bot_token()
    sent = 0
    failed = 0

    try:
        result = send_card(token, to_send)
        if result:
            mid = result.get("message_id")
            mark_as_sent(to_send["item_id"], mid)
            sent = 1
            for it in remainder:
                if it.get("telegram_sent") not in ("queued", "true"):
                    mark_as_queued(it["item_id"])
        else:
            failed = 1
    except Exception as e:
        failed = 1
        logger.error(json.dumps({"error": str(e), "item_id": to_send.get("item_id")}))

    logger.info(
        json.dumps(
            {
                "action": "publish_cadence",
                "sent": sent,
                "failed": failed,
                "queued_remaining_after": len(remainder) if sent else len(combined),
                "item_id": to_send.get("item_id"),
            }
        )
    )

    return {
        "published": sent,
        "sent": sent,
        "failed": failed,
        "total_new_in_run": len(relevant_items),
        "queued_remaining": len(remainder) if sent else len(combined),
    }
