# lambdas/publish_telegram/handler.py
import json
import logging
import os
import re
import secrets
import string
import sys
import time
from datetime import datetime, timezone
from html import escape as html_escape
from html import unescape as html_unescape

import boto3
import requests
from botocore.exceptions import ClientError

_pkg = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _pkg)
_shared = os.path.normpath(os.path.join(_pkg, "..", "..", "shared"))
if os.path.isdir(_shared):
    sys.path.insert(0, _shared)

from dynamo import mark_as_queued, mark_as_sent, query_by_telegram_status

logger = logging.getLogger()
logger.setLevel(os.environ.get("LOG_LEVEL", "INFO"))

CHANNEL_ID = os.environ["TELEGRAM_CHANNEL_ID"]
PUBLIC_LINK_BASE = (
    os.environ.get("PUBLIC_LINK_BASE") or os.environ.get("PUBLIC_API_BASE") or ""
).rstrip("/")
SHORT_LINKS_TABLE = os.environ["SHORT_LINKS_TABLE"]
_region = os.environ.get("AWS_REGION", "us-east-1")

_slug_alphabet = string.ascii_letters + string.digits
_short_links = boto3.resource("dynamodb", region_name=_region).Table(SHORT_LINKS_TABLE)

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

    return "\n".join(parts)


def _article_read_url(item_id: str) -> str:
    if not PUBLIC_LINK_BASE:
        raise RuntimeError("PUBLIC_LINK_BASE is not set")
    return f"{PUBLIC_LINK_BASE}/r/{item_id}"


def _public_p_url(slug: str) -> str:
    return f"{PUBLIC_LINK_BASE}/p/{slug}"


def _allocate_slug(item_id: str) -> str:
    ttl = int(time.time()) + 86400 * 120
    for _ in range(16):
        slug = "".join(secrets.choice(_slug_alphabet) for _ in range(8))
        try:
            _short_links.put_item(
                Item={"slug": slug, "item_id": item_id, "ttl": ttl},
                ConditionExpression="attribute_not_exists(#s)",
                ExpressionAttributeNames={"#s": "slug"},
            )
            return slug
        except ClientError as e:
            if e.response["Error"]["Code"] == "ConditionalCheckFailedException":
                continue
            raise
    raise RuntimeError("No se pudo generar slug único")


def build_inline_keyboard(
    item_id: str,
    like_count: int = 0,
    read_button_url: str | None = None,
) -> dict:
    read_u = (read_button_url or "").strip() or _article_read_url(item_id)
    like_label = f"👍 {like_count}"
    return {
        "inline_keyboard": [
            [
                {"text": "📖 Leer artículo", "url": read_u},
                {"text": like_label, "callback_data": f"like:{item_id}"},
            ]
        ]
    }


def _plain_fallback(item: dict) -> str:
    cat_info = CATEGORY_MAP.get(item.get("category", ""), {"emoji": "📌", "label": "NEWS"})
    return (
        f"◉ PULSO IA\n"
        f"{cat_info['emoji']} {cat_info['label']}\n\n"
        f"{(item.get('title') or '')[:100]}\n\n"
        f"{(item.get('summary_es') or '')[:280]}"
    )


def get_bot_token() -> str:
    ssm = boto3.client("ssm", region_name=os.environ.get("AWS_REGION", "us-east-1"))
    return ssm.get_parameter(
        Name="/pulso-ia/telegram-bot-token", WithDecryption=True
    )["Parameter"]["Value"]


_OG_IMAGE_PATTERNS = (
    re.compile(
        r'<meta\s+[^>]*property\s*=\s*["\']og:image["\'][^>]*content\s*=\s*["\']([^"\']+)["\']',
        re.I,
    ),
    re.compile(
        r'<meta\s+[^>]*content\s*=\s*["\']([^"\']+)["\'][^>]*property\s*=\s*["\']og:image["\']',
        re.I,
    ),
    re.compile(
        r'<meta\s+[^>]*name\s*=\s*["\']twitter:image["\'][^>]*content\s*=\s*["\']([^"\']+)["\']',
        re.I,
    ),
    re.compile(
        r'<meta\s+[^>]*name\s*=\s*["\']twitter:image:src["\'][^>]*content\s*=\s*["\']([^"\']+)["\']',
        re.I,
    ),
)


def _extract_og_image_url(html: str) -> str | None:
    for pat in _OG_IMAGE_PATTERNS:
        m = pat.search(html)
        if not m:
            continue
        raw = html_unescape(m.group(1).strip())
        if raw.startswith(("https://", "http://")):
            return raw
    return None


