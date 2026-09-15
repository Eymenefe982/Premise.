"""Kaynaklardan gelen kayıtların ortak veri modeli."""
from __future__ import annotations

import math
import re
from dataclasses import dataclass, field, asdict
from datetime import datetime

# Kanıt piramidinde üst sıradaki yayın tipleri -> puan
EVIDENCE_WEIGHTS = {
    "practice guideline": 6.0,
    "guideline": 5.5,
    "meta-analysis": 5.0,
    "systematic review": 4.5,
    "randomized controlled trial": 3.5,
    "clinical trial, phase iii": 3.0,
    "multicenter study": 1.5,
    "clinical trial": 1.5,
    "review": 1.0,
    "case reports": -1.5,
    "editorial": -2.0,
    "comment": -2.5,
    "letter": -2.0,
}

EVIDENCE_LABELS = {
    "practice guideline": "Guideline",
    "guideline": "Guideline",
    "meta-analysis": "Meta-analysis",
    "systematic review": "Systematic review",
    "randomized controlled trial": "RCT",
    "clinical trial": "Clinical trial",
    "review": "Review",
    "case reports": "Case report",
    "observational study": "Observational",
}


def _clean(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "").replace("\u00a0", " ")).strip()


# Erişilebilirlik bir kolaylık sinyalidir, kalite sinyali değildir. Küçük bir seçim
# kümesinde bu bonus yüksek tutulursa sonuç sistematik olarak açık erişim dergilerine
# kayar ve klinik sonucu değiştirebilir; bu yüzden kanıt düzeyinin altında tutulur.
ACCESS_FULLTEXT_BONUS = 1.2
ACCESS_OA_BONUS = 0.6

# Yayın türü filtresine uymayan kaydın puan cezası. Elemek yerine cezalandırırız:
# pub_types alanı boş gelen (çoğunlukla yeni) kayıtlar haksız yere kaybolmasın.
FILTER_PENALTY = {"match": 0.0, "unknown": -1.0, "mismatch": -4.0}


