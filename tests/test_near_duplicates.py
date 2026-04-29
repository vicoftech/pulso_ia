import os
import sys
from unittest.mock import MagicMock, patch

# Satisfy boto3 region requirement before importing dynamo (module-level init).
os.environ.setdefault("AWS_DEFAULT_REGION", "us-east-1")

with patch("boto3.resource", return_value=MagicMock()):
    from dynamo import find_near_duplicate_ids

from models import RawNewsItem


def _item(title: str, url: str, published_at: str) -> RawNewsItem:
    return RawNewsItem(
        title=title,
        url=url,
        source="test",
        published_at=published_at,
        raw_content="",
    )


def test_identical_titles_discards_older():
    older = _item("OpenAI launches GPT-5 model", "https://a.com/1", "2024-01-01T10:00:00Z")
    newer = _item("OpenAI launches GPT-5 model", "https://b.com/2", "2024-01-01T12:00:00Z")
    result = find_near_duplicate_ids([older, newer])
    assert older.item_id in result
    assert newer.item_id not in result


def test_high_similarity_discards_older():
    # "OpenAI GPT-5 launch" vs "OpenAI launches GPT-5" — share "openai", "gpt-5" out of union
    older = _item("OpenAI GPT-5 launch announcement", "https://a.com/1", "2024-01-01T08:00:00Z")
    newer = _item("OpenAI GPT-5 launch announcement today", "https://b.com/2", "2024-01-01T09:00:00Z")
    result = find_near_duplicate_ids([older, newer])
    assert older.item_id in result
    assert newer.item_id not in result


def test_low_similarity_keeps_both():
    item_a = _item("Python 3.12 released with performance improvements", "https://a.com/1", "2024-01-01T10:00:00Z")
    item_b = _item("Rust programming language gains new async features", "https://b.com/2", "2024-01-01T10:00:00Z")
    result = find_near_duplicate_ids([item_a, item_b])
    assert len(result) == 0


def test_single_item_returns_empty():
    item = _item("OpenAI launches new model", "https://a.com/1", "2024-01-01T10:00:00Z")
    result = find_near_duplicate_ids([item])
    assert result == set()


def test_equal_published_at_discards_second():
    first = _item("Meta releases Llama 3 open source model", "https://a.com/1", "2024-01-01T10:00:00Z")
    second = _item("Meta releases Llama 3 open source model", "https://b.com/2", "2024-01-01T10:00:00Z")
    result = find_near_duplicate_ids([first, second])
    assert second.item_id in result
    assert first.item_id not in result


def test_stopwords_ignored_in_similarity():
    # Titles with only stopwords differ — both stopword-filtered titles are empty sets
    # so they produce no significant words → union is empty → not near-duplicate
    item_a = _item("the and or is to", "https://a.com/1", "2024-01-01T10:00:00Z")
    item_b = _item("el la los de en", "https://b.com/2", "2024-01-01T10:00:00Z")
    result = find_near_duplicate_ids([item_a, item_b])
    assert len(result) == 0


def test_three_items_chains_correctly():
    # a and b are near-dups (b newer), b and c are near-dups (c newer) → discard a and b
    a = _item("Google Gemini Ultra model released by Google", "https://a.com/1", "2024-01-01T08:00:00Z")
    b = _item("Google Gemini Ultra model released by Google", "https://b.com/2", "2024-01-01T09:00:00Z")
    c = _item("Python 3.12 brings speed improvements to CPython", "https://c.com/3", "2024-01-01T10:00:00Z")
    result = find_near_duplicate_ids([a, b, c])
    assert a.item_id in result
    assert b.item_id not in result
    assert c.item_id not in result
