#!/usr/bin/env python3
"""Registra la URL del API Gateway como webhook de Telegram (solo callback_query)."""
import argparse
import os

import boto3
import requests

REGION = os.environ.get("AWS_REGION", "us-east-1")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", required=True, help="POST URL del webhook, ej. .../webhook")
    args = parser.parse_args()

    ssm = boto3.client("ssm", region_name=REGION)
    token = ssm.get_parameter(
        Name="/pulso-ia/telegram-bot-token",
        WithDecryption=True,
    )["Parameter"]["Value"]

    resp = requests.post(
        f"https://api.telegram.org/bot{token}/setWebhook",
        json={
            "url": args.url,
            "allowed_updates": ["callback_query"],
        },
        timeout=15,
    )
    data = resp.json()
    if data.get("ok"):
        print(f"Webhook registrado: {args.url}")
    else:
        print(f"Error al registrar webhook: {data}")
        raise SystemExit(1)

    info = requests.get(
        f"https://api.telegram.org/bot{token}/getWebhookInfo",
        timeout=10,
    ).json()
    result = info.get("result", {})
    print(f"URL activa:      {result.get('url', 'ninguna')}")
    print(f"Pending updates: {result.get('pending_update_count', 0)}")
    if result.get("last_error_message"):
        print(f"Ultimo error:    {result['last_error_message']}")


if __name__ == "__main__":
    main()
