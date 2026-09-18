"""Havuzdan sentezlenecek makalelerin seçimi.

Üç ayrı iş yapar:

1. Yayın türü filtresini kaynaktan bağımsız olarak **doğrular**. PubMed ve Europe PMC
   filtreyi kendi sorgu dillerinde uygular, ama ikisinin `pub_types` metadatası aynı
   değildir ve genişletme turu filtreyi bilerek kaldırır. Bu yüzden son söz burada
   söylenir: uymayan kayıt elenmez, işaretlenir ve puanı düşürülür.
2. Makalenin sorunun konusuyla kelime düzeyinde örtüşmesini ölçer. Sorgu çevirisi
   bozulduğunda hattın getirdiği alakasız literatürü yakalayan ilk, bedava savunma budur.
3. Seçimi kanıt düzeyi kotasıyla yapar: en üst basamaktaki birkaç makale, erişilebilirlik
   ya da güncellik puanı düşük olsa bile listeye girer.
"""
from __future__ import annotations

import re

from .models import Article

# Filtrelerin `pub_types` içinde aranacak karşılıkları. Kaynaklar aynı türü farklı
# yazar (randomized / randomised), hepsi burada toplanır.
# Değerler tiresiz yazılır; karşılaştırmadan önce kaydın tipleri de tiresizleştirilir.
FILTER_MATCH = {
    "rct": ("randomized controlled trial", "randomised controlled trial",
            "controlled clinical trial"),
    "meta": ("meta analysis", "systematic review"),
    "guideline": ("guideline", "consensus development conference"),
    "review": ("review",),
}

# Yayın türüyle ilgisi olmayan, kayıt üstünde doğrulanabilen filtreler
ACCESS_FILTERS = {"free_fulltext"}

# `humans` burada yok: kaydın MeSH listesi yalnızca MEDLINE'da indekslenmiş makalelerde
# dolu gelir. Europe PMC'den gelen bir kaydı MeSH eksikliği yüzünden "insan çalışması
# değil" saymak yanlış olur; bu filtre PubMed sorgusunda uygulanır ve burada doğrulanmaz.
UNVERIFIABLE_FILTERS = {"humans"}

_WORD_RE = re.compile(r"[a-zçğıöşü]+", re.IGNORECASE)

STOPWORDS = {
    "the", "and", "for", "with", "from", "that", "this", "are", "was", "were", "has",
    "have", "had", "not", "but", "its", "their", "than", "then", "into", "over",
    "under", "between", "among", "does", "did", "what", "which", "when", "how",
    "who", "whom", "effect", "effects", "study", "studies", "trial", "trials",
    "patients", "patient", "treatment", "outcome", "outcomes", "compared", "versus",
    "efficacy", "safety", "risk", "use", "using", "role", "management", "therapy",
}


def _tokens(text: str) -> set[str]:
    return {w.lower() for w in _WORD_RE.findall(text or "")
            if len(w) > 3 and w.lower() not in STOPWORDS}


def topic_terms(translation: dict) -> set[str]:
    """Sorunun konusunu temsil eden terim kümesi: İngilizce ifade + MeSH başlıkları."""
    terms = _tokens(translation.get("english", "")) | _tokens(translation.get("academic", ""))
    for mesh in translation.get("mesh", []):
        terms |= _tokens(mesh)
    return terms


def topic_overlap(article: Article, terms: set[str]) -> float:
    """Konu terimlerinin kaçta kaçı makalenin metninde geçiyor (0.0 - 1.0)."""
    if not terms:
        return 1.0
    haystack = _tokens(f"{article.title} {article.abstract} "
                       f"{' '.join(article.mesh_terms)} {' '.join(article.keywords)}")
    if not haystack:
        return 0.0
    # Kök eşleşmesi: "mortality" ile "mortalities", "ablation" ile "ablations"
    hit = 0
    for term in terms:
        stem = term[:6]
        if any(word.startswith(stem) for word in haystack):
            hit += 1
    return hit / len(terms)


