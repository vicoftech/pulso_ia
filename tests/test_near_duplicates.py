import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "shared"))

from dynamo import find_near_duplicate_ids


def _item(item_id, title, published_at):
    return SimpleNamespace(item_id=item_id, title=title, published_at=published_at)


def test_identical_titles_discards_older():
    items = [
        _item("a", "OpenAI launches GPT-5 model", "2024-01-02T10:00:00Z"),
        _item("b", "OpenAI launches GPT-5 model", "2024-01-01T10:00:00Z"),
    ]
    result = find_near_duplicate_ids(items)
    assert result == {"b"}


def test_similar_titles_above_threshold_discards_older():
    # Significant words: openai, launches, gpt5, major, new, capabilities, release, features
    # A: {openai, launches, gpt5, major, new, capabilities}
    # B: {openai, launches, gpt5, major, new, features}
    # Intersection: {openai, launches, gpt5, major, new} = 5
    # Union: {openai, launches, gpt5, major, new, capabilities, features} = 7
    # Jaccard: 5/7 ≈ 0.71 > 0.6 → near-duplicate
    items = [
        _item("a", "OpenAI launches GPT-5 major new capabilities", "2024-01-01T08:00:00Z"),
        _item("b", "OpenAI launches GPT-5 major new features", "2024-01-02T08:00:00Z"),
    ]
    result = find_near_duplicate_ids(items)
    assert result == {"a"}


def test_dissimilar_titles_below_threshold_keeps_both():
    # A: {google, releases, gemini, model}
    # B: {openai, launches, gpt5, capabilities}
    # Intersection: {} = 0 → Jaccard 0 < 0.6 → not a near-duplicate
    items = [
        _item("a", "Google releases Gemini model", "2024-01-01T08:00:00Z"),
        _item("b", "OpenAI launches GPT-5 capabilities", "2024-01-02T08:00:00Z"),
    ]
    result = find_near_duplicate_ids(items)
    assert result == set()


def test_single_item_returns_empty_set():
    items = [
        _item("a", "OpenAI launches new model", "2024-01-01T08:00:00Z"),
    ]
    result = find_near_duplicate_ids(items)
    assert result == set()


def test_equal_published_at_discards_second():
    items = [
        _item("a", "OpenAI launches GPT-5 model release", "2024-01-01T08:00:00Z"),
        _item("b", "OpenAI launches GPT-5 model release", "2024-01-01T08:00:00Z"),
    ]
    result = find_near_duplicate_ids(items)
    assert result == {"b"}


def test_empty_list_returns_empty_set():
    assert find_near_duplicate_ids([]) == set()


def test_stopwords_ignored_in_similarity():
    # Titles that differ only by stopwords should be treated as identical
    # A: "the model of openai" → significant: {model, openai}
    # B: "a model for openai" → significant: {model, openai}
    # Jaccard: 2/2 = 1.0 > 0.6 → near-duplicate
    items = [
        _item("a", "the model of openai", "2024-01-01T00:00:00Z"),
        _item("b", "a model for openai", "2024-01-02T00:00:00Z"),
    ]
    result = find_near_duplicate_ids(items)
    assert result == {"a"}
