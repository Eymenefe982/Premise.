"""Gizlilik politikasının söz verdiği davranışlar: sorgu metni hesap dışında süresiz
tutulmaz, hesap silinince her şey silinir, oturum 30 gün boyunca kendiliğinden uzar."""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

import app as appmod
from litrag import accounts, cache, mailer, store
from litrag.pipeline import SearchRequest
from litrag.store import conn

PASSWORD = "gizli-sifre-1"


@pytest.fixture()
def outbox(monkeypatch):
    box: list[tuple[str, str, str]] = []
    monkeypatch.setattr(mailer, "send", lambda to, subject, body: box.append((to, subject, body)))
    return box


@pytest.fixture()
def client(outbox):
    accounts.init()
    with accounts._lock:
        for table in ("users", "auth_tokens", "credit_holds", "credit_ledger",
                      "reports", "library", "search_cache"):
            conn().execute(f"DELETE FROM {table}")
        conn().commit()
    return TestClient(appmod.app)


def _signup(client, email="silinecek@example.com"):
    res = client.post("/api/auth/signup", json={"email": email, "password": PASSWORD,
                                                "role": "physician", "kvkk_consent": True})
    assert res.status_code == 200, res.text
    return res.json()["id"]


def _count(table, user_id, column="user_id"):
    return conn().execute(f"SELECT COUNT(*) AS n FROM {table} WHERE {column} = ?",
                          (user_id,)).fetchone()["n"]


# ------------------------------------------------------------ sahipsiz sorgu kaydı
def test_legacy_query_log_is_dropped_and_no_longer_written(client):
    conn().execute("CREATE TABLE IF NOT EXISTS searches (id INTEGER PRIMARY KEY, query TEXT)")
    conn().commit()
    accounts.init()
    tables = {r["name"] for r in conn().execute(
        "SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
    assert "searches" not in tables
    store.save_report({"query": "hasta sorusu", "articles": []}, "English", user_id=1)
    assert "searches" not in store.stats()


def test_old_cache_table_gets_an_owner_column(client):
    conn().execute("DROP TABLE search_cache")
    conn().execute("CREATE TABLE search_cache (key TEXT PRIMARY KEY, created_at TEXT NOT NULL,"
                   " last_used TEXT, query TEXT, language TEXT, max_articles INTEGER,"
                   " hits INTEGER NOT NULL DEFAULT 0, payload TEXT NOT NULL)")
    conn().commit()
    accounts.init()
    assert "user_id" in conn().column_names("search_cache")


def test_cache_rows_are_owned_and_deleted_with_their_owner(client):
    req = SearchRequest(query="sahipli soru", user_id=42)
    key = cache.make_key(req)
    cache.put(key, req, {"query": req.query, "report": "r"})
    assert _count("search_cache", 42) == 1
    assert "user_id" not in (cache.get(key) or {})         # başka kullanıcıya sahip sızmaz
    assert cache.make_key(SearchRequest(query="sahipli soru", user_id=7)) == key
    assert cache.delete_for_user(42) == 1


# --------------------------------------------------------------------- hesap silme
def test_delete_requires_the_password(client, outbox):
    _signup(client)
    res = client.request("DELETE", "/api/me", json={"password": "yanlis"})
    assert res.status_code == 400
    assert client.get("/api/me").status_code == 200


def test_admin_account_cannot_be_deleted_from_the_app(client):
    user_id = _signup(client, "yonetici@example.com")
    conn().execute("UPDATE users SET role = 'admin' WHERE id = ?", (user_id,))
    conn().commit()
    res = client.request("DELETE", "/api/me", json={"password": PASSWORD})
    assert res.status_code == 400 and accounts.by_id(user_id) is not None


def test_delete_is_refused_while_a_search_is_running(client, monkeypatch):
    user_id = _signup(client)
    monkeypatch.setitem(appmod._jobs, "calisan", {"user_id": user_id, "finished_at": None,
                                                  "events": None})
    res = client.request("DELETE", "/api/me", json={"password": PASSWORD})
    assert res.status_code == 409 and accounts.by_id(user_id) is not None


def test_delete_removes_every_trace_of_the_account(client, outbox):
    user_id = _signup(client)
    report_id = store.save_report({"query": "q", "articles": []}, "English", user_id=user_id)
    store.add_to_library({"pmid": "1", "title": "t"}, user_id=user_id)
    accounts.grant(user_id, 5, "hediye")
    accounts.reserve(user_id, 3, hold_id="bekleyen")
    req = SearchRequest(query="silinecek soru", user_id=user_id)
    cache.put(cache.make_key(req), req, {"query": req.query})

    res = client.request("DELETE", "/api/me", json={"password": PASSWORD})
    assert res.status_code == 200
    assert accounts.by_id(user_id) is None
    for table in ("reports", "library", "credit_ledger", "credit_holds", "auth_tokens",
                  "search_cache"):
        assert _count(table, user_id) == 0, table
    assert store.get_report(report_id, user_id) is None
    assert outbox[-1][0] == "silinecek@example.com" and "deleted" in outbox[-1][1]
    assert client.get("/api/me").status_code == 401            # çerezler silindi

    client.cookies.clear()
    _signup(client)                                            # adres yeniden kullanılabilir


def test_old_session_of_a_deleted_account_is_rejected(client):
    _signup(client)
    refresh = client.cookies.get("premise_refresh")
    client.request("DELETE", "/api/me", json={"password": PASSWORD})
    client.cookies.clear()
    client.cookies.set("premise_refresh", refresh)
    assert client.get("/api/me").status_code == 401


# ------------------------------------------------------------------ oturum uzatma
def test_expired_access_token_is_renewed_from_the_refresh_token(client):
    _signup(client)
    refresh = client.cookies.get("premise_refresh")
    client.cookies.clear()
    client.cookies.set("premise_refresh", refresh)             # 2 saat geçti: erişim yok
    res = client.get("/api/me")
    assert res.status_code == 200
    assert "premise_session" in res.cookies                    # yeni erişim çerezi yazıldı


def test_signed_out_refresh_token_does_not_renew(client):
    _signup(client)
    refresh = client.cookies.get("premise_refresh")
    client.post("/api/auth/logout")
    client.cookies.clear()
    client.cookies.set("premise_refresh", refresh)
    assert client.get("/api/me").status_code == 401


def test_password_change_stops_renewal_on_other_devices(client):
    _signup(client)
    other = TestClient(appmod.app)
    other.post("/api/auth/login", json={"email": "silinecek@example.com", "password": PASSWORD})
    old_refresh = other.cookies.get("premise_refresh")
    client.post("/api/me/password", json={"current_password": PASSWORD,
                                          "new_password": "yeni-sifre-12"})
    other.cookies.clear()
    other.cookies.set("premise_refresh", old_refresh)
    assert other.get("/api/me").status_code == 401
    assert client.get("/api/me").status_code == 200            # bu tarayıcı devam eder
