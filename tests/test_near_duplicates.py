from models import RawNewsItem
from dynamo import find_near_duplicate_ids


def _item(title: str, url: str, published_at: str) -> RawNewsItem:
    return RawNewsItem(
        title=title,
        url=url,
        source="test",
        published_at=published_at,
        raw_content="",
    )


def test_identical_titles_discards_oldest():
    older = _item("OpenAI releases GPT-5 model", "http://a.com/1", "2026-04-28T10:00:00Z")
    newer = _item("OpenAI releases GPT-5 model", "http://b.com/2", "2026-04-28T12:00:00Z")

    result = find_near_duplicate_ids([older, newer])

    assert result == {older.item_id}


def test_similar_titles_discards_oldest():
    # sig words A: {openai, releases, gpt-5, model}
    # sig words B: {openai, releases, gpt-5, new, model}
    # intersection=4, union=5 → Jaccard=0.8 > 0.6
    older = _item("OpenAI releases GPT-5 model", "http://a.com/1", "2026-04-27T08:00:00Z")
    newer = _item("OpenAI releases GPT-5 new model", "http://b.com/2", "2026-04-28T08:00:00Z")

    result = find_near_duplicate_ids([older, newer])

    assert result == {older.item_id}


def test_dissimilar_titles_discards_none():
    # intersection={} → Jaccard=0 ≤ 0.6
    item_a = _item("OpenAI releases GPT-5", "http://a.com/1", "2026-04-28T10:00:00Z")
    item_b = _item("Google launches Gemini Ultra", "http://b.com/2", "2026-04-28T10:00:00Z")

    result = find_near_duplicate_ids([item_a, item_b])

    assert result == set()


def test_single_item_returns_empty_set():
    item = _item("OpenAI releases GPT-5", "http://a.com/1", "2026-04-28T10:00:00Z")

    result = find_near_duplicate_ids([item])

    assert result == set()


def test_equal_published_at_discards_second_in_list():
    first = _item("OpenAI releases GPT-5 model", "http://a.com/1", "2026-04-28T10:00:00Z")
    second = _item("OpenAI releases GPT-5 model", "http://b.com/2", "2026-04-28T10:00:00Z")

    result = find_near_duplicate_ids([first, second])

    assert result == {second.item_id}


def test_below_threshold_not_discarded():
    # sig words A: {openai, gpt-5, launches}
    # sig words B: {google, gemini, launches, enterprise}
    # intersection={launches}=1, union=6 → Jaccard≈0.17 ≤ 0.6
    item_a = _item("OpenAI GPT-5 launches", "http://a.com/1", "2026-04-28T10:00:00Z")
    item_b = _item("Google Gemini launches enterprise", "http://b.com/2", "2026-04-28T10:00:00Z")

    result = find_near_duplicate_ids([item_a, item_b])

    assert result == set()
