# shared/like_counts.py — contador agregado de likes por item (botón + webhook)
import os

import boto3

_t = None
_tname: str | None = None


def _table():
    global _t, _tname
    n = (os.environ.get("DYNAMODB_LIKE_COUNTS_TABLE") or "").strip()
    if not n:
        return None
    if _t is None or _tname != n:
        _tname = n
        _t = boto3.resource("dynamodb").Table(n)
    return _t


def get_count(item_id: str) -> int:
    """0 si no hay tabla, error o aún no hay likes."""
    if not (item_id or "").strip():
        return 0
    tbl = _table()
    if tbl is None:
        return 0
    key = (item_id or "")[:100]
    try:
        r = tbl.get_item(Key={"item_id": key})
    except Exception:
        return 0
    c = (r.get("Item") or {}).get("count")
    if c is None:
        return 0
    try:
        return int(c)
    except (TypeError, ValueError):
        return 0


def increment_and_get(item_id: str) -> int:
    """
    Incrementa en 1 y devuelve el total.
    Crea el ítem en Dynamo si no existía.
    """
    if not (item_id or "").strip() or (item_id or "")[:100] == "unknown":
        return 0
    tbl = _table()
    if tbl is None:
        return 0
    key = (item_id or "")[:100]
    r = tbl.update_item(
        Key={"item_id": key},
        UpdateExpression="ADD #c :one",
        ExpressionAttributeNames={"#c": "count"},
        ExpressionAttributeValues={":one": 1},
        ReturnValues="ALL_NEW",
    )
    c = (r.get("Attributes") or {}).get("count")
    return int(c) if c is not None else 1
