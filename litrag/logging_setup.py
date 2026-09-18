"""Uygulama günlüğü ve güvenlik denetim kaydı.

`print` çağrıları kapsayıcı yeniden başladığında kaybolur, seviyesi yoktur ve
filtrelenemez. Buradaki iki kanal onun yerine geçer:

* ``log``   — işletim olayları (başlangıç, önbellek temizliği, beklenmeyen hata).
* ``audit`` — kimlik ve yetkiyle ilgili olaylar. Bir hesabın ele geçirildiğinden
  şüphelenildiğinde sorulacak soru "ne zaman, nereden, kaç kez" olduğu için bu
  kayıtlar ayrı bir logger'da ve sabit alan düzeniyle tutulur.

Denetim kaydına e-posta ham haliyle yazılmaz: günlükler çoğu zaman uygulamanın
kendisinden daha geniş bir kitleye açıktır (platform panosu, log toplayıcı), oradaki
bir sızıntı kullanıcı listesinin sızması demek olurdu.
"""
from __future__ import annotations

import logging
import os
import sys

_LEVEL = os.getenv("LOG_LEVEL", "INFO").strip().upper()
_FORMAT = "%(asctime)s %(levelname)-7s %(name)s: %(message)s"

log = logging.getLogger("premise")
audit_log = logging.getLogger("premise.audit")


def configure() -> None:
    """Kök günlükçüyü bir kez kurar. Uvicorn kendi handler'larını ekler; bizimki
    stdout'a yazar ki Render'ın log akışında görünsün."""
    if log.handlers:
        return
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(logging.Formatter(_FORMAT))
    for logger in (log, audit_log):
        logger.setLevel(getattr(logging, _LEVEL, logging.INFO))
        logger.addHandler(handler)
        logger.propagate = False


def mask_email(email: str | None) -> str:
    """`ayse@example.com` -> `a***@example.com`. Aynı adres her zaman aynı maskeye
    düşer, yani kayıtlar birbirine bağlanabilir ama adres okunamaz."""
    if not email or "@" not in email:
        return "-"
    name, _, domain = email.partition("@")
    return f"{name[0]}***@{domain}" if name else f"***@{domain}"


def audit(event: str, *, user_id: int | None = None, email: str | None = None,
          ip: str | None = None, ok: bool = True, **extra: object) -> None:
    """Güvenlik olayını tek satır, sabit alanlı biçimde yazar."""
    fields = [f"event={event}", f"ok={'1' if ok else '0'}",
              f"user={user_id if user_id is not None else '-'}",
              f"email={mask_email(email)}", f"ip={ip or '-'}"]
    fields += [f"{key}={value}" for key, value in extra.items()]
    audit_log.log(logging.INFO if ok else logging.WARNING, " ".join(fields))
