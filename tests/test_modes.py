"""Güç modları, maliyet ölçümü ve kredi hesabı.

Bu katmanın sessiz bozulma biçimi pahalıdır: profil worker thread'lere geçmezse ya da
kullanım verisi okunmazsa hat çalışmaya devam eder, yalnızca kullanıcının seçtiği mod
uygulanmaz ve fatura beklenenin katı olur. Testler bu sessiz halleri hedefler.
"""
from __future__ import annotations

import threading
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

import pytest

from litrag import accounts, cache, meter, modes
from litrag.config import CREDIT_TRY, PLANS, USD_TRY
from litrag.pipeline import SearchRequest


def usage(prompt: int = 0, out: int = 0, thoughts: int = 0,
          total: int | None = None) -> SimpleNamespace:
    return SimpleNamespace(prompt_token_count=prompt, candidates_token_count=out,
                           thoughts_token_count=thoughts,
                           total_token_count=prompt + out + thoughts if total is None else total)


# ------------------------------------------------------------------ profiller
@pytest.mark.parametrize("name", modes.POWER_MODES)
def test_every_mode_defines_every_stage(name):
    prof = modes.profile(name)
    assert prof.name == name
    assert set(prof.stages) == set(modes.STAGES)
    assert all(s.max_output_tokens > 0 for s in prof.stages.values())


def test_unknown_mode_falls_back_to_default():
    assert modes.profile("turbo").name == modes.DEFAULT_MODE
    assert modes.profile(None).name == modes.DEFAULT_MODE


def test_modes_get_progressively_more_expensive():
    low, medium, high = (modes.profile(m) for m in ("low", "medium", "high"))
    assert low.estimate_try < medium.estimate_try < high.estimate_try
    assert low.ceiling_try < medium.ceiling_try < high.ceiling_try
    assert low.max_articles < medium.max_articles < high.max_articles


def test_mode_budgets_match_their_promise():
    """Kullanıcıya verilen söz: 0.50 / 1.00 / 5.00 TL üst sınırları."""
    assert modes.profile("low").ceiling_try <= 0.50
    assert modes.profile("medium").ceiling_try <= 1.00
    assert modes.profile("high").ceiling_try <= 5.00


def test_only_high_mode_uses_the_expensive_model():
    """Ucuz modlar zamlanan modele hiç dokunmamalı, yoksa 2027'de bütçeyi aşarlar."""
    from litrag.config import GEMINI_HIGH_MODEL
    for name in ("low", "medium"):
        assert all(s.model != GEMINI_HIGH_MODEL for s in modes.profile(name).stages.values())
    assert modes.profile("high").stage("synthesis").model == GEMINI_HIGH_MODEL


def test_high_synthesis_does_not_inherit_the_legacy_model():
    """.env'deki GEMINI_MODEL (gemini-3.5-flash, 1.50/9.00 USD) buraya sızarsa
    yüksek mod tek başına 5 TL tavanını doldurur."""
    from litrag.config import GEMINI_HIGH_MODEL, GEMINI_MODEL, MODEL_PRICES
    if GEMINI_MODEL != GEMINI_HIGH_MODEL and GEMINI_MODEL in MODEL_PRICES:
        assert MODEL_PRICES[GEMINI_HIGH_MODEL][1] <= MODEL_PRICES[GEMINI_MODEL][1]


def test_every_stage_model_has_a_known_price():
    """Fiyatı bilinmeyen model, ölçümü sessizce yanlışlar."""
    from litrag.config import MODEL_PRICES
    for name in modes.POWER_MODES:
        for stage in modes.profile(name).stages.values():
            assert stage.model in MODEL_PRICES, f"{name}/{stage.model} fiyat tablosunda yok"


# --------------------------------------------------------------- bağlam taşıma
def test_profile_reaches_thread_pool_workers():
    """`modes.submit` olmadan hattın en pahalı iki adımı varsayılan profile düşerdi."""
    seen = []
    with modes.use("high"):
        with ThreadPoolExecutor(max_workers=2) as pool:
            job = modes.submit(pool, lambda: modes.active().name)
            seen.append(job.result())
    assert seen == ["high"]


