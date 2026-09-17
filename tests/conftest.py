"""Test ortamı.

Bu dosya, test modüllerinden ÖNCE çalışır ve hiçbir `litrag` modülünü içe aktarmaz.
Sebebi şu: `litrag.config` ortam değişkenlerini içe aktarılma anında okur ve
`litrag.store` veritabanı bağlantısını o yola açar. Veritabanı yolu burada
değiştirilmezse testler kullanıcının gerçek `history.db` dosyasına yazar.
"""
from __future__ import annotations

import os
import tempfile
from pathlib import Path

_TMP = Path(tempfile.mkdtemp(prefix="premise-tests-"))

os.environ["LITRAG_DB"] = str(_TMP / "test.db")
os.environ["CACHE_ENABLED"] = "0"
os.environ["ADMIN_EMAIL"] = ""            # testlerde yönetici hesabı tohumlanmasın
os.environ["ADMIN_PASSWORD"] = ""
os.environ["BREVO_API_KEY"] = ""          # e-posta gönderimi kapalı, konsola düşer
os.environ.setdefault("GEMINI_API_KEYS", "")
os.environ.setdefault("GROQ_API_KEY", "")


import pytest


@pytest.fixture(autouse=True)
def _clean_throttles():
    """Hız sınırları process ömrü boyunca sayar.

    Testler arasında sıfırlanmazsa sekizinci kayıt denemesi 429 alır ve o testin
    asıl sınadığı şeyle ilgisi olmayan bir hatayla düşer. `litrag` buradan değil,
    fixture'ın içinden içe aktarılır: modül düzeyinde içe aktarmak yukarıdaki
    ortam değişkeni ayarlarını etkisiz bırakırdı.
    """
    from litrag import security
    for throttle in (security.login_throttle, security.signup_throttle,
                     security.search_throttle, security.reset_throttle):
        with throttle._lock:
            throttle._hits.clear()
    yield