def _fetch_article_preview_image(article_url: str) -> str | None:
    try:
        r = requests.get(
            article_url,
            timeout=(3, 6),
            headers={
                "User-Agent": "Mozilla/5.0 (compatible; PulsoIA/1.0; +https://workium.ai)",
                "Accept": "text/html,application/xhtml+xml;q=0.9,*/*;q=0.8",
            },
            allow_redirects=True,
        )
    except (requests.RequestException, OSError) as e:
        logger.info("og:image fetch skip: %s", e)
        return None
    if r.status_code != 200 or not r.text:
        return None
    snippet = r.text[:900_000]
    return _extract_og_image_url(snippet)


def _send_message_no_preview(
    token: str, text: str, keyboard: dict | None, plain: bool
) -> dict | None:
    payload: dict = {
        "chat_id": CHANNEL_ID,
        "text": text,
        "disable_web_page_preview": True,
    }
    if not plain:
        payload["parse_mode"] = "HTML"
    if keyboard:
        payload["reply_markup"] = keyboard
    resp = requests.post(
        f"https://api.telegram.org/bot{token}/sendMessage",
        json=payload,
        timeout=15,
    )
    return resp.json().get("result") if resp.ok else None


_MAX_CAPTION = 1024


def _telegram_caption(html: str) -> str:
    if len(html) <= _MAX_CAPTION:
        return html
    return html[: _MAX_CAPTION - 1] + "…"


def send_card(
    token: str,
    item: dict,
    keyboard: dict | None = None,
) -> dict | None:
    article_url = (item.get("url") or "").strip()
    card_html = build_card(item)
    photo_url: str | None = None
    if article_url.lower().startswith(("http://", "https://")):
        photo_url = _fetch_article_preview_image(article_url)

    if photo_url:
        caption = _telegram_caption(card_html)
        p_payload: dict = {
            "chat_id": CHANNEL_ID,
            "photo": photo_url,
            "caption": caption,
            "parse_mode": "HTML",
        }
        if keyboard:
            p_payload["reply_markup"] = keyboard
        for _ in range(4):
            resp = requests.post(
                f"https://api.telegram.org/bot{token}/sendPhoto",
                json=p_payload,
                timeout=25,
            )
            if resp.ok:
                return resp.json().get("result")
            if resp.status_code == 429:
                retry_after = resp.json().get("parameters", {}).get("retry_after", 5)
                time.sleep(retry_after)
                continue
            logger.warning(
                "sendPhoto falló (%s), texto sin preview: %s",
                resp.status_code,
                (resp.text or "")[:250],
            )
            break

    payload: dict = {
        "chat_id": CHANNEL_ID,
        "text": card_html,
        "parse_mode": "HTML",
        "disable_web_page_preview": True,
    }
    if keyboard:
        payload["reply_markup"] = keyboard

    for _ in range(4):
        resp = requests.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            json=payload,
            timeout=15,
        )
        if resp.ok:
            return resp.json().get("result")
        if resp.status_code == 429:
            retry_after = resp.json().get("parameters", {}).get("retry_after", 5)
            time.sleep(retry_after)
            continue
        err_txt = resp.text or ""
        logger.error("Telegram error %s: %s", resp.status_code, err_txt[:400])
        if resp.status_code == 400 and "parse" in err_txt.lower():
            logger.warning("HTML parse error — retrying as plain text")
            plain_text = _plain_fallback(item)
            if photo_url:
                cap = _telegram_caption(plain_text)
                for _ in range(4):
                    resp_p = requests.post(
                        f"https://api.telegram.org/bot{token}/sendPhoto",
                        json={
                            "chat_id": CHANNEL_ID,
                            "photo": photo_url,
                            "caption": cap,
                            "reply_markup": keyboard,
                        },
                        timeout=25,
                    )
                    if resp_p.ok:
                        return resp_p.json().get("result")
                    if resp_p.status_code == 429:
                        time.sleep(
                            resp_p.json().get("parameters", {}).get("retry_after", 5)
                        )
                        continue
                    break
            return _send_message_no_preview(token, plain_text, keyboard, plain=True)
        return None

    return None


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

    token = get_bot_token()
    sent = 0
    failed = 0

    try:
        item_id = to_send["item_id"]
        slug = _allocate_slug(item_id)
        read_btn = _public_p_url(slug)
        keyboard = build_inline_keyboard(item_id, 0, read_button_url=read_btn)
        result = send_card(token, to_send, keyboard)
        if result:
            mid = result.get("message_id")
            mark_as_sent(item_id, mid, read_slug=slug)
            sent = 1
            for it in remainder:
                if it.get("telegram_sent") not in ("queued", "true"):
                    mark_as_queued(it["item_id"])
        else:
            try:
                _short_links.delete_item(Key={"slug": slug})
            except Exception:
                pass
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
