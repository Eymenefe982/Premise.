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
import uuid
import webbrowser
from datetime import timedelta
from typing import Any

import uvicorn
from fastapi import Depends, FastAPI, HTTPException, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, PlainTextResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

from litrag import accounts, cache, exporters, mailer, security, store
from litrag.config import (ACCESS_TOKEN_HOURS, ALLOWED_ORIGINS, APP_NAME, COOKIE_SECURE,
                           GEMINI_API_KEYS, GEMINI_MODEL, GROQ_API_KEY, NCBI_API_KEY,
                           NCBI_EMAIL, PLANS, REFRESH_TOKEN_DAYS, REQUIRE_EMAIL_VERIFICATION,
                           RESET_TOKEN_MINUTES, VERIFY_TOKEN_HOURS, WEB_DIR)
from litrag.pdf import to_pdf
from litrag.pipeline import SearchCancelled, SearchRequest, run_search
from litrag.schemas import (ExportIn, ForgotPasswordIn, GrantIn, LibraryIn, LoginIn,
                            NcbiKeyIn, PasswordChangeIn, ResetPasswordIn, SearchIn,
                            SignupIn, VerifyEmailIn)

app = FastAPI(title=APP_NAME, docs_url=None, redoc_url=None)

if ALLOWED_ORIGINS:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=ALLOWED_ORIGINS,
        allow_credentials=True,
        allow_methods=["GET", "POST", "DELETE"],
        allow_headers=["Content-Type"],
    )

@app.on_event("startup")
def _init_db() -> None:
    # uvicorn'un `app:app` ile doğrudan başlatıldığı ortamlarda (ör. Render) `main()`
    # hiç çalışmaz; şema kurulumu bu yüzden burada, ASGI startup event'inde yapılır.
    accounts.init()
    removed = cache.purge_expired()
    if removed:
        print(f"  {removed} expired cache entries removed")
    stale = accounts.purge_expired_tokens()
    if stale:
        print(f"  {stale} expired auth tokens removed")
    if not mailer.configured():
        print("  ! SMTP yapılandırılmamış: şifre sıfırlama e-postaları konsola yazılır.")


ACCESS_COOKIE = "premise_session"
REFRESH_COOKIE = "premise_refresh"

# job_id -> {"events": Queue, "user_id": int}
_jobs: dict[str, dict] = {}
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
        raise HTTPException(401, "Oturum bulunamadı, lütfen giriş yapın.")
    return accounts.roll_period(user)


def require_admin(user: dict = Depends(current_user)) -> dict:
    if not accounts.is_admin(user):
        raise HTTPException(403, "Bu işlem için yönetici yetkisi gerekir.")
    return user


def _client_key(request: Request) -> str:
    return request.client.host if request.client else "unknown"


# ------------------------------------------------------------------ kimlik API
@app.post("/api/auth/signup")
async def signup(body: SignupIn, request: Request, response: Response) -> dict:
    if not security.signup_throttle.allow(_client_key(request)):
        raise HTTPException(429, "Çok fazla kayıt denemesi. Bir süre sonra tekrar deneyin.")
    try:
        user = accounts.create_user(str(body.email), body.password, body.role,
                                    consented=body.kvkk_consent)
    except ValueError as exc:
        raise HTTPException(409, str(exc))
    _send_verification(user)
    return _login_response(response, user)


@app.post("/api/auth/login")
async def login(body: LoginIn, request: Request, response: Response) -> dict:
    key = f"{_client_key(request)}:{body.email}"
    if not security.login_throttle.allow(key):
        raise HTTPException(
            429, "Çok fazla başarısız giriş denemesi. Lütfen biraz bekleyin.",
            headers={"Retry-After": str(security.login_throttle.retry_after(key))})
    user = accounts.authenticate(str(body.email), body.password)
    if user is None:
        raise HTTPException(401, "E-posta veya şifre hatalı.")
    return _login_response(response, user)


