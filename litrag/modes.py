"""Güç modları: bir aramanın hangi modelleri ve hangi hacmi kullanacağı.

Kullanıcı arama başına medium / high seçer. Profil, hattın her aşaması için
modeli ve çıktı sınırını, ayrıca kaç makale okunacağını belirler. Amaç kalite ile
arama başına gerçek para arasında kullanıcının kendi seçtiği bir denge kurmaktır.

Profiller 1 Ocak 2027'deki Gemini zammına dayanıklı kuruldu: medium zamlanan
modele hiç dokunmadığı için maliyeti sabit kalır, yalnızca high onu kullanır ve o
da 5 TL tavanının altında kalacak şekilde ölçüldü.

Eskiden bir "low" mod da vardı (yalnızca özetler, sayı çıkarımı yok). Klinik soruda
sayısız bir cevap ürünün kalitesini temsil etmediği için 2026-09-19'da kaldırıldı;
eski istemcilerden gelen "low" sessizce medium'a düşer (bkz. `profile`).
"""
from __future__ import annotations

import contextvars
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Iterator

from .config import GEMINI_HIGH_MODEL, GEMINI_LITE_MODEL, GEMINI_MID_MODEL

STAGES = ("translate", "triage", "extract", "synthesis", "claims")
POWER_MODES = ("medium", "high")
DEFAULT_MODE = "medium"


@dataclass(frozen=True)
class Stage:
    model: str
    # Maliyetin tek gerçek freni. Modelin görünmeyen düşünme adımları da çıktı
    # fiyatından faturalanır ve bu sınırın içinden harcanır (ölçüm: gemini-3.6-flash
    # çağrı başına ~1.200 token, lite modeller sıfır). Sınır bu yüzden raporun değil,
    # düşünme + rapor toplamının bütçesidir; dar tutulursa rapor ortadan kesilir.
    max_output_tokens: int


@dataclass(frozen=True)
class Profile:
    name: str
    label: str
    summary: str
    use_case: str
    ceiling_try: float          # bu tutara yaklaşınca hat kendini kısar
    estimate_try: float         # tipik arama maliyeti (arayüzdeki "≈" değeri)
    stages: dict[str, Stage]

    max_articles: int
    triage_candidates: int
    fulltext_top_n: int
    fulltext_chars: int
    abstract_chars: int
    extract: bool
    extract_excerpt_chars: int
    claim_check: bool
    max_claims: int
    claim_excerpt_chars: int
    tables: bool = False                # deterministik kanıt/bulgu tabloları

    def stage(self, name: str) -> Stage:
        return self.stages[name]

    @property
    def features(self) -> list[str]:
        """Arayüzde gösterilen özellik listesi.

        Metin profilin kendisinden türetilir: bir knob değişince açıklama da
        değişir, ikisi asla ayrışmaz.
        """
        items = [f"Screens {self.triage_candidates} candidates, answers from "
                 f"{self.max_articles}"]
        items.append("Abstracts only — full text is not read" if not self.fulltext_top_n
                     else f"Reads the full text of up to {self.fulltext_top_n} articles")
        if self.extract:
            items.append("Extracts effect sizes, confidence intervals and sample "
                         "sizes, each checked against the source text")
        if self.tables:
            items.append("Adds an evidence table and a numeric findings table")
        items.append("Drops off-topic articles, flags invented citations and checks "
                     "every claim against the article it cites")
        return items


PROFILES: dict[str, Profile] = {
    "medium": Profile(
        name="medium",
        label="Medium power",
        summary="Balanced depth: full-text reading and verified numbers.",
        use_case="The everyday setting for a clinical question — enough depth to "
                 "quote a number, without paying for a full review.",
        ceiling_try=1.00, estimate_try=0.70,      # ölçüldü: 0,595 ve 0,747 TL
        stages={
            "translate": Stage(GEMINI_LITE_MODEL, 300),
            "triage":    Stage(GEMINI_LITE_MODEL, 800),
            "extract":   Stage(GEMINI_LITE_MODEL, 1500),
            "synthesis": Stage(GEMINI_MID_MODEL, 3000),
            "claims":    Stage(GEMINI_LITE_MODEL, 600),
        },
        max_articles=15, triage_candidates=25,
        fulltext_top_n=3, fulltext_chars=8000, abstract_chars=1200,
        extract=True, extract_excerpt_chars=2000,
        claim_check=True, max_claims=5, claim_excerpt_chars=2000,
    ),
    "high": Profile(
        name="high",
        label="High power",
        summary="A deep report: the widest net, long full texts and data tables.",
        use_case="For work you will have to defend — a case presentation, a journal "
                 "club, a review draft, or a decision someone will question.",
        ceiling_try=5.00, estimate_try=2.50,      # ölçüldü: 2,349 TL
        stages={
            "translate": Stage(GEMINI_LITE_MODEL, 400),
            "triage":    Stage(GEMINI_LITE_MODEL, 1200),
            "extract":   Stage(GEMINI_LITE_MODEL, 2500),
            # Bu sınır yalnızca raporu değil, modelin görünmeyen düşünme adımlarını da
            # kapsar: 4.000 denendiğinde düşünme bütçeyi yiyip rapor cümle ortasında
            # kesildi. Sınır rapora değil, düşünme + rapor toplamına göre konur.
            "synthesis": Stage(GEMINI_HIGH_MODEL, 10000),
            "claims":    Stage(GEMINI_LITE_MODEL, 800),
        },
        max_articles=20, triage_candidates=40,
        fulltext_top_n=5, fulltext_chars=7000, abstract_chars=1600,
        extract=True, extract_excerpt_chars=4000,
        claim_check=True, max_claims=8, claim_excerpt_chars=2500,
        tables=True,
    ),
}


def profile(name: str | None) -> Profile:
    return PROFILES.get((name or "").strip().lower(), PROFILES[DEFAULT_MODE])


# --------------------------------------------------------------- iş bağlamı
@dataclass
class JobCtx:
    """Tek bir aramanın profili ve maliyet sayacı."""
    profile: Profile
    meter: object | None = None     # meter.Meter; döngüsel import olmasın diye gevşek


_ctx: contextvars.ContextVar[JobCtx] = contextvars.ContextVar("litrag_job_ctx")


def current() -> JobCtx:
    """Aktif bağlam; context dışında çağrılırsa varsayılan profil (cli.py, testler)."""
    try:
        return _ctx.get()
    except LookupError:
        return JobCtx(PROFILES[DEFAULT_MODE])


def active() -> Profile:
    return current().profile


@contextmanager
def use(mode: str | None, meter: object | None = None) -> Iterator[JobCtx]:
    ctx = JobCtx(profile(mode), meter)
    token = _ctx.set(ctx)
    try:
        yield ctx
    finally:
        _ctx.reset(token)


def submit(pool, fn, *args, **kwargs):
    """Havuz görevini aktif bağlamla çalıştırır.

    `concurrent.futures` contextvar'ları worker thread'lere taşımaz; bu sarmalayıcı
    olmadan havuz içindeki her model çağrısı sessizce varsayılan profile döner ve
    kullanıcının seçtiği mod hattın en pahalı iki adımında hiç uygulanmazdı.
    """
    ctx = contextvars.copy_context()
    return pool.submit(ctx.run, lambda: fn(*args, **kwargs))
