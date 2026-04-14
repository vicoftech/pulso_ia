# lambdas/engagement_handler/handler.py
"""
- GET /p/{slug} → slug en Dynamo → item_id → article_click + 302 (enlace corto propio).
- GET /r/{item_id} → legado / sin slug.
- POST /webhook → callback_query like:{item_id} (read: legado).
"""
import base64
import json
import logging
import os
import time
import uuid
from datetime import datetime, timezone
from urllib.error import URLError
from urllib.request import Request, urlopen

import boto3
from boto3.dynamodb.conditions import Attr, Key
from botocore.exceptions import ClientError

logger = logging.getLogger()
logger.setLevel(os.environ.get("LOG_LEVEL", "INFO"))

ITEMS_TABLE = os.environ["DYNAMODB_TABLE"]
EVENTS_TABLE = os.environ["EVENTS_TABLE"]
SHORT_LINKS_TABLE = os.environ["SHORT_LINKS_TABLE"]
_region = os.environ.get("AWS_REGION") or os.environ.get("AWS_DEFAULT_REGION") or "us-east-1"
PUBLIC_LINK_BASE = (
    os.environ.get("PUBLIC_LINK_BASE") or os.environ.get("PUBLIC_API_BASE") or ""
).rstrip("/")

dynamodb = boto3.resource("dynamodb", region_name=_region)
items_table = dynamodb.Table(ITEMS_TABLE)
events_table = dynamodb.Table(EVENTS_TABLE)
short_links_table = dynamodb.Table(SHORT_LINKS_TABLE)
ssm = boto3.client("ssm", region_name=_region)

_token_cache: str | None = None


def _post_json(url: str, payload: dict, timeout: float = 10) -> None:
    data = json.dumps(payload).encode("utf-8")
    req = Request(
        url,
        data=data,
        headers={"Content-Type": "application/json; charset=utf-8"},
        method="POST",
    )
    with urlopen(req, timeout=timeout) as resp:
        resp.read()


def get_token() -> str:
    global _token_cache
    if not _token_cache:
        _token_cache = ssm.get_parameter(
            Name="/pulso-ia/telegram-bot-token",
            WithDecryption=True,
        )["Parameter"]["Value"]
    return _token_cache


def get_item_metadata(item_id: str) -> dict:
    try:
        resp = items_table.get_item(
            Key={"item_id": item_id},
            ProjectionExpression="item_id, title, #src, category, #url, read_slug, read_short_url",
            ExpressionAttributeNames={"#src": "source", "#url": "url"},
        )
        return resp.get("Item") or {}
    except Exception as e:
        logger.warning("Could not fetch item metadata for %s: %s", item_id, e)
        return {}


def claim_callback_query_once(callback_query_id: str) -> bool:
    """
    Telegram puede reenviar el mismo callback si el webhook tarda o falla.
    Sin esto, la 1ª invocación hace like y la 2ª hace unlike (mismo cq_id).
    """
    if not callback_query_id:
        return False
    try:
        events_table.put_item(
            Item={
                "event_id": f"tgcb:{callback_query_id}",
                "ttl": int(time.time()) + 7 * 86400,
            },
            ConditionExpression="attribute_not_exists(event_id)",
        )
        return True
    except ClientError as e:
        if e.response.get("Error", {}).get("Code") == "ConditionalCheckFailedException":
            return False
        raise


def record_event(
    item_id: str,
    item_meta: dict,
    user_id: str,
    username: str,
    event_name: str,
) -> None:
    try:
        now = datetime.now(timezone.utc)
        events_table.put_item(
            Item={
                "event_id": str(uuid.uuid4()),
                "item_id": item_id,
                "item_title": (item_meta.get("title") or "")[:200],
                "item_source": item_meta.get("source") or "unknown",
                "item_category": item_meta.get("category") or "unknown",
                "item_url": item_meta.get("url") or "",
                "user_id": str(user_id),
                "username": username or "",
                "event_name": event_name,
                "occurred_at": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
                "ttl": int(now.timestamp()) + (90 * 86400),
            }
        )
        logger.info(
            json.dumps(
                {
                    "event": event_name,
                    "item_id": item_id,
                    "user_id": user_id,
                    "username": username,
                }
            )
        )
    except Exception as e:
        logger.error("Failed to record event %s for %s: %s", event_name, item_id, e)


