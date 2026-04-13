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

# Telegram HTML: solo <b>, <i>, <u>, <s>, <code>, <pre>, <a href="">, etc. Sin CSS ni divs.
CATEGORY_META = {
    "NEW_PRODUCT": {"emoji": "🚀", "label": "Nuevo producto"},
    "MODEL_UPDATE": {"emoji": "⚡", "label": "Modelo y actualización"},
    "METHODOLOGY": {"emoji": "🧠", "label": "Metodología"},
    "MARKET_NEWS": {"emoji": "📊", "label": "Industria"},
    "USE_CASE": {"emoji": "💡", "label": "Caso de uso"},
    "VIDEO_EXPLAINER": {"emoji": "🎬", "label": "Video"},
}

SOURCE_META = {
    "arxiv": {"icon": "📄", "label": "ArXiv"},
    "producthunt": {"icon": "🐱", "label": "Product Hunt"},
    "github": {"icon": "⚙️", "label": "GitHub"},
    "youtube": {"icon": "▶️", "label": "YouTube"},
    "instagram_reels": {"icon": "📸", "label": "Instagram"},
    "tiktok": {"icon": "🎵", "label": "TikTok"},
}


def _rss_label(source: str) -> dict:
    feed_name = source.replace("rss_", "").replace("_", " ").title()
    return {"icon": "📰", "label": feed_name}


def build_card(item: dict) -> str:
    cat = item.get("category", "MARKET_NEWS")
    source = item.get("source", "rss")
    url = (item.get("url") or "").strip()
    title = (item.get("title") or "")[:200]
    summary = (item.get("summary_es") or "")[:400]

    cat_info = CATEGORY_META.get(cat, {"emoji": "✨", "label": "Inteligencia artificial"})
    src_info = SOURCE_META.get(source) or _rss_label(source)
    now_str = datetime.now(timezone.utc).strftime("%Y-%m-%d · %H:%M UTC")

    t_esc = html_escape(title, quote=False)
    s_esc = html_escape(summary, quote=False)
    cat_lbl = html_escape(cat_info["label"], quote=False)
    src_lbl = html_escape(src_info["label"], quote=False)

    # Cabecera de marca: compacta, sin reglas ni líneas decorativas
    header = (
        f"<b>PULSO IA</b>  <code>{html_escape(now_str, quote=False)}</code>\n"
        f"<i>Selección diaria de IA</i>"
    )

    # Categoría como “chip” textual (Telegram no permite fondos)
    badge = f"{cat_info['emoji']}  <b>{cat_lbl}</b>"

    # Cuerpo: título dominante + resumen con aire
    body = f"<b>{t_esc}</b>\n\n{s_esc}"

    # Pie: fuente + CTA (preview del enlace aporta imagen si el sitio la define)
    if url:
        footer = (
            f"{src_info['icon']} <i>{src_lbl}</i>\n"
            f"<a href=\"{html_escape(url, quote=True)}\">Abrir noticia completa →</a>"
        )
    else:
        footer = f"{src_info['icon']} <i>{src_lbl}</i>"

    return f"{header}\n\n{badge}\n\n{body}\n\n{footer}"


def get_bot_token() -> str:
    ssm = boto3.client("ssm", region_name=os.environ.get("AWS_REGION", "us-east-1"))
    return ssm.get_parameter(
        Name="/pulso-ia/telegram-bot-token", WithDecryption=True
    )["Parameter"]["Value"]


def send_card(token: str, item: dict, attempt: int = 0) -> dict | None:
    resp = requests.post(
        f"https://api.telegram.org/bot{token}/sendMessage",
        json={
            "chat_id": CHANNEL_ID,
            "text": build_card(item),
            "parse_mode": "HTML",
            "disable_web_page_preview": False,
        },
        timeout=15,
    )

    if resp.status_code == 429 and attempt < 3:
        retry_after = resp.json().get("parameters", {}).get("retry_after", 5)
        logger.warning("Rate limited waiting %ss", retry_after)
        time.sleep(retry_after)
        return send_card(token, item, attempt + 1)

    if not resp.ok:
        logger.error("Telegram error %s: %s", resp.status_code, resp.text[:300])
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
