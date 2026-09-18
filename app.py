"""Premise web sunucusu — FastAPI + tek sayfa arayüz.

Çalıştırmak için:  python app.py       (tarayıcı otomatik açılır)
"""
from __future__ import annotations

import asyncio
import io
import json
import os
import queue
import threading
import time
import traceback
import uuid
import webbrowser
from contextlib import asynccontextmanager
from datetime import timedelta
from typing import Any
from urllib.parse import urlparse

import uvicorn
from fastapi import Depends, FastAPI, HTTPException, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import (FileResponse, JSONResponse, PlainTextResponse,
                               StreamingResponse)
from fastapi.staticfiles import StaticFiles

from litrag import accounts, cache, exporters, mailer, security, store
from litrag.config import (ACCESS_TOKEN_HOURS, ALLOWED_ORIGINS, APP_NAME, COOKIE_SECURE,
                           DB_IS_EPHEMERAL, DB_PATH,
                           GEMINI_API_KEYS, GEMINI_FAST_MODEL, GEMINI_MODEL, NCBI_API_KEY,
                           NCBI_EMAIL, PLANS, REFRESH_TOKEN_DAYS, REQUIRE_EMAIL_VERIFICATION,
                           TRUST_PROXY_HEADERS, VERIFY_TOKEN_HOURS, WEB_DIR)
from litrag.pdf import to_pdf
from litrag.pipeline import SearchCancelled, SearchRequest, run_search
from litrag.schemas import (ExportIn, ForgotPasswordIn, GrantIn, LibraryIn, LoginIn,
                            NcbiKeyIn, PasswordChangeIn, SearchIn,
                            SignupIn, VerifyEmailIn)

@asynccontextmanager
async def _lifespan(app: FastAPI):
    # uvicorn'un `app:app` ile doğrudan başlatıldığı ortamlarda (ör. Render) `main()`
    # hiç çalışmaz; şema kurulumu bu yüzden burada, ASGI lifespan'ında yapılır.
    accounts.init()
    if DB_IS_EPHEMERAL:
        print("  !! VERİTABANI GEÇİCİ DİSKTE: " + str(DB_PATH))
        print("  !! Bu kapsayıcı yeniden başladığında hesaplar, geçmiş, krediler ve")
        print("  !! önbellek silinir. Kalıcı disk bağlayıp LITRAG_DB'yi oraya yönlendirin.")
    removed = cache.purge_expired()
    if removed:
        print(f"  {removed} expired cache entries removed")
    stale = accounts.purge_expired_tokens()
    if stale:
        print(f"  {stale} expired auth tokens removed")
    if not mailer.configured():
        print("  ! SMTP yapılandırılmamış: şifre sıfırlama e-postaları konsola yazılır.")
    yield


app = FastAPI(title=APP_NAME, docs_url=None, redoc_url=None, lifespan=_lifespan)

if ALLOWED_ORIGINS:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=ALLOWED_ORIGINS,
        allow_credentials=True,
        allow_methods=["GET", "POST", "DELETE"],
        allow_headers=["Content-Type"],
    )

_CSP = ("default-src 'self'; script-src 'self' 'unsafe-inline'; "
        "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; "
        "font-src 'self' https://fonts.gstatic.com; img-src 'self' data:; "
        "connect-src 'self'; object-src 'none'; base-uri 'self'; form-action 'self'; "
        "frame-ancestors 'none'")


@app.middleware("http")
async def _hardening(request: Request, call_next):
    """Tarayıcı tarafı savunma başlıkları ve durum değiştiren isteklerde köken kontrolü.

    Çerezler SameSite=Lax olsa da başka bir siteden gelen POST/DELETE burada ayrıca
    reddedilir (CSRF'e karşı ikinci katman)."""
    if request.method not in ("GET", "HEAD", "OPTIONS"):
        origin = request.headers.get("origin")
        if origin and origin not in ALLOWED_ORIGINS \
                and urlparse(origin).netloc != request.headers.get("host", ""):
            return JSONResponse({"detail": "Cross-site request rejected."}, status_code=403)
    response = await call_next(request)
    headers = response.headers
    headers.setdefault("Content-Security-Policy", _CSP)
    headers.setdefault("X-Content-Type-Options", "nosniff")
    headers.setdefault("X-Frame-Options", "DENY")
    headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
    headers.setdefault("Permissions-Policy", "camera=(), microphone=(), geolocation=()")
    if COOKIE_SECURE:
        headers.setdefault("Strict-Transport-Security", "max-age=31536000; includeSubDomains")
    if request.url.path.startswith("/api/"):
        headers.setdefault("Cache-Control", "no-store")
    return response


