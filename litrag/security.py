"""Parola özetleme, oturum jetonları, alan şifreleme ve istek hız sınırı."""
from __future__ import annotations

import hashlib
import secrets
import threading
import time
from collections import defaultdict, deque
from datetime import datetime, timedelta, timezone

import jwt
from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError, VerificationError, InvalidHashError

from .config import ACCESS_TOKEN_HOURS, REFRESH_TOKEN_DAYS

_hasher = PasswordHasher()


# --------------------------------------------------------------------- parola
def hash_password(password: str) -> str:
    return _hasher.hash(password)


# Kayıtlı olmayan bir e-postayla giriş denendiğinde de aynı argon2 işi yapılır; yoksa
# yanıt süresi, adresin kayıtlı olup olmadığını ele verir.
_DUMMY_HASH = _hasher.hash(secrets.token_urlsafe(16))


def burn_password_check(password: str) -> None:
    verify_password(_DUMMY_HASH, password)


def verify_password(stored_hash: str, password: str) -> bool:
    try:
        _hasher.verify(stored_hash, password)
        return True
    except (VerifyMismatchError, VerificationError, InvalidHashError):
        return False


def needs_rehash(stored_hash: str) -> bool:
    try:
        return _hasher.check_needs_rehash(stored_hash)
    except InvalidHashError:
        return False


# --------------------------------------------------------------------- jetonlar
def issue_token(secret: str, user_id: int, kind: str = "access", epoch: int = 0) -> str:
    """`epoch`, hesabın oturum kuşağıdır: şifre değişince artar ve o ana kadar
    verilmiş bütün jetonlar geçersizleşir."""
    now = datetime.now(timezone.utc)
    life = (timedelta(hours=ACCESS_TOKEN_HOURS) if kind == "access"
            else timedelta(days=REFRESH_TOKEN_DAYS))
    # jti: jetonun kendi kimliği; çıkışta tek bir oturumu geri almayı mümkün kılar.
    payload = {"sub": str(user_id), "typ": kind, "gen": int(epoch),
               "jti": secrets.token_urlsafe(12), "iat": now, "exp": now + life}
    return jwt.encode(payload, secret, algorithm="HS256")


def read_claims(secret: str, token: str, kind: str = "access") -> dict | None:
    """İmzası ve süresi geçerli, türü doğru jetonun içeriği; değilse None."""
    try:
        data = jwt.decode(token, secret, algorithms=["HS256"])
    except jwt.PyJWTError:
        return None
    return data if data.get("typ") == kind else None


def read_token(secret: str, token: str, kind: str = "access") -> tuple[int, int] | None:
    """Geçerliyse (kullanıcı kimliği, oturum kuşağı), değilse None döndürür."""
    data = read_claims(secret, token, kind)
    if data is None:
        return None
    try:
        return int(data["sub"]), int(data.get("gen", 0))
    except (KeyError, TypeError, ValueError):
        return None


# ------------------------------------------------------- tek kullanımlık jetonlar
def new_secret_token() -> str:
    """Şifre sıfırlama / e-posta doğrulama bağlantısındaki rastgele değer."""
    return secrets.token_urlsafe(32)


def token_fingerprint(token: str) -> str:
    """Jetonun veritabanında saklanan biçimi.

    Ham jeton saklanmaz: veritabanı sızarsa saklanan değerle hesap ele geçirilememeli.
    Jeton zaten yüksek entropili olduğu için parola özetlemesi (argon2) gereksiz,
    SHA-256 yeterli ve hızlıdır."""
    return hashlib.sha256(token.encode()).hexdigest()



# --------------------------------------------------------------------- hız sınırı
class Throttle:
    """Kayan pencereli istek sayacı. Tek process içinde çalışır."""

    def __init__(self, limit: int, window_seconds: float):
        self.limit = limit
        self.window = window_seconds
        self._hits: dict[str, deque] = defaultdict(deque)
        self._lock = threading.Lock()

    MAX_KEYS = 20_000

    def _sweep(self, now: float) -> None:
        """Süresi geçmiş bütün anahtarları atar.

        İstemci IP'si başlıktan okunduğu için saldırgan istediği kadar farklı anahtar
        üretebilir; sözlük kendiliğinden temizlenmezse bu tek başına bir bellek
        tüketme yolu olur."""
        for key, hits in list(self._hits.items()):
            if not hits or now - hits[-1] > self.window:
                self._hits.pop(key, None)

    def allow(self, key: str) -> bool:
        now = time.monotonic()
        with self._lock:
            if len(self._hits) > self.MAX_KEYS:
                self._sweep(now)
            hits = self._hits[key]
            while hits and now - hits[0] > self.window:
                hits.popleft()
            if len(hits) >= self.limit:
                return False
            hits.append(now)
            self._hits[key] = hits
            return True

    def blocked(self, key: str) -> bool:
        """Sayacı artırmadan sınırın dolup dolmadığını söyler."""
        now = time.monotonic()
        with self._lock:
            hits = self._hits.get(key)
            if not hits:
                return False
            while hits and now - hits[0] > self.window:
                hits.popleft()
            return len(hits) >= self.limit

    def retry_after(self, key: str) -> int:
        with self._lock:
            hits = self._hits.get(key)
            if not hits:
                return 0
            return max(1, int(self.window - (time.monotonic() - hits[0])) + 1)


# Giriş denemeleri kaba kuvvete karşı dar, arama ise normal kullanımı engellemeyecek kadar geniş.
login_throttle = Throttle(limit=8, window_seconds=300)
# IP'den bağımsız, hesap başına yalnızca BAŞARISIZ denemeleri sayar: IP değiştirerek
# tek bir hesaba dağıtık kaba kuvvet yapılmasını engeller.
login_failures = Throttle(limit=20, window_seconds=3600)
signup_throttle = Throttle(limit=5, window_seconds=3600)
search_throttle = Throttle(limit=20, window_seconds=60)
# Şifre sıfırlama dar tutulur: bu uç, bir e-posta adresinin kayıtlı olup
# olmadığını yoklamak ve gelen kutusunu doldurmak için de kullanılabilir.
reset_throttle = Throttle(limit=4, window_seconds=900)
