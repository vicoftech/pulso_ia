# lambdas/fetch_sources/sources/arxiv.py
import feedparser
from datetime import datetime, timezone, timedelta
from typing import List
from .base import BaseSource
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '../../../../shared'))
from models import RawNewsItem

ARXIV_CATEGORIES = ["cs.AI", "cs.LG", "cs.CL", "cs.CV", "stat.ML"]
ARXIV_API = "http://export.arxiv.org/api/query"

class ArxivSource(BaseSource):
    def source_id(self) -> str:
        return "arxiv"

    def fetch(self, lookback_hours: int) -> List[RawNewsItem]:
        query = "+OR+".join([f"cat:{c}" for c in ARXIV_CATEGORIES])
        url = (
            f"{ARXIV_API}?search_query={query}"
            f"&sortBy=submittedDate&sortOrder=descending&max_results=50"
        )
        feed = feedparser.parse(url)
        cutoff = datetime.now(timezone.utc) - timedelta(hours=lookback_hours)
        items = []

        for entry in feed.entries:
            published = datetime(*entry.published_parsed[:6], tzinfo=timezone.utc)
            if published < cutoff:
                continue
            items.append(RawNewsItem(
                title=entry.title.replace("\n", " ").strip(),
                url=entry.link,
                source=self.source_id(),
                published_at=published.isoformat(),
                raw_content=entry.summary[:500] if entry.summary else ""
            ))
        return items
