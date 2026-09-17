"""Kullanıcı hesapları, planlar ve kredi defteri."""
from __future__ import annotations

import secrets
import sqlite3
import threading
from datetime import datetime, timedelta

from cryptography.fernet import Fernet

from . import security
from .config import (ADMIN_EMAIL, ADMIN_PASSWORD, CREDIT_BASE, CREDIT_PER_ARTICLE,
                     CREDIT_PER_FULLTEXT, CREDIT_SYNTHESIS, CREDIT_VERIFY, JWT_SECRET,
                     LOVE_EMAIL, PLANS, SECRET_KEY)
from .store import conn, migrate_library

_lock = threading.Lock()
PERIOD_DAYS = 30
TEMP_PASSWORD_LIFETIME = timedelta(hours=1)

SCHEMA = """
CREATE TABLE IF NOT EXISTS app_settings (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    email TEXT NOT NULL UNIQUE,
    password_hash TEXT NOT NULL,
    role TEXT NOT NULL DEFAULT 'physician',
    plan TEXT NOT NULL DEFAULT 'free',
    credits_used INTEGER NOT NULL DEFAULT 0,
    credits_extra INTEGER NOT NULL DEFAULT 0,
    period_start TEXT NOT NULL,
    ncbi_key_enc TEXT NOT NULL DEFAULT '',
    kvkk_consent_at TEXT,
    created_at TEXT NOT NULL,
    last_login_at TEXT
);
CREATE TABLE IF NOT EXISTS credit_ledger (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    created_at TEXT NOT NULL,
    amount INTEGER NOT NULL,
    reason TEXT NOT NULL,
    report_id INTEGER
);
CREATE INDEX IF NOT EXISTS idx_ledger_user ON credit_ledger(user_id, id DESC);
CREATE TABLE IF NOT EXISTS auth_tokens (
    fingerprint TEXT PRIMARY KEY,      -- jetonun kendisi değil, SHA-256 özeti
    user_id INTEGER NOT NULL,
    kind TEXT NOT NULL,                -- reset | verify
    expires_at TEXT NOT NULL,
    used_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_tokens_user ON auth_tokens(user_id, kind);
"""


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _column_names(table: str) -> set[str]:
    return {r["name"] for r in conn().execute(f"PRAGMA table_info({table})").fetchall()}


def init() -> None:
    """Şemayı kurar, eski tabloları göç ettirir ve yönetici hesabını tohumlar."""
    with _lock:
        c = conn()
        c.executescript(SCHEMA)
        # Tek kullanıcılı sürümden kalan kayıtlar yöneticiye devredilir.
        for table in ("reports", "library"):
            if "user_id" not in _column_names(table):
                c.execute(f"ALTER TABLE {table} ADD COLUMN user_id INTEGER NOT NULL DEFAULT 1")
        user_columns = _column_names("users")
        # session_epoch: şifre değişince artar, o ana kadarki bütün oturumları düşürür.
        if "session_epoch" not in user_columns:
            c.execute("ALTER TABLE users ADD COLUMN session_epoch INTEGER NOT NULL DEFAULT 0")
        if "email_verified_at" not in user_columns:
            c.execute("ALTER TABLE users ADD COLUMN email_verified_at TEXT")
        # Geçici şifreyle giren kullanıcı kalıcı bir şifre belirleyene kadar işaretli kalır.
        if "must_change_password" not in user_columns:
            c.execute("ALTER TABLE users ADD COLUMN must_change_password INTEGER NOT NULL DEFAULT 0")
        c.commit()
    migrate_library()
    with _lock:
        conn().execute("CREATE INDEX IF NOT EXISTS idx_reports_user ON reports(user_id, id DESC)")
        conn().execute("CREATE INDEX IF NOT EXISTS idx_library_user ON library(user_id, id DESC)")
        conn().commit()
    _seed_admin()


