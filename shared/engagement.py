# shared/engagement.py — eventos de engagement (apertura / like) para análisis
import os
import time
import uuid
from datetime import datetime, timezone

import boto3

_table: object = None
_table_name: str | None = None


def _t():
    global _table, _table_name
    n = os.environ.get("DYNAMODB_ENGAGEMENT_TABLE")
    if not n:
        raise RuntimeError("DYNAMODB_ENGAGEMENT_TABLE is not set")
    if _table is None or _table_name != n:
        _table_name = n
        _table = boto3.resource("dynamodb").Table(n)
    return _table


def put_event(
    item_id: str,
    event_type: str,
    *,
    telegram_user_id: str | None = None,
) -> None:
    """Escribe un evento; SK único con timestamp ISO (ordenable) + random."""
    if not item_id or not event_type:
        return
    at = (
        datetime.now(timezone.utc)
        .replace(microsecond=0)
        .isoformat()
        .replace("+00:00", "Z")
    )
    uid = uuid.uuid4().hex[:16]
    sk = f"{at}#{event_type}#{uid}"[:900]
    item: dict = {
        "item_id": item_id[:100],
        "event_sk": sk,
        "event_type": event_type[:20],
        "at": at,
        "ttl": int(time.time()) + 2 * 365 * 24 * 3600,
    }
    if telegram_user_id is not None and str(telegram_user_id).strip():
        item["telegram_user_id"] = str(telegram_user_id)[:32]
    _t().put_item(Item=item)
