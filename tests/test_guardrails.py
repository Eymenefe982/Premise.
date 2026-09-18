"""Cevap güvenlik katmanının testleri.

Bu dosyadaki her test, kullanıcıya yanlış bilgi gitmesini engelleyen bir kuralı korur.
Kurallar sessizce bozulabilecek türden: bir filtre eşleşmesi, bir yön hesabı ya da bir
eşik değeri değiştiğinde uygulama çalışmaya devam eder, sadece verdiği cevap güvenilmez
hale gelir. Bu yüzden ayrı ayrı sınanırlar.

Çalıştırmak için:  py -m pytest tests -q
"""
from __future__ import annotations

import pytest

from litrag import agreement, selection
from litrag.llm import short_answer_citations, validate_citations
from litrag.models import Article
from litrag.pipeline import _answer_mode, _is_direct, _is_relevant


def art(**kw) -> Article:
    base = dict(pmid="1", title="t", abstract="a")
    base.update(kw)
    return Article(**base)


# --------------------------------------------------------------- filtre eşleşmesi
@pytest.mark.parametrize("pub_types, filters, expected", [
    (["Meta-Analysis"], ["meta"], "match"),
    (["systematic-review"], ["meta"], "match"),          # Europe PMC tireli yazar
    (["Systematic Review"], ["meta"], "match"),
    (["Journal Article"], ["meta"], "mismatch"),
    ([], ["meta"], "unknown"),                           # tür bilgisi yok: elenmez
    (["Randomized Controlled Trial"], ["rct"], "match"),
    (["Randomised Controlled Trial"], ["rct"], "match"),
    (["Practice Guideline"], ["guideline"], "match"),
    (["Journal Article"], [], "match"),                  # filtre yoksa herkes uyar
    # Filtreler PubMed'deki gibi VE ile birleşir: ikisini birden sağlamalı
    (["Meta-Analysis"], ["meta", "rct"], "mismatch"),
])
def test_filter_status(pub_types, filters, expected):
    assert selection.filter_status(art(pub_types=pub_types), filters) == expected


def test_free_fulltext_filter_uses_access_not_pub_type():
    assert selection.filter_status(art(is_oa=True), ["free_fulltext"]) == "match"
    assert selection.filter_status(art(pmcid="PMC1"), ["free_fulltext"]) == "match"
    assert selection.filter_status(art(), ["free_fulltext"]) == "mismatch"


def test_humans_filter_is_not_verified_locally():
    """MeSH listesi yalnızca MEDLINE kayıtlarında dolu gelir; Europe PMC'den gelen bir
    kaydı MeSH eksikliği yüzünden elemek yanlış olur."""
    assert selection.filter_status(art(pub_types=["Journal Article"]), ["humans"]) == "match"


# ------------------------------------------------------------------ konu örtüşmesi
def test_topic_overlap_separates_relevant_from_unrelated():
    terms = selection.topic_terms(
        {"english": "SGLT2 inhibitors heart failure mortality", "mesh": ["Heart Failure"]})
    on_topic = art(title="SGLT2 inhibitors reduce mortality in heart failure",
                   abstract="Empagliflozin lowered mortality among heart failure patients.")
    off_topic = art(title="Calcium supplementation for people with obesity",
                    abstract="Calcium had no effect on weight in obese adults.")
    assert selection.topic_overlap(on_topic, terms) > selection.topic_overlap(off_topic, terms)
    assert selection.topic_overlap(off_topic, terms) < 0.4


# ------------------------------------------------------------------- puan ve seçim
def test_access_bonus_never_outranks_evidence_level():
    """Erişilebilirlik kolaylık sinyalidir. Küçük bir listede kanıt düzeyini
    geçerse sonuç sistematik olarak açık erişim dergilerine kayar."""
    meta = art(pub_types=["Meta-Analysis"], year=2024)
    oa_opinion = art(pub_types=["Editorial"], year=2024, is_oa=True, has_fulltext=True)
    meta.compute_score()
    oa_opinion.compute_score()
    assert meta.score > oa_opinion.score


def test_off_filter_article_is_ranked_down_not_removed():
    keeper = art(pub_types=["Meta-Analysis"], year=2024)
    stray = art(pub_types=["Meta-Analysis"], year=2024)
    stray.filter_status = "mismatch"
    keeper.compute_score()
    stray.compute_score()
    assert stray.score < keeper.score


