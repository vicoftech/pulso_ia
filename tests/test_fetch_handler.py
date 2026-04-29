import importlib.util
import sys
from pathlib import Path
from types import SimpleNamespace

FETCH_HANDLER_PATH = Path(__file__).resolve().parent.parent / "lambdas" / "fetch_sources" / "handler.py"
_FETCH_MODULE_NAME = "pulso_fetch_sources_handler_lambda"


def _load_fetch_lambda_module():
    """Carga handler.py de fetch_sources sin colisionar con otros handler.py en sys.modules."""
    if _FETCH_MODULE_NAME in sys.modules:
        return sys.modules[_FETCH_MODULE_NAME]
    spec = importlib.util.spec_from_file_location(_FETCH_MODULE_NAME, FETCH_HANDLER_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError("No se pudo cargar fetch_sources/handler.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[_FETCH_MODULE_NAME] = mod
    spec.loader.exec_module(mod)
    return mod


class _FakeSource:
    def fetch(self, _lookback_hours):
        return [
            SimpleNamespace(item_id="id1", source="arxiv"),
            SimpleNamespace(item_id="id2", source="rss_x"),
        ]


def test_fetch_handler_keeps_only_new_items(monkeypatch):
    fetch_mod = _load_fetch_lambda_module()

    monkeypatch.setattr(fetch_mod, "SOURCE_REGISTRY", {"arxiv": _FakeSource})
    monkeypatch.setattr(fetch_mod, "batch_get_existing_ids", lambda ids: {"id1"})

    result = fetch_mod.handler({"sources": ["arxiv"], "lookback_hours": 24}, None)

    assert result["count"] == 1
    assert result["items"][0]["item_id"] == "id2"
    assert result["by_source"]["rss_x"] == 1
