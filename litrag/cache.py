"""Arama önbelleği.

Tıbbi sorular birbirini çok tekrar eder. Aynı soru aynı filtrelerle geldiğinde hattı
baştan çalıştırmak yerine saklanan sonucu döndürürüz: hem model maliyeti hem de NCBI
hız limiti tüketimi ortadan kalkar.

Anahtar, sorunun normalleştirilmiş hâli ile sonucu etkileyen tüm parametrelerden üretilir.
Sadece görüntülemeyi etkileyen bir alan değişirse (örneğin rapor dili) anahtar da değişir,
çünkü sentez o dilde yazılır.
"""
from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from datetime import datetime, timedelta

from .config import CACHE_TTL_DAYS
from .store import _lock, conn

# Aksanlar ve Türkçe harfler sadeleştirilir: "Yaşlı hastalarda" ile "yasli hastalarda"
# aynı aramadır ve aynı önbellek satırını kullanmalıdır.
_TR_MAP = str.maketrans("çğıİöşüÇĞÖŞÜ", "cgiiosuCGOSU")


def normalise_query(query: str) -> str:
    text = unicodedata.normalize("NFKC", query or "").translate(_TR_MAP).lower()
    text = unicodedata.normalize("NFD", text)
    text = "".join(c for c in text if unicodedata.category(c) != "Mn")
    text = re.sub(r"\s+", " ", text).strip()
    return text.rstrip("?!. ")


def make_key(req) -> str:
    """Sonucu belirleyen her parametreyi içeren kararlı bir anahtar üretir."""
    payload = {
        "q": normalise_query(req.query),
        "author": normalise_query(req.author),
        "journal": normalise_query(req.journal),
        "language": req.language,
        "max_articles": req.max_articles,
        "recent_years": req.recent_years,
        "filters": sorted(req.filters),
        "only_open_access": req.only_open_access,
        "use_fulltext": req.use_fulltext,
        "use_clinical": req.use_clinical,
        "extract_stats": req.extract_stats,
        "synthesize": req.synthesize,
        # Mod, sonucun kendisini belirler: düşük güçte yazılmış bir cevap yüksek güç
        # isteyen kullanıcıya verilemez.
        "power_mode": getattr(req, "power_mode", "medium"),
    }
    blob = json.dumps(payload, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def get(key: str, ttl_days: int = CACHE_TTL_DAYS) -> dict | None:
    """Süresi geçmemiş bir kayıt varsa döndürür ve isabet sayacını artırır."""
    row = conn().execute(
        "SELECT created_at, payload, hits FROM search_cache WHERE key = ?", (key,)
    ).fetchone()
    if row is None:
        return None

    created = datetime.strptime(row["created_at"], "%Y-%m-%d %H:%M:%S")
    age = datetime.now() - created
    if age > timedelta(days=ttl_days):
        with _lock:
            conn().execute("DELETE FROM search_cache WHERE key = ?", (key,))
            conn().commit()
        return None

    with _lock:
        conn().execute("UPDATE search_cache SET hits = hits + 1, last_used = ? WHERE key = ?",
                       (datetime.now().strftime("%Y-%m-%d %H:%M:%S"), key))
        conn().commit()

    result = json.loads(row["payload"])
    result["cached"] = True
    result["cached_at"] = row["created_at"]
    result["cache_age_hours"] = round(age.total_seconds() / 3600, 1)
    result.pop("report_id", None)          # yeni arama kendi kaydını oluşturur
    return result


def put(key: str, req, result: dict) -> None:
    """Sonucu önbelleğe yazar. Önbellekten gelen bir sonuç tekrar yazılmaz."""
    if result.get("cached"):
        return
    stored = {k: v for k, v in result.items() if k != "report_id"}
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    payload = json.dumps(stored, ensure_ascii=False)
    # Varsa güncelle, isabet sayısını koru. ON CONFLICT hem Postgres'te hem SQLite'ta
    # (3.24+) çalışır. Önceden SQLite yolu düz INSERT'ti: "run it fresh" ile aynı
    # anahtar ikinci kez yazılınca IntegrityError verip aramayı, parası harcandıktan
    # sonra düşürüyordu.
    # Kaydı yazan hesap saklanır: hesap silindiğinde onun sorusundan üretilen önbellek
    # satırı da silinebilsin (bkz. delete_for_user). Başka bir kullanıcıya dönen
    # sonuçta bu alan yer almaz.
    upsert = ("INSERT INTO search_cache (key, created_at, last_used, query, language,"
              " max_articles, hits, payload, user_id) VALUES (?,?,?,?,?,?,"
              " COALESCE((SELECT hits FROM search_cache WHERE key = ?), 0), ?, ?)"
              " ON CONFLICT (key) DO UPDATE SET"
              " created_at = EXCLUDED.created_at, last_used = EXCLUDED.last_used,"
              " query = EXCLUDED.query, language = EXCLUDED.language,"
              " max_articles = EXCLUDED.max_articles, payload = EXCLUDED.payload,"
              " user_id = EXCLUDED.user_id")
    with _lock:
        conn().execute(upsert, (key, now, now, req.query, req.language,
                                req.max_articles, key, payload,
                                getattr(req, "user_id", None)))
        conn().commit()


def delete_for_user(user_id: int) -> int:
    """Hesabın sorularından üretilmiş önbellek satırlarını siler."""
    with _lock:
        cur = conn().execute("DELETE FROM search_cache WHERE user_id = ?", (user_id,))
        conn().commit()
        return cur.rowcount


def purge_expired(ttl_days: int = CACHE_TTL_DAYS) -> int:
    cutoff = (datetime.now() - timedelta(days=ttl_days)).strftime("%Y-%m-%d %H:%M:%S")
    with _lock:
        cur = conn().execute("DELETE FROM search_cache WHERE created_at < ?", (cutoff,))
        conn().commit()
        return cur.rowcount


def clear() -> int:
    with _lock:
        cur = conn().execute("DELETE FROM search_cache")
        conn().commit()
        return cur.rowcount


def stats() -> dict:
    """Önbellek isabet oranı: maliyet tasarrufunun doğrudan ölçüsü."""
    row = conn().execute(
        "SELECT COUNT(*) AS entries, COALESCE(SUM(hits), 0) AS hits FROM search_cache"
    ).fetchone()
    entries, hits = int(row["entries"]), int(row["hits"])
    total = entries + hits                       # her satır bir kez hesaplanıp n kez kullanıldı
    return {
        "entries": entries,
        "hits": hits,
        "hit_rate": round(hits / total, 3) if total else 0.0,
        "ttl_days": CACHE_TTL_DAYS,
    }
