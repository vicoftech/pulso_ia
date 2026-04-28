from models import RawNewsItem
from dynamo import find_near_duplicate_ids


def _item(title, url, published_at):
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
    assert result == {older.item_id}


def test_similar_titles_discards_older():
    older = _item("Google releases new Gemini AI model for developers", "https://a.com/1", "2024-03-01T08:00:00Z")
    newer = _item("Google releases Gemini AI model developers preview", "https://b.com/2", "2024-03-01T09:00:00Z")
    result = find_near_duplicate_ids([older, newer])
    assert result == {older.item_id}


def test_dissimilar_titles_discards_nothing():
    item_a = _item("OpenAI releases new ChatGPT feature", "https://a.com/1", "2024-01-01T10:00:00Z")
    item_b = _item("Tesla recalls vehicles over battery issue", "https://b.com/2", "2024-01-01T10:00:00Z")
    result = find_near_duplicate_ids([item_a, item_b])
    assert result == set()


def test_single_item_returns_empty_set():
    item = _item("Anthropic launches Claude 3 assistant", "https://a.com/1", "2024-01-01T10:00:00Z")
    result = find_near_duplicate_ids([item])
    assert result == set()


def test_equal_published_at_discards_second():
    first = _item("Meta releases open source LLM model", "https://a.com/1", "2024-05-01T00:00:00Z")
    second = _item("Meta releases open source LLM model", "https://b.com/2", "2024-05-01T00:00:00Z")
    result = find_near_duplicate_ids([first, second])
    assert result == {second.item_id}


def test_empty_list_returns_empty_set():
    assert find_near_duplicate_ids([]) == set()


def test_three_items_two_duplicates_keeps_newest():
    oldest = _item("OpenAI launches GPT model update", "https://a.com/1", "2024-01-01T08:00:00Z")
    middle = _item("OpenAI launches GPT model update", "https://b.com/2", "2024-01-01T09:00:00Z")
    newest = _item("OpenAI launches GPT model update", "https://c.com/3", "2024-01-01T10:00:00Z")
    result = find_near_duplicate_ids([oldest, middle, newest])
    assert oldest.item_id in result
    assert newest.item_id not in result


def test_stopwords_ignored_in_similarity():
    # Titles differ only in stopwords — significant words identical
    item_a = _item("the model is for developers", "https://a.com/1", "2024-01-01T10:00:00Z")
    item_b = _item("the model is for developers", "https://b.com/2", "2024-01-01T11:00:00Z")
    result = find_near_duplicate_ids([item_a, item_b])
    # item_a is older, should be discarded
    assert result == {item_a.item_id}