def test_concurrent_searches_keep_their_own_mode():
    results: dict[str, str] = {}
    ready = threading.Barrier(2)

    def run(mode: str) -> None:
        with modes.use(mode):
            ready.wait(timeout=5)          # iki iş aynı anda açık olsun
            with ThreadPoolExecutor(max_workers=1) as pool:
                results[mode] = modes.submit(pool, lambda: modes.active().name).result()

    threads = [threading.Thread(target=run, args=(m,)) for m in ("low", "high")]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=5)
    assert results == {"low": "low", "high": "high"}


def test_context_outside_a_search_is_the_default():
    """cli.py ve testler `use()` olmadan çağırır; profil yine de olmalı."""
    assert modes.active().name == modes.DEFAULT_MODE
    assert modes.current().meter is None


# ------------------------------------------------------------------- ölçüm
def test_cost_follows_the_price_table():
    # gemini-3.6-flash: 0.75 USD / 1M girdi
    assert meter.cost_try("gemini-3.6-flash", 1_000_000, 0) == pytest.approx(0.75 * USD_TRY)
    assert meter.cost_try("gemini-3.6-flash", 0, 1_000_000) == pytest.approx(3.75 * USD_TRY)


def test_unknown_model_is_priced_as_expensive():
    """Bilinmeyen modeli ucuz saymak, faturayı sessizce büyütmenin yoludur."""
    known = meter.cost_try("gemini-3.1-flash-lite", 1_000_000, 0)
    unknown = meter.cost_try("some-new-model", 1_000_000, 0)
    assert unknown > known


def test_thinking_tokens_are_billed_as_output():
    m = meter.Meter(5.0)
    m.record("synthesis", "gemini-3.6-flash", usage(prompt=0, out=0, thoughts=1_000_000))
    assert m.total_try == pytest.approx(3.75 * USD_TRY)


def test_hidden_thinking_tokens_are_counted_from_the_total():
    """SDK düşünmeyi `thoughts_token_count`'ta bildirmiyor; toplamda bildiriyor.

    Ölçülen gerçek örnek: gemini-3.6-flash, girdi 7.792, candidates 538,
    thoughts alanı 0, toplam 9.504 → 1.174 token görünmeyen düşünme.
    """
    m = meter.Meter(5.0)
    m.record("synthesis", "gemini-3.6-flash",
             usage(prompt=7792, out=538, thoughts=0, total=9504))
    assert m.summary()["tokens"]["out"] == 538 + 1174
    naive = meter.cost_try("gemini-3.6-flash", 7792, 538)
    assert m.total_try > naive        # saf sayım faturayı olduğundan ucuz gösterirdi


def test_meter_accumulates_per_stage():
    m = meter.Meter(5.0)
    m.record("triage", "gemini-3.1-flash-lite", usage(prompt=1000, out=100))
    m.record("triage", "gemini-3.1-flash-lite", usage(prompt=500, out=50))
    m.record("synthesis", "gemini-3.6-flash", usage(prompt=2000, out=800))
    summary = m.summary()
    assert summary["stages"]["triage"]["calls"] == 2
    assert summary["stages"]["triage"]["in"] == 1500
    assert summary["tokens"]["in"] == 3500
    assert summary["cost_try"] > 0


def test_meter_ignores_missing_usage_metadata():
    m = meter.Meter(1.0)
    m.record("triage", "gemini-3.6-flash", None)
    assert m.total_try == 0


def test_meter_blocks_stages_once_the_ceiling_is_reached():
    m = meter.Meter(0.01)
    assert m.allow("claims")
    m.record("synthesis", "gemini-3.6-flash", usage(prompt=1_000_000, out=0))
    assert not m.allow("claims")
    m.note_degraded("claims")
    assert m.degraded == ["claims"]


