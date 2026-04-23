# tests/test_outbound_url.py
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "shared"))

import outbound_url  # noqa: E402


def test_build_open_workium_uses_r_item_id(monkeypatch):
    monkeypatch.delenv("PULSO_OPEN_TRACKER_URL", raising=False)
    u = outbound_url.build_open_and_track_url("item123", "https://a.com/x")
    assert u == "https://news.workium.ai/r/item123"
    assert "execute-api" not in u


def test_build_open_falls_back_to_dest_without_item_id(monkeypatch):
    monkeypatch.delenv("PULSO_OPEN_TRACKER_URL", raising=False)
    u = outbound_url.build_open_and_track_url("", "https://a.com/x")
    assert u == "https://a.com/x"


def test_build_open_tracks_aws_c_when_set(monkeypatch):
    monkeypatch.setenv("PULSO_OPEN_TRACKER_URL", "https://abc.execute-api.us-east-1.amazonaws.com/c")
    u = outbound_url.build_open_and_track_url("item123", "https://a.com/x")
    assert u.startswith("https://abc.execute-api")
    assert "i=" in u and "item123" in u
    assert "d=" in u
