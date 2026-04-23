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
    image_url: str | None = None
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
    """URL de la imagen principal (RSS, API u og:image en publicación)."""
    image_url: str | None = None
    """Present on relevant items that still need Telegram publish (GSI outbox)."""
    outbox_key: str | None = None
    telegram_sent: str = "false"
    ttl: int = field(default_factory=lambda: int(time.time()) + 2592000)
