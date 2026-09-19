"""Ücretsiz kredinin çok hesapla toplanmasına karşı önlemler.

Bedava kredi tamamen kaldırılamaz; amaç tek bir kişinin onu çoğaltabildiği yolları
kapatmaktır. Kapatılan yollar:

- Takma adlar: Gmail adresteki noktaları ve `+etiket`'i yok sayar, `googlemail.com`
  `gmail.com`'dur. Bunlar ayrı hesap sayıldığında tek bir posta kutusu, her biri
  doğrulanmış, sınırsız hesap açabiliyordu. Hesaplar kanonik adrese göre tekildir.
- Tek kullanımlık adresler: bilinen geçici posta servisleriyle kayıt olunamaz.
- Toplu kayıt: aynı ağdan ya da tarayıcıdan açılan hesapların yalnızca ilk birkaçı
  ücretsiz kredi alır (bkz. accounts.create_user). Kurumsal adresler muaftır.

Bu modül yalnızca saf fonksiyonlar içerir; veritabanına dokunan kısım accounts.py'dedir.
"""
from __future__ import annotations

import ipaddress

from disposable_email_domains import blocklist as _DISPOSABLE

from .config import INSTITUTIONAL_EMAIL_SUFFIXES

_GMAIL_DOMAINS = {"gmail.com", "googlemail.com"}


def _split(email: str) -> tuple[str, str]:
    local, _, domain = email.strip().lower().rpartition("@")
    return local, domain


def canonical_email(email: str) -> str:
    """Aynı posta kutusuna düşen bütün yazımları tek biçime indirir.

    `+etiket` her alan adında atılır: büyük sağlayıcıların hepsi (Gmail, Outlook,
    iCloud, Proton, Yandex) onu aynı kutuya teslim eder. Noktalar yalnız Gmail'de
    anlamsızdır; başka sağlayıcılarda `ali.veli` ile `aliveli` farklı kişiler olabilir.
    """
    local, domain = _split(email)
    if not local or not domain:
        return email.strip().lower()
    local = local.split("+", 1)[0] or local
    if domain in _GMAIL_DOMAINS:
        domain = "gmail.com"
        local = local.replace(".", "") or local
    return f"{local}@{domain}"


def _domain_and_parents(domain: str) -> list[str]:
    parts = domain.split(".")
    return [".".join(parts[i:]) for i in range(len(parts) - 1)]


def is_disposable(email: str) -> bool:
    """Geçici posta servisi mi? Alt alan adları da yakalanır (x.mailinator.com)."""
    _, domain = _split(email)
    return any(d in _DISPOSABLE for d in _domain_and_parents(domain))


def is_institutional(email: str) -> bool:
    """Üniversite ya da kamu kurumu adresi mi (ör. .edu.tr, saglik.gov.tr)?"""
    _, domain = _split(email)
    return any(domain == s or domain.endswith("." + s) for s in INSTITUTIONAL_EMAIL_SUFFIXES)


def network_of(ip: str) -> str:
    """Kota için ağ kimliği. IPv6'da tek bir ev ya da cihaz bütün bir /64 bloğunu alır;
    adres adres saymak, bloğun içinde adres değiştirerek kotayı aşmaya izin verirdi."""
    try:
        addr = ipaddress.ip_address(ip.strip())
    except ValueError:
        return ip.strip()
    if addr.version == 6:
        return str(ipaddress.ip_network(f"{addr}/64", strict=False))
    return str(addr)
