"""Kullanıcı hesapları, planlar ve kredi defteri."""
from __future__ import annotations

import logging
import secrets
import threading
from datetime import datetime, timedelta

from cryptography.fernet import Fernet

from . import cache, db, meter, modes, security, store
from .config import (ADMIN_EMAIL, ADMIN_PASSWORD, JWT_SECRET, LOVE_EMAIL, PLANS,
                     SECRET_KEY)
from .logging_setup import mask_email
from .store import conn, migrate_library

log = logging.getLogger("premise.accounts")

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
CREATE TABLE IF NOT EXISTS credit_holds (
    job_id TEXT PRIMARY KEY,           -- aramanın iş kimliği
    user_id INTEGER NOT NULL,
    amount INTEGER NOT NULL,           -- rezerve edilen kredi
    instance TEXT NOT NULL,            -- rezervasyonu yapan sunucu süreci
    created_at TEXT NOT NULL
);
"""

# Bu sürecin kimliği. Açılışta başka bir süreçten kalan rezervasyonlar sahipsiz sayılır:
# işler bellekte tutulduğu için o aramaların sonucu hiçbir zaman gelmeyecek.
INSTANCE_ID = secrets.token_hex(6)
# Bundan eski rezervasyon, hangi süreçten gelirse gelsin serbest bırakılır.
HOLD_MAX_AGE = timedelta(minutes=30)


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _column_names(table: str) -> set[str]:
    return conn().column_names(table)


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
        # Önbellek satırı onu oluşturan hesaba bağlanır (hesap silinince silinsin diye).
        # Eski satırların sahibi bilinmez; en fazla CACHE_TTL_DAYS içinde kendiliğinden düşer.
        if "user_id" not in _column_names("search_cache"):
            c.execute("ALTER TABLE search_cache ADD COLUMN user_id INTEGER")
        # Eski sorgu günlüğü: her sorunun metnini sahipsiz ve süresiz tutuyordu. Hiçbir
        # hesaba bağlı olmadığı için silme talebinde ayıklanamıyordu ve tek kullanımı bir
        # sayaçtı; kaldırılır.
        c.execute("DROP TABLE IF EXISTS searches")
        c.commit()
    migrate_library()
    with _lock:
        conn().execute("CREATE INDEX IF NOT EXISTS idx_reports_user ON reports(user_id, id DESC)")
        conn().execute("CREATE INDEX IF NOT EXISTS idx_library_user ON library(user_id, id DESC)")
        conn().execute("CREATE INDEX IF NOT EXISTS idx_cache_user ON search_cache(user_id)")
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
def _row_to_user(row) -> dict | None:
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
        except Exception as exc:
            if not db.is_integrity_error(exc):
                raise
            raise ValueError("Something went wrong. If you already have an account, "
                             "please try signing in.")
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
    return _mark_login(user["id"])


def authenticate_temp_only(email: str, password: str) -> dict | None:
    """Yalnızca e-postayla gönderilen geçici şifreyi kabul eder.

    Hesap başarısız denemeler yüzünden kilitliyken kullanılır. Kilit, başkası
    şifre tahmin etmesin diye vardır; ama saldırgan kurbanın hesabını kasıtlı olarak
    kilitleyip onu dışarıda bırakabiliyordu ("şifremi unuttum" da aynı kapıdan
    geçtiği için kurtuluş yolu yoktu). Geçici şifre 96 bit rastgeledir ve yalnız hesap
    sahibinin gelen kutusuna gider: kilitliyken kabul etmek tahmin kapısını açmaz."""
    user = by_email(email)
    if not user or not _redeem_temp_password(user["id"], password):
        return None
    return _mark_login(user["id"])


def _mark_login(user_id: int) -> dict | None:
    with _lock:
        conn().execute("UPDATE users SET last_login_at = ? WHERE id = ?", (_now(), user_id))
        conn().commit()
    return by_id(user_id)


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
        empty = conn().execute("SELECT COUNT(*) AS n FROM users").fetchone()["n"] == 0
        # Boş tabloda id'yi 1 olarak sabitlemek eski tek-kullanıcılı raporların
        # (user_id=1 varsayılanı) yöneticiye devrini garanti eder. Postgres'te SERIAL
        # sütununa NULL göndermek NOT NULL ihlali olur; bu yüzden id yalnızca
        # gerektiğinde, sütun listesine dahil edilerek gönderilir.
        if empty:
            conn().execute(
                "INSERT INTO users (id, email, password_hash, role, plan, period_start,"
                " kvkk_consent_at, created_at) VALUES (?,?,?,?,?,?,?,?)",
                (1, ADMIN_EMAIL, security.hash_password(ADMIN_PASSWORD),
                 "admin", "pro", _now(), _now(), _now()),
            )
            conn().fix_serial("users")
        else:
            conn().execute(
                "INSERT INTO users (email, password_hash, role, plan, period_start,"
                " kvkk_consent_at, created_at) VALUES (?,?,?,?,?,?,?)",
                (ADMIN_EMAIL, security.hash_password(ADMIN_PASSWORD),
                 "admin", "pro", _now(), _now(), _now()),
            )
        conn().commit()
    log.info("admin account created: %s", mask_email(ADMIN_EMAIL))


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


def user_count() -> int:
    with _lock:
        return conn().execute("SELECT COUNT(*) AS n FROM users").fetchone()["n"]


def allowed_modes(user: dict) -> tuple[str, ...]:
    """Hesabın kullanabileceği güç modları."""
    if is_unlimited(user):
        return modes.POWER_MODES
    return tuple(plan_of(user).get("modes", modes.POWER_MODES))


def estimated_cost_of(req) -> int:
    """Tipik maliyet — kullanıcıya "≈ şu kadar kredi" derken kullanılır."""
    return meter.credits_for(modes.profile(getattr(req, "power_mode", None)).estimate_try)


def max_cost_of(req) -> int:
    """Rezerve edilecek kredi: modun tavan maliyeti.

    Gerçek tutar ancak arama bitince, harcanan token'lardan bilinir; aradaki fark
    `settle` ile iade edilir. Önce bloke etmek eşzamanlı aramalarda bakiyenin
    aşılmasını engeller.
    """
    return meter.credits_for(modes.profile(getattr(req, "power_mode", None)).ceiling_try)


def reserve(user_id: int, amount: int, hold_id: str | None = None) -> bool:
    """Aramaya başlamadan krediyi bloke eder. Yetmiyorsa False döner.

    Bakiyeyi okuyup sonra düşmek yarış durumu yaratır: eşzamanlı iki arama aynı bakiyeyi
    görüp ikisi de kontrolü geçer ve hesap eksiye düşer. Okuma ve düşme tek kilidin
    altında yapılır; gerçek maliyet belli olunca `settle` farkı geri verir.

    `hold_id` verilirse rezervasyon `credit_holds` tablosuna da yazılır. Arama işleri
    bellekte tutulduğu için sunucu yeniden başlarsa `settle` hiç çalışmaz; kayıt olmadan
    rezerve edilen kredi (high modda hesabın tamamı) kalıcı olarak kaybolurdu.
    Sahipsiz kalan rezervasyonları `release_stale_holds` iade eder.
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
        if hold_id:
            conn().execute(
                "INSERT INTO credit_holds (job_id, user_id, amount, instance, created_at)"
                " VALUES (?,?,?,?,?)", (hold_id, user_id, amount, INSTANCE_ID, _now()))
        conn().commit()
    return True


