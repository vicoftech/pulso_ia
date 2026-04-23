# tests/test_like_counts.py
import importlib.util
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _load_like_counts():
    p = ROOT / "shared" / "like_counts.py"
    spec = importlib.util.spec_from_file_location("like_counts", p)
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader
    spec.loader.exec_module(mod)
    return mod


def test_get_count_zero_without_table_env(monkeypatch):
    monkeypatch.delenv("DYNAMODB_LIKE_COUNTS_TABLE", raising=False)
    m = _load_like_counts()
    assert m.get_count("any") == 0
