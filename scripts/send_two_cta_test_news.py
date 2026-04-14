#!/usr/bin/env python3
"""Publica N cards con URL real desde DynamoDB (prueba «Leer artículo» vía /r/{item_id})."""
import json
import os
from decimal import Decimal
from urllib.parse import urlparse

import boto3
from boto3.dynamodb.conditions import Key

REGION = os.environ.get("AWS_REGION", "us-east-1")
TABLE = os.environ.get("PULSO_ITEMS_TABLE", "pulso-ia_items")
FN = os.environ.get("PULSO_PUBLISH_FN", "pulso-ia-publish-telegram")
GSI = "telegram_sent-processed_at-index"
NEED = int(os.environ.get("PULSO_TEST_NEWS_COUNT", "2"))


def _is_real_article_url(url: str) -> bool:
    u = (url or "").strip()
    if not u.lower().startswith(("http://", "https://")):
        return False
    ul = u.lower()
    if "pulso-ia-test.invalid" in ul:
        return False
    try:
        host = (urlparse(u).hostname or "").lower()
    except ValueError:
        return False
    if not host:
        return False
    if host == "example.com" or host.endswith(".example.com"):
        return False
    if host.endswith(".invalid") or "pulso-ia-test" in host:
        return False
    return True


def _jsonable(o):
    if isinstance(o, Decimal):
        return int(o) if o % 1 == 0 else float(o)
    if isinstance(o, dict):
        return {k: _jsonable(v) for k, v in o.items()}
    if isinstance(o, list):
        return [_jsonable(x) for x in o]
    return o


def pick_items_with_real_urls(table, n: int) -> list[dict]:
    out: list[dict] = []
    seen: set[str] = set()
    for status in ("true", "queued", "false"):
        kwargs = {
            "IndexName": GSI,
            "KeyConditionExpression": Key("telegram_sent").eq(status),
            "ScanIndexForward": False,
            "Limit": 25,
        }
        while len(out) < n:
            resp = table.query(**kwargs)
            for row in resp.get("Items", []):
                iid = row.get("item_id")
                if not iid or iid in seen:
                    continue
                if not _is_real_article_url(row.get("url") or ""):
                    continue
                seen.add(iid)
                out.append(dict(row))
                if len(out) >= n:
                    return out
            lek = resp.get("LastEvaluatedKey")
            if not lek:
                break
            kwargs["ExclusiveStartKey"] = lek
    raise SystemExit(
        f"Hace falta al menos {n} ítems con URL real en Dynamo (excl. pulso-ia-test.invalid). "
        "Encontrados: {}. Ejecutá el pipeline o revisá la tabla.".format(len(out))
    )


def main() -> None:
    table = boto3.resource("dynamodb", region_name=REGION).Table(TABLE)
    lam = boto3.client("lambda", region_name=REGION)

    rows = pick_items_with_real_urls(table, NEED)
    payloads: list[dict] = []
    for row in rows:
        iid = row["item_id"]
        table.update_item(
            Key={"item_id": iid},
            UpdateExpression="SET telegram_sent = :f",
            ExpressionAttributeValues={":f": "false"},
        )
        p = _jsonable(row)
        p["telegram_sent"] = "false"
        payloads.append(p)
        print("Ítem:", iid, (row.get("url") or "")[:90])

    items = sorted(payloads, key=lambda x: (x.get("published_at") or "")[:32])

    def _invoke(payload: dict, label: str) -> int:
        r = lam.invoke(
            FunctionName=FN,
            InvocationType="RequestResponse",
            Payload=json.dumps(payload).encode(),
        )
        body = r["Payload"].read().decode()
        print(label, "->", body)
        try:
            return int(json.loads(body).get("queued_remaining") or 0)
        except (json.JSONDecodeError, TypeError, ValueError):
            return 0

    remaining = _invoke(
        {"relevant_items": items},
        f"invoke 1 (publica 1, encola hasta {NEED - 1})",
    )
    n = 2
    while remaining > 0:
        remaining = _invoke({"relevant_items": []}, f"invoke {n} (publica cola)")
        n += 1


if __name__ == "__main__":
    main()