# --------------------------------------------------------------- uygulama sırları
def app_secret(name: str, default: str = "") -> str:
    """Ortam değişkeni verilmişse onu, yoksa veritabanında saklanan üretilmiş sırrı verir."""
    if default:
        return default
    with _lock:
        row = conn().execute("SELECT value FROM app_settings WHERE key = ?", (name,)).fetchone()
        if row:
            return row["value"]
        value = (Fernet.generate_key().decode() if name == "secret_key"
                 else secrets.token_urlsafe(48))
        conn().execute("INSERT INTO app_settings (key, value) VALUES (?,?)", (name, value))
        conn().commit()
        return value


def jwt_secret() -> str:
    return app_secret("jwt_secret", JWT_SECRET)


def field_key() -> str:
    return app_secret("secret_key", SECRET_KEY)


# --------------------------------------------------------------------- kullanıcılar
def _row_to_user(row: sqlite3.Row | None) -> dict | None:
    return dict(row) if row else None


def by_id(user_id: int) -> dict | None:
    return _row_to_user(conn().execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone())


def by_email(email: str) -> dict | None:
    return _row_to_user(conn().execute(
        "SELECT * FROM users WHERE email = ?", (email.strip().lower(),)).fetchone())


def create_user(email: str, password: str, role: str, plan: str = "free",
                consented: bool = False) -> dict:
    email = email.strip().lower()
    with _lock:
        try:
            cur = conn().execute(
                "INSERT INTO users (email, password_hash, role, plan, period_start,"
                " kvkk_consent_at, created_at) VALUES (?,?,?,?,?,?,?)",
                (email, security.hash_password(password), role, plan, _now(),
                 _now() if consented else None, _now()),
            )
            conn().commit()
        except sqlite3.IntegrityError:
            raise ValueError("This account is already registered. Please sign in instead.")
    return by_id(int(cur.lastrowid))


def authenticate(email: str, password: str) -> dict | None:
    user = by_email(email)
    if not user:
        security.burn_password_check(password)
        return None
    if security.verify_password(user["password_hash"], password):
        if security.needs_rehash(user["password_hash"]):
            set_password(user["id"], password)
    elif not _redeem_temp_password(user["id"], password):
        return None
    with _lock:
        conn().execute("UPDATE users SET last_login_at = ? WHERE id = ?", (_now(), user["id"]))
        conn().commit()
    return by_id(user["id"])


def bump_session_epoch(user_id: int) -> None:
    """Hesabın bütün açık oturumlarını düşürür (şifre değişimi ve sıfırlama sonrası).

    Şifre değişip eski jetonlar geçerli kalırsa, hesabı ele geçiren birini dışarı
    atmanın yolu olmaz: asıl senaryo tam da budur."""
    with _lock:
        conn().execute("UPDATE users SET session_epoch = session_epoch + 1 WHERE id = ?",
                       (user_id,))
        conn().commit()


def set_password(user_id: int, password: str) -> None:
    with _lock:
        conn().execute(
            "UPDATE users SET password_hash = ?, must_change_password = 0 WHERE id = ?",
            (security.hash_password(password), user_id))
        conn().commit()


def issue_temp_password(user_id: int) -> str:
    """"Şifremi unuttum" akışı: tek seferlik, kısa ömürlü geçici bir şifre üretir.

    Mevcut şifreye DOKUNULMAZ. Önceden şifre isteğin anında değiştiriliyordu; bu,
    e-posta adresini bilen herkesin hesap sahibini dışarıda bırakıp oturumlarını
    düşürebilmesi demekti. Geçici şifre ancak kullanıldığında kalıcı şifrenin yerine
    geçer (bkz. `_redeem_temp_password`)."""
    temp_password = secrets.token_urlsafe(12)
    expires = (datetime.now() + TEMP_PASSWORD_LIFETIME).strftime("%Y-%m-%d %H:%M:%S")
    with _lock:
        conn().execute("DELETE FROM auth_tokens WHERE user_id = ? AND kind = 'temp'",
                       (user_id,))
        conn().execute(
            "INSERT INTO auth_tokens (fingerprint, user_id, kind, expires_at)"
            " VALUES (?,?,'temp',?)",
            (security.token_fingerprint(temp_password), user_id, expires))
        conn().commit()
    return temp_password


