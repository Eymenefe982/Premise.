"""2026-09-18 denetiminde bulunan hataların regresyon testleri.

Her test, düzeltilmeden önce hatayı birebir yeniden üreten senaryonun tersidir: hata
geri gelirse bu dosya kırılır.
"""
from __future__ import annotations

import os
import subprocess
import sys
import threading
import time
from pathlib import Path

import psycopg
import pytest
from fastapi.testclient import TestClient

import app as appmod
from litrag import accounts, cache, db, llm, mailer, security
from litrag.extract import _normalize, _verify
from litrag.store import conn

ROOT = Path(__file__).resolve().parent.parent
PASSWORD = "dogru-sifre-1"


@pytest.fixture()
def outbox(monkeypatch):
    box: list[tuple[str, str, str]] = []
    monkeypatch.setattr(mailer, "send", lambda to, subject, body: box.append((to, subject, body)))
    return box


@pytest.fixture()
def client(outbox):
    accounts.init()
    with accounts._lock:
        for table in ("users", "auth_tokens", "credit_holds", "credit_ledger"):
            conn().execute(f"DELETE FROM {table}")
        conn().commit()
    return TestClient(appmod.app)


def _signup(client, email):
    res = client.post("/api/auth/signup", json={"email": email, "password": PASSWORD,
                                                "role": "physician", "kvkk_consent": True})
    assert res.status_code == 200, res.text
    return res


# ----------------------------------------------------------------- Postgres katmanı
class _FakeCursor:
    def __init__(self, conn):
        self.conn, self._row, self.rowcount = conn, None, 1

    def execute(self, sql, params):
        self.conn.seen.append(params)
        if sql.startswith("INSERT INTO tokens"):
            if "RETURNING id" in sql:
                raise psycopg.errors.UndefinedColumn('column "id" does not exist')
            return
        if params[0] in self.conn.emails:
            raise psycopg.errors.UniqueViolation("duplicate key value")
        self.conn.emails.add(params[0])
        self.conn.next_id += 1
        self._row = {"id": self.conn.next_id} if "RETURNING id" in sql else None

    def fetchone(self):
        return self._row


class _FakeConn:
    def __init__(self):
        self.emails, self.next_id, self.seen = set(), 0, []

    def cursor(self):
        return _FakeCursor(self)


def _fake_pg() -> db.Database:
    d = db.Database.__new__(db.Database)
    d.backend, d._lock, d._no_id_tables, d._conn = "postgres", threading.Lock(), set(), _FakeConn()
    return d


def test_duplicate_insert_does_not_poison_the_table():
    d = _fake_pg()
    insert = "INSERT INTO users (email) VALUES (?)"
    assert d.execute(insert, ("a@x.com",)).lastrowid == 1
    with pytest.raises(psycopg.errors.UniqueViolation):
        d.execute(insert, ("a@x.com",))
    assert "users" not in d._no_id_tables
    assert d.execute(insert, ("b@x.com",)).lastrowid == 2      # sonraki kayıt id'sini alır


def test_table_without_id_column_is_still_learned():
    d = _fake_pg()
    d.execute("INSERT INTO tokens (fingerprint) VALUES (?)", ("f",))
    assert "tokens" in d._no_id_tables


def test_nul_bytes_are_stripped_before_reaching_postgres():
    d = _fake_pg()
    d.execute("INSERT INTO users (email) VALUES (?)", ("a\x00b@x.com",))
    assert d._conn.seen[-1] == ("ab@x.com",)


def test_search_query_with_nul_is_rejected(client):
    _signup(client, "nul@example.com")
    res = client.post("/api/search", json={"query": "sglt2\x00 heart failure"})
    assert res.status_code == 422


# ----------------------------------------------------------------------- Gemini
class _Model:
    """Sahte google-genai istemcisi: `client.models.generate_content(...)`."""
    behaviour: dict[str, str] = {}
    calls: list[str] = []
    delay = 0.0

    def __init__(self, key: str):
        self.key = key
        self.models = self

    def generate_content(self, *_a, **_k):
        _Model.calls.append(self.key)
        time.sleep(_Model.delay)
        if _Model.behaviour.get(self.key) == "quota":
            raise RuntimeError("429 RESOURCE_EXHAUSTED quota")
        return f"ok:{self.key}"


@pytest.fixture()
def fake_gemini(monkeypatch):
    _Model.behaviour, _Model.calls, _Model.delay = {}, [], 0.0
    monkeypatch.setattr(llm, "GEMINI_API_KEYS", ["k0", "k1", "k2"])
    monkeypatch.setattr(llm, "_key_index", 0)
    monkeypatch.setattr(llm, "_client_for", _Model)            # anahtar başına istemci
    return _Model


