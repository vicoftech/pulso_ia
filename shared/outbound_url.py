# shared/outbound_url.py — Workium: GET /r/{item_id} (API news.workium.ai)
# Opcional: PULSO_OPEN_TRACKER_URL (API /c) para contar aperturas en Dynamo antes de redirigir.
import os
from urllib.parse import quote

__all__ = (
    "build_workium_r_url",
    "build_workium_url",  # alias redirect p/ compat
    "build_open_and_track_url",
)


def build_workium_r_url(item_id: str) -> str:
    """
    Enlace al redirect de noticias: https://news.workium.ai/r/<item_id>
    (item_id = MD5 del artículo en el pipeline de Pulso).
    """
    iid = (item_id or "").strip()[:100]
    if not iid or iid == "unknown":
        return ""
    base = (
        os.environ.get("PULSO_OUTBOUND_TRACKING_BASE", "https://news.workium.ai")
        or ""
    ).rstrip("/")
    if not base:
        return ""
    path = (os.environ.get("PULSO_OUTBOUND_TRACKING_PATH", "/r") or "/r").strip()
    if not path.startswith("/"):
        path = "/" + path
    return f"{base}{path}/{iid}"


def build_workium_url(_dest: str) -> str:
    """
    Compat. El flujo con query ?url= ya no se usa; preferir build_workium_r_url con el item.
    Se mantiene para imports viejos: devuelve el destino sin envolver.
    """
    d = (_dest or "").strip()[:2000]
    return d if d.startswith("http://") or d.startswith("https://") else d


def build_open_and_track_url(item_id: str, dest: str) -> str:
    """
    Botón «Leer más»: Workium /r/{item_id}. Sin item_id, enlace directo a dest.
    Si PULSO_OPEN_TRACKER_URL, primero /c?i&d= y luego 302 a Workium /r/{id}.
    """
    d = (dest or "").strip()[:2000]
    if not d.startswith("http://") and not d.startswith("https://"):
        return d
    i = (item_id or "").strip()[:100] or "unknown"
    tracker = (os.environ.get("PULSO_OPEN_TRACKER_URL") or "").strip()
    if tracker and i != "unknown":
        u = f"{tracker.rstrip('/')}?i={quote(i, safe='')}&d={quote(d, safe='')}"
        if len(u) <= 2000:
            return u
    w = build_workium_r_url(i)
    if w and len(w) <= 2000:
        return w
    return d