def test_credits_follow_real_spend():
    assert meter.credits_for(0) == 0
    assert meter.credits_for(CREDIT_TRY * 10) == 10
    # Sıfır olmayan en küçük gider bile bir krediye yuvarlanır
    assert meter.credits_for(CREDIT_TRY / 100) == 1


# ------------------------------------------------------------------ rezervasyon
def test_reservation_covers_the_worst_case_of_its_mode():
    for name in modes.POWER_MODES:
        req = SearchRequest(query="q", power_mode=name)
        prof = modes.profile(name)
        reserved = accounts.max_cost_of(req)
        assert reserved >= meter.credits_for(prof.ceiling_try)
        assert reserved >= accounts.estimated_cost_of(req)


def test_user_settings_cannot_raise_the_cost_of_a_mode():
    """Kaydırıcıyı 40'a çekmek ya da tam metni açmak modun bütçesini büyütmez.

    Hacim ayarlarının tamamı profilden `min()` ile geçer; kullanıcı yalnızca
    aşağı çekebilir. Rezervasyon da makale sayısına değil, modun tavanına bakar.
    """
    for name in modes.POWER_MODES:
        frugal = SearchRequest(query="q", power_mode=name, max_articles=3,
                               use_fulltext=False, extract_stats=False)
        maxed = SearchRequest(query="q", power_mode=name, max_articles=40,
                              use_fulltext=True, extract_stats=True)
        assert accounts.max_cost_of(frugal) == accounts.max_cost_of(maxed)


def test_low_mode_ignores_the_full_text_request():
    """Düşük güçte tam metin hiç okunmaz; anahtar açık bırakılsa bile."""
    assert modes.profile("low").fulltext_top_n == 0


def test_every_mode_keeps_the_answer_guardrails():
    """Ucuzluk, denetimi kapatmanın gerekçesi değil: hangi mod seçilirse seçilsin
    makaleler alakaya göre elenir ve iddialar kaynağına karşı sınanır."""
    for name in modes.POWER_MODES:
        prof = modes.profile(name)
        assert prof.claim_check, f"{name}: iddia doğrulama kapalı"
        assert prof.max_claims > 0
        assert prof.triage_candidates >= prof.max_articles


def test_high_mode_reserves_more_than_low():
    low = accounts.max_cost_of(SearchRequest(query="q", power_mode="low"))
    high = accounts.max_cost_of(SearchRequest(query="q", power_mode="high"))
    assert high > low


def test_request_without_a_mode_still_prices(monkeypatch):
    """Eski çağıranlar (cli.py, kayıtlı işler) power_mode alanı olmadan gelebilir."""
    legacy = SimpleNamespace(query="q", max_articles=15, use_fulltext=True, synthesize=True)
    assert accounts.max_cost_of(legacy) > 0


@pytest.mark.parametrize("plan", sorted(PLANS))
def test_every_account_costs_us_at_most_five_lira(plan):
    """Ücretli katman açılana kadar hiçbir hesap bize 5 TL'den fazlaya mal olamaz."""
    assert PLANS[plan]["credits"] * CREDIT_TRY == pytest.approx(5.0, abs=0.05)


def test_one_high_power_search_fits_in_an_account_budget():
    """Tavan, en pahalı modun tek bir aramasını karşılayabilmeli; yoksa yüksek güç
    hiçbir hesapta çalıştırılamaz."""
    budget = min(p["credits"] for p in PLANS.values())
    assert budget >= meter.credits_for(modes.profile("high").ceiling_try)


# ---------------------------------------------------------------- önbellek
def test_cache_key_separates_power_modes():
    base = dict(query="aynı soru", author="", journal="")
    low = cache.make_key(SearchRequest(**base, power_mode="low"))
    high = cache.make_key(SearchRequest(**base, power_mode="high"))
    assert low != high


def test_cache_key_is_stable_for_the_same_mode():
    a = cache.make_key(SearchRequest(query="aynı soru", power_mode="medium"))
    b = cache.make_key(SearchRequest(query="aynı soru", power_mode="medium"))
    assert a == b
