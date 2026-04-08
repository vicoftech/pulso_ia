# shared/dynamo.py
import boto3
import os
from typing import List
from models import ProcessedNewsItem

TABLE_NAME = os.environ["DYNAMODB_TABLE"]
dynamodb = boto3.resource("dynamodb")
table = dynamodb.Table(TABLE_NAME)

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

def mark_as_sent(item_id: str):
    """Actualiza telegram_sent=True para un item."""
    table.update_item(
        Key={"item_id": item_id},
        UpdateExpression="SET telegram_sent = :val",
        ExpressionAttributeValues={":val": "true"}
    )
