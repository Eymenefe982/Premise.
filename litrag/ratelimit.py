"""NCBI E-utilities hız sınırı yönetimi.

NCBI anahtarsız istemcilere saniyede 3, API anahtarı olanlara 10 istek izni verir.
Paralel arama yaparken bu sınırı aşmamak için tüm Entrez çağrıları buradan geçer.
"""
from __future__ import annotations

import threading
import time

from .config import NCBI_API_KEY

_MIN_INTERVAL = 1.0 / (9.0 if NCBI_API_KEY else 2.8)
_lock = threading.Lock()
_last_call = 0.0


def throttle() -> None:
    """Bir sonraki NCBI isteği için gereken süre kadar bekler."""
    global _last_call
    with _lock:
        now = time.monotonic()
        wait = _MIN_INTERVAL - (now - _last_call)
        if wait > 0:
            time.sleep(wait)
            now = time.monotonic()
        _last_call = now


def with_retry(func, *args, attempts: int = 3, **kwargs):
    """Entrez çağrısını hız sınırına uyarak çalıştırır, 429 alırsa artan aralıkla tekrarlar."""
    delay = 1.0
    last_exc: Exception | None = None
    for attempt in range(attempts):
        throttle()
        try:
            return func(*args, **kwargs)
        except Exception as exc:                       # HTTPError dâhil
            last_exc = exc
            message = str(exc).lower()
            if "429" in message or "too many requests" in message or "timed out" in message:
                time.sleep(delay)
                delay *= 2
                continue
            raise
    raise last_exc if last_exc else RuntimeError("NCBI isteği başarısız")
