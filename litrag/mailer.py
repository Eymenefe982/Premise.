"""İşlemsel e-posta gönderimi.

Brevo'nun HTTP API'si üzerinden gönderilir (SMTP değil): birçok host (Render dahil)
container'lardan giden ham SMTP bağlantılarını (port 587/465) engelliyor ya da
IPv6 route eksikliğinden "Network is unreachable" hatası veriyor. HTTPS üzerinden
çalışan bir API bu kısıtlamadan etkilenmez. Yapılandırma yoksa uygulama çökmez,
bağlantıyı konsola yazar — geliştirirken akışı e-posta kurmadan denemek için.

Gönderim ana isteği bloke etmez: arka planda bir iş parçacığında yapılır.
"""
from __future__ import annotations

import threading

import httpx

from .config import APP_NAME, APP_URL, BREVO_API_KEY, MAIL_FROM

BREVO_ENDPOINT = "https://api.brevo.com/v3/smtp/email"


def configured() -> bool:
    return bool(BREVO_API_KEY and MAIL_FROM)


def _deliver(to: str, subject: str, body: str) -> None:
    try:
        r = httpx.post(
            BREVO_ENDPOINT,
            timeout=20,
            headers={
                "api-key": BREVO_API_KEY,
                "Content-Type": "application/json",
                "Accept": "application/json",
            },
            json={
                "sender": {"email": MAIL_FROM, "name": APP_NAME},
                "to": [{"email": to}],
                "subject": subject,
                "textContent": body,
            },
        )
        if r.status_code >= 400:
            print(f"[mail] delivery failed to {to}: {r.status_code} {r.text}")
    except Exception as exc:                     # gönderim hatası akışı durdurmaz
        print(f"[mail] delivery failed to {to}: {exc}")


def send(to: str, subject: str, body: str) -> None:
    """E-postayı arka planda yollar. Yapılandırma yoksa konsola yazar."""
    if not configured():
        print(f"\n[mail] Brevo yapılandırılmamış, e-posta gönderilmedi.\n"
              f"       Alıcı : {to}\n       Konu  : {subject}\n"
              f"       İçerik:\n{body}\n")
        return
    threading.Thread(target=_deliver, args=(to, subject, body), daemon=True).start()


# --------------------------------------------------------------------- şablonlar
def send_welcome(to: str) -> None:
    send(to, f"{APP_NAME}'e hoş geldiniz",
         f"""Merhaba,

{APP_NAME} hesabınız oluşturuldu. Klinik sorularınız için PubMed, Europe PMC ve
diğer tıbbi kaynakları tek seferde tarayıp kanıta dayalı bir özet çıkarabilirsiniz.

Hemen bir arama yaparak başlayabilirsiniz: {APP_URL}

{APP_NAME}""")


def send_temp_password(to: str, temp_password: str) -> None:
    send(to, f"{APP_NAME} geçici şifreniz",
         f"""Merhaba,

{APP_NAME} hesabınız için bir geçici şifre oluşturuldu:

{temp_password}

Bu şifreyle giriş yapabilirsiniz. Diğer cihazlardaki oturumlarınız bu işlemle
kapatıldı. Giriş yaptıktan sonra hesabınızdan ("Şifre değiştir" bölümü) kalıcı
bir şifre belirlemenizi öneririz.

Bu isteği siz yapmadıysanız, birisi hesabınıza erişmeye çalışıyor olabilir:
giriş yapıp şifrenizi hemen değiştirin.

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
