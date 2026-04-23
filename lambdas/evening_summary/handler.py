# lambdas/evening_summary/handler.py
import json
import logging
import os
import sys
import time
from datetime import datetime
from zoneinfo import ZoneInfo

import boto3
import requests

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../../shared"))
from dynamo import count_outbox_items, get_queued_titles

logger = logging.getLogger()
logger.setLevel(os.environ.get("LOG_LEVEL", "INFO"))
CHANNEL_ID = os.environ["TELEGRAM_CHANNEL_ID"]
TZ = os.environ.get("TIMEZONE", "America/Argentina/Buenos_Aires")

def get_bot_token() -> str:
    ssm = boto3.client("ssm")
    return ssm.get_parameter(
        Name="/pulso-ia/telegram-bot-token", WithDecryption=True
    )["Parameter"]["Value"]

def escape_md2(text: str) -> str:
    special = r"_*[]()~`>#+-=|{}.!"
    return "".join(f"\\{c}" if c in special else c for c in text)

def send_message(token: str, text: str) -> bool:
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    for attempt in range(3):
        resp = requests.post(
            url,
            json={
                "chat_id": CHANNEL_ID,
                "text": text,
                "parse_mode": "MarkdownV2",
                "disable_web_page_preview": True,
            },
            timeout=15,
        )
        data = resp.json()
        if data.get("ok"):
            return True
        if resp.status_code == 429:
            ra = data.get("parameters", {}).get("retry_after", 3)
            time.sleep(ra)
        else:
            logger.error(json.dumps({"telegram": data, "attempt": attempt}))
            time.sleep(1.5 * (attempt + 1))
    return False

def build_body() -> tuple[str, int]:
    n = count_outbox_items()
    titles = get_queued_titles(8)
    now = datetime.now(ZoneInfo(TZ))
    d = now.strftime("%d/%m/%Y")
    q = escape_md2(str(n))
    date_line = escape_md2(d)
    lines: list[str] = [
        f"🌙 *{escape_md2('Buenas noches')}*",
        "",
        f"*{escape_md2('Resumen')}* — {date_line}",
        "",
        f"{escape_md2('Noticias en cola')}: *{q}*",
    ]
    if titles:
        lines.append("")
        lines.append(escape_md2("Algunos títulos en la cola:"))
        for t in titles:
            if t:
                lines.append("• " + escape_md2(t[:120]))
    lines.extend([
        "",
        escape_md2(
            "Modo descanso: las publicaciones de noticias retoman mañana a las 9:00 (Argentina, GMT-3)."
        ),
    ])
    return "\n".join(lines), n

def handler(event, context):
    text, n = build_body()
    token = get_bot_token()
    if not send_message(token, text):
        return {"ok": False, "error": "telegram_send"}
    return {
        "ok": True,
        "queued": n,
        "timezone": TZ,
    }