def test_evidence_quota_keeps_top_level_evidence_in_the_list():
    """Güncelliği düşük ama kanıt düzeyi en yüksek makale listeden düşmemeli."""
    old_meta = art(pmid="meta", pub_types=["Meta-Analysis"], year=2015)
    old_meta.compute_score()
    fresh = []
    for i in range(10):
        a = art(pmid=f"f{i}", pub_types=["Journal Article"], year=2026, citations=500)
        a.compute_score()
        fresh.append(a)
    assert old_meta.score < min(a.score for a in fresh)          # puanca en sonda
    chosen = selection.select(fresh + [old_meta], limit=5)
    assert old_meta in chosen                                    # yine de seçilir


# ---------------------------------------------------------------------- seçim
def test_direct_answer_outranks_a_merely_popular_article():
    """Triyaj notu puandan önce gelir.

    Puan atıf sayısını ödüllendirir; konuya yalnızca değen ama çok atıf almış bir
    makale, soruyu doğrudan araştıran yeni bir çalışmanın yerini alabiliyordu.
    """
    famous = art(pmid="1", title="popüler ama dolaylı")
    famous.relevance, famous.score = 1, 30.0
    direct = art(pmid="2", title="soruyu doğrudan araştırıyor")
    direct.relevance, direct.score = 2, 12.0

    chosen = selection.select([famous, direct], limit=1, evidence_quota=0)
    assert chosen == [direct]


def test_on_topic_article_reaches_triage_despite_a_low_score():
    """Konusu tam tutan ama az atıflı çalışma, ünlü ama dolaylı makalelerin
    altında kalıp triyaja hiç ulaşamıyordu."""
    crowd = []
    for i in range(10):
        a = art(pmid=f"c{i}", title="ünlü ama dolaylı")
        a.topic_overlap, a.score = 0.2, 30.0 + i      # hepsi puanca çok yüksek
        crowd.append(a)
    on_topic = art(pmid="hedef", title="soruyla tam örtüşüyor")
    on_topic.topic_overlap, on_topic.score = 0.95, 9.0

    without = selection.select(crowd + [on_topic], limit=5, evidence_quota=0)
    with_quota = selection.select(crowd + [on_topic], limit=5, evidence_quota=0,
                                  topic_quota=2)
    assert on_topic not in without          # eski davranış: puan elerdi
    assert on_topic in with_quota           # kontenjan görülmesini garanti eder


def test_quotas_never_exceed_the_limit():
    pool = []
    for i in range(12):
        a = art(pmid=str(i), pub_types=["Meta-Analysis"])
        a.topic_overlap, a.score = 0.9, float(i)
        pool.append(a)
    assert len(selection.select(pool, limit=4, evidence_quota=3, topic_quota=4)) == 4


def test_selection_falls_back_to_score_before_triage_runs():
    """Triyaj çalışmadıysa (relevance -1) sıralama eskisi gibi puana dayanır."""
    low, high = art(pmid="1"), art(pmid="2")
    low.score, high.score = 5.0, 20.0
    assert selection.select([low, high], limit=1, evidence_quota=0) == [high]


# ------------------------------------------------------------------- cevap kipi
def _graded(relevances: list[int]) -> list[Article]:
    out = []
    for i, r in enumerate(relevances):
        a = art(pmid=str(i))
        a.relevance = r
        out.append(a)
    return out


def test_answer_mode_refuses_when_nothing_is_relevant():
    assert _answer_mode(_graded([0, 0, 0, 0, 0])) == "none"
    assert _answer_mode(_graded([2, 1, 0, 0])) == "none"          # 2 alakalı: eşiğin altı


def test_answer_mode_is_limited_when_evidence_is_thin():
    assert _answer_mode(_graded([2, 1, 1])) == "limited"
    assert _answer_mode(_graded([2, 2, 2, 1])) == "limited"       # 4 alakalı, 5'in altı


def test_answer_mode_is_full_only_with_direct_evidence():
    assert _answer_mode(_graded([2, 2, 2, 1, 1])) == "full"


def test_answer_mode_does_not_depend_on_how_many_articles_were_requested():
    """Bir cevabın güvenilirliği kullanıcının talebine değil, elde ne olduğuna bağlıdır."""
    assert _answer_mode(_graded([2, 1])) == "none"


def test_relevance_falls_back_to_word_overlap_when_triage_fails():
    a = art()
    assert a.relevance == -1
    a.topic_overlap = 0.5
    assert _is_relevant(a) and _is_direct(a)
    a.topic_overlap = 0.2
    assert _is_relevant(a) and not _is_direct(a)
    a.topic_overlap = 0.0
    assert not _is_relevant(a)


