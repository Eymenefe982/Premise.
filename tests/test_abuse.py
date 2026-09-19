"""Ücretsiz kredinin çok hesapla toplanmasına karşı önlemler.

Her biri bedava krediyi çoğaltmanın bir yolunu kapatır: aynı posta kutusunun takma
adları, geçici posta servisleri, hesabı silip yeniden açmak ve aynı ağdan ya da
tarayıcıdan toplu kayıt. Bu yolların hiçbiri uygulamayı bozmaz, yalnızca bize para
kaybettirir; bu yüzden sessizce geri gelebilirler.
"""
from __future__ import annotations

from datetime import datetime, timedelta

import pytest
from fastapi.testclient import TestClient

import app as appmod
from litrag import abuse, accounts, mailer
from litrag.config import FREE_ACCOUNTS_PER_SOURCE, PLANS
from litrag.store import conn

PASSWORD = "gizli-sifre-1"
FREE = PLANS["free"]["credits"]


@pytest.fixture()
def outbox(monkeypatch):
    box: list[tuple[str, str, str]] = []
    monkeypatch.setattr(mailer, "send", lambda to, subject, body: box.append((to, subject, body)))
    return box


@pytest.fixture()
def fresh(outbox):
    accounts.init()
    with accounts._lock:
        for table in ("users", "auth_tokens", "credit_holds", "credit_ledger"):
            conn().execute(f"DELETE FROM {table}")
        conn().commit()


def _signup(email, client=None, ip=None):
    """Her çağrı varsayılan olarak ayrı bir tarayıcıdır (ayrı çerez kavanozu)."""
    client = client or TestClient(appmod.app)
    headers = {"cf-connecting-ip": ip} if ip else {}
    return client.post("/api/auth/signup", headers=headers,
                       json={"email": email, "password": PASSWORD,
                             "role": "physician", "kvkk_consent": True})


# ------------------------------------------------------------------ kanonik adres
@pytest.mark.parametrize("raw, canonical", [
    ("Ali.Veli@gmail.com", "aliveli@gmail.com"),
    ("a.l.i.v.e.l.i+premise@GMAIL.com", "aliveli@gmail.com"),
    ("aliveli@googlemail.com", "aliveli@gmail.com"),
    ("ali.veli+x@outlook.com", "ali.veli@outlook.com"),      # nokta yalnız Gmail'de anlamsız
    ("+only@example.com", "+only@example.com"),              # yerel kısım boşalmaz
])
def test_canonical_email(raw, canonical):
    assert abuse.canonical_email(raw) == canonical


def test_gmail_aliases_cannot_open_a_second_account(fresh):
    assert _signup("ali.veli@gmail.com").status_code == 200
    for alias in ("aliveli@gmail.com", "Ali.Veli+2@gmail.com", "aliveli@googlemail.com"):
        res = _signup(alias)
        assert res.status_code == 409, alias
        assert "already have an account" in res.json()["detail"]


def test_dots_still_distinguish_people_outside_gmail(fresh):
    assert _signup("ali.veli@outlook.com").status_code == 200
    assert _signup("aliveli@outlook.com").status_code == 200


def test_existing_alias_accounts_are_kept_but_no_new_one_opens(fresh):
    """Kural gelmeden açılmış kardeş hesaplar kapatılmaz; yenisi açılamaz."""
    for email in ("dup.lu@gmail.com", "duplu@gmail.com"):
        conn().execute("INSERT INTO users (email, password_hash, role, plan, period_start,"
                       " created_at) VALUES (?,?,?,?,?,?)",
                       (email, "x", "physician", "free", accounts._now(), accounts._now()))
    conn().commit()
    accounts.init()                                           # geri doldurma burada koşar
    rows = conn().execute("SELECT email, email_canonical FROM users ORDER BY id").fetchall()
    assert [r["email_canonical"] for r in rows] == ["duplu@gmail.com", None]
    assert _signup("d.u.p.l.u@gmail.com").status_code == 409


