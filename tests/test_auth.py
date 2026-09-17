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


def _temp_password_in(body: str) -> str:
    return body.split("created for your")[1].split(":\n\n")[1].split("\n")[0]


# ------------------------------------------------------------------------ signup
def test_signup_sends_verification_and_welcome_and_logs_in(client, outbox):
    res = client.post("/api/auth/signup", json=SIGNUP)
    assert res.status_code == 200
    assert res.json()["email_verified"] is False
    assert len(outbox) == 2              # verification + welcome
    assert client.get("/api/me").status_code == 200


def test_invalid_email_is_rejected_with_clear_message(client, outbox):
    bad = {**SIGNUP, "email": "gecersiz-adres"}
    res = client.post("/api/auth/signup", json=bad)
    assert res.status_code == 422
    assert "Enter a valid email address." in res.text
    assert not outbox


def test_verification_token_works_once(client, outbox):
    client.post("/api/auth/signup", json=SIGNUP)
    verify_mail = next(m for m in outbox if "Verify your" in m[1])
    token = _token_in(verify_mail[2])
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


def test_forgot_does_not_lock_out_the_account(client, outbox):
    """E-posta adresini bilen biri "şifremi unuttum" diyerek hesap sahibini dışarıda
    bırakamamalı: mevcut şifre ve oturum geçici şifre kullanılana kadar geçerli kalır."""
    client.post("/api/auth/signup", json=SIGNUP)
    client.post("/api/auth/forgot", json={"email": SIGNUP["email"]})
    assert client.get("/api/me").status_code == 200
    fresh = TestClient(appmod.app)
    assert fresh.post("/api/auth/login",
                      json={"email": SIGNUP["email"], "password": SIGNUP["password"]}
                      ).status_code == 200


def test_temp_password_logs_in_and_flags_must_change(client, outbox):
    client.post("/api/auth/signup", json=SIGNUP)
    outbox.clear()
    client.post("/api/auth/forgot", json={"email": SIGNUP["email"]})
    temp_password = _temp_password_in(outbox[-1][2])

    fresh = TestClient(appmod.app)
    res = fresh.post("/api/auth/login",
                     json={"email": SIGNUP["email"], "password": temp_password})
    assert res.status_code == 200
    assert res.json()["must_change_password"] is True
    # Geçici şifre kullanılınca eski şifre ve diğer oturumlar düşer; tekrar kullanılamaz
    assert client.get("/api/me").status_code == 401
    assert TestClient(appmod.app).post(
        "/api/auth/login", json={"email": SIGNUP["email"], "password": SIGNUP["password"]}
    ).status_code == 401

    fresh.post("/api/me/password",
              json={"current_password": temp_password, "new_password": "yenisifre1"})
    assert fresh.get("/api/me").json()["must_change_password"] is False


def test_signup_with_existing_account_says_sign_in(client, outbox):
    client.post("/api/auth/signup", json=SIGNUP)
    res = TestClient(appmod.app).post("/api/auth/signup", json=SIGNUP)
    assert res.status_code == 409
    assert "already registered" in res.json()["detail"]


# --------------------------------------------------------------------- güvenlik
def test_security_headers_present(client):
    res = client.get("/api/plans")
    assert res.headers["x-frame-options"] == "DENY"
    assert "frame-ancestors 'none'" in res.headers["content-security-policy"]
    assert res.headers["x-content-type-options"] == "nosniff"


def test_cross_site_post_rejected(client, outbox):
    res = client.post("/api/auth/signup", json=SIGNUP,
                      headers={"Origin": "https://evil.example"})
    assert res.status_code == 403


def test_love_account_unlimited_only_when_verified(client, outbox):
    love = {**SIGNUP, "email": accounts.LOVE_EMAIL}
    me = client.post("/api/auth/signup", json=love).json()
    assert me["love"] is False and me["credits_left"] is not None
    token = _token_in(next(m for m in outbox if "Verify your" in m[1])[2])
    client.post("/api/auth/verify", json={"token": token})
    me = client.get("/api/me").json()
    assert me["love"] is True and me["unlimited"] is True and me["credits_left"] is None
    user = accounts.by_email(love["email"])
    assert accounts.reserve(user["id"], 10_000_000) is True


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