# Kullanılan krediyi sıfırın altına düşürmeden artırır/azaltır. Alt sınır CASE ile
# SQL'de uygulanır: SQLite'ın iki argümanlı MAX(a, b)'sinin Postgres'te karşılığı yok
# (orada MAX bir toplama fonksiyonudur), CASE ise ikisinde de aynı çalışır. Tek ifade
# olduğu için başka bir sürecin eşzamanlı güncellemesi de kaybolmaz.
_ADJUST_USED = ("UPDATE users SET credits_used = CASE WHEN credits_used + ? < 0 THEN 0"
                " ELSE credits_used + ? END WHERE id = ?")


def _adjust_used(user_id: int, delta: int) -> None:
    conn().execute(_ADJUST_USED, (delta, delta, user_id))


def settle(user_id: int, reserved: int, actual: int, reason: str,
           report_id: int | None = None, hold_id: str | None = None) -> int:
    """Rezerve edilen krediyi gerçek maliyete indirir ve deftere tek satır yazar.

    `actual` sıfır olabilir: iptal edilen, hata veren ya da cevap üretilemeyen arama
    ücretlendirilmez, rezervasyon tamamen geri verilir.

    `hold_id`'nin kaydı artık yoksa rezervasyon `release_stale_holds` tarafından zaten
    iade edilmiştir; o durumda iade tekrarlanmaz, yalnızca gerçek maliyet düşülür.
    Kaydı silen taraf (bu fonksiyon ya da serbest bırakma) iadeyi yapan taraftır:
    silme veritabanında tek seferlik olduğu için iade asla iki kez yapılmaz.
    """
    actual = max(0, actual)
    user = by_id(user_id)
    if user is None:
        return 0
    with _lock:
        delta = actual - reserved
        if hold_id:
            released = conn().execute("DELETE FROM credit_holds WHERE job_id = ?",
                                      (hold_id,)).rowcount
            if not released and not is_unlimited(user):
                delta = actual
        if not is_unlimited(user) and delta:
            _adjust_used(user_id, delta)
        if actual:
            conn().execute(
                "INSERT INTO credit_ledger (user_id, created_at, amount, reason, report_id)"
                " VALUES (?,?,?,?,?)", (user_id, _now(), -actual, reason, report_id))
        conn().commit()
    return actual


