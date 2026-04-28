# shared/dynamo.py
import boto3
from datetime import datetime, timezone
import os
from decimal import Decimal
from typing import Any, List, Optional

from boto3.dynamodb.conditions import Key

from models import ProcessedNewsItem, RawNewsItem

TABLE_NAME = os.environ["DYNAMODB_TABLE"]
dynamodb = boto3.resource("dynamodb")
table = dynamodb.Table(TABLE_NAME)
GSI_TELEGRAM = "telegram_sent-processed_at-index"


_STOPWORDS = {
    "el", "la", "los", "las", "de", "del", "en", "y", "a",
    "the", "of", "for", "with", "and", "or", "is", "to",
}


def _significant_words(title: str) -> set[str]:
    return {w for w in title.lower().split() if w not in _STOPWORDS}


def find_near_duplicate_ids(items: list[RawNewsItem]) -> set[str]:
    """Retorna item_ids a descartar por títulos near-duplicados dentro del lote.

    Dos ítems son near-duplicados si la similitud Jaccard de sus palabras
    significativas supera 0.6. De cada par se descarta el más antiguo;
    si published_at es igual, se descarta el que aparece segundo en la lista.
    """
    word_sets = [(item, _significant_words(item.title)) for item in items]
    to_discard: set[str] = set()

    for i in range(len(word_sets)):
        item_a, words_a = word_sets[i]
        if item_a.item_id in to_discard:
            continue
        for j in range(i + 1, len(word_sets)):
            item_b, words_b = word_sets[j]
            if item_b.item_id in to_discard:
                continue
            union = words_a | words_b
            if not union:
                continue
            if len(words_a & words_b) / len(union) > 0.6:
                # item_a appears first (i < j): if equal published_at, discard item_b
                if item_a.published_at >= item_b.published_at:
                    to_discard.add(item_b.item_id)
                else:
                    to_discard.add(item_a.item_id)
                    break  # item_a descartado, saltar sus pares restantes

    return to_discard


def batch_get_existing_ids(item_ids: List[str]) -> set:
    """Retorna el set de item_ids que ya existen en DynamoDB."""
    existing = set()
    for i in range(0, len(item_ids), 25):
        batch = item_ids[i : i + 25]
        response = dynamodb.batch_get_item(
            RequestItems={
                TABLE_NAME: {
                    "Keys": [{"item_id": id_} for id_ in batch],
                    "ProjectionExpression": "item_id",
                }
            }
        )
        for item in response.get("Responses", {}).get(TABLE_NAME, []):
            existing.add(item["item_id"])
    return existing


def _item_to_dynamo(item: ProcessedNewsItem) -> dict[str, Any]:
    return {k: v for k, v in item.__dict__.items() if v is not None}


def batch_save_items(items: List[ProcessedNewsItem]):
    """Guarda o actualiza items en DynamoDB en batches de 25."""
    with table.batch_writer() as batch:
        for item in items:
            batch.put_item(Item=_item_to_dynamo(item))


def mark_as_sent(
    item_id: str,
    message_id: int | None = None,
    read_slug: str | None = None,
):
    """Marca item como publicado; quita outbox; read_slug opcional para /p/{slug}."""
    sent_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    if message_id is not None:
        if read_slug:
            table.update_item(
                Key={"item_id": item_id},
                UpdateExpression=(
                    "SET telegram_sent = :t, telegram_message_id = :mid, "
                    "read_slug = :slug, sent_at = :sent_at REMOVE read_short_url, outbox_key"
                ),
                ExpressionAttributeValues={
                    ":t": "true",
                    ":mid": message_id,
                    ":slug": read_slug,
                    ":sent_at": sent_at,
                },
            )
        else:
            table.update_item(
                Key={"item_id": item_id},
                UpdateExpression=(
                    "SET telegram_sent = :t, telegram_message_id = :mid, sent_at = :sent_at "
                    "REMOVE outbox_key"
                ),
                ExpressionAttributeValues={
                    ":t": "true",
                    ":mid": message_id,
                    ":sent_at": sent_at,
                },
            )
    else:
        table.update_item(
            Key={"item_id": item_id},
            UpdateExpression="SET telegram_sent = :val, sent_at = :sent_at REMOVE outbox_key",
            ExpressionAttributeValues={":val": "true", ":sent_at": sent_at},
        )