# ---------------------------------------------------------------- yön uyuşması
@pytest.mark.parametrize("finding, expected", [
    ({"measure": "HR", "value": "0.86", "ci": "0.79 - 0.93"}, "decrease"),
    ({"measure": "HR", "value": "0.96", "ci": "0.88 - 1.05"}, "null"),   # GA 1'i kesiyor
    ({"measure": "OR", "value": "1.42", "ci": "1.10 - 1.83"}, "increase"),
    ({"measure": "MD", "value": "-136.03", "ci": "-253.36 - -18.70"}, "decrease"),
    ({"measure": "MD", "value": "2.4", "ci": "-1.1 - 5.9"}, "null"),     # GA 0'ı kesiyor
    ({"measure": "%", "value": "12"}, "unknown"),                        # yön çıkarılamaz
])
def test_direction(finding, expected):
    assert agreement.direction(finding) == expected


@pytest.mark.parametrize("a, b, same", [
    ("all-cause mortality", "all-cause death", True),
    ("cardiovascular death", "cardiovascular mortality", True),
    ("heart failure hospitalisation", "hospitalization for heart failure", True),
    # Bileşik sonlanım bileşenini kapsar; birleşirlerse uyuşma olduğundan güçlü görünür
    ("cardiovascular death or hospitalisation for heart failure",
     "hospitalisation for heart failure", False),
    ("cardiovascular death", "all-cause death", False),
    ("kidney disease progression", "acute kidney injury", False),
])
def test_outcome_clustering(a, b, same):
    assert agreement._same_outcome(agreement._key_tokens(a),
                                   agreement._key_tokens(b)) is same


def test_agreement_flags_conflicting_studies():
    down = art(pmid="1", year=2024)
    down.findings = [{"outcome": "all-cause mortality", "measure": "HR",
                      "value": "0.80", "ci": "0.70 - 0.91", "p": ""}]
    up = art(pmid="2", year=2023)
    up.findings = [{"outcome": "all-cause death", "measure": "HR",
                    "value": "1.30", "ci": "1.10 - 1.55", "p": ""}]
    groups = agreement.build([down, up])
    assert len(groups) == 1
    assert groups[0]["conflicting"] is True
    assert groups[0]["counts"] == {"decrease": 1, "increase": 1, "null": 0}


def test_agreement_ignores_outcomes_reported_by_a_single_study():
    only = art(pmid="1", year=2024)
    only.findings = [{"outcome": "all-cause mortality", "measure": "HR",
                      "value": "0.80", "ci": "0.70 - 0.91", "p": ""}]
    assert agreement.build([only]) == []


# ------------------------------------------------------------- atıf doğrulama
def test_fabricated_pmid_is_flagged():
    real = art(pmid="38768620")
    text, fake = validate_citations("Bu doğru [PMID: 38768620], bu uydurma [PMID: 99999999].",
                                    [real])
    assert fake == ["99999999"]
    assert "⚠ unverified" in text


def test_fabricated_pmid_cannot_hide_in_a_multi_citation_bracket():
    """Model kuralı çiğneyip tek paranteze birkaç PMID sığdırabiliyor. Yalnız ilkine
    bakmak, uydurma numaranın kalabalığın arkasına saklanmasına izin verirdi."""
    real = art(pmid="38768620")
    _, fake = validate_citations(
        "Kanıt tutarlı [PMID: 38768620, PMID: 31535829, PMID: 99999999].", [real])
    assert fake == ["31535829", "99999999"]


def test_uncited_short_answer_is_detected():
    """Kaynaksız Kısa Cevap sessizce geçmemeli: doğrulanacak iddia yoktur."""
    real = art(pmid="38768620")
    cited = "## Short Answer\nEtkili bulunmuştur [PMID: 38768620].\n\n## Evidence Summary\n..."
    bare = "## Short Answer\nEtkili bulunmuştur.\n\n## Evidence Summary\nDetay [PMID: 38768620]."
    assert short_answer_citations(cited, [real]) == ["38768620"]
    assert short_answer_citations(bare, [real]) == []     # atıf başka bölümde


def test_uncited_short_answer_ignores_fabricated_pmids():
    """Uydurma numara, Kısa Cevabı kaynaklı saymaya yetmez."""
    real = art(pmid="38768620")
    made_up = "## Short Answer\nEtkili bulunmuştur [PMID: 99999999].\n\n## Evidence Summary\n..."
    assert short_answer_citations(made_up, [real]) == []


def test_index_numbers_are_not_mistaken_for_citations():
    """Bağlamdaki 'ARTICLE 4' sıra numarası PMID sayılmamalı."""
    real = art(pmid="38768620")
    _, fake = validate_citations("ARTICLE 4 shows this [PMID: 38768620].", [real])
    assert fake == []