def filter_status(article: Article, filters: list[str]) -> str:
    """match / mismatch / unknown. Filtreler PubMed'deki gibi VE ile birleşir."""
    type_filters = [f for f in filters if f in FILTER_MATCH]
    access_filters = [f for f in filters if f in ACCESS_FILTERS]

    for f in access_filters:
        if f == "free_fulltext" and not (article.is_oa or article.pmcid or article.has_fulltext):
            return "mismatch"

    if not type_filters:
        return "match"
    if not article.pub_types:
        return "unknown"

    # Kaynaklar aynı türü tire ya da boşlukla yazar: "systematic-review" == "systematic review"
    low = [pt.lower().replace("-", " ") for pt in article.pub_types]
    for f in type_filters:
        wanted = FILTER_MATCH[f]
        if not any(any(w in pt for w in wanted) for pt in low):
            return "mismatch"
    return "match"


def annotate(articles: list[Article], filters: list[str], terms: set[str]) -> None:
    """Havuzdaki her kayda filtre durumunu ve konu örtüşmesini yazar."""
    for art in articles:
        art.filter_status = filter_status(art, filters)
        art.topic_overlap = round(topic_overlap(art, terms), 3)


def _rank(article: Article) -> tuple[int, float]:
    """Önce triyaj notu, sonra puan.

    Puan güncelliği ve atıf sayısını da ödüllendirdiği için, soruyu doğrudan
    araştıran yeni bir çalışma, konuya yalnızca değen ünlü bir makalenin altında
    kalabiliyordu. Triyaj çalıştıysa sıralamayı önce o belirler: az yer varsa yer
    soruyu gerçekten yanıtlayana gider. Triyaj çalışmadıysa (relevance -1) bütün
    kayıtlar aynı basamakta olur ve sıralama eskisi gibi puana düşer.
    """
    return (max(article.relevance, 0), article.score)


def select(articles: list[Article], limit: int, evidence_quota: int = 3,
           topic_quota: int = 0) -> list[Article]:
    """Alaka ve puana göre seçer, iki kontenjanı garanti ederek.

    Puanlama güncelliği ve atıfı da ödüllendirir; küçük bir listede bu iki şeyi
    dışarıda bırakabilir:

    - `evidence_quota`: on yıl önce yayımlanmış ama hâlâ geçerli bir meta-analiz.
    - `topic_quota`: soruyla tam örtüşen ama yeni ve az atıflı bir çalışma. Puanda
      konu örtüşmesi en fazla 2,5 katkı verirken atıf 8'i geçebiliyor; bu kontenjan
      olmadan konusu tam tutan makale, konuya yalnızca değen ünlü bir makalenin
      altında kalıp triyaja hiç ulaşamıyordu. Eleme değil görülme garantisi:
      alakasızsa triyaj zaten eler.
    """
    # Eleme gerekmese bile sıra önemlidir: rapor, okuyucunun ilk gördüğü makaleyi
    # en alakalı sanmasına güvenir.
    if len(articles) <= limit:
        return sorted(articles, key=_rank, reverse=True)

    chosen: list[Article] = []
    seen: set[int] = set()

    def reserve(candidates: list[Article], quota: int) -> None:
        for art in candidates[:quota]:
            if len(chosen) >= limit:
                return
            if id(art) not in seen:
                chosen.append(art)
                seen.add(id(art))

    reserve(sorted((a for a in articles if a.evidence_rank >= 4.5),
                   key=lambda a: (a.evidence_rank, a.score), reverse=True),
            evidence_quota)
    reserve(sorted((a for a in articles if a.topic_overlap > 0),
                   key=lambda a: (a.topic_overlap, a.score), reverse=True),
            topic_quota)
    reserve(sorted(articles, key=_rank, reverse=True), len(articles))

    chosen.sort(key=_rank, reverse=True)
    return chosen
