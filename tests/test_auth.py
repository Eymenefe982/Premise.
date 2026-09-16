"""Kimlik akışlarının uçtan uca testleri: kayıt, doğrulama, şifre sıfırlama.

Bu akışların her biri hesap güvenliğinin bir parçasını taşır ve sessizce bozulabilir.
Bir jetonun tekrar kullanılabilir hale gelmesi ya da şifre sıfırlandıktan sonra eski
oturumun ayakta kalması, uygulamanın çalışmasını engellemez; yalnızca güvenliğini
ortadan kaldırır.
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

import app as appmod
from litrag import accounts, mailer
from litrag.store import conn

SIGNUP = {"email": "dr@ornek.com", "password": "eskisifre1",
          "role": "physician", "kvkk_consent": True}


@pytest.fixture()
def outbox(monkeypatch):
    """Gönderilen e-postaları yakalar; test hiçbir zaman gerçek posta yollamaz."""
    box: list[tuple[str, str, str]] = []
    monkeypatch.setattr(mailer, "send", lambda to, subject, body: box.append((to, subject, body)))
    return box


@pytest.fixture()
def client():
    accounts.init()
    # Her test kendi temiz hesap tablosuyla başlasın
    with accounts._lock:
        conn().execute("DELETE FROM users")
        conn().execute("DELETE FROM auth_tokens")
        conn().commit()
    return TestClient(appmod.app)


def _token_in(body: str) -> str:
    return body.split("token=")[1].split()[0]


# ------------------------------------------------------------------------ kayıt
def test_signup_sends_verification_and_logs_in(client, outbox):
    res = client.post("/api/auth/signup", json=SIGNUP)
    assert res.status_code == 200
    assert res.json()["email_verified"] is False
    assert len(outbox) == 1
    assert client.get("/api/me").status_code == 200


def test_verification_token_works_once(client, outbox):
    client.post("/api/auth/signup", json=SIGNUP)
    token = _token_in(outbox[-1][2])
    assert client.post("/api/auth/verify", json={"token": token}).status_code == 200
    assert client.get("/api/me").json()["email_verified"] is True
    # Aynı bağlantı ikinci kez çalışmamalı
    assert client.post("/api/auth/verify", json={"token": token}).status_code == 400


# -------------------------------------------------------------- şifre sıfırlama
def test_forgot_does_not_reveal_whether_an_account_exists(client, outbox):
    client.post("/api/auth/signup", json=SIGNUP)
    outbox.clear()
    unknown = client.post("/api/auth/forgot", json={"email": "yok@ornek.com"})
    known = client.post("/api/auth/forgot", json={"email": SIGNUP["email"]})
    assert unknown.status_code == known.status_code == 200
    assert unknown.json() == known.json()
    assert len(outbox) == 1              # posta yalnızca kayıtlı adrese gider


def test_reset_token_is_not_stored_in_plain_text(client, outbox):
    client.post("/api/auth/signup", json=SIGNUP)
    client.post("/api/auth/forgot", json={"email": SIGNUP["email"]})
    token = _token_in(outbox[-1][2])
    stored = [r["fingerprint"] for r in conn().execute("SELECT fingerprint FROM auth_tokens")]
    assert stored and token not in stored


def test_reset_revokes_other_sessions_but_keeps_this_one(client, outbox):
    client.post("/api/auth/signup", json=SIGNUP)
    client.post("/api/auth/forgot", json={"email": SIGNUP["email"]})
    token = _token_in(outbox[-1][2])

    # Başka bir cihazdaki açık oturum
    other = TestClient(appmod.app)
    other.cookies.update(client.cookies)
    assert other.get("/api/me").status_code == 200

    assert client.post("/api/auth/reset",
                       json={"token": token, "new_password": "yenisifre1"}).status_code == 200
    assert other.get("/api/me").status_code == 401       # eski oturum düştü
    assert client.get("/api/me").status_code == 200      # sıfırlayan oturum devam eder


def test_reset_token_cannot_be_replayed(client, outbox):
    client.post("/api/auth/signup", json=SIGNUP)
    client.post("/api/auth/forgot", json={"email": SIGNUP["email"]})
    token = _token_in(outbox[-1][2])
    client.post("/api/auth/reset", json={"token": token, "new_password": "yenisifre1"})
    again = client.post("/api/auth/reset", json={"token": token, "new_password": "baskabir1"})
    assert again.status_code == 400


def test_password_actually_changes(client, outbox):
    client.post("/api/auth/signup", json=SIGNUP)
    client.post("/api/auth/forgot", json={"email": SIGNUP["email"]})
    token = _token_in(outbox[-1][2])
    client.post("/api/auth/reset", json={"token": token, "new_password": "yenisifre1"})

    fresh = TestClient(appmod.app)
    assert fresh.post("/api/auth/login",
                      json={"email": SIGNUP["email"], "password": SIGNUP["password"]}
                      ).status_code == 401
    assert fresh.post("/api/auth/login",
                      json={"email": SIGNUP["email"], "password": "yenisifre1"}
                      ).status_code == 200


def test_invalid_reset_token_is_rejected(client):
    res = client.post("/api/auth/reset",
                      json={"token": "x" * 40, "new_password": "yenisifre1"})
    assert res.status_code == 400


# ------------------------------------------------------------------- krediler
def test_credit_reservation_prevents_overdraft(client):
    """Bakiyeyi okuyup sonra düşmek, eşzamanlı aramalarda hesabın eksiye
    düşmesine izin verirdi; rezervasyon tek kilidin altında yapılır."""
    client.post("/api/auth/signup", json=SIGNUP)
    user = accounts.by_email(SIGNUP["email"])
    start = accounts.balance(user)

    assert accounts.reserve(user["id"], start) is True
    assert accounts.reserve(user["id"], 1) is False          # bakiye bitti
    assert accounts.balance(accounts.by_id(user["id"])) == 0

    # Gerçek maliyet rezervasyondan düşükse fark iade edilir
    accounts.settle(user["id"], start, 10, "search")
    assert accounts.balance(accounts.by_id(user["id"])) == start - 10


def test_cancelled_search_is_refunded_in_full(client):
    client.post("/api/auth/signup", json=SIGNUP)
    user = accounts.by_email(SIGNUP["email"])
    start = accounts.balance(user)
    accounts.reserve(user["id"], 40)
    accounts.settle(user["id"], 40, 0, "search cancelled")
    assert accounts.balance(accounts.by_id(user["id"])) == start
