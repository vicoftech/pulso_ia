import hashlib
import time

from models import ProcessedNewsItem, RawNewsItem


def test_raw_news_item_generates_md5_item_id_from_url():
    url = "https://example.com/news/1"
    item = RawNewsItem(
        title="Title",
        url=url,
        source="rss",
        published_at="2026-04-08T00:00:00+00:00",
        raw_content="Body",
    )
    assert item.item_id == hashlib.md5(url.encode()).hexdigest()


def test_processed_news_item_ttl_defaults_to_about_30_days():
    before = int(time.time())
    item = ProcessedNewsItem(
        item_id="abc",
        source="rss",
        title="Title",
        url="https://example.com",
        summary_es="Resumen",
        category="USE_CASE",
        published_at="2026-04-08T00:00:00+00:00",
        processed_at="2026-04-08T00:10:00+00:00",
        is_relevant=True,
        relevance_score=90,
    )
    after = int(time.time())
    assert before + 2592000 <= item.ttl <= after + 2592000
