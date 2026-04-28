from models import RawNewsItem
from dynamo import find_near_duplicate_ids


def _item(title, published_at, url="http://example.com/"):
    return RawNewsItem(
        title=title,
        url=url + title.replace(" ", "-"),
        source="test",
        published_at=published_at,
        raw_content="",
    )


def test_identical_titles_discards_oldest():
    newer = _item("OpenAI launches GPT-5 model", "2024-01-01T10:00:00Z", "http://a.com/")
    older = _item("OpenAI launches GPT-5 model", "2024-01-01T09:00:00Z", "http://b.com/")
    result = find_near_duplicate_ids([newer, older])
    assert older.item_id in result
    assert newer.item_id not in result


def test_similar_titles_above_threshold_discards_oldest():
    # Intersection: {openai, gpt-5, raises, bar, enterprise} = 5
    # Union: {openai, gpt-5, raises, bar, enterprise, ai, deployment} = 7
    # Jaccard = 5/7 ≈ 0.71 > 0.60
    newer = _item("OpenAI GPT-5 raises bar enterprise AI", "2024-01-02T00:00:00Z", "http://a.com/")
    older = _item("OpenAI GPT-5 raises bar enterprise deployment", "2024-01-01T00:00:00Z", "http://b.com/")
    result = find_near_duplicate_ids([newer, older])
    assert older.item_id in result
    assert newer.item_id not in result


def test_dissimilar_titles_below_threshold_keeps_both():
    # No words in common — Jaccard = 0
    item_a = _item("Python 3.12 released with new features", "2024-01-01T10:00:00Z", "http://a.com/")
    item_b = _item("OpenAI GPT-5 model launched enterprise", "2024-01-01T10:00:00Z", "http://b.com/")
    result = find_near_duplicate_ids([item_a, item_b])
    assert len(result) == 0


def test_single_item_returns_empty_set():
    item = _item("Single article title here", "2024-01-01T10:00:00Z")
    result = find_near_duplicate_ids([item])
    assert result == set()


def test_equal_published_at_discards_second():
    same_time = "2024-01-01T12:00:00Z"
    first = _item("OpenAI GPT-5 raises bar enterprise AI", same_time, "http://a.com/")
    second = _item("OpenAI GPT-5 raises bar enterprise deployment", same_time, "http://b.com/")
    result = find_near_duplicate_ids([first, second])
    assert second.item_id in result
    assert first.item_id not in result


def test_empty_list_returns_empty_set():
    assert find_near_duplicate_ids([]) == set()