def test_client_is_bound_to_its_key_and_reused():
    first, again = llm._client_for("anahtar-a"), llm._client_for("anahtar-a")
    assert first is again and first is not llm._client_for("anahtar-b")
    llm._clients.clear()


def test_quota_on_first_key_moves_to_the_next(fake_gemini):
    fake_gemini.behaviour = {"k0": "quota"}
    assert llm._generate_with_model("m", "p", "", {}) == "ok:k1"
    # Sonraki çağrı doğrudan çalışan anahtardan başlar
    fake_gemini.calls.clear()
    assert llm._generate_with_model("m", "p", "", {}) == "ok:k1"
    assert fake_gemini.calls == ["k1"]


def test_all_keys_exhausted_returns_instead_of_looping(fake_gemini):
    fake_gemini.behaviour = {"k0": "quota", "k1": "quota", "k2": "quota"}
    assert llm._generate_with_model("m", "p", "", {}) is None
    assert sorted(fake_gemini.calls) == ["k0", "k1", "k2"]      # her anahtar bir kez


def test_model_calls_run_concurrently(fake_gemini):
    fake_gemini.delay = 0.4
    threads = [threading.Thread(target=llm._generate_with_model, args=("m", "p", "", {}))
               for _ in range(4)]
    started = time.monotonic()
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert time.monotonic() - started < 1.2        # seri olsaydı ~1.6 sn


def test_truncated_triage_response_keeps_the_complete_grades(monkeypatch):
    from litrag.models import Article
    arts = [Article(pmid=str(p), title="t", abstract="a") for p in (111, 222, 333)]
    cut = '{"articles": [\n {"pmid": "111", "relevance": 2},\n {"pmid": "222", "relevance": 0},\n {"pmid": "33'
    monkeypatch.setattr(llm, "generate", lambda *a, **k: cut)
    assert llm.triage("q", arts) == 2
    assert [a.relevance for a in arts] == [2, 0, -1]      # kesilen makale örtüşmeye düşer


def test_topic_is_written_in_the_report_language(monkeypatch):
    seen = {}

    def fake_generate(prompt, system="", **_):
        seen["system"] = system
        return '{"english": "q", "topic": "t"}'

    monkeypatch.setattr(llm, "generate", fake_generate)
    llm.translate_query("SGLT2 in HFpEF", "English")
    assert "short title of the topic in English" in seen["system"]
    llm.translate_query("HFpEF'de SGLT2")
    assert "same language the question is written in" in seen["system"]


# ------------------------------------------------------------ depo ve önbellek
def test_first_database_write_under_the_lock_does_not_deadlock():
    script = (
        "import os, sys, tempfile;"
        f"sys.path.insert(0, {str(ROOT)!r});"
        "os.environ['DATABASE_URL']='';"
        "os.environ['LITRAG_DB']=os.path.join(tempfile.mkdtemp(), 'fresh.db');"
        "from litrag import store;"
        "print(store.save_report({'query': 'x', 'articles': []}, 'English'))")
    out = subprocess.run([sys.executable, "-c", script], capture_output=True, text=True,
                         timeout=60, env={**os.environ, "DATABASE_URL": ""})
    assert out.returncode == 0 and out.stdout.strip() == "1", out.stderr


def test_cache_can_be_rewritten_for_a_fresh_run():
    from litrag.pipeline import SearchRequest
    req = SearchRequest(query="sglt2 heart failure tekrar", refresh=True)
    key = cache.make_key(req)
    cache.put(key, req, {"query": req.query, "report": "v1"})
    cache.get(key)                                          # 1 isabet
    cache.put(key, req, {"query": req.query, "report": "v2"})
    row = conn().execute("SELECT hits, payload FROM search_cache WHERE key = ?",
                         (key,)).fetchone()
    assert '"v2"' in row["payload"] and row["hits"] == 1


# ------------------------------------------------------------- sayısal doğrulama
SOURCE = _normalize("Mortality fell (HR 0.75, 95% CI 0.60-0.93; p<.001). Mean age 61.3 "
                    "years. Score changed -3.2 (-5.1 to -1.3); ranges were 12.4-15.8.")


def _finding(**kw):
    return {"outcome": "x", "measure": "HR", "value": "0.75", "ci": "", "p": "", **kw}


def test_value_inside_another_number_is_not_verified():
    ages = _normalize("Mean age 61.3 years, HR 0.75.")
    assert _verify(_finding(value="1.3"), ages) is None            # yalnızca "61.3" var
    assert _verify(_finding(value="0.7"), ages) is None            # yalnızca "0.75" var
    assert _verify(_finding(value="0.75"), ages)["value"] == "0.75"


def test_fabricated_interval_drops_the_finding():
    assert _verify(_finding(ci="0.11 to 0.22"), SOURCE) is None
    ok = _verify(_finding(ci="95% CI 0.60-0.93"), SOURCE)
    assert ok["ci"] == "0.60 – 0.93"