def has_liked(user_id: str, item_id: str) -> bool:
    try:
        kwargs: dict = {
            "IndexName": "item_id-occurred_at-index",
            "KeyConditionExpression": Key("item_id").eq(item_id),
            "FilterExpression": Attr("user_id").eq(str(user_id))
            & (
                Attr("event_name").eq("article_like")
                | Attr("event_name").eq("article_unlike")
            ),
            "ScanIndexForward": False,
        }
        while True:
            resp = events_table.query(**kwargs)
            for row in resp.get("Items", []):
                return row.get("event_name") == "article_like"
            lek = resp.get("LastEvaluatedKey")
            if not lek:
                return False
            kwargs["ExclusiveStartKey"] = lek
    except Exception:
        return False


def get_like_count(item_id: str) -> int:
    try:
        likes = 0
        unlikes = 0
        kwargs: dict = {
            "IndexName": "item_id-occurred_at-index",
            "KeyConditionExpression": Key("item_id").eq(item_id),
            "FilterExpression": Attr("event_name").eq("article_like")
            | Attr("event_name").eq("article_unlike"),
        }
        while True:
            resp = events_table.query(**kwargs)
            for row in resp.get("Items", []):
                if row.get("event_name") == "article_like":
                    likes += 1
                elif row.get("event_name") == "article_unlike":
                    unlikes += 1
            lek = resp.get("LastEvaluatedKey")
            if not lek:
                break
            kwargs["ExclusiveStartKey"] = lek
        return max(0, likes - unlikes)
    except Exception:
        return 0


def article_read_button_url(item_id: str) -> str:
    if not PUBLIC_LINK_BASE:
        raise RuntimeError("PUBLIC_LINK_BASE is not set")
    return f"{PUBLIC_LINK_BASE}/r/{item_id}"


def resolve_read_button_url(item_id: str, meta: dict) -> str:
    slug = (meta.get("read_slug") or "").strip()
    if slug and slug.isalnum() and 4 <= len(slug) <= 16:
        return f"{PUBLIC_LINK_BASE}/p/{slug}"
    legacy = (meta.get("read_short_url") or "").strip()
    if legacy.startswith("http"):
        return legacy
    return article_read_button_url(item_id)


def build_keyboard(
    item_id: str,
    like_count: int = 0,
    item_meta: dict | None = None,
) -> dict:
    meta = item_meta if item_meta is not None else get_item_metadata(item_id)
    read_u = resolve_read_button_url(item_id, meta)
    like_label = f"👍 {like_count}"
    return {
        "inline_keyboard": [
            [
                {"text": "📖 Leer artículo", "url": read_u},
                {"text": like_label, "callback_data": f"like:{item_id}"},
            ]
        ]
    }


def answer_callback(
    token: str,
    callback_query_id: str,
    text: str = "",
    url: str | None = None,
) -> None:
    try:
        payload: dict = {
            "callback_query_id": callback_query_id,
            "show_alert": False,
        }
        if url:
            payload["url"] = url
        else:
            payload["text"] = text
        _post_json(
            f"https://api.telegram.org/bot{token}/answerCallbackQuery",
            payload,
            timeout=5,
        )
    except (URLError, OSError) as e:
        logger.warning("answerCallbackQuery failed: %s", e)


def edit_keyboard(
    token: str,
    chat_id: str,
    message_id: int,
    keyboard: dict,
) -> None:
    try:
        _post_json(
            f"https://api.telegram.org/bot{token}/editMessageReplyMarkup",
            {
                "chat_id": chat_id,
                "message_id": message_id,
                "reply_markup": keyboard,
            },
            timeout=10,
        )
    except (URLError, OSError) as e:
        logger.warning("editMessageReplyMarkup failed: %s", e)


def _parse_body(event: dict) -> dict:
    raw = event.get("body") or "{}"
    if event.get("isBase64Encoded"):
        raw = base64.b64decode(raw).decode("utf-8")
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return {}


def _redirect_response(location: str) -> dict:
    return {
        "statusCode": 302,
        "headers": {"Location": location},
        "body": "",
    }


def handle_article_redirect(item_id: str) -> dict:
    item_id = (item_id or "").strip()
    if not item_id or len(item_id) > 40:
        return {
            "statusCode": 400,
            "headers": {"content-type": "text/plain; charset=utf-8"},
            "body": "Solicitud no válida",
        }

    item_meta = get_item_metadata(item_id)
    article_url = (item_meta.get("url") or "").strip()
    if not article_url:
        return {
            "statusCode": 404,
            "headers": {"content-type": "text/plain; charset=utf-8"},
            "body": "Artículo sin enlace",
        }

    if not article_url.lower().startswith(("http://", "https://")):
        return {
            "statusCode": 400,
            "headers": {"content-type": "text/plain; charset=utf-8"},
            "body": "Enlace no permitido",
        }

    record_event(
        item_id=item_id,
        item_meta=item_meta,
        user_id="link_open",
        username="",
        event_name="article_click",
    )

    return _redirect_response(article_url)


