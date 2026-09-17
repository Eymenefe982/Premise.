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
    """Sends the email in the background. Logs to console if not configured."""
    if not configured():
        print(f"\n[mail] Brevo is not configured, email not sent.\n"
              f"       To      : {to}\n       Subject : {subject}\n"
              f"       Body:\n{body}\n")
        return
    threading.Thread(target=_deliver, args=(to, subject, body), daemon=True).start()


# --------------------------------------------------------------------- templates
def send_welcome(to: str) -> None:
    send(to, f"Welcome to {APP_NAME}",
         f"""Hi,

Your {APP_NAME} account has been created. For any clinical question, you can
search PubMed, Europe PMC and other medical sources at once and get an
evidence-based summary.

Start a search right away: {APP_URL}

{APP_NAME}""")


def send_temp_password(to: str, temp_password: str) -> None:
    send(to, f"Your {APP_NAME} temporary password",
         f"""Hi,

A temporary password was created for your {APP_NAME} account:

{temp_password}

You can sign in with it. Your sessions on other devices have been signed out.
After signing in, we recommend setting a permanent password from your account
page ("Change password" section).

If you did not request this, someone may be trying to access your account:
sign in and change your password right away.

{APP_NAME}""")


def send_email_verification(to: str, token: str, valid_hours: int) -> None:
    link = f"{APP_URL}/eposta-dogrula?token={token}"
    send(to, f"Verify your {APP_NAME} email address",
         f"""Hi,

You created a {APP_NAME} account. To confirm this email address is yours,
open the link below:

{link}

The link is valid for {valid_hours} hours.

If you did not create this account, you can ignore this message.

{APP_NAME}""")
