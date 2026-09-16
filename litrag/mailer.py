"""İşlemsel e-posta gönderimi.

Sağlayıcıdan bağımsızdır: SMTP konuşan her servis (Resend, Postmark, SES, Brevo,
Mailgun, hatta bir kurumsal posta sunucusu) `.env` doldurularak çalışır. Yapılandırma
yoksa uygulama çökmez, bağlantıyı konsola yazar — geliştirirken akışı e-posta kurmadan
denemek için.

Gönderim ana isteği bloke etmez: arka planda bir iş parçacığında yapılır, çünkü SMTP
el sıkışması saniyeler sürebilir ve kullanıcı "şifremi unuttum" düğmesine bastığında
o kadar beklememelidir.
"""
from __future__ import annotations

import smtplib
import threading
from email.message import EmailMessage

from .config import (APP_NAME, APP_URL, MAIL_FROM, SMTP_HOST, SMTP_PASSWORD, SMTP_PORT,
                     SMTP_STARTTLS, SMTP_USER)


def configured() -> bool:
    return bool(SMTP_HOST and MAIL_FROM)


def _deliver(message: EmailMessage) -> None:
    try:
        if SMTP_PORT == 465:
            server = smtplib.SMTP_SSL(SMTP_HOST, SMTP_PORT, timeout=20)
        else:
            server = smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=20)
            if SMTP_STARTTLS:
                server.starttls()
        with server:
            if SMTP_USER:
                server.login(SMTP_USER, SMTP_PASSWORD)
            server.send_message(message)
    except Exception as exc:                     # gönderim hatası akışı durdurmaz
        print(f"[mail] delivery failed to {message['To']}: {exc}")


def send(to: str, subject: str, body: str) -> None:
    """E-postayı arka planda yollar. Yapılandırma yoksa konsola yazar."""
    if not configured():
        print(f"\n[mail] SMTP yapılandırılmamış, e-posta gönderilmedi.\n"
              f"       Alıcı : {to}\n       Konu  : {subject}\n"
              f"       İçerik:\n{body}\n")
        return

    message = EmailMessage()
    message["From"] = f"{APP_NAME} <{MAIL_FROM}>"
    message["To"] = to
    message["Subject"] = subject
    message.set_content(body)
    threading.Thread(target=_deliver, args=(message,), daemon=True).start()


# --------------------------------------------------------------------- şablonlar
def send_password_reset(to: str, token: str, valid_minutes: int) -> None:
    link = f"{APP_URL}/sifre-sifirla?token={token}"
    send(to, f"{APP_NAME} şifre sıfırlama",
         f"""Merhaba,

{APP_NAME} hesabınızın şifresini sıfırlamak için aşağıdaki bağlantıyı açın:

{link}

Bağlantı {valid_minutes} dakika geçerlidir ve yalnızca bir kez kullanılabilir.

Bu isteği siz yapmadıysanız hiçbir şey yapmanıza gerek yok: şifreniz değişmez.
Ancak hesabınıza başkasının erişmeye çalıştığını düşünüyorsanız şifrenizi
değiştirmenizi öneririz.

{APP_NAME}""")


def send_email_verification(to: str, token: str, valid_hours: int) -> None:
    link = f"{APP_URL}/eposta-dogrula?token={token}"
    send(to, f"{APP_NAME} e-posta adresinizi doğrulayın",
         f"""Merhaba,

{APP_NAME} hesabınızı oluşturdunuz. E-posta adresinizin size ait olduğunu
doğrulamak için aşağıdaki bağlantıyı açın:

{link}

Bağlantı {valid_hours} saat geçerlidir.

Bu hesabı siz oluşturmadıysanız bu iletiyi yok sayabilirsiniz.

{APP_NAME}""")