ACCESS_COOKIE = "premise_session"
REFRESH_COOKIE = "premise_refresh"

# job_id -> {"events": Queue, "user_id": int, "finished_at": float | None}
_jobs: dict[str, dict] = {}
MAX_ACTIVE_JOBS_PER_USER = 2
_ORPHAN_JOB_SECONDS = 600
_cancelled: set[str] = set()


# --------------------------------------------------------------------- oturum
def _set_cookie(response: Response, name: str, token: str, max_age: int) -> None:
    response.set_cookie(name, token, max_age=max_age, httponly=True,
                        samesite="lax", secure=COOKIE_SECURE, path="/")


def _login_response(response: Response, user: dict) -> dict:
    secret = accounts.jwt_secret()
    epoch = int(user.get("session_epoch") or 0)
    _set_cookie(response, ACCESS_COOKIE,
                security.issue_token(secret, user["id"], "access", epoch),
                ACCESS_TOKEN_HOURS * 3600)
    _set_cookie(response, REFRESH_COOKIE,
                security.issue_token(secret, user["id"], "refresh", epoch),
                REFRESH_TOKEN_DAYS * 86400)
    return accounts.public(user)


def _user_from_cookie(request: Request, cookie: str, kind: str) -> dict | None:
    """Jetonu çözer ve oturum kuşağını hesabınkiyle karşılaştırır.

    Kuşak eşleşmezse jeton bir şifre değişiminden önce verilmiş demektir; süresi
    dolmamış olsa bile kabul edilmez."""
    token = request.cookies.get(cookie, "")
    claim = security.read_token(accounts.jwt_secret(), token, kind) if token else None
    if claim is None:
        return None
    user_id, epoch = claim
    user = accounts.by_id(user_id)
    if user is None or int(user.get("session_epoch") or 0) != epoch:
        return None
    return user


def current_user(request: Request) -> dict:
    """Oturum çerezini doğrular. Geçersizse 401 verir."""
    user = _user_from_cookie(request, ACCESS_COOKIE, "access")
    if user is None:
        raise HTTPException(401, "No active session, please sign in.")
    return accounts.roll_period(user)


def require_admin(user: dict = Depends(current_user)) -> dict:
    if not accounts.is_admin(user):
        raise HTTPException(403, "Administrator access is required for this action.")
    return user


def _client_key(request: Request) -> str:
    """İstemcinin IP'si. Vekil arkasında vekilin kendi adresi herkes için aynıdır;
    o yüzden vekilin koyduğu başlıklar okunur (bkz. config.TRUST_PROXY_HEADERS)."""
    if TRUST_PROXY_HEADERS:
        for name in ("true-client-ip", "cf-connecting-ip"):
            value = request.headers.get(name, "").strip()
            if value:
                return value
        forwarded = request.headers.get("x-forwarded-for", "")
        if forwarded:
            return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


# ------------------------------------------------------------------ kimlik API
@app.post("/api/auth/signup")
async def signup(body: SignupIn, request: Request, response: Response) -> dict:
    if not security.signup_throttle.allow(_client_key(request)):
        raise HTTPException(429, "Too many signup attempts. Please try again later.")
    try:
        user = accounts.create_user(str(body.email), body.password, body.role,
                                    consented=body.kvkk_consent)
    except ValueError as exc:
        raise HTTPException(409, str(exc))
    _send_verification(user)
    mailer.send_welcome(user["email"])
    return _login_response(response, user)