def handle_short_redirect(slug: str) -> dict:
    slug = (slug or "").strip()
    if not slug or not slug.isalnum() or len(slug) > 16:
        return {
            "statusCode": 400,
            "headers": {"content-type": "text/plain; charset=utf-8"},
            "body": "Enlace no válido",
        }
    try:
        r = short_links_table.get_item(Key={"slug": slug}, ProjectionExpression="item_id")
        row = r.get("Item") or {}
        item_id = (row.get("item_id") or "").strip()
    except Exception as e:
        logger.warning("short_links get_item: %s", e)
        item_id = ""
    if not item_id:
        return {
            "statusCode": 404,
            "headers": {"content-type": "text/plain; charset=utf-8"},
            "body": "Enlace expirado o no encontrado",
        }
    return handle_article_redirect(item_id)


def handle_post_webhook(event: dict) -> dict:
    body = _parse_body(event)

    if "callback_query" not in body:
        return {"statusCode": 200, "body": json.dumps({"ok": True})}

    cq = body["callback_query"]
    cq_id = cq["id"]
    data = cq.get("data") or ""
    from_user = cq.get("from") or {}
    user_id = str(from_user.get("id", ""))
    username = (from_user.get("username") or "") or ""
    msg = cq.get("message") or {}
    chat_id = str(msg.get("chat", {}).get("id", ""))
    message_id = msg.get("message_id")

    if ":" not in data or message_id is None:
        answer_callback(get_token(), cq_id, "⚠️ Acción no reconocida")
        return {"statusCode": 200, "body": json.dumps({"ok": True})}

    action, item_id = data.split(":", 1)
    token = get_token()

    if not claim_callback_query_once(cq_id):
        answer_callback(token, cq_id, "")
        return {"statusCode": 200, "body": json.dumps({"ok": True})}

    if action == "read":
        item_meta = get_item_metadata(item_id)
        record_event(
            item_id=item_id,
            item_meta=item_meta,
            user_id=user_id,
            username=username,
            event_name="article_click",
        )
        article_url = (item_meta.get("url") or "").strip()
        if article_url:
            answer_callback(token, cq_id, url=article_url)
        else:
            answer_callback(token, cq_id, "📖 Sin enlace disponible")

    elif action == "like":
        item_meta = get_item_metadata(item_id)
        before_total = get_like_count(item_id)
        already = has_liked(user_id, item_id)
        if already:
            event_name = "article_unlike"
            toast = "Listo — sin me gusta"
        else:
            event_name = "article_like"
            toast = "👍 ¡Registrado!"

        record_event(
            item_id=item_id,
            item_meta=item_meta,
            user_id=user_id,
            username=username,
            event_name=event_name,
        )
        # GSI eventual: get_like_count justo después del put suele quedar desfasado.
        new_count = max(0, before_total + (-1 if already else 1))
        keyboard = build_keyboard(item_id, like_count=new_count, item_meta=item_meta)
        answer_callback(token, cq_id, toast)
        edit_keyboard(token, chat_id, int(message_id), keyboard)
    else:
        answer_callback(token, cq_id, "⚠️ Acción no reconocida")

    return {"statusCode": 200, "body": json.dumps({"ok": True})}


def handler(event, context):
    try:
        http = (event.get("requestContext") or {}).get("http") or {}
        method = (http.get("method") or "GET").upper()
        raw_path = event.get("rawPath") or ""

        if method == "GET" and raw_path.startswith("/p/"):
            slug = (event.get("pathParameters") or {}).get("slug", "")
            if not slug:
                slug = raw_path.removeprefix("/p/").split("/")[0]
            return handle_short_redirect(slug)

        if method == "GET" and raw_path.startswith("/r/"):
            item_id = (event.get("pathParameters") or {}).get("item_id", "")
            if not item_id:
                item_id = raw_path.removeprefix("/r/").split("/")[0]
            return handle_article_redirect(item_id)

        if method == "POST":
            return handle_post_webhook(event)
    except Exception as e:
        logger.exception("handler error: %s", e)
        return {"statusCode": 500, "body": json.dumps({"ok": False})}

    return {"statusCode": 200, "body": json.dumps({"ok": True})}
