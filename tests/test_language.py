"""Arayüz dili ve cevap dilinin ayrı tutulması.

Kullanıcı kayıt ekranında arayüzünü Türkçeye çevirdiğinde cevapların da Türkçe geleceğini
varsayıyor; oysa cevap dili aramanın kendi ayarı. Bu boşluğu bir kereye mahsus gösterilen
bir uyarı kapatıyor. Sessiz bozulma biçimleri: uyarının hiç çıkmaması, her girişte tekrar
çıkması, ya da arayüz dilinin tarayıcıda kalıp başka cihazda kaybolması.
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

import app as appmod
from litrag import accounts, mailer
from litrag.config import DEFAULT_UI_LANGUAGE
from litrag.store import conn

SIGNUP = {"email": "dil@ornek.com", "password": "gizli-sifre-1",
          "role": "physician", "kvkk_consent": True}


@pytest.fixture()
def outbox(monkeypatch):
    box: list[tuple[str, str, str]] = []
    monkeypatch.setattr(mailer, "send", lambda to, subject, body: box.append((to, subject, body)))
    return box


@pytest.fixture()
def client(outbox):
    accounts.init()
    with accounts._lock:
        conn().execute("DELETE FROM users")
        conn().execute("DELETE FROM auth_tokens")
        conn().commit()
    return TestClient(appmod.app)


def _verify(client, outbox):
    """Kayıt e-postasındaki doğrulama jetonunu harcar."""
    token = outbox[0][2].split("token=")[1].split()[0]
    assert client.post("/api/auth/verify", json={"token": token}).status_code == 200


# ------------------------------------------------------------------ dil seçimi
def test_signup_without_a_language_stays_on_the_default(client):
    me = client.post("/api/auth/signup", json=SIGNUP).json()
    assert me["ui_language"] == DEFAULT_UI_LANGUAGE == "en"


def test_signup_stores_the_language_chosen_on_the_form(client):
    me = client.post("/api/auth/signup", json={**SIGNUP, "ui_language": "tr"}).json()
    assert me["ui_language"] == "tr"
    # Hesapta durur, tarayıcıda değil: başka bir istemci aynı değeri görmeli.
    other = TestClient(appmod.app)
    other.post("/api/auth/login", json={"email": SIGNUP["email"],
                                        "password": SIGNUP["password"]})
    assert other.get("/api/me").json()["ui_language"] == "tr"


def test_an_unsupported_language_is_refused_by_the_schema(client):
    res = client.post("/api/auth/signup", json={**SIGNUP, "ui_language": "klingon"})
    assert res.status_code == 422


def test_an_unsupported_language_stored_somehow_reads_back_as_the_default(client):
    """Şema bir yana, çevirisi olmayan bir kod arayüzü yarı boş bırakırdı."""
    client.post("/api/auth/signup", json=SIGNUP)
    with accounts._lock:
        conn().execute("UPDATE users SET ui_language = 'klingon'")
        conn().commit()
    assert client.get("/api/me").json()["ui_language"] == DEFAULT_UI_LANGUAGE


def test_language_can_be_changed_after_signup(client):
    client.post("/api/auth/signup", json=SIGNUP)
    assert client.post("/api/me/language", json={"ui_language": "tr"}).json()["ui_language"] == "tr"
    assert client.get("/api/me").json()["ui_language"] == "tr"


def test_changing_the_interface_language_needs_a_session(client):
    assert client.post("/api/me/language", json={"ui_language": "tr"}).status_code == 401


# ------------------------------------------------------- bir kerelik uyarı
def test_notice_waits_for_the_email_to_be_verified(client, outbox):
    me = client.post("/api/auth/signup", json={**SIGNUP, "ui_language": "tr"}).json()
    assert me["language_notice_pending"] is False      # henüz doğrulanmadı
    _verify(client, outbox)
    assert client.get("/api/me").json()["language_notice_pending"] is True


def test_notice_is_not_shown_to_someone_who_kept_the_default(client, outbox):
    """İngilizce seçende değişen bir şey yok; uyarı ona gürültüdür."""
    client.post("/api/auth/signup", json=SIGNUP)
    _verify(client, outbox)
    assert client.get("/api/me").json()["language_notice_pending"] is False


def test_notice_is_shown_once_and_never_again(client, outbox):
    client.post("/api/auth/signup", json={**SIGNUP, "ui_language": "tr"})
    _verify(client, outbox)
    assert client.get("/api/me").json()["language_notice_pending"] is True

    assert client.post("/api/me/language-notice-seen").json()["ok"] is True
    assert client.get("/api/me").json()["language_notice_pending"] is False

    # Yeni bir oturum da onu tekrar tetiklememeli: işaret hesapta duruyor.
    other = TestClient(appmod.app)
    other.post("/api/auth/login", json={"email": SIGNUP["email"],
                                        "password": SIGNUP["password"]})
    assert other.get("/api/me").json()["language_notice_pending"] is False


def test_switching_language_later_does_not_bring_the_notice_back(client, outbox):
    """Uyarı bir kez okunur. Dili sonradan değiştiren kullanıcı ikisinin ayrı olduğunu
    zaten öğrenmiştir; her değişimde tekrar göstermek gürültü olurdu."""
    client.post("/api/auth/signup", json={**SIGNUP, "ui_language": "tr"})
    _verify(client, outbox)
    client.post("/api/me/language-notice-seen")
    client.post("/api/me/language", json={"ui_language": "en"})
    client.post("/api/me/language", json={"ui_language": "tr"})
    assert client.get("/api/me").json()["language_notice_pending"] is False
