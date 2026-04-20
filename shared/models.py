# shared/models.py
import hashlib
from dataclasses import dataclass, field
import time

@dataclass
class RawNewsItem:
    title: str
    url: str
    source: str
    published_at: str
    raw_content: str
    item_id: str = field(init=False)

    def __post_init__(self):
        self.item_id = hashlib.md5(self.url.encode()).hexdigest()

@dataclass
class ProcessedNewsItem:
    item_id: str
    source: str
    title: str
    url: str
    summary_es: str
    category: str
    published_at: str
    processed_at: str
    is_relevant: bool
    relevance_score: int
    subcategory: str = ""
    telegram_sent: str = "false"
    ttl: int = field(default_factory=lambda: int(time.time()) + 2592000)