@app.post("/api/auth/login")
async def login(body: LoginIn, request: Request, response: Response) -> dict:
    email = str(body.email).lower()
    key = f"{_client_key(request)}:{email}"
    if not security.login_throttle.allow(key) or security.login_failures.blocked(email):
        raise HTTPException(
            429, "Too many failed login attempts. Please wait a moment.",
            headers={"Retry-After": str(max(security.login_throttle.retry_after(key),
                                            security.login_failures.retry_after(email)))})
    user = accounts.authenticate(email, body.password)
    if user is None:
        security.login_failures.allow(email)
        raise HTTPException(401, "Incorrect email or password.")
    return _login_response(response, user)


@app.post("/api/auth/refresh")
async def refresh(request: Request, response: Response) -> dict:
    user = _user_from_cookie(request, REFRESH_COOKIE, "refresh")
    if user is None:
        raise HTTPException(401, "Your session has expired, please sign in again.")
    return _login_response(response, user)


@app.post("/api/auth/logout")
async def logout(response: Response) -> dict:
    for name in (ACCESS_COOKIE, REFRESH_COOKIE):
        response.delete_cookie(name, path="/")
    return {"ok": True}


# -------------------------------------------------------------------- hesap API
@app.get("/api/me")
async def me(user: dict = Depends(current_user)) -> dict:
    return {**accounts.public(user), "stats": store.stats(user["id"])}


@app.post("/api/me/password")
async def change_password(body: PasswordChangeIn, response: Response,
                          user: dict = Depends(current_user)) -> dict:
    if not security.verify_password(user["password_hash"], body.current_password):
        raise HTTPException(400, "Current password is incorrect.")
    accounts.set_password(user["id"], body.new_password)
    # Diğer cihazlardaki oturumlar düşer; bu tarayıcıya yeni çerez verilir.
    accounts.bump_session_epoch(user["id"])
    _login_response(response, accounts.by_id(user["id"]))
    return {"ok": True, "sessions_revoked": True}


# ------------------------------------------------- şifre sıfırlama ve doğrulama
def _send_verification(user: dict) -> None:
    token = accounts.issue_auth_token(user["id"], "verify",
                                      timedelta(hours=VERIFY_TOKEN_HOURS))
    mailer.send_email_verification(user["email"], token, VERIFY_TOKEN_HOURS)


@app.post("/api/auth/forgot")
async def forgot_password(body: ForgotPasswordIn, request: Request) -> dict:
    """Her durumda aynı yanıtı verir.

    "Bu e-posta kayıtlı değil" demek, bir adresin sistemde olup olmadığını herkese
    sorulabilir hale getirir; hekimlerin kullandığı bir üründe bu tek başına bir
    mahremiyet sızıntısıdır.

    Bağlantı yerine doğrudan giriş yapılabilir bir geçici şifre gönderilir; kullanıcı
    onunla giriş yapıp hesabından kalıcı bir şifre belirler (`must_change_password`)."""
    if not security.reset_throttle.allow(_client_key(request)):
        raise HTTPException(429, "Too many reset requests. Please wait a moment.")
    user = accounts.by_email(str(body.email))
    if user is not None:
        temp_password = accounts.issue_temp_password(user["id"])
        mailer.send_temp_password(user["email"], temp_password)
    return {"ok": True, "message": "If this address is registered, a temporary password has been sent."}


@app.post("/api/auth/verify")
async def verify_email(body: VerifyEmailIn) -> dict:
    user_id = accounts.consume_auth_token(body.token, "verify")
    if user_id is None:
        raise HTTPException(400, "This verification link is invalid or has expired.")
    accounts.mark_email_verified(user_id)
    return {"ok": True}


@app.post("/api/auth/resend-verification")
async def resend_verification(request: Request, user: dict = Depends(current_user)) -> dict:
    if user.get("email_verified_at"):
        return {"ok": True, "already_verified": True}
    if not security.reset_throttle.allow(_client_key(request)):
        raise HTTPException(429, "Too many requests. Please wait a moment.")
    _send_verification(user)
    return {"ok": True}


@app.post("/api/me/ncbi-key")
async def save_ncbi_key(body: NcbiKeyIn, user: dict = Depends(current_user)) -> dict:
    accounts.set_ncbi_key(user["id"], body.api_key)
    return {"ok": True, "has_ncbi_key": bool(body.api_key)}