def _redeem_temp_password(user_id: int, password: str) -> bool:
    """Geçici şifre geçerliyse onu kalıcı şifre yapar, `must_change_password` işaretler
    ve diğer cihazlardaki oturumları düşürür."""
    fingerprint = security.token_fingerprint(password)
    with _lock:
        row = conn().execute(
            "SELECT expires_at, used_at FROM auth_tokens"
            " WHERE fingerprint = ? AND kind = 'temp' AND user_id = ?",
            (fingerprint, user_id)).fetchone()
        if row is None or row["used_at"]:
            return False
        if datetime.strptime(row["expires_at"], "%Y-%m-%d %H:%M:%S") < datetime.now():
            return False
        conn().execute("DELETE FROM auth_tokens WHERE user_id = ? AND kind = 'temp'",
                       (user_id,))
        conn().execute(
            "UPDATE users SET password_hash = ?, must_change_password = 1,"
            " session_epoch = session_epoch + 1 WHERE id = ?",
            (security.hash_password(password), user_id))
        conn().commit()
    return True


def _seed_admin() -> None:
    if not ADMIN_EMAIL or not ADMIN_PASSWORD:
        return
    if by_email(ADMIN_EMAIL):
        return
    with _lock:
        empty = conn().execute("SELECT COUNT(*) FROM users").fetchone()[0] == 0
        conn().execute(
            "INSERT INTO users (id, email, password_hash, role, plan, period_start,"
            " kvkk_consent_at, created_at) VALUES (?,?,?,?,?,?,?,?)",
            (1 if empty else None, ADMIN_EMAIL, security.hash_password(ADMIN_PASSWORD),
             "admin", "pro", _now(), _now(), _now()),
        )
        conn().commit()
    print(f"  [accounts] yönetici hesabı oluşturuldu: {ADMIN_EMAIL}")


# ------------------------------------------------------------------ NCBI anahtarı
def set_ncbi_key(user_id: int, key: str) -> None:
    blob = security.encrypt(field_key(), key.strip()) if key.strip() else ""
    with _lock:
        conn().execute("UPDATE users SET ncbi_key_enc = ? WHERE id = ?", (blob, user_id))
        conn().commit()


def ncbi_key(user: dict) -> str:
    return security.decrypt(field_key(), user.get("ncbi_key_enc") or "")


# ------------------------------------------------------------------------ krediler
def is_admin(user: dict) -> bool:
    return user.get("role") == "admin"


def is_love(user: dict) -> bool:
    """Kalp rozetli özel hesap. E-posta doğrulanmadan tanınmaz."""
    return bool(LOVE_EMAIL) and (user.get("email") or "").lower() == LOVE_EMAIL         and bool(user.get("email_verified_at"))


def is_unlimited(user: dict) -> bool:
    return is_admin(user) or is_love(user)


def plan_of(user: dict) -> dict:
    return PLANS.get(user.get("plan") or "free", PLANS["free"])


def roll_period(user: dict) -> dict:
    """Dönem dolduysa aylık kredi kullanımını sıfırlar."""
    started = datetime.strptime(user["period_start"], "%Y-%m-%d %H:%M:%S")
    if datetime.now() - started < timedelta(days=PERIOD_DAYS):
        return user
    with _lock:
        conn().execute("UPDATE users SET credits_used = 0, period_start = ? WHERE id = ?",
                       (_now(), user["id"]))
        conn().commit()
    return by_id(user["id"])


def balance(user: dict) -> int | None:
    """Kalan kredi. Yönetici ve özel hesap için sınır yoktur, None döner."""
    if is_unlimited(user):
        return None
    allowance = plan_of(user)["credits"] + int(user["credits_extra"] or 0)
    return max(0, allowance - int(user["credits_used"] or 0))