def batch_get_telegram_sent(item_ids: List[str]) -> dict[str, str]:
    """Devuelve item_id -> telegram_sent para ítems existentes (solo atributo telegram_sent)."""
    out: dict[str, str] = {}
    for i in range(0, len(item_ids), 100):
        chunk = item_ids[i : i + 100]
        if not chunk:
            continue
        resp = dynamodb.batch_get_item(
            RequestItems={
                TABLE_NAME: {
                    "Keys": [{"item_id": id_} for id_ in chunk],
                    "ProjectionExpression": "item_id, telegram_sent",
                }
            }
        )
        for row in resp.get("Responses", {}).get(TABLE_NAME, []):
            out[row["item_id"]] = row.get("telegram_sent", "false")
    return out


def _deserialize_item(item: dict) -> dict:
    """Normaliza Decimal de Dynamo a tipos JSON-compatibles."""
    fixed = {}
    for k, v in item.items():
        if isinstance(v, Decimal):
            fixed[k] = int(v) if v % 1 == 0 else float(v)
        elif isinstance(v, dict):
            fixed[k] = _deserialize_item(v)
        elif isinstance(v, list):
            fixed[k] = [
                int(x) if isinstance(x, Decimal) and x % 1 == 0 else float(x) if isinstance(x, Decimal) else x
                for x in v
            ]
        else:
            fixed[k] = v
    return fixed


def query_by_telegram_status(status: str) -> List[dict]:
    """Consulta GSI telegram_sent + processed_at (todos los ítems con ese estado)."""
    rows: List[dict] = []
    kwargs: dict = {
        "IndexName": GSI_TELEGRAM,
        "KeyConditionExpression": "telegram_sent = :s",
        "ExpressionAttributeValues": {":s": status},
        "ScanIndexForward": True,
    }
    while True:
        resp = table.query(**kwargs)
        for it in resp.get("Items", []):
            rows.append(_deserialize_item(dict(it)))
        lek = resp.get("LastEvaluatedKey")
        if not lek:
            break
        kwargs["ExclusiveStartKey"] = lek
    return rows


def mark_as_queued(item_id: str) -> None:
    table.update_item(
        Key={"item_id": item_id},
        UpdateExpression="SET telegram_sent = :q",
        ExpressionAttributeValues={":q": "queued"},
    )


def get_oldest_queued_item() -> Optional[dict]:
    """Un ítem con outbox_key=1 (relevante, pendiente), por processed_at asc."""
    r = table.query(
        IndexName="outbox_key-processed_at-index",
        KeyConditionExpression=Key("outbox_key").eq("1"),
        ScanIndexForward=True,
        Limit=1,
    )
    items = r.get("Items", [])
    return items[0] if items else None


def get_latest_sent_item() -> Optional[dict]:
    """Última noticia publicada (telegram_sent=true), por processed_at mais reciente."""
    r = table.query(
        IndexName="telegram_sent-processed_at-index",
        KeyConditionExpression=Key("telegram_sent").eq("true"),
        ScanIndexForward=False,
        Limit=1,
    )
    items = r.get("Items", [])
    return items[0] if items else None


def count_outbox_items() -> int:
    """Cuenta ítems en cola de publicación (GSI outbox)."""
    total = 0
    start_key = None
    while True:
        qkwargs = {
            "IndexName": "outbox_key-processed_at-index",
            "KeyConditionExpression": Key("outbox_key").eq("1"),
            "Select": "COUNT",
        }
        if start_key:
            qkwargs["ExclusiveStartKey"] = start_key
        r = table.query(**qkwargs)
        total += r.get("Count", 0)
        start_key = r.get("LastEvaluatedKey")
        if not start_key:
            break
    return total


def get_queued_titles(limit: int = 8) -> list[str]:
    r = table.query(
        IndexName="outbox_key-processed_at-index",
        KeyConditionExpression=Key("outbox_key").eq("1"),
        ScanIndexForward=True,
        Limit=limit,
    )
    return [i.get("title", "") for i in r.get("Items", [])]
