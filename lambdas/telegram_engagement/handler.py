# lambdas/telegram_engagement/handler.py — /c (apertura) + POST /webhook/telegram (like)
import base64
import json
import logging
import os
import sys
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import unquote

import boto3
import requests

_sh = os.path.join(os.path.dirname(__file__), "../../shared")
if os.path.isfile(os.path.join(_sh, "engagement.py")) and _sh not in sys.path:
    sys.path.insert(0, _sh)

from engagement import put_event  # noqa: E402
from like_counts import increment_and_get  # noqa: E402
from outbound_url import build_workium_r_url  # noqa: E402

logger = logging.getLogger()
logger.setLevel(os.environ.get("LOG_LEVEL", "INFO"))

# Reutilizar token y TCP: SSM en cada cold start es lento; Telegram penaliza si answerCallbackQuery tarda.
_bot_token: str | None = None
_tg = requests.Session()


def get_bot_token() -> str:
    global _bot_token
    if _bot_token:
        return _bot_token
    ssm = boto3.client("ssm")
    _bot_token = ssm.get_parameter(
        Name="/pulso-ia/telegram-bot-token", WithDecryption=True
    )["Parameter"]["Value"]
    return _bot_token


def _response(status: int, body: str, headers: dict | None = None) -> dict:
    h = {"content-type": "text/plain; charset=utf-8"}
    if headers:
        h.update(headers)
    return {
        "statusCode": status,
        "headers": h,
        "body": body,
    }


def _handle_get_open(event: dict) -> dict:
    """GET /c?i=item_id&d=url — registra apertura en Dynamo, redirige a Workium o al destino."""
    qs = event.get("queryStringParameters") or {}
    if isinstance(qs, str):
        return _response(400, "bad request")
    iid = (qs.get("i") or qs.get("item_id") or "").strip()[:100]
    raw = qs.get("d") or ""
    d = unquote(raw) if raw else ""
    d = d.strip()[:2000]
    if not iid or not d:
        return _response(400, "missing i or d")
    if not d.startswith("http://") and not d.startswith("https://"):
        return _response(400, "invalid d")
    try:
        put_event(iid, "open")
    except Exception as e:
        logger.error(json.dumps({"action": "put_open_failed", "error": str(e)}))
    loc = build_workium_r_url(iid) or d
    if len(loc) > 2040:
        loc = d
    return {
        "statusCode": 302,
        "headers": {
            "Location": loc,
            "content-type": "text/plain; charset=utf-8",
            "Cache-Control": "no-store",
        },
        "body": "",
    }


def _rebuild_reply_markup(
    message: dict, like_count: int, callback_data: str
) -> dict | None:
    """Misma fila (Leer más + like) con el texto del like actualizado."""
    rows = (message.get("reply_markup") or {}).get("inline_keyboard") or []
    if not rows or not rows[0] or len(rows[0]) < 2:
        return None
    read_btn = {k: v for k, v in rows[0][0].items() if k in ("text", "url")}
    if "url" not in read_btn or not (read_btn.get("url") or "").strip():
        return None
    cb = (callback_data or "")[:64]
    like_label = f"👍 {like_count}" if like_count else "👍 0"
    return {
        "inline_keyboard": [
            [read_btn, {"text": like_label, "callback_data": cb}]
        ]
    }


def _handle_telegram_webhook(event: dict) -> dict:
    body = event.get("body") or "{}"
    if event.get("isBase64Encoded"):
        body = base64.b64decode(body).decode("utf-8", errors="replace")
    # Validación opcional (setWebhook con secret)
    secret = os.environ.get("TELEGRAM_WEBHOOK_SECRET", "").strip()
    if secret:
        got = (event.get("headers") or {}).get("x-telegram-bot-api-secret-token")
        if got != secret and (event.get("headers") or {}).get("X-Telegram-Bot-Api-Secret-Token") != secret:
            return _response(401, "unauthorized")
    try:
        u = json.loads(body) if body else {}
    except json.JSONDecodeError:
        return _response(400, "bad json")
    cq = u.get("callback_query")
    if not cq or not isinstance(cq, dict):
        return _response(200, "ok")
    data = (cq.get("data") or "").strip()
    if not data.startswith("like:"):
        return _response(200, "ok")
    item_id = data.split("like:", 1)[1].strip()[:100]
    uid = cq.get("from", {}).get("id")
    uid_s = str(uid) if uid is not None else None
    n = 0
    token: str | None = None
    cq_id = cq.get("id")
    # 1) Contador + token en paralelo (menos tiempo hasta answerCallbackQuery).
    # 2) answerCallbackQuery antes de put_event: el toast depende de la API de Telegram, no del analytics.
    if item_id and item_id != "unknown":
        try:
            with ThreadPoolExecutor(max_workers=2) as ex:
                f_n = ex.submit(increment_and_get, item_id)
                f_t = ex.submit(get_bot_token)
                n = f_n.result(timeout=6)
                token = f_t.result(timeout=6)
        except Exception as e:
            logger.error(json.dumps({"action": "like_parallel_failed", "error": str(e)}))
            try:
                n = increment_and_get(item_id)
            except Exception as e2:
                logger.error(json.dumps({"action": "increment_like_failed", "error": str(e2)}))
            try:
                token = get_bot_token()
            except Exception as e3:
                logger.error(json.dumps({"action": "get_token_failed", "error": str(e3)}))
    else:
        n = 0
        try:
            token = get_bot_token()
        except Exception as e:
            logger.error(json.dumps({"action": "get_token_failed", "error": str(e)}))
    if n:
        toast = f"Listo. Total: {n} me gusta"
    else:
        toast = "Listo"
    if len(toast) > 200:
        toast = toast[:200]
    if token and cq_id:
        try:
            r = _tg.post(
                f"https://api.telegram.org/bot{token}/answerCallbackQuery",
                json={
                    "callback_query_id": cq_id,
                    "text": toast,
                    "show_alert": False,
                },
                timeout=(3, 5),
            )
            td = r.json()
            if not td.get("ok"):
                logger.warning(
                    json.dumps({"action": "answer_callback_telegram_error", "body": td})
                )
        except Exception as e:
            logger.warning(json.dumps({"action": "answer_callback_failed", "error": str(e)}))
    if item_id and item_id != "unknown":
        try:
            put_event(item_id, "like", telegram_user_id=uid_s)
        except Exception as e:
            logger.error(json.dumps({"action": "put_like_failed", "error": str(e)}))
    msg = cq.get("message")
    if token and n and isinstance(msg, dict):
        new_markup = _rebuild_reply_markup(msg, n, data)
        if new_markup:
            ch = (msg.get("chat") or {})
            mid = msg.get("message_id")
            cid = ch.get("id")
            if cid is not None and mid is not None:
                try:
                    _tg.post(
                        f"https://api.telegram.org/bot{token}/editMessageReplyMarkup",
                        json={
                            "chat_id": cid,
                            "message_id": mid,
                            "reply_markup": new_markup,
                        },
                        timeout=(3, 8),
                    )
                except Exception as e:
                    logger.warning(
                        json.dumps({"action": "edit_reply_markup_failed", "error": str(e)})
                    )
    return _response(200, "ok")


def handler(event, context):
    ctx = event.get("requestContext") or {}
    http = ctx.get("http", {}) or {}
    method = (http.get("method") or "GET").upper()
    path = (event.get("rawPath") or event.get("path") or "/").rstrip() or "/"

    if method == "GET" and (path == "/c" or path.endswith("/c")):
        return _handle_get_open(event)
    if method == "POST" and ("webhook" in path):
        return _handle_telegram_webhook(event)
    return _response(404, "not found")
