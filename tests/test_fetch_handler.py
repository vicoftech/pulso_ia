from types import SimpleNamespace

from handler import handler


class _FakeSource:
    def fetch(self, _lookback_hours):
        return [
            SimpleNamespace(item_id="id1", source="arxiv"),
            SimpleNamespace(item_id="id2", source="rss_x"),
        ]


def test_fetch_handler_keeps_only_new_items(monkeypatch):
    monkeypatch.setattr("handler.SOURCE_REGISTRY", {"arxiv": _FakeSource})
    monkeypatch.setattr("handler.batch_get_existing_ids", lambda ids: {"id1"})

    result = handler({"sources": ["arxiv"], "lookback_hours": 24}, None)

    assert result["count"] == 1
    assert result["items"][0]["item_id"] == "id2"
    assert result["by_source"]["rss_x"] == 1