@app.get("/api/me/ledger")
async def my_ledger(user: dict = Depends(current_user), limit: int = 50) -> list[dict]:
    return accounts.ledger(user["id"], min(max(limit, 1), 200))


@app.get("/api/plans")
async def plans() -> dict:
    return {"plans": PLANS}


# --------------------------------------------------------------------- arama
def _worker(job_id: str, req: SearchRequest, user_id: int, reserved: int) -> None:
    events = _jobs[job_id]["events"]

    def progress(message: str, pct: float) -> None:
        events.put({"type": "progress", "message": message, "pct": round(pct, 3)})

    charged = 0
    try:
        result = run_search(req, progress=progress, cancel=lambda: job_id in _cancelled)
        report_id = store.save_report(result, req.language, user_id)
        result["report_id"] = report_id

        # Önbellekten dönen sonuç hiçbir model çağrısı üretmez, ücretsizdir. Soruyu
        # yanıtlayan makale bulunamadığında da ücret alınmaz: kullanıcı cevap almadı.
        if not result.get("cached") and result.get("answer_mode") != "none":
            # Kredi, aramanın gerçekten harcadığı token maliyetinden hesaplanır
            # (bkz. meter.py); rezerve edilen tavanla arasındaki fark iade edilir.
            charged = int(result.get("usage", {}).get("credits", 0))
        result["credits_charged"] = charged
        accounts.settle(user_id, reserved, charged, "search", report_id)

        user = accounts.by_id(user_id)
        result["credits_left"] = accounts.balance(user) if user else None
        events.put({"type": "done", "result": result})
    except SearchCancelled:                        # iptal edilen tarama ücretlendirilmez
        accounts.settle(user_id, reserved, 0, "search cancelled")
        events.put({"type": "cancelled"})
    except Exception as exc:
        accounts.settle(user_id, reserved, 0, "search failed")
        # Ham istisna metni kullanıcıya gönderilmez: sağlayıcı hataları istek URL'sini
        # (ve içindeki API anahtarını) ya da iç ayrıntıları taşıyabilir.
        if isinstance(exc, ValueError):
            message = str(exc)
        else:
            traceback.print_exc()
            message = "The search could not be completed. Please try again in a moment."
        events.put({"type": "error", "message": message})
    finally:
        events.put(None)
        _cancelled.discard(job_id)
        if job_id in _jobs:
            _jobs[job_id]["finished_at"] = time.monotonic()


@app.post("/api/search")
async def start_search(body: SearchIn, request: Request,
                       user: dict = Depends(current_user)) -> dict:
    if not security.search_throttle.allow(str(user["id"])):
        raise HTTPException(429, "You're searching too fast, please slow down a moment.")
    if REQUIRE_EMAIL_VERIFICATION and not user.get("email_verified_at") \
            and not accounts.is_admin(user):
        raise HTTPException(403, "Please verify your email address before searching. "
                                 "You can request a new verification link from your "
                                 "account page.")

    # Akışı hiç okunmamış bitmiş işler bellekte birikmesin.
    now = time.monotonic()
    for old_id, job in list(_jobs.items()):
        if job.get("finished_at") and now - job["finished_at"] > _ORPHAN_JOB_SECONDS:
            _jobs.pop(old_id, None)
    active = sum(1 for j in _jobs.values()
                 if j["user_id"] == user["id"] and not j.get("finished_at"))
    if active >= MAX_ACTIVE_JOBS_PER_USER:
        raise HTTPException(429, "You already have searches running. Please wait for "
                                 "them to finish.")

    payload = body.model_dump()
    # Ücretsiz katmanda tam metin okuma kapalıdır — en pahalı adım budur.
    if not accounts.public(user)["fulltext_allowed"]:
        payload["use_fulltext"] = False

    allowed = accounts.allowed_modes(user)
    if payload.get("power_mode") not in allowed:
        raise HTTPException(403, "Your plan does not include this power mode. "
                                 "Upgrade your plan to run high-power searches.")
    req = SearchRequest.from_dict(payload)

    # Kredi aramaya başlamadan bloke edilir; gerçek maliyet belli olunca fark iade
    # edilir. Okuyup sonra düşmek, eşzamanlı aramalarda bakiyenin aşılmasına izin verirdi.
    needed = accounts.max_cost_of(req)
    if not accounts.reserve(user["id"], needed):
        left = accounts.balance(user)
        raise HTTPException(402, f"This search needs {needed} credits, you have "
                                 f"{left} left. You can upgrade your plan or "
                                 f"buy extra credits.")

    job_id = uuid.uuid4().hex[:12]
    _jobs[job_id] = {"events": queue.Queue(), "user_id": user["id"], "finished_at": None}
    threading.Thread(target=_worker, args=(job_id, req, user["id"], needed),
                     daemon=True).start()
    return {"job_id": job_id, "estimated_credits": accounts.estimated_cost_of(req),
            "reserved_credits": needed}


