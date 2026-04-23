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

def _image_from_rss_entry(entry) -> str | None:
    def _get(key, default=None):
        if isinstance(entry, dict):
            return entry.get(key, default)
        return getattr(entry, key, default)

    mc = _get("media_content")
    if mc:
        for m in (mc or []):
            u = m.get("url") or m.get("href")
            t = (m.get("type") or "").lower()
            if u and (t.startswith("image/") or not t or "image" in t):
                return u.strip()[:2000]
        for m in (mc or []):
            u = m.get("url") or m.get("href")
            if u:
                return u.strip()[:2000]
    mth = _get("media_thumbnail")
    if mth and len(mth) > 0:
        t0 = mth[0]
        u0 = t0.get("url") if isinstance(t0, dict) else t0
        if isinstance(u0, str) and u0.strip():
            return u0.strip()[:2000]
    for enc in (_get("enclosures") or []):
        if (enc.get("type") or "").lower().startswith("image") and enc.get("href"):
            return enc.get("href", "").strip()[:2000]
    return None


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
                img = _image_from_rss_entry(entry)
                items.append(RawNewsItem(
                    title=entry.get("title", ""),
                    url=entry.get("link", ""),
                    source=f"rss_{feed_cfg['name']}",
                    published_at=published.isoformat(),
                    raw_content=(entry.get("summary", "") or "")[:500],
                    image_url=img,
                ))
        return items