def cost_of(article_count: int, fulltext_count: int, synthesised: bool,
            verified: bool = False) -> int:
    return (CREDIT_BASE
            + CREDIT_PER_ARTICLE * max(0, article_count)
            + CREDIT_PER_FULLTEXT * max(0, fulltext_count)
            + (CREDIT_SYNTHESIS if synthesised else 0)
            + (CREDIT_VERIFY if verified else 0))


def max_cost_of(req) -> int:
    """Arama başlamadan önceki en kötü ihtimal — rezervasyon bunun üzerinden yapılır."""
    from .config import CLAIM_CHECK_ENABLED, FULLTEXT_TOP_N, TRIAGE_ENABLED
    fulltext = FULLTEXT_TOP_N if req.use_fulltext else 0
    return cost_of(req.max_articles, fulltext, req.synthesize,
                   verified=TRIAGE_ENABLED or CLAIM_CHECK_ENABLED)


def reserve(user_id: int, amount: int) -> bool:
    """Aramaya başlamadan krediyi bloke eder. Yetmiyorsa False döner.

    Bakiyeyi okuyup sonra düşmek yarış durumu yaratır: eşzamanlı iki arama aynı bakiyeyi
    görüp ikisi de kontrolü geçer ve hesap eksiye düşer. Okuma ve düşme tek kilidin
    altında yapılır; gerçek maliyet belli olunca `settle` farkı geri verir.
    """
    user = by_id(user_id)
    if user is None:
        return False
    if is_unlimited(user):
        return True
    with _lock:
        row = conn().execute(
            "SELECT plan, credits_used, credits_extra FROM users WHERE id = ?",
            (user_id,)).fetchone()
        if row is None:
            return False
        allowance = (PLANS.get(row["plan"] or "free", PLANS["free"])["credits"]
                     + int(row["credits_extra"] or 0))
        if int(row["credits_used"] or 0) + amount > allowance:
            return False
        conn().execute("UPDATE users SET credits_used = credits_used + ? WHERE id = ?",
                       (amount, user_id))
        conn().commit()
    return True


def settle(user_id: int, reserved: int, actual: int, reason: str,
           report_id: int | None = None) -> int:
    """Rezerve edilen krediyi gerçek maliyete indirir ve deftere tek satır yazar.

    `actual` sıfır olabilir: iptal edilen, hata veren ya da cevap üretilemeyen arama
    ücretlendirilmez, rezervasyon tamamen geri verilir.
    """
    actual = max(0, actual)
    user = by_id(user_id)
    if user is None:
        return 0
    with _lock:
        if not is_unlimited(user) and reserved != actual:
            conn().execute("UPDATE users SET credits_used = MAX(0, credits_used + ?)"
                           " WHERE id = ?", (actual - reserved, user_id))
        if actual:
            conn().execute(
                "INSERT INTO credit_ledger (user_id, created_at, amount, reason, report_id)"
                " VALUES (?,?,?,?,?)", (user_id, _now(), -actual, reason, report_id))
        conn().commit()
    return actual


def charge(user_id: int, amount: int, reason: str, report_id: int | None = None) -> None:
    """Krediyi düşer ve deftere yazar. Yöneticide yalnızca deftere yazılır."""
    if amount <= 0:
        return
    user = by_id(user_id)
    if user is None:
        return
    with _lock:
        if not is_unlimited(user):
            conn().execute("UPDATE users SET credits_used = credits_used + ? WHERE id = ?",
                           (amount, user_id))
        conn().execute(
            "INSERT INTO credit_ledger (user_id, created_at, amount, reason, report_id)"
            " VALUES (?,?,?,?,?)", (user_id, _now(), -amount, reason, report_id))
        conn().commit()


