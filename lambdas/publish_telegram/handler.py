# lambdas/publish_telegram/handler.py
import json
import logging
import os
import sys
import time
from datetime import datetime
from zoneinfo import ZoneInfo

import boto3
import requests

_pkg = os.path.dirname(os.path.abspath(__file__))
if _pkg not in sys.path:
    sys.path.insert(0, _pkg)
_sh = os.path.join(_pkg, "../../shared")
if os.path.isfile(os.path.join(_sh, "dynamo.py")) and _sh not in sys.path:
    sys.path.insert(0, _sh)

from dynamo import get_latest_sent_item, get_oldest_queued_item, mark_as_sent
from like_counts import get_count as get_like_count
from og_image import extract_og_image_url
from outbound_url import build_open_and_track_url

logger = logging.getLogger()
logger.setLevel(os.environ.get("LOG_LEVEL", "INFO"))
CHANNEL_ID = os.environ["TELEGRAM_CHANNEL_ID"]
TZ = os.environ.get("TIMEZONE", "America/Argentina/Buenos_Aires")

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
# Nombre de categoría para lectura (Categoría / Subcategoría en el card)
CATEGORY_LABEL_ES = {
    "NEW_PRODUCT": "Nuevo producto",
    "MODEL_UPDATE": "Actualización de modelo o versión",
    "METHODOLOGY": "Metodología, papers y técnicas",
    "MARKET_NEWS": "Mercado, inversión y regulación",
    "USE_CASE": "Caso de uso y aplicación",
    "UNCATEGORIZED": "Sin clasificar",
}

def get_bot_token() -> str:
    ssm = boto3.client("ssm")
    return ssm.get_parameter(
        Name="/pulso-ia/telegram-bot-token", WithDecryption=True
    )["Parameter"]["Value"]

def escape_md2(text: str) -> str:
    special = r"_*[]()~`>#+-=|{}.!"
    return "".join(f"\\{c}" if c in special else c for c in text)


def _category_label_es(category: str) -> str:
    c = (category or "").strip() or "USE_CASE"
    return CATEGORY_LABEL_ES.get(
        c, c.replace("_", " ").title() if c else "Sin clasificar"
    )


def _source_data_label(source: str) -> str:
    s = (source or "").strip()
    if s == "arxiv":
        return "ArXiv"
    if s == "producthunt":
        return "Product Hunt"
    if s == "github":
        return "GitHub"
    if s.startswith("rss_"):
        tail = s[4:].replace("_", " ").strip()
        return f"RSS · {tail}" if tail else "RSS"
    return s or "—"


def _relevance_line(item: dict) -> str:
    v = item.get("relevance_score", 0)
    try:
        n = int(v)  # int o Decimal luego de _normalize
    except (TypeError, ValueError):
        n = 0
    n = max(0, min(100, n))
    return f"{n}/100"


def _inline_keyboard(item: dict) -> dict:
    """Leer más: /c + Workium; me gusta: callback → contador (Dynamo) + webhook (evento)."""
    url = (item.get("url") or "").strip()[:2000]
    if not url:
        return {}
    iid = (item.get("item_id") or "")[:50]
    url = build_open_and_track_url(iid, url)
    # callback_data: máx 64 bytes (Telegram) — el número va solo en el texto del botón
    cb = f"like:{iid}" if iid else "like:unknown"
    if len(cb.encode("utf-8")) > 64:
        cb = cb[:64]
    n = get_like_count(iid) if iid and iid != "unknown" else 0
    like_label = f"👍 {n}" if n else "👍 0"
    return {
        "inline_keyboard": [
            [
                {"text": "🔗 Leer más", "url": url},
                {"text": like_label, "callback_data": cb},
            ]
        ]
    }


def _caption_under_telegram_limit(item: dict, max_len: int = 1000) -> str:
    """Límite sendPhoto: caption 1024; dejamos margen y acortamos el resumen."""
    summary_full = (item.get("summary_es") or "")
    for n in (min(len(summary_full), 500), 400, 300, 220, 160, 100, 60):
        it = {**item, "summary_es": summary_full[:n] + ("…" if len(summary_full) > n else "")}
        t = format_message(it)
        if len(t) <= max_len:
            return t
    it = {**item, "summary_es": (summary_full[:30] + "…") if summary_full else ""}
    return format_message(it)[:max_len]


def resolve_hero_image_url(item: dict) -> str | None:
    u = (item.get("image_url") or "").strip() if item.get("image_url") else ""
    if u.startswith("http://") or u.startswith("https://"):
        return u[:2000]
    page = (item.get("url") or "").strip()
    if not page.startswith("http://") and not page.startswith("https://"):
        return None
    got = extract_og_image_url(page, timeout=8.0)
    return (got or "")[:2000] or None


