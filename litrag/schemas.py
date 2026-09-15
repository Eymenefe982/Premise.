"""API girdi modelleri. Dışarıdan gelen her gövde önce buradan geçer."""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, EmailStr, Field, field_validator

LANGUAGES = ("English", "Türkçe", "Deutsch", "Français", "Español")
FILTERS = ("rct", "meta", "guideline", "free_fulltext", "humans")


class SignupIn(BaseModel):
    email: EmailStr
    password: str = Field(min_length=8, max_length=128)
    role: Literal["physician", "student"]
    kvkk_consent: bool

    @field_validator("kvkk_consent")
    @classmethod
    def must_consent(cls, v: bool) -> bool:
        if not v:
            raise ValueError("Kayıt için açık rıza onayı gereklidir.")
        return v


class LoginIn(BaseModel):
    email: EmailStr
    password: str = Field(min_length=1, max_length=128)


class PasswordChangeIn(BaseModel):
    current_password: str = Field(min_length=1, max_length=128)
    new_password: str = Field(min_length=8, max_length=128)


class NcbiKeyIn(BaseModel):
    api_key: str = Field(default="", max_length=120)

    @field_validator("api_key")
    @classmethod
    def clean(cls, v: str) -> str:
        v = v.strip()
        if v and not v.replace("-", "").isalnum():
            raise ValueError("NCBI anahtarı yalnızca harf, rakam ve tire içerebilir.")
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

    @field_validator("filters")
    @classmethod
    def known_filters(cls, v: list[str]) -> list[str]:
        unknown = [f for f in v if f not in FILTERS]
        if unknown:
            raise ValueError(f"Bilinmeyen filtre: {', '.join(unknown)}")
        return v


class LibraryIn(BaseModel):
    article: dict = Field(default_factory=dict)
    tag: str = Field(default="", max_length=60)


class ExportIn(BaseModel):
    report_id: int | None = Field(default=None, ge=1)


class GrantIn(BaseModel):
    """Yönetici tarafından elle kredi yüklemesi."""
    user_id: int = Field(ge=1)
    amount: int = Field(ge=1, le=100_000)
    reason: str = Field(default="manual grant", max_length=120)