# ------------------------------------------------------------ geçici posta
@pytest.mark.parametrize("email", ["x@mailinator.com", "x@inbox.mailinator.com",
                                   "x@10minutemail.com"])
def test_disposable_inboxes_are_refused(fresh, email):
    res = _signup(email)
    assert res.status_code == 400 and "permanent email" in res.json()["detail"]
    assert accounts.by_email(email) is None


def test_a_disposable_address_can_still_sign_in_to_an_old_account(fresh):
    """Kural yalnız yeni kayıtta; o adresle önceden açılmış hesap kilitlenmez."""
    accounts.create_user("eski@mailinator.com", PASSWORD, "physician")
    res = TestClient(appmod.app).post("/api/auth/login",
                                      json={"email": "eski@mailinator.com", "password": PASSWORD})
    assert res.status_code == 200


# ------------------------------------------------------------ sil ve yeniden aç
def _use(user_id, amount):
    assert accounts.reserve(user_id, amount)


def _delete(client):
    assert client.request("DELETE", "/api/me", json={"password": PASSWORD}).status_code == 200


def test_deleting_and_signing_up_again_does_not_reset_free_credits(fresh):
    client = TestClient(appmod.app)
    first = _signup("sil@example.com", client).json()
    _use(first["id"], 60)
    _delete(client)

    again = _signup("sil+yeni@example.com").json()           # takma adla bile
    assert again["credits_left"] == FREE - 60
    assert again["period_start"] == first["period_start"]   # dönem de devam eder
    # Kayıt bir kez kullanılır, sonra silinir
    assert conn().execute("SELECT COUNT(*) AS n FROM deleted_accounts").fetchone()["n"] == 0


def test_the_deletion_record_holds_no_readable_email(fresh):
    client = TestClient(appmod.app)
    _signup("gizli.kisi@example.com", client)
    _delete(client)
    row = conn().execute("SELECT * FROM deleted_accounts").fetchone()
    assert "@" not in str(dict(row)) and "gizli" not in str(dict(row))


def test_the_deletion_record_expires_with_its_period(fresh):
    client = TestClient(appmod.app)
    first = _signup("donem@example.com", client).json()
    _use(first["id"], FREE)
    old = (datetime.now() - timedelta(days=accounts.PERIOD_DAYS + 1)).strftime(
        "%Y-%m-%d %H:%M:%S")
    _delete(client)
    conn().execute("UPDATE deleted_accounts SET period_start = ?", (old,))
    conn().commit()
    assert _signup("donem@example.com").json()["credits_left"] == FREE


def test_purchased_credits_are_not_carried_as_debt(fresh):
    client = TestClient(appmod.app)
    first = _signup("alan@example.com", client).json()
    accounts.grant(first["id"], 300, "satın alma")
    _use(first["id"], 250)
    _delete(client)
    assert _signup("alan@example.com").json()["credits_left"] == 0   # free hakkı bitmişti
    row = accounts.by_email("alan@example.com")
    assert row["credits_used"] == FREE                               # 250 değil


# ------------------------------------------------------------ ağ ve tarayıcı kotası
def test_only_the_first_accounts_from_one_network_get_free_credits(fresh, monkeypatch):
    monkeypatch.setattr(appmod, "TRUST_PROXY_HEADERS", True)
    for i in range(FREE_ACCOUNTS_PER_SOURCE):
        me = _signup(f"ag{i}@example.com", ip="203.0.113.7").json()
        assert me["credits_left"] == FREE and not me["free_limited"]

    client = TestClient(appmod.app)
    me = _signup("ag-fazla@example.com", client, ip="203.0.113.7").json()
    assert me["credits_left"] == 0 and me["free_limited"] is True
    res = client.post("/api/search", json={"query": "sglt2 heart failure"},
                      headers={"cf-connecting-ip": "203.0.113.7"})
    assert res.status_code == 402 and "institutional" in res.json()["detail"]

    # Başka bir ağ etkilenmez
    assert _signup("baska@example.com", ip="198.51.100.9").json()["credits_left"] == FREE


