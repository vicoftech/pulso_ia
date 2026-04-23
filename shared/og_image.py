# shared/og_image.py
"""Extrae la URL de og:image / twitter:image del HTML del artículo (fallback en publicación)."""
from __future__ import annotations

import re
from urllib.parse import urljoin, urlparse

import requests

_OG1 = re.compile(
    r'<meta[^>]+property\s*=\s*["\']og:image["\'][^>]+content\s*=\s*["\']([^"\']+)["\']',
    re.IGNORECASE,
)
_OG2 = re.compile(
    r'<meta[^>]+content\s*=\s*["\']([^"\']+)["\'][^>]+property\s*=\s*["\']og:image["\']',
    re.IGNORECASE,
)
_TW = re.compile(
    r'<meta[^>]+name\s*=\s*["\']twitter:image:src["\'][^>]+content\s*=\s*["\']([^"\']+)["\']',
    re.IGNORECASE,
)
_TW2 = re.compile(
    r'<meta[^>]+name\s*=\s*["\']twitter:image["\'][^>]+content\s*=\s*["\']([^"\']+)["\']',
    re.IGNORECASE,
)
_MAX_HTML = 600_000


def _absolutize(base: str, maybe: str) -> str:
    u = (maybe or "").strip()
    if not u:
        return u
    if u.startswith("http://") or u.startswith("https://"):
        return u
    if u.startswith("//"):
        p = urlparse(base)
        return f"{p.scheme or 'https'}:{u}"
    return urljoin(base, u)


def extract_og_image_url(article_url: str, timeout: float = 8.0) -> str | None:
    if not article_url or not (
        article_url.startswith("http://") or article_url.startswith("https://")
    ):
        return None
    try:
        r = requests.get(
            article_url,
            timeout=timeout,
            headers={
                "User-Agent": "PulsoIA/1.0 (news; +https://t.me) LikeTelegram/1.0"
            },
        )
        r.raise_for_status()
        t = r.text[:_MAX_HTML]
    except (OSError, requests.RequestException):
        return None
    for rx in (_OG1, _OG2, _TW, _TW2):
        m = rx.search(t)
        if m and m.group(1):
            u = m.group(1).strip()
            u = u.replace("&amp;", "&")
            u = _absolutize(article_url, u)
            if u.startswith("http://") or u.startswith("https://"):
                return u[:2000]
    return None
