from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Optional, Tuple

__all__ = ("FAVICON_PATHS", "render_head_links", "load_asset", "try_load_asset")

_PACKAGE_DIR = Path(__file__).resolve().parent

_ASSET_META = {
    "/favicon.ico": ("favicon.ico", "image/x-icon"),
    "/favicon-32x32.png": ("favicon-32x32.png", "image/png"),
    "/favicon-16x16.png": ("favicon-16x16.png", "image/png"),
    "/apple-touch-icon.png": ("apple-touch-icon.png", "image/png"),
    "/android-chrome-192x192.png": ("android-chrome-192x192.png", "image/png"),
    "/android-chrome-512x512.png": ("android-chrome-512x512.png", "image/png"),
    "/site.webmanifest": ("site.webmanifest", "application/manifest+json"),
}

FAVICON_PATHS = set(_ASSET_META.keys())

_HEAD_LINKS = [
    "<link rel='apple-touch-icon' sizes='180x180' href='/apple-touch-icon.png'>",
    "<link rel='icon' type='image/png' sizes='192x192' href='/android-chrome-192x192.png'>",
    "<link rel='icon' type='image/png' sizes='512x512' href='/android-chrome-512x512.png'>",
    "<link rel='icon' type='image/png' sizes='32x32' href='/favicon-32x32.png'>",
    "<link rel='icon' type='image/png' sizes='16x16' href='/favicon-16x16.png'>",
    "<link rel='icon' type='image/x-icon' href='/favicon.ico'>",
    "<link rel='manifest' href='/site.webmanifest'>",
]


def render_head_links(indent: str = "    ") -> str:
    return "\n".join(f"{indent}{line}" for line in _HEAD_LINKS)


def _normalize_path(path: str) -> str:
    root = path.split("?", 1)[0]
    if not root.startswith("/"):
        root = "/" + root
    return root


@lru_cache(maxsize=None)
def load_asset(path: str) -> Tuple[bytes, str]:
    normalized = _normalize_path(path)
    try:
        filename, content_type = _ASSET_META[normalized]
    except KeyError as exc:  # pragma: no cover - defensive branch
        raise KeyError(f"Desteklenmeyen favicon yolu: {path}") from exc
    data = (_PACKAGE_DIR / filename).read_bytes()
    return data, content_type


def try_load_asset(path: str) -> Optional[Tuple[bytes, str]]:
    normalized = _normalize_path(path)
    if normalized not in _ASSET_META:
        return None
    return load_asset(normalized)
