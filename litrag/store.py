"""Yerel SQLite deposu: arama geçmişi, kaydedilen raporlar ve kişisel kütüphane."""
from __future__ import annotations

import json
import sqlite3
import threading
from datetime import datetime

from .config import DB_PATH

_lock = threading.Lock()
_conn: sqlite3.Connection | None = None

SCHEMA = """
CREATE TABLE IF NOT EXISTS searches (
    id INTEGER PRIMARY KEY, date TEXT, query TEXT, author TEXT
);
CREATE TABLE IF NOT EXISTS reports (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at TEXT NOT NULL,
    query TEXT NOT NULL,
    topic TEXT,
    language TEXT,
    article_count INTEGER,
    elapsed REAL,
    payload TEXT NOT NULL,
    user_id INTEGER NOT NULL DEFAULT 1
);
CREATE TABLE IF NOT EXISTS library (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    added_at TEXT NOT NULL,
    pmid TEXT, doi TEXT, title TEXT, authors TEXT, journal TEXT,
    year INTEGER, url TEXT, tag TEXT, note TEXT,
    user_id INTEGER NOT NULL DEFAULT 1,
    UNIQUE(user_id, pmid, doi, title)
);
CREATE TABLE IF NOT EXISTS search_cache (
    key TEXT PRIMARY KEY,
    created_at TEXT NOT NULL,
    last_used TEXT,
    query TEXT,
    language TEXT,
    max_articles INTEGER,
    hits INTEGER NOT NULL DEFAULT 0,
    payload TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_reports_created ON reports(created_at DESC);
CREATE INDEX IF NOT EXISTS idx_cache_created ON search_cache(created_at);
"""


def conn() -> sqlite3.Connection:
    global _conn
    if _conn is None:
        _conn = sqlite3.connect(DB_PATH, check_same_thread=False, timeout=20)
        _conn.row_factory = sqlite3.Row
        # Web sunucusu ve terminal aracı aynı veritabanını aynı anda kullanabilsin
        _conn.execute("PRAGMA journal_mode=WAL")
        _conn.execute("PRAGMA busy_timeout=20000")
        _conn.executescript(SCHEMA)
        _conn.commit()
    return _conn


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def migrate_library() -> None:
    """Kütüphanedeki tekillik kısıtını kullanıcı bazına çevirir.

    Eski sürümde UNIQUE(pmid, doi, title) genel kapsamlıydı: bir makaleyi bir kullanıcı
    kaydettiyse başka kimse kaydedemiyordu. SQLite kısıt değiştirmeye izin vermediği için
    tablo bir kez yeniden kurulur.
    """
    row = conn().execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name='library'").fetchone()
    if not row or "UNIQUE(pmid, doi, title)" not in (row["sql"] or ""):
        return
    with _lock:
        c = conn()
        c.executescript("""
            CREATE TABLE library_new (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                added_at TEXT NOT NULL,
                pmid TEXT, doi TEXT, title TEXT, authors TEXT, journal TEXT,
                year INTEGER, url TEXT, tag TEXT, note TEXT,
                user_id INTEGER NOT NULL DEFAULT 1,
                UNIQUE(user_id, pmid, doi, title)
            );
            INSERT INTO library_new
                (id, added_at, pmid, doi, title, authors, journal, year, url, tag, note, user_id)
                SELECT id, added_at, pmid, doi, title, authors, journal, year, url, tag, note,
                       COALESCE(user_id, 1) FROM library;
            DROP TABLE library;
            ALTER TABLE library_new RENAME TO library;
        """)
        c.commit()
    print("  [store] kütüphane tekillik kısıtı kullanıcı bazına taşındı")


# --------------------------------------------------------------------- geçmiş
def save_report(result: dict, language: str = "Türkçe", user_id: int = 1) -> int:
    """Tamamlanan aramayı tüm sonuçlarıyla saklar ve rapor kimliğini döndürür."""
    with _lock:
        cur = conn().execute(
            "INSERT INTO reports (created_at, query, topic, language, article_count, elapsed,"
            " payload, user_id) VALUES (?,?,?,?,?,?,?,?)",
            (_now(), result.get("query", ""),
             (result.get("translation") or {}).get("topic", ""),
             language, len(result.get("articles", [])),
             result.get("elapsed", 0), json.dumps(result, ensure_ascii=False), user_id),
        )
        conn().execute("INSERT INTO searches (date, query, author) VALUES (?,?,?)",
                       (_now(), result.get("query", ""), ""))
        conn().commit()
        return int(cur.lastrowid)


def list_reports(user_id: int, limit: int = 50) -> list[dict]:
    rows = conn().execute(
        "SELECT id, created_at, query, topic, language, article_count, elapsed"
        " FROM reports WHERE user_id = ? ORDER BY id DESC LIMIT ?", (user_id, limit)).fetchall()
    return [dict(r) for r in rows]


def get_report(report_id: int, user_id: int) -> dict | None:
    row = conn().execute("SELECT payload FROM reports WHERE id = ? AND user_id = ?",
                         (report_id, user_id)).fetchone()
    return json.loads(row["payload"]) if row else None


def delete_report(report_id: int, user_id: int) -> None:
    with _lock:
        conn().execute("DELETE FROM reports WHERE id = ? AND user_id = ?", (report_id, user_id))
        conn().commit()


# ------------------------------------------------------------------ kütüphane
def add_to_library(article: dict, tag: str = "", note: str = "", user_id: int = 1) -> bool:
    """Makaleyi kişisel kütüphaneye ekler. Zaten varsa False döner."""
    with _lock:
        try:
            conn().execute(
                "INSERT INTO library (added_at, pmid, doi, title, authors, journal, year, url,"
                " tag, note, user_id) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (_now(), article.get("pmid", ""), article.get("doi", ""),
                 article.get("title", ""), article.get("authors", ""),
                 article.get("journal", ""), int(article.get("year") or 0),
                 article.get("best_free_url") or article.get("pubmed_url", ""), tag, note, user_id),
            )
            conn().commit()
            return True
        except sqlite3.IntegrityError:
            return False


def list_library(user_id: int, limit: int = 500) -> list[dict]:
    rows = conn().execute("SELECT * FROM library WHERE user_id = ? ORDER BY id DESC LIMIT ?",
                          (user_id, limit)).fetchall()
    return [dict(r) for r in rows]


def remove_from_library(item_id: int, user_id: int) -> None:
    with _lock:
        conn().execute("DELETE FROM library WHERE id = ? AND user_id = ?", (item_id, user_id))
        conn().commit()


def stats(user_id: int | None = None) -> dict:
    c = conn()
    if user_id is None:
        return {
            "reports": c.execute("SELECT COUNT(*) FROM reports").fetchone()[0],
            "library": c.execute("SELECT COUNT(*) FROM library").fetchone()[0],
            "searches": c.execute("SELECT COUNT(*) FROM searches").fetchone()[0],
        }
    return {
        "reports": c.execute("SELECT COUNT(*) FROM reports WHERE user_id = ?",
                             (user_id,)).fetchone()[0],
        "library": c.execute("SELECT COUNT(*) FROM library WHERE user_id = ?",
                             (user_id,)).fetchone()[0],
    }