def grant(user_id: int, amount: int, reason: str) -> None:
    """Satın alınan ya da elle verilen ek kredi."""
    if amount <= 0:
        return
    with _lock:
        conn().execute("UPDATE users SET credits_extra = credits_extra + ? WHERE id = ?",
                       (amount, user_id))
        conn().execute(
            "INSERT INTO credit_ledger (user_id, created_at, amount, reason)"
            " VALUES (?,?,?,?)", (user_id, _now(), amount, reason))
        conn().commit()


def ledger(user_id: int, limit: int = 50) -> list[dict]:
    rows = conn().execute(
        "SELECT created_at, amount, reason, report_id FROM credit_ledger"
        " WHERE user_id = ? ORDER BY id DESC LIMIT ?", (user_id, limit)).fetchall()
    return [dict(r) for r in rows]


# ------------------------------------------------------ tek kullanımlık jetonlar
def issue_auth_token(user_id: int, kind: str, lifetime: timedelta) -> str:
    """Şifre sıfırlama / e-posta doğrulama jetonu üretir ve özetini saklar.

    Aynı türdeki eski jetonlar iptal edilir: kullanıcı üst üste üç kez "şifremi
    unuttum" derse yalnızca son bağlantı çalışsın, gelen kutusunda çalışan üç ayrı
    bağlantı birikmesin.
    """
    raw = security.new_secret_token()
    expires = (datetime.now() + lifetime).strftime("%Y-%m-%d %H:%M:%S")
    with _lock:
        conn().execute("DELETE FROM auth_tokens WHERE user_id = ? AND kind = ?",
                       (user_id, kind))
        conn().execute(
            "INSERT INTO auth_tokens (fingerprint, user_id, kind, expires_at)"
            " VALUES (?,?,?,?)",
            (security.token_fingerprint(raw), user_id, kind, expires))
        conn().commit()
    return raw


def consume_auth_token(raw: str, kind: str) -> int | None:
    """Jetonu tek seferlik harcar; geçerliyse kullanıcı kimliğini döndürür."""
    if not raw:
        return None
    fingerprint = security.token_fingerprint(raw)
    with _lock:
        row = conn().execute(
            "SELECT user_id, expires_at, used_at FROM auth_tokens"
            " WHERE fingerprint = ? AND kind = ?", (fingerprint, kind)).fetchone()
        if row is None or row["used_at"]:
            return None
        if datetime.strptime(row["expires_at"], "%Y-%m-%d %H:%M:%S") < datetime.now():
            return None
        conn().execute("UPDATE auth_tokens SET used_at = ? WHERE fingerprint = ?",
                       (_now(), fingerprint))
        conn().commit()
    return int(row["user_id"])


def purge_expired_tokens() -> int:
    with _lock:
        cursor = conn().execute("DELETE FROM auth_tokens WHERE expires_at < ?", (_now(),))
        conn().commit()
    return cursor.rowcount or 0


def mark_email_verified(user_id: int) -> None:
    with _lock:
        conn().execute("UPDATE users SET email_verified_at = ? WHERE id = ?",
                       (_now(), user_id))
        conn().commit()


# --------------------------------------------------------------------- API görünümü
def public(user: dict) -> dict:
    """Arayüze gönderilebilecek alanlar. Parola özeti ve şifreli anahtar dışarı çıkmaz."""
    plan = plan_of(user)
    return {
        "id": user["id"],
        "email": user["email"],
        "role": user["role"],
        "plan": user["plan"],
        "plan_label": plan["label"],
        "is_admin": is_admin(user),
        "credits_total": None if is_unlimited(user) else plan["credits"] + int(user["credits_extra"] or 0),
        "credits_left": balance(user),
        "unlimited": is_unlimited(user),
        "love": is_love(user),
        "fulltext_allowed": True if is_unlimited(user) else plan["fulltext"],
        "has_ncbi_key": bool(user.get("ncbi_key_enc")),
        "email_verified": bool(user.get("email_verified_at")),
        "must_change_password": bool(user.get("must_change_password")),
        "period_start": user["period_start"],
        "created_at": user["created_at"],
    }
