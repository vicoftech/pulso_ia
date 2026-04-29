import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "shared"))

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
    older = _item("OpenAI releases GPT-5 model", "https://a.com/1", "2026-01-01T00:00:00Z")
    newer = _item("OpenAI releases GPT-5 model", "https://b.com/2", "2026-01-02T00:00:00Z")
    result = find_near_duplicate_ids([older, newer])
    assert older.item_id in result
    assert newer.item_id not in result


def test_similar_titles_above_threshold_discards_older():
    # Common significant words: openai, gpt, model, available, enterprise = 5
    # Max words: 7 → similarity 5/7 ≈ 0.71 > 0.6
    older = _item(
        "OpenAI GPT-5 model now available for enterprise customers",
        "https://techcrunch.com/1",
        "2026-01-01T10:00:00Z",
    )
    newer = _item(
        "OpenAI GPT-5 model available for enterprise",
        "https://venturebeat.com/2",
        "2026-01-01T12:00:00Z",
    )
    result = find_near_duplicate_ids([older, newer])
    assert older.item_id in result
    assert newer.item_id not in result


def test_dissimilar_titles_below_threshold_keeps_both():
    # No words in common
    item_a = _item("Python 3.12 released with performance improvements", "https://a.com/1", "2026-01-01T00:00:00Z")
    item_b = _item("JavaScript framework announcement today", "https://b.com/2", "2026-01-01T00:00:00Z")
    result = find_near_duplicate_ids([item_a, item_b])
    assert len(result) == 0


def test_single_item_returns_empty_set():
    item = _item("Solo news item about AI", "https://solo.com/1", "2026-01-01T00:00:00Z")
    result = find_near_duplicate_ids([item])
    assert result == set()


def test_equal_published_at_discards_second_in_list():
    first = _item("Anthropic Claude model launched enterprise tier", "https://a.com/1", "2026-01-01T00:00:00Z")
    second = _item("Anthropic Claude model launched enterprise tier", "https://b.com/2", "2026-01-01T00:00:00Z")
    result = find_near_duplicate_ids([first, second])
    assert second.item_id in result
    assert first.item_id not in result


def test_empty_list_returns_empty_set():
    assert find_near_duplicate_ids([]) == set()


def test_below_threshold_boundary_keeps_both():
    # Common: openai, releases = 2; max words: 5 → 2/5 = 0.4 < 0.6
    item_a = _item("OpenAI releases new product today officially", "https://a.com/1", "2026-01-01T00:00:00Z")
    item_b = _item("OpenAI releases different unrelated announcement here", "https://b.com/2", "2026-01-01T00:00:00Z")
    result = find_near_duplicate_ids([item_a, item_b])
    assert len(result) == 0
