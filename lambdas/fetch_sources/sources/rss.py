# lambdas/fetch_sources/sources/rss.py
import feedparser
import json
import os
from datetime import datetime, timezone, timedelta
from typing import List
from .base import BaseSource
import sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '../../../../shared'))
from models import RawNewsItem

DEFAULT_FEEDS = [
    {"name": "techcrunch_ai",  "url": "https://techcrunch.com/category/artificial-intelligence/feed/"},
    {"name": "venturebeat_ai", "url": "https://venturebeat.com/category/ai/feed/"},
    {"name": "mit_tech_review","url": "https://www.technologyreview.com/feed/"},
    {"name": "openai_blog",    "url": "https://openai.com/blog/rss.xml"},
    {"name": "anthropic_blog", "url": "https://www.anthropic.com/rss.xml"},
    {"name": "google_deepmind","url": "https://deepmind.google/blog/rss.xml"},
    {"name": "the_batch",      "url": "https://www.deeplearning.ai/the-batch/feed/"},
]

class RSSSource(BaseSource):
    def source_id(self) -> str:
        return "rss"

    def fetch(self, lookback_hours: int) -> List[RawNewsItem]:
        feeds = json.loads(os.environ.get("RSS_FEEDS_JSON", json.dumps(DEFAULT_FEEDS)))
        cutoff = datetime.now(timezone.utc) - timedelta(hours=lookback_hours)
        items = []
        for feed_cfg in feeds:
            feed = feedparser.parse(feed_cfg["url"])
            for entry in feed.entries:
                if not hasattr(entry, "published_parsed") or not entry.published_parsed:
                    continue
                published = datetime(*entry.published_parsed[:6], tzinfo=timezone.utc)
                if published < cutoff:
                    continue
                items.append(RawNewsItem(
                    title=entry.get("title", ""),
                    url=entry.get("link", ""),
                    source=f"rss_{feed_cfg['name']}",
                    published_at=published.isoformat(),
                    raw_content=(entry.get("summary", "") or "")[:500]
                ))
        return items
