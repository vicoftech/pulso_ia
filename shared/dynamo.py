# shared/dynamo.py
import boto3
import os
from decimal import Decimal
from typing import List

from models import ProcessedNewsItem

TABLE_NAME = os.environ["DYNAMODB_TABLE"]
dynamodb = boto3.resource("dynamodb")
table = dynamodb.Table(TABLE_NAME)
GSI_TELEGRAM = "telegram_sent-processed_at-index"

def batch_get_existing_ids(item_ids: List[str]) -> set:
    """Retorna el set de item_ids que ya existen en DynamoDB."""
    existing = set()
    for i in range(0, len(item_ids), 25):
        batch = item_ids[i:i+25]
        response = dynamodb.batch_get_item(
            RequestItems={
                TABLE_NAME: {
                    "Keys": [{"item_id": id_} for id_ in batch],
                    "ProjectionExpression": "item_id"
                }
            }
        )
        for item in response.get("Responses", {}).get(TABLE_NAME, []):
            existing.add(item["item_id"])
    return existing

def batch_save_items(items: List[ProcessedNewsItem]):
    """Guarda o actualiza items en DynamoDB en batches de 25."""
    with table.batch_writer() as batch:
        for item in items:
            batch.put_item(Item=item.__dict__)

def mark_as_sent(item_id: str, message_id: int | None = None):
    """Marca item como publicado en Telegram."""
    if message_id is not None:
        table.update_item(
            Key={"item_id": item_id},
            UpdateExpression="SET telegram_sent = :t, telegram_message_id = :mid",
            ExpressionAttributeValues={":t": "true", ":mid": message_id},
        )
    else:
        table.update_item(
            Key={"item_id": item_id},
            UpdateExpression="SET telegram_sent = :val",
            ExpressionAttributeValues={":val": "true"},
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