def send_message(
    token: str, text: str, item: dict | None = None, max_retries: int = 3
) -> bool:
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    payload: dict = {
        "chat_id": CHANNEL_ID,
        "text": text,
        "parse_mode": "MarkdownV2",
        "disable_web_page_preview": False,
    }
    if item:
        mark = _inline_keyboard(item)
        if mark:
            payload["reply_markup"] = mark
    for attempt in range(max_retries):
        resp = requests.post(
            url,
            json=payload,
            timeout=10,
        )
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


def send_telegram_card(token: str, item: dict) -> bool:
    """Foto con caption + teclado; si falla, mensaje de solo texto (sin imagen)."""
    text = format_message(item)
    img = resolve_hero_image_url(item)
    cap = _caption_under_telegram_limit(item)
    if img:
        pl: dict = {
            "chat_id": CHANNEL_ID,
            "photo": img,
            "caption": cap,
            "parse_mode": "MarkdownV2",
        }
        mark = _inline_keyboard(item)
        if mark:
            pl["reply_markup"] = mark
        r = requests.post(
            f"https://api.telegram.org/bot{token}/sendPhoto",
            json=pl,
            timeout=20,
        )
        d = r.json()
        if d.get("ok"):
            return True
        logger.warning(
            json.dumps(
                {
                    "action": "sendPhoto_fallback",
                    "telegram": d,
                    "photo": img[:150],
                }
            )
        )
    return send_message(token, text, item)

def format_message(item: dict) -> str:
    """Cabecera Pulso IA, metadatos, resumen y hashtags. El enlace al artículo va solo en el botón (con tracker)."""
    cat = (item.get("category") or "USE_CASE").strip() or "USE_CASE"
    meta = CATEGORY_META.get(cat, {"emoji": "📌", "tag": "#IA"})
    source = item.get("source", "") or ""
    source_tag = (
        SOURCE_TAGS.get(source, "#Blog")
        if not str(source).startswith("rss_")
        else "#Blog"
    )
    title_raw = (item.get("title") or "")[:100] or "Sin título"
    title = escape_md2(title_raw)
    summary = escape_md2((item.get("summary_es") or ""))
    sub_tag = meta["tag"]
    cat_lbl = _category_label_es(cat)
    rel = escape_md2(_relevance_line(item))
    body = [
        "*Pulso IA*",
        "",
        f"{meta['emoji']} *{title}*",
        "",
        summary,
        "",
        f"*Categoría:* {escape_md2(cat_lbl)}",
        f"*Subcategoría:* {escape_md2(sub_tag)}",
        f"*Fuente de datos:* {escape_md2(_source_data_label(str(source)))}",
        f"*Relevancia:* {rel}",
        "",
        f"{escape_md2(sub_tag)} {escape_md2(source_tag)}",
    ]
    return "\n".join(body)

def in_daytime_channel_window() -> bool:
    """Activo 09:00–20:59 AR (excluye 21:00 en adelante = sleep)."""
    now = datetime.now(ZoneInfo(TZ))
    return 9 <= now.hour < 21

def _normalize_item(d: dict) -> dict:
    from decimal import Decimal

    out = {}
    for k, v in d.items():
        if isinstance(v, Decimal):
            out[k] = int(v) if v % 1 == 0 else float(v)
        else:
            out[k] = v
    return out


def handler(event, context):
    ev = event if isinstance(event, dict) else {}
    if ev.get("action") == "resend_latest":
        item = get_latest_sent_item()
        if not item:
            return {"ok": False, "error": "no_sent_items"}
        item = _normalize_item(item)
        token = get_bot_token()
        ok = send_telegram_card(token, item)
        return {
            "ok": ok,
            "action": "resend_latest",
            "item_id": item.get("item_id"),
        }

    if not in_daytime_channel_window():
        return {
            "action": "skip_sleep_window",
            "timezone": TZ,
        }

    # Un ítem por invocación, FIFO por processed_at (GSI outbox).
    item = get_oldest_queued_item()
    if not item:
        return {"action": "noop", "reason": "empty_queue"}

    item = _normalize_item(item)
    token = get_bot_token()
    ok = send_telegram_card(token, item)
    if not ok:
        return {"action": "send_failed", "item_id": item.get("item_id")}

    mark_as_sent(item["item_id"])
    return {
        "action": "published",
        "item_id": item.get("item_id"),
    }
