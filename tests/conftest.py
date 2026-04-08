import os
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent

# Ensure local project modules can be imported in tests.
paths = [
    ROOT / "lambdas" / "fetch_sources",
    ROOT / "lambdas" / "filter_ai_news",
    ROOT / "lambdas" / "publish_telegram",
    ROOT / "shared",
]
for p in reversed(paths):
    p_str = str(p)
    if p_str not in sys.path:
        sys.path.insert(0, p_str)

# Safe defaults for imports that expect AWS env vars.
os.environ.setdefault("DYNAMODB_TABLE", "test-table")
os.environ.setdefault("TELEGRAM_CHANNEL_ID", "-1000000000000")