def test_fabricated_p_value_is_cleared_but_the_finding_kept():
    kept = _verify(_finding(p="0.0001"), SOURCE)
    assert kept is not None and kept["p"] == ""
    assert _verify(_finding(p="<0.001"), SOURCE)["p"] == "<0.001"   # metinde "p<.001"


def test_range_dash_is_not_read_as_a_minus_sign():
    assert _verify(_finding(measure="MD", value="15.8"), SOURCE)["value"] == "15.8"
    assert _verify(_finding(measure="MD", value="3.2"), SOURCE)["value"] == "-3.2"


# --------------------------------------------------------------------- oturumlar
def test_locked_account_can_still_sign_in_with_the_emailed_temp_password(client, outbox):
    _signup(client, "kurban@example.com")
    client.cookies.clear()
    for _ in range(security.login_failures.limit):
        security.login_failures.allow("kurban@example.com")
    locked = client.post("/api/auth/login", json={"email": "kurban@example.com",
                                                  "password": PASSWORD})
    assert locked.status_code == 429
    client.post("/api/auth/forgot", json={"email": "kurban@example.com"})
    temp = outbox[-1][2].split("created for your")[1].split(":\n\n")[1].split("\n")[0]
    res = client.post("/api/auth/login", json={"email": "kurban@example.com", "password": temp})
    assert res.status_code == 200 and res.json()["must_change_password"] is True


def test_client_ip_ignores_a_forged_first_forwarded_entry(monkeypatch):
    from starlette.requests import Request

    monkeypatch.setattr(appmod, "TRUST_PROXY_HEADERS", True)

    def key(headers):
        scope = {"type": "http", "headers": [(k.encode(), v.encode()) for k, v in headers.items()],
                 "client": ("10.0.0.9", 1)}
        return appmod._client_key(Request(scope))

    assert key({"x-forwarded-for": "6.6.6.6, 203.0.113.5"}) == "203.0.113.5"
    assert key({"cf-connecting-ip": "198.51.100.1", "x-forwarded-for": "6.6.6.6"}) == "198.51.100.1"


def test_logout_revokes_this_sessions_refresh_token_only(client):
    _signup(client, "cikis@example.com")
    stolen = client.cookies.get("premise_refresh")
    other = TestClient(appmod.app)
    other.post("/api/auth/login", json={"email": "cikis@example.com", "password": PASSWORD})

    client.post("/api/auth/logout")
    client.cookies.clear()
    client.cookies.set("premise_refresh", stolen)
    assert client.post("/api/auth/refresh").status_code == 401
    assert other.post("/api/auth/refresh").status_code == 200     # diğer cihaz etkilenmez


def test_temp_password_must_be_replaced_before_searching(client, outbox):
    _signup(client, "gecici@example.com")
    client.cookies.clear()
    user = accounts.by_email("gecici@example.com")
    temp = accounts.issue_temp_password(user["id"])
    client.post("/api/auth/login", json={"email": "gecici@example.com", "password": temp})
    res = client.post("/api/search", json={"query": "sglt2 heart failure"})
    assert res.status_code == 403 and "permanent password" in res.json()["detail"]


# ------------------------------------------------------------------ kredi rezervasyonu
def _used(user_id):
    return accounts.by_id(user_id)["credits_used"]


def test_reservation_is_refunded_after_a_restart(client, monkeypatch):
    user = accounts.by_id(_signup(client, "restart@example.com").json()["id"])
    full = accounts.balance(user)                                  # hesabın tamamı
    assert accounts.reserve(user["id"], full, hold_id="job-a")
    assert _used(user["id"]) == full
    monkeypatch.setattr(accounts, "INSTANCE_ID", "yeni-surec")    # sunucu yeniden başladı
    assert accounts.release_stale_holds() == 1
    assert _used(user["id"]) == 0


def test_late_settle_after_release_charges_only_the_real_cost(client, monkeypatch):
    user = accounts.by_id(_signup(client, "gec@example.com").json()["id"])
    accounts.reserve(user["id"], 30, hold_id="job-b")
    monkeypatch.setattr(accounts, "INSTANCE_ID", "yeni-surec")
    accounts.release_stale_holds()                                 # iade: 0
    accounts.settle(user["id"], 30, 12, "search", hold_id="job-b")
    assert _used(user["id"]) == 12                                 # iade ikinci kez yapılmaz


def test_normal_settle_clears_the_hold(client):
    user = accounts.by_id(_signup(client, "normal@example.com").json()["id"])
    accounts.reserve(user["id"], 30, hold_id="job-c")
    accounts.settle(user["id"], 30, 8, "search", hold_id="job-c")
    assert _used(user["id"]) == 8
    assert accounts.release_stale_holds() == 0
    assert _used(user["id"]) == 8