def release_stale_holds() -> int:
    """Sonucu hiç gelmeyecek rezervasyonların kredisini iade eder.

    Başka bir süreçten kalanlar (sunucu yeniden başladı ya da yeni sürüm yayına alındı)
    ve `HOLD_MAX_AGE`'den eski olanlar serbest bırakılır. Eski süreç aramayı yine de
    bitirirse `settle` kaydı bulamaz ve yalnızca gerçek maliyeti düşer; bakiye her iki
    sırada da doğru kalır.
    """
    cutoff = (datetime.now() - HOLD_MAX_AGE).strftime("%Y-%m-%d %H:%M:%S")
    rows = conn().execute(
        "SELECT job_id, user_id, amount, created_at FROM credit_holds"
        " WHERE instance <> ? OR created_at < ?", (INSTANCE_ID, cutoff)).fetchall()
    released = 0
    for row in rows:
        with _lock:
            if not conn().execute("DELETE FROM credit_holds WHERE job_id = ?",
                                  (row["job_id"],)).rowcount:
                continue                     # arama bu arada kendi hesabını kapattı
            user = by_id(row["user_id"])
            # Dönem o arada sıfırlandıysa rezerve edilen kredi yeni dönemin kullanımında
            # değildir; iade etmek yeni döneme bedava kredi eklemek olurdu.
            if user and user["period_start"] <= row["created_at"]:
                _adjust_used(row["user_id"], -int(row["amount"]))
            conn().commit()
        released += 1
    if released:
        log.info("%d orphaned credit reservation(s) released", released)
    return released


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


def revoke_session_token(user_id: int, jti: str, expires_ts: int) -> None:
    """Çıkış yapılan oturumun yenileme jetonunu süresi dolana kadar geçersiz kılar.

    JWT kendi başına geri alınamaz: çıkışta yalnız çerez silinirse, çalınmış bir kopya
    30 gün boyunca yeni oturum açmaya devam ederdi. Kayıt süresi dolunca
    `purge_expired_tokens` tarafından temizlenir."""
    expires = datetime.fromtimestamp(expires_ts).strftime("%Y-%m-%d %H:%M:%S")
    fingerprint = security.token_fingerprint("jti:" + jti)
    with _lock:
        if conn().execute("SELECT 1 FROM auth_tokens WHERE fingerprint = ?",
                          (fingerprint,)).fetchone():
            return
        conn().execute(
            "INSERT INTO auth_tokens (fingerprint, user_id, kind, expires_at)"
            " VALUES (?,?,'revoked',?)", (fingerprint, user_id, expires))
        conn().commit()


def is_session_token_revoked(jti: str) -> bool:
    return conn().execute(
        "SELECT 1 FROM auth_tokens WHERE fingerprint = ? AND kind = 'revoked'",
        (security.token_fingerprint("jti:" + jti),)).fetchone() is not None


def purge_expired_tokens() -> int:
    with _lock:
        cursor = conn().execute("DELETE FROM auth_tokens WHERE expires_at < ?", (_now(),))
        conn().commit()
    return cursor.rowcount or 0


def delete_account(user_id: int) -> dict:
    """Hesabı ve hesaba bağlı bütün veriyi kalıcı olarak siler (KVKK silme hakkı).

    Önce içerik, en son kullanıcı satırı silinir: arada bir adım hata verirse hesap
    hâlâ vardır ve kullanıcı silmeyi tekrar deneyebilir. Yönetici hesapları ortam
    değişkeninden tohumlandığı için buradan silinmez (bkz. app.delete_me).
    """
    removed = store.delete_user_content(user_id)
    removed["cache"] = cache.delete_for_user(user_id)
    with _lock:
        removed["holds"] = conn().execute(
            "DELETE FROM credit_holds WHERE user_id = ?", (user_id,)).rowcount
        removed["ledger"] = conn().execute(
            "DELETE FROM credit_ledger WHERE user_id = ?", (user_id,)).rowcount
        removed["tokens"] = conn().execute(
            "DELETE FROM auth_tokens WHERE user_id = ?", (user_id,)).rowcount
        conn().execute("DELETE FROM users WHERE id = ?", (user_id,))
        conn().commit()
    log.info("account %d deleted: %s", user_id, removed)
    return removed


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
        "modes": list(allowed_modes(user)),
        "mode_costs": {name: {"label": p.label, "summary": p.summary,
                              "use_case": p.use_case, "features": p.features,
                              "estimate": meter.credits_for(p.estimate_try),
                              "max": meter.credits_for(p.ceiling_try),
                              "articles": p.max_articles,
                              "fulltext": p.fulltext_top_n}
                       for name, p in modes.PROFILES.items()},
        "has_ncbi_key": bool(user.get("ncbi_key_enc")),
        "email_verified": bool(user.get("email_verified_at")),
        "must_change_password": bool(user.get("must_change_password")),
        "period_start": user["period_start"],
        "created_at": user["created_at"],
    }
