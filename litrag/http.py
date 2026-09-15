"""Tüm kaynakların paylaştığı HTTP istemcisi."""
from __future__ import annotations

import httpx

from .config import HTTP_TIMEOUT, USER_AGENT

_client: httpx.Client | None = None


def client() -> httpx.Client:
    global _client
    if _client is None:
        _client = httpx.Client(
            timeout=HTTP_TIMEOUT,
            follow_redirects=True,
            headers={"User-Agent": USER_AGENT},
        )
    return _client


def get_json(url: str, params: dict | None = None) -> dict | None:
    try:
        r = client().get(url, params=params, headers={"Accept": "application/json"})
        if r.status_code != 200:
            return None
        return r.json()
    except Exception:
        return None


def get_text(url: str, params: dict | None = None) -> str:
    try:
        r = client().get(url, params=params, headers={"Accept": "application/xml, text/xml, */*"})
        return r.text if r.status_code == 200 else ""
    except Exception:
        return ""