def test_one_browser_cannot_collect_free_credits_across_networks(fresh, monkeypatch):
    monkeypatch.setattr(appmod, "TRUST_PROXY_HEADERS", True)
    client = TestClient(appmod.app)                          # aynı çerez kavanozu
    for i in range(FREE_ACCOUNTS_PER_SOURCE):
        assert _signup(f"cihaz{i}@example.com", client, ip=f"198.51.100.{i + 1}"
                       ).json()["credits_left"] == FREE
        client.post("/api/auth/logout")
    assert appmod.DEVICE_COOKIE in client.cookies             # çıkışta silinmez
    me = _signup("cihaz-fazla@example.com", client, ip="198.51.100.99").json()
    assert me["free_limited"] is True


def test_an_ipv6_network_counts_as_one_source(fresh, monkeypatch):
    monkeypatch.setattr(appmod, "TRUST_PROXY_HEADERS", True)
    for i in range(FREE_ACCOUNTS_PER_SOURCE):
        _signup(f"v6-{i}@example.com", ip=f"2001:db8:1:2::{i + 1}")
    assert _signup("v6-fazla@example.com", ip="2001:db8:1:2::ffff").json()["free_limited"]


def test_institutional_addresses_are_exempt_and_do_not_fill_the_quota(fresh, monkeypatch):
    """Hastane ve kampüs ağlarında herkes tek IP'den çıkar."""
    monkeypatch.setattr(appmod, "TRUST_PROXY_HEADERS", True)
    # Kayıt hız sınırı IP başına saatte 5: 3 kurumsal + 1 öğrenci + 1 bireysel sığar.
    for i in range(FREE_ACCOUNTS_PER_SOURCE):
        me = _signup(f"hekim{i}@saglik.gov.tr", ip="192.0.2.50").json()
        assert me["credits_left"] == FREE
    assert _signup("ogrenci@ogr.hacettepe.edu.tr", ip="192.0.2.50").json()["credits_left"] == FREE
    # Kurumsal hesaplar kotayı doldurmadığı için bireysel adresler de hâlâ alır
    assert _signup("kisisel@gmail.com", ip="192.0.2.50").json()["credits_left"] == FREE


def test_the_quota_window_expires(fresh, monkeypatch):
    monkeypatch.setattr(appmod, "TRUST_PROXY_HEADERS", True)
    for i in range(FREE_ACCOUNTS_PER_SOURCE):
        _signup(f"eski{i}@example.com", ip="203.0.113.20")
    old = (datetime.now() - timedelta(days=31)).strftime("%Y-%m-%d %H:%M:%S")
    conn().execute("UPDATE free_grants SET created_at = ?", (old,))
    conn().commit()
    assert _signup("yeni@example.com", ip="203.0.113.20").json()["credits_left"] == FREE


def test_a_limited_account_that_upgrades_gets_the_paid_allowance(fresh, monkeypatch):
    monkeypatch.setattr(appmod, "TRUST_PROXY_HEADERS", True)
    for i in range(FREE_ACCOUNTS_PER_SOURCE):
        _signup(f"u{i}@example.com", ip="203.0.113.30")
    me = _signup("yukselt@example.com", ip="203.0.113.30").json()
    conn().execute("UPDATE users SET plan = 'pro' WHERE id = ?", (me["id"],))
    conn().commit()
    user = accounts.by_id(me["id"])
    assert accounts.balance(user) == PLANS["pro"]["credits"]
    assert accounts.is_free_limited(user) is False


def test_the_quota_stores_no_readable_ip(fresh, monkeypatch):
    monkeypatch.setattr(appmod, "TRUST_PROXY_HEADERS", True)
    _signup("iz@example.com", ip="203.0.113.44")
    rows = conn().execute("SELECT source_hash FROM free_grants").fetchall()
    assert rows and all("203.0.113" not in r["source_hash"] for r in rows)