@app.post("/api/auth/refresh")
async def refresh(request: Request, response: Response) -> dict:
    user = _user_from_cookie(request, REFRESH_COOKIE, "refresh")
    if user is None:
        raise HTTPException(401, "Oturum süresi doldu, lütfen tekrar giriş yapın.")
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
        raise HTTPException(400, "Mevcut şifre hatalı.")
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
    mahremiyet sızıntısıdır."""
    if not security.reset_throttle.allow(_client_key(request)):
        raise HTTPException(429, "Çok fazla sıfırlama isteği. Lütfen biraz bekleyin.")
    user = accounts.by_email(str(body.email))
    if user is not None:
        token = accounts.issue_auth_token(user["id"], "reset",
                                          timedelta(minutes=RESET_TOKEN_MINUTES))
        mailer.send_password_reset(user["email"], token, RESET_TOKEN_MINUTES)
    return {"ok": True, "message": "Bu adres kayıtlıysa sıfırlama bağlantısı gönderildi."}


@app.post("/api/auth/reset")
async def reset_password(body: ResetPasswordIn, request: Request,
                         response: Response) -> dict:
    if not security.reset_throttle.allow(_client_key(request)):
        raise HTTPException(429, "Çok fazla deneme. Lütfen biraz bekleyin.")
    user_id = accounts.consume_auth_token(body.token, "reset")
    if user_id is None:
        raise HTTPException(400, "Bağlantı geçersiz ya da süresi dolmuş. "
                                 "Yeni bir sıfırlama bağlantısı isteyin.")
    accounts.set_password(user_id, body.new_password)
    # Sıfırlamanın asıl amacı hesabı geri almaktır: eski oturumlar kapatılmalı.
    accounts.bump_session_epoch(user_id)
    user = accounts.by_id(user_id)
    # Şifresini sıfırlayabilen kişi zaten e-posta kutusuna erişmiştir.
    if user is not None and not user.get("email_verified_at"):
        accounts.mark_email_verified(user_id)
        user = accounts.by_id(user_id)
    return _login_response(response, user)


@app.post("/api/auth/verify")
async def verify_email(body: VerifyEmailIn) -> dict:
    user_id = accounts.consume_auth_token(body.token, "verify")
    if user_id is None:
        raise HTTPException(400, "Doğrulama bağlantısı geçersiz ya da süresi dolmuş.")
    accounts.mark_email_verified(user_id)
    return {"ok": True}


@app.post("/api/auth/resend-verification")
async def resend_verification(request: Request, user: dict = Depends(current_user)) -> dict:
    if user.get("email_verified_at"):
        return {"ok": True, "already_verified": True}
    if not security.reset_throttle.allow(_client_key(request)):
        raise HTTPException(429, "Çok fazla istek. Lütfen biraz bekleyin.")
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
            charged = accounts.cost_of(len(result.get("articles", [])),
                                       result.get("fulltext_count", 0),
                                       bool(result.get("report")),
                                       verified=bool(result.get("triaged")))
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
        events.put({"type": "error", "message": str(exc)})
    finally:
        events.put(None)
        _cancelled.discard(job_id)


@app.post("/api/search")
async def start_search(body: SearchIn, request: Request,
                       user: dict = Depends(current_user)) -> dict:
    if not security.search_throttle.allow(str(user["id"])):
        raise HTTPException(429, "Çok hızlı arama yapıyorsunuz, lütfen biraz bekleyin.")
    if REQUIRE_EMAIL_VERIFICATION and not user.get("email_verified_at") \
            and not accounts.is_admin(user):
        raise HTTPException(403, "Arama yapmadan önce e-posta adresinizi doğrulayın. "
                                 "Doğrulama bağlantısını hesap sayfanızdan yeniden "
                                 "isteyebilirsiniz.")

    payload = body.model_dump()
    # Ücretsiz katmanda tam metin okuma kapalıdır — en pahalı adım budur.
    if not accounts.public(user)["fulltext_allowed"]:
        payload["use_fulltext"] = False
    req = SearchRequest.from_dict(payload)

    # Kredi aramaya başlamadan bloke edilir; gerçek maliyet belli olunca fark iade
    # edilir. Okuyup sonra düşmek, eşzamanlı aramalarda bakiyenin aşılmasına izin verirdi.
    needed = accounts.max_cost_of(req)
    if not accounts.reserve(user["id"], needed):
        left = accounts.balance(user)
        raise HTTPException(402, f"Bu arama için {needed} kredi gerekiyor, "
                                 f"{left} krediniz kaldı. Planınızı yükseltebilir "
                                 f"veya ek kredi alabilirsiniz.")

    job_id = uuid.uuid4().hex[:12]
    _jobs[job_id] = {"events": queue.Queue(), "user_id": user["id"]}
    threading.Thread(target=_worker, args=(job_id, req, user["id"], needed),
                     daemon=True).start()
    return {"job_id": job_id, "estimated_credits": needed}


def _own_job(job_id: str, user: dict) -> dict:
    job = _jobs.get(job_id)
    if job is None:
        raise HTTPException(404, "Arama bulunamadı.")
    if job["user_id"] != user["id"] and not accounts.is_admin(user):
        raise HTTPException(403, "Bu arama size ait değil.")
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
        raise HTTPException(404, "Rapor bulunamadı.")
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
        raise HTTPException(400, "Dışa aktarılacak rapor belirtilmedi.")
    result = store.get_report(body.report_id, user["id"])
    if not result:
        raise HTTPException(404, "Rapor bulunamadı.")
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
        raise HTTPException(400, "Bilinmeyen dışa aktarma biçimi.")
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
        "groq_fallback": bool(GROQ_API_KEY),
        "ncbi_email": bool(NCBI_EMAIL),
        "ncbi_api_key": bool(NCBI_API_KEY),
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
        raise HTTPException(404, "Kullanıcı bulunamadı.")
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
