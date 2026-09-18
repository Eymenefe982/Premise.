"""Yayın öncesi sertleştirme: HTTPS yönlendirmesi, hata sayfaları, SEO dosyaları,
bot tuzağı ve denetim kaydı."""
from __future__ import annotations

import logging

import pytest
from fastapi.testclient import TestClient

import app as appmod
from litrag import accounts, mailer
from litrag.logging_setup import mask_email
from litrag.store import conn

SIGNUP = {"email": "bot@ornek.com", "password": "gizlisifre1",
          "role": "physician", "kvkk_consent": True}


@pytest.fixture()
def client(monkeypatch):
    accounts.init()
    with accounts._lock:
        conn().execute("DELETE FROM users")
        conn().commit()
    monkeypatch.setattr(mailer, "send", lambda *a, **k: None)
    return TestClient(appmod.app)


def test_http_is_redirected_to_https_when_forced(client, monkeypatch):
    monkeypatch.setattr(appmod, "FORCE_HTTPS", True)
    r = client.get("/gizlilik?x=1", headers={"x-forwarded-proto": "http"},
                   follow_redirects=False)
    assert r.status_code == 308
    assert r.headers["location"].startswith("https://")
    assert r.headers["location"].endswith("/gizlilik?x=1")


def test_requests_without_proxy_header_are_not_redirected(client, monkeypatch):
    # Render'ın iç sağlık kontrolleri başlıksız gelir; yönlendirilirlerse servis "down" görünür.
    monkeypatch.setattr(appmod, "FORCE_HTTPS", True)
    assert client.get("/robots.txt", follow_redirects=False).status_code == 200


def test_unknown_page_gets_html_404_but_api_gets_json(client):
    page = client.get("/no-such-page", headers={"accept": "text/html"})
    assert page.status_code == 404
    assert "text/html" in page.headers["content-type"]
    assert "doesn't exist" in page.text

    api = client.get("/api/no-such-endpoint", headers={"accept": "text/html"})
    assert api.status_code == 404
    assert api.headers["content-type"].startswith("application/json")


def test_unhandled_error_does_not_leak_details(monkeypatch):
    def boom():
        raise RuntimeError("secret-internal-detail")
    monkeypatch.setattr(appmod.store, "stats", lambda *a, **k: boom())
    client = TestClient(appmod.app, raise_server_exceptions=False)
    signup = client.post("/api/auth/signup", json=SIGNUP)
    assert signup.status_code == 200
    r = client.get("/api/me")
    assert r.status_code == 500
    assert "secret-internal-detail" not in r.text


def test_robots_and_sitemap(client):
    robots = client.get("/robots.txt").text
    assert "Disallow: /api/" in robots and "Sitemap:" in robots
    sitemap = client.get("/sitemap.xml")
    assert sitemap.headers["content-type"].startswith("application/xml")
    assert "/kosullar</loc>" in sitemap.text and "/hesap" not in sitemap.text


def test_public_pages_have_absolute_social_tags(client):
    for path in ("/", "/kosullar", "/gizlilik", "/giris", "/tanitim"):
        html = client.get(path).text
        assert "__APP_URL__" not in html, path
        assert 'name="description"' in html, path
        assert f'og:image" content="{appmod.APP_URL}/static/img/og-image.png"' in html, path
        assert "cookie-notice.js" in html, path


def test_honeypot_blocks_signup(client):
    r = client.post("/api/auth/signup", json={**SIGNUP, "website": "http://spam.example"})
    assert r.status_code == 400
    assert accounts.by_email(SIGNUP["email"]) is None


def test_login_events_are_audited_without_raw_email(client, caplog):
    client.post("/api/auth/signup", json=SIGNUP)
    audit_logger = logging.getLogger("premise.audit")
    audit_logger.addHandler(caplog.handler)
    try:
        with caplog.at_level(logging.INFO, logger="premise.audit"):
            client.post("/api/auth/login", json={"email": SIGNUP["email"], "password": "yanlis-sifre"})
    finally:
        audit_logger.removeHandler(caplog.handler)
    lines = [rec.getMessage() for rec in caplog.records if rec.name == "premise.audit"]
    assert any("event=login" in l and "ok=0" in l and "bad_credentials" in l for l in lines)
    assert all(SIGNUP["email"] not in l for l in lines)
    assert any(mask_email(SIGNUP["email"]) in l for l in lines)


def test_mask_email():
    assert mask_email("ayse@example.com") == "a***@example.com"
    assert mask_email("") == "-"
    assert mask_email("not-an-email") == "-"
