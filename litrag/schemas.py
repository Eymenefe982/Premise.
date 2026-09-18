"""API girdi modelleri. Dışarıdan gelen her gövde önce buradan geçer."""
from __future__ import annotations

import json
from typing import Literal

from email_validator import EmailNotValidError, validate_email
from pydantic import BaseModel, EmailStr, Field, field_validator

LANGUAGES = ("English", "Türkçe", "Deutsch", "Français", "Español")
FILTERS = ("rct", "meta", "guideline", "free_fulltext", "humans")
POWER_MODES = ("low", "medium", "high")


def _clean_email(v: object) -> str:
    """E-posta biçimini kontrol eder ve Türkçe, kullanıcıya gösterilebilir bir hata verir.

    `EmailStr`'in kendi hatası `email_validator` kütüphanesinden İngilizce gelir;
    bu, arayüzde doğrudan gösterildiği için burada yakalanıp Türkçeleştirilir."""
    try:
        return validate_email(str(v), check_deliverability=False).normalized
    except EmailNotValidError:
        raise ValueError("Enter a valid email address.")


class SignupIn(BaseModel):
    email: EmailStr
    password: str = Field(min_length=8, max_length=128)
    role: Literal["physician", "student"]
    kvkk_consent: bool

    @field_validator("email", mode="before")
    @classmethod
    def valid_email(cls, v: object) -> str:
        return _clean_email(v)

    @field_validator("kvkk_consent")
    @classmethod
    def must_consent(cls, v: bool) -> bool:
        if not v:
            raise ValueError("Consent is required to sign up.")
        return v


class LoginIn(BaseModel):
    email: EmailStr
    password: str = Field(min_length=1, max_length=128)

    @field_validator("email", mode="before")
    @classmethod
    def valid_email(cls, v: object) -> str:
        return _clean_email(v)


class PasswordChangeIn(BaseModel):
    current_password: str = Field(min_length=1, max_length=128)
    new_password: str = Field(min_length=8, max_length=128)


class ForgotPasswordIn(BaseModel):
    email: EmailStr

    @field_validator("email", mode="before")
    @classmethod
    def valid_email(cls, v: object) -> str:
        return _clean_email(v)


class VerifyEmailIn(BaseModel):
    token: str = Field(min_length=16, max_length=256)


class NcbiKeyIn(BaseModel):
    api_key: str = Field(default="", max_length=120)

    @field_validator("api_key")
    @classmethod
    def clean(cls, v: str) -> str:
        v = v.strip()
        if v and not v.replace("-", "").isalnum():
            raise ValueError("The NCBI key can only contain letters, digits and hyphens.")
        return v


class SearchIn(BaseModel):
    query: str = Field(min_length=3, max_length=500)
    author: str = Field(default="", max_length=120)
    journal: str = Field(default="", max_length=120)
    language: Literal[LANGUAGES] = "Türkçe"       # type: ignore[valid-type]
    max_articles: int = Field(default=15, ge=3, le=40)
    recent_years: int = Field(default=10, ge=0, le=100)
    filters: list[str] = Field(default_factory=list, max_length=8)
    only_open_access: bool = False
    use_fulltext: bool = True
    use_clinical: bool = True
    extract_stats: bool = True
    synthesize: bool = True
    refresh: bool = False
    power_mode: Literal[POWER_MODES] = "medium"   # type: ignore[valid-type]

    @field_validator("filters")
    @classmethod
    def known_filters(cls, v: list[str]) -> list[str]:
        unknown = [f for f in v if f not in FILTERS]
        if unknown:
            raise ValueError(f"Unknown filter: {', '.join(unknown)}")
        return v


class LibraryIn(BaseModel):
    article: dict = Field(default_factory=dict)
    tag: str = Field(default="", max_length=60)

    @field_validator("article")
    @classmethod
    def bounded(cls, v: dict) -> dict:
        # Veritabanına yalnız birkaç kısa alan yazılır; sınırsız gövde disk doldurur.
        if len(json.dumps(v, default=str)) > 60_000:
            raise ValueError("Article payload is too large.")
        for field in ("pmid", "doi", "title", "authors", "journal", "best_free_url", "pubmed_url"):
            if v.get(field) is not None and not isinstance(v[field], str):
                raise ValueError(f"Invalid article field: {field}")
        try:
            int(v.get("year") or 0)
        except (TypeError, ValueError):
            raise ValueError("Invalid article field: year")
        return v


class ExportIn(BaseModel):
    report_id: int | None = Field(default=None, ge=1)


class GrantIn(BaseModel):
    """Yönetici tarafından elle kredi yüklemesi."""
    user_id: int = Field(ge=1)
    amount: int = Field(ge=1, le=100_000)
    reason: str = Field(default="manual grant", max_length=120)