@dataclass
class Article:
    """Tek bir literatür kaydı. Birden çok kaynaktan zenginleştirilebilir."""

    pmid: str = ""
    pmcid: str = ""
    doi: str = ""
    title: str = ""
    abstract: str = ""
    authors: str = ""
    journal: str = ""
    year: int = 0
    pub_types: list[str] = field(default_factory=list)
    mesh_terms: list[str] = field(default_factory=list)
    keywords: list[str] = field(default_factory=list)
    citations: int = 0
    sample_size: int = 0                                # çalışmaya alınan denek sayısı (n)
    design: str = ""                                    # RKÇ, meta-analiz, kohort ...
    population: str = ""                                # çalışılan hasta grubu
    findings: list[dict] = field(default_factory=list)  # sayısal sonuçlar
    is_oa: bool = False
    pdf_url: str = ""
    fulltext_url: str = ""
    has_fulltext: bool = False          # Europe PMC'den tam metin XML çekilebilir mi
    fulltext_used: bool = False         # Sentezde tam metin kullanıldı mı
    sources: list[str] = field(default_factory=list)   # pubmed / europepmc / pmc / openalex ...
    score: float = 0.0
    # Kullanıcının yayın türü filtresine göre durum: match / mismatch / unknown.
    # "unknown", kaydın pub_types alanı boş olduğu için doğrulanamadığı anlamına gelir;
    # bunu "uymuyor" saymak haksızlık olur, bu yüzden ayrı tutulur ve cezası hafiftir.
    filter_status: str = "match"
    # Sorunun konusuyla kelime düzeyinde örtüşme oranı (0.0 - 1.0), pipeline doldurur.
    topic_overlap: float = 1.0
    # Modelin makaleyi soruya göre değerlendirmesi: 2 doğrudan, 1 dolaylı, 0 alakasız.
    # -1, henüz değerlendirilmedi demektir.
    relevance: int = -1

    # ---------- türetilmiş alanlar ----------
    @property
    def key(self) -> str:
        if self.doi:
            return "doi:" + self.doi.lower()
        if self.pmid:
            return "pmid:" + self.pmid
        if self.pmcid:
            return "pmc:" + self.pmcid
        return "title:" + re.sub(r"[^a-z0-9]", "", self.title.lower())[:80]

    @property
    def pubmed_url(self) -> str:
        return f"https://pubmed.ncbi.nlm.nih.gov/{self.pmid}/" if self.pmid else ""

    @property
    def pmc_url(self) -> str:
        return f"https://www.ncbi.nlm.nih.gov/pmc/articles/{self.pmcid}/" if self.pmcid else ""

    @property
    def doi_url(self) -> str:
        return f"https://doi.org/{self.doi}" if self.doi else ""

    @property
    def evidence_label(self) -> str:
        for pt in self.pub_types:
            low = pt.lower()
            if low in EVIDENCE_LABELS:
                return EVIDENCE_LABELS[low]
        return "Article"

    @property
    def best_free_url(self) -> str:
        return self.pdf_url or self.fulltext_url or self.pmc_url or self.pubmed_url or self.doi_url

    # ---------- birleştirme ----------
    def merge(self, other: "Article") -> None:
        """Aynı makalenin başka kaynaktan gelen kaydıyla birleştir (boş alanları doldur)."""
        for fld in ("pmid", "pmcid", "doi", "title", "abstract", "authors", "journal",
                    "pdf_url", "fulltext_url"):
            if not getattr(self, fld) and getattr(other, fld):
                setattr(self, fld, getattr(other, fld))
        if len(other.abstract) > len(self.abstract) * 1.2:
            self.abstract = other.abstract
        self.year = self.year or other.year
        self.citations = max(self.citations, other.citations)
        self.sample_size = max(self.sample_size, other.sample_size)
        self.design = self.design or other.design
        self.population = self.population or other.population
        self.findings = self.findings or other.findings
        self.is_oa = self.is_oa or other.is_oa
        self.has_fulltext = self.has_fulltext or other.has_fulltext
        for lst in ("pub_types", "mesh_terms", "keywords", "sources"):
            merged = list(dict.fromkeys(getattr(self, lst) + getattr(other, lst)))
            setattr(self, lst, merged)

    # ---------- sıralama ----------
    def compute_score(self, current_year: int | None = None) -> float:
        current_year = current_year or datetime.now().year
        age = max(0, current_year - self.year) if self.year else 25
        recency = max(0.0, 10.0 - age * 0.8)

        evidence = 0.0
        for pt in self.pub_types:
            evidence += EVIDENCE_WEIGHTS.get(pt.lower(), 0.0)
        evidence = max(-3.0, min(evidence, 7.0))

        impact = 2.2 * math.log10(1 + max(0, self.citations))
        access = (ACCESS_FULLTEXT_BONUS if self.has_fulltext
                  else (ACCESS_OA_BONUS if self.is_oa else 0.0))
        completeness = 1.0 if self.abstract else -4.0
        cross = 0.8 * (len(self.sources) - 1)
        topic = 2.5 * max(0.0, min(1.0, self.topic_overlap))
        off_filter = FILTER_PENALTY.get(self.filter_status, 0.0)

        self.score = round(recency + evidence + impact + access + completeness
                           + cross + topic + off_filter, 3)
        return self.score

    @property
    def evidence_rank(self) -> float:
        """Kanıt piramidindeki en yüksek basamağı. Seçim kotası bunu kullanır."""
        return max((EVIDENCE_WEIGHTS.get(pt.lower(), 0.0) for pt in self.pub_types),
                   default=0.0)

    def to_dict(self) -> dict:
        d = asdict(self)
        d.update(
            pubmed_url=self.pubmed_url,
            pmc_url=self.pmc_url,
            doi_url=self.doi_url,
            best_free_url=self.best_free_url,
            evidence_label=self.evidence_label,
        )
        return d

    @staticmethod
    def sanitize(**kwargs) -> "Article":
        kwargs["title"] = _clean(kwargs.get("title", ""))
        kwargs["abstract"] = _clean(kwargs.get("abstract", ""))
        kwargs["authors"] = _clean(kwargs.get("authors", ""))
        kwargs["journal"] = _clean(kwargs.get("journal", ""))
        return Article(**kwargs)


class Hits(list):
    """Kayıt listesi + kaynağın bildirdiği toplam eşleşme sayısı.

    Kaynaklar sorguya uyan kaydın tamamını değil, istenen sayfayı döndürür. Kullanıcıya
    "uyan 1.240 kayıttan en nitelikli 15'i okundu" diyebilmek için toplamı da taşırız;
    listenin kendisi olağan bir `list` gibi davranmaya devam eder, çağıranlar değişmez.
    """

    total: int = 0


@dataclass
class ClinicalResource:
    """Klinik başvuru kaynağı: StatPearls bölümü, kılavuz ya da derin bağlantı."""

    title: str
    url: str
    source: str                 # StatPearls / Cochrane / NICE / UpToDate ...
    kind: str = "link"          # chapter | guideline | link
    summary: str = ""
    year: str = ""
    free: bool = True
    note: str = ""

    def to_dict(self) -> dict:
        return asdict(self)
