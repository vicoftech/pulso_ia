# tests/test_publish_telegram_format.py
import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _load_publish_handler():
    p = ROOT / "lambdas" / "publish_telegram" / "handler.py"
    spec = importlib.util.spec_from_file_location("publish_tg_handler", p)
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader
    spec.loader.exec_module(mod)
    return mod


def test_format_message_pulso_header_and_metadata():
    h = _load_publish_handler()
    t = h.format_message(
        {
            "title": "Test de título",
            "summary_es": "Resumen corto.",
            "url": "https://example.com/path?q=1",
            "category": "NEW_PRODUCT",
            "source": "producthunt",
            "relevance_score": 88,
        }
    )
    assert "*Pulso IA*" in t
    assert "Test de título" in t
    assert "*Categoría:*" in t and "Nuevo producto" in t
    assert "*Subcategoría:*" in t and "#NuevoProducto" in t
    assert "*Fuente de datos:*" in t and "Product Hunt" in t
    assert "*Relevancia:*" in t and "88/100" in t
    assert "🔗" not in t
    assert "example.com" not in t
    assert "#ProductHunt" in t


def test_leer_mas_button_uses_workium_tracker():
    h = _load_publish_handler()
    k = h._inline_keyboard(
        {
            "url": "https://example.com/p?q=1",
            "item_id": "abc",
        }
    )
    u = k["inline_keyboard"][0][0]["url"]
    # Sin PULSO_OPEN_TRACKER: Workium /r/{item_id}
    assert u == "https://news.workium.ai/r/abc"
    like = k["inline_keyboard"][0][1]
    assert like.get("text", "").startswith("👍")
    assert "0" in like.get("text", "") or "👍" in like.get("text", "")
    assert like.get("callback_data") == "like:abc"


def test_rss_source_label_in_message():
    h = _load_publish_handler()
    t = h.format_message(
        {
            "title": "A",
            "summary_es": "S",
            "url": "https://x.com",
            "category": "USE_CASE",
            "source": "rss_techcrunch_ai",
            "relevance_score": 60,
        }
    )
    assert "RSS · techcrunch ai" in t
