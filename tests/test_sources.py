from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from sources.arxiv import ArxivSource
from sources.rss import RSSSource


class _FeedEntry(dict):
    def __getattr__(self, item):
        return self[item]


def _entry(title: str, link: str, published_dt: datetime, summary: str = "summary"):
    return _FeedEntry(
        title=title,
        link=link,
        summary=summary,
        published_parsed=published_dt.utctimetuple(),
    )


def test_arxiv_fetch_filters_items_by_lookback(monkeypatch):
    now = datetime.now(timezone.utc)
    fresh = _entry("Fresh", "https://a/fresh", now - timedelta(hours=1))
    old = _entry("Old", "https://a/old", now - timedelta(days=20))
    fake_feed = SimpleNamespace(entries=[fresh, old])

    monkeypatch.setattr("sources.arxiv.feedparser.parse", lambda _url: fake_feed)

    items = ArxivSource().fetch(lookback_hours=24)
    assert len(items) == 1
    assert items[0].title == "Fresh"
    assert items[0].source == "arxiv"


def test_rss_fetch_uses_default_feeds_and_filters(monkeypatch):
    now = datetime.now(timezone.utc)
    fresh = _entry("RSS Fresh", "https://r/fresh", now - timedelta(hours=2), "content")
    old = _entry("RSS Old", "https://r/old", now - timedelta(days=30), "content")
    fake_feed = SimpleNamespace(entries=[fresh, old])

    monkeypatch.setattr("sources.rss.feedparser.parse", lambda _url: fake_feed)

    items = RSSSource().fetch(lookback_hours=24)
    assert len(items) >= 1
    assert all(i.source.startswith("rss_") for i in items)
    assert any(i.title == "RSS Fresh" for i in items)