def _own_job(job_id: str, user: dict) -> dict:
    job = _jobs.get(job_id)
    if job is None:
        raise HTTPException(404, "Search not found.")
    if job["user_id"] != user["id"] and not accounts.is_admin(user):
        raise HTTPException(403, "This search does not belong to you.")
    return job


@app.post("/api/cancel/{job_id}")
async def cancel_search(job_id: str, user: dict = Depends(current_user)) -> dict:
    _own_job(job_id, user)
    _cancelled.add(job_id)
    return {"ok": True, "cancelled": job_id}


@app.get("/api/stream/{job_id}")
async def stream(job_id: str, user: dict = Depends(current_user)) -> StreamingResponse:
    events = _own_job(job_id, user)["events"]

    async def generator():
        loop = asyncio.get_event_loop()
        while True:
            event = await loop.run_in_executor(None, events.get)
            if event is None:
                break
            yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"
        _jobs.pop(job_id, None)

    return StreamingResponse(generator(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


# ------------------------------------------------------------------- geçmiş
@app.get("/api/history")
async def history(limit: int = 50, user: dict = Depends(current_user)) -> list[dict]:
    return store.list_reports(user["id"], min(max(limit, 1), 200))


@app.get("/api/report/{report_id}")
async def report(report_id: int, user: dict = Depends(current_user)) -> dict:
    data = store.get_report(report_id, user["id"])
    if not data:
        raise HTTPException(404, "Report not found.")
    data["report_id"] = report_id
    return data


@app.delete("/api/report/{report_id}")
async def remove_report(report_id: int, user: dict = Depends(current_user)) -> dict:
    store.delete_report(report_id, user["id"])
    return {"ok": True}


# ----------------------------------------------------------------- kütüphane
@app.get("/api/library")
async def library(user: dict = Depends(current_user)) -> list[dict]:
    return store.list_library(user["id"])


@app.post("/api/library")
async def library_add(body: LibraryIn, user: dict = Depends(current_user)) -> dict:
    added = store.add_to_library(body.article, body.tag, user_id=user["id"])
    return {"ok": True, "added": added}


@app.delete("/api/library/{item_id}")
async def library_remove(item_id: int, user: dict = Depends(current_user)) -> dict:
    store.remove_from_library(item_id, user["id"])
    return {"ok": True}


# ------------------------------------------------------------- dışa aktarma
@app.post("/api/export/{fmt}")
async def export(fmt: str, body: ExportIn, user: dict = Depends(current_user)) -> Any:
    if body.report_id is None:
        raise HTTPException(400, "No report specified to export.")
    result = store.get_report(body.report_id, user["id"])
    if not result:
        raise HTTPException(404, "Report not found.")
    articles = result.get("articles", [])

    binary = {
        "docx": (exporters.to_docx,
                 "application/vnd.openxmlformats-officedocument.wordprocessingml.document"),
        "pdf": (to_pdf, "application/pdf"),
    }
    if fmt in binary:
        build, media_type = binary[fmt]
        return StreamingResponse(
            io.BytesIO(build(result)), media_type=media_type,
            headers={"Content-Disposition":
                     exporters.content_disposition(exporters.filename(result, fmt))})

    mapping = {
        "ris": (exporters.to_ris(articles), "application/x-research-info-systems", "ris"),
        "bib": (exporters.to_bibtex(articles), "application/x-bibtex", "bib"),
        "md": (exporters.to_markdown(result), "text/markdown", "md"),
        "csv": ("﻿" + exporters.to_csv(articles), "text/csv", "csv"),
    }
    if fmt not in mapping:
        raise HTTPException(400, "Unknown export format.")
    body_text, media, ext = mapping[fmt]
    return PlainTextResponse(
        body_text, media_type=f"{media}; charset=utf-8",
        headers={"Content-Disposition":
                 exporters.content_disposition(exporters.filename(result, ext))})


# -------------------------------------------------------------------- yönetim
@app.get("/api/admin/status")
async def admin_status(_: dict = Depends(require_admin)) -> dict:
    return {
        "gemini_keys": len(GEMINI_API_KEYS),
        "gemini_model": GEMINI_MODEL,
        "gemini_fast_model": GEMINI_FAST_MODEL,
        "ncbi_email": bool(NCBI_EMAIL),
        "ncbi_api_key": bool(NCBI_API_KEY),
        # Veritabanı geçici diskteyse hesaplar her yeniden başlatmada silinir;
        # bunu fark etmenin tek yolu bakmaktır, uygulama hata vermez.
        "database": {"path": str(DB_PATH), "ephemeral": DB_IS_EPHEMERAL,
                     "users": accounts.user_count()},
        "stats": store.stats(),
        "cache": cache.stats(),
    }


@app.get("/api/admin/users")
async def admin_users(_: dict = Depends(require_admin), limit: int = 200) -> list[dict]:
    rows = store.conn().execute(
        "SELECT id FROM users ORDER BY id LIMIT ?", (min(max(limit, 1), 500),)).fetchall()
    return [accounts.public(accounts.by_id(r["id"])) for r in rows]


@app.post("/api/admin/grant")
async def admin_grant(body: GrantIn, _: dict = Depends(require_admin)) -> dict:
    if accounts.by_id(body.user_id) is None:
        raise HTTPException(404, "User not found.")
    accounts.grant(body.user_id, body.amount, body.reason)
    return {"ok": True, "credits_left": accounts.balance(accounts.by_id(body.user_id))}


@app.delete("/api/admin/cache")
async def cache_clear(_: dict = Depends(require_admin)) -> dict:
    return {"ok": True, "removed": cache.clear()}


# --------------------------------------------------------------------- sayfalar
def _page(name: str) -> FileResponse:
    return FileResponse(WEB_DIR / name, headers={"Cache-Control": "no-cache"})


@app.get("/")
async def index() -> FileResponse:
    return _page("index.html")


@app.get("/tanitim")
async def intro_page() -> FileResponse:
    return _page("intro.html")


@app.get("/giris")
async def auth_page() -> FileResponse:
    return _page("auth.html")


@app.get("/hesap")
async def account_page() -> FileResponse:
    return _page("account.html")


@app.get("/sifre-sifirla")
async def reset_page() -> FileResponse:
    return _page("reset.html")


@app.get("/eposta-dogrula")
async def verify_page() -> FileResponse:
    return _page("verify.html")


app.mount("/static", StaticFiles(directory=WEB_DIR), name="static")


def main() -> None:
    # Canlıda sunucu 0.0.0.0'a ve platformun verdiği porta bağlanır; yerelde eski
    # davranış (127.0.0.1:8765 ve tarayıcıyı aç) korunur.
    host = os.getenv("HOST", "127.0.0.1")
    port = int(os.getenv("PORT", "8765"))
    if os.getenv("OPEN_BROWSER", "1").strip() not in ("0", "false", "False"):
        threading.Timer(1.2, lambda: webbrowser.open(f"http://{host}:{port}")).start()
    print(f"\n  {APP_NAME} is running:  http://{host}:{port}\n  Press Ctrl+C to stop\n")
    uvicorn.run(app, host=host, port=port, log_level="warning")


if __name__ == "__main__":
    main()
