"""Europe PMC kaynağı: ücretsiz tam metin, açık erişim durumu ve atıf sayısı."""
from __future__ import annotations

from datetime import datetime

from ..http import get_json
from ..models import Article, Hits

BASE = "https://www.ebi.ac.uk/europepmc/webservices/rest"

# Arayüzdeki filtrelerin Europe PMC karşılıkları. PUB_TYPE değerleri büyük/küçük harfe
# ve tire/boşluk farkına duyarsızdır ("meta-analysis" == "Meta-Analysis").
#
# "humans" bilerek dışarıda: Europe PMC'nin MeSH indekslemesi PubMed'inki kadar kapsamlı
# değil. Ölçüm: "heart failure" 1.299.575 kayıt döndürürken MESH:"Humans" eklenince
# 23.601'e düşüyor (%2). Bu filtreyi buraya taşımak havuzu yok eder; insan çalışması
# sınırlaması PubMed tarafında kalır.
FILTER_TERMS = {
    "rct": 'PUB_TYPE:"Randomized Controlled Trial"',
    "meta": '(PUB_TYPE:"meta-analysis" OR PUB_TYPE:"systematic review")',
    "guideline": '(PUB_TYPE:"guideline" OR PUB_TYPE:"practice guideline")',
    "review": 'PUB_TYPE:"review"',
    "free_fulltext": "(OPEN_ACCESS:y OR HAS_FT:y)",
}


def _pick_urls(result: dict) -> tuple[str, str]:
    """(pdf_url, html_fulltext_url) döndürür; yalnız açık erişim bağlantıları."""
    pdf, html = "", ""
    for item in (result.get("fullTextUrlList") or {}).get("fullTextUrl", []):
        if item.get("availabilityCode") not in ("OA", "F"):
            continue
        style = item.get("documentStyle")
        if style == "pdf" and not pdf:
            pdf = item.get("url", "")
        elif style in ("html", "doi") and not html:
            html = item.get("url", "")
    return pdf, html


def _parse(result: dict) -> Article:
    journal = ((result.get("journalInfo") or {}).get("journal") or {}).get("title", "")
    pdf, html = _pick_urls(result)
    pub_types = list((result.get("pubTypeList") or {}).get("pubType", []))
    keywords = list((result.get("keywordList") or {}).get("keyword", []))[:10]
    year = 0
    try:
        year = int(result.get("pubYear") or 0)
    except (TypeError, ValueError):
        pass
    return Article.sanitize(
        pmid=str(result.get("pmid") or ""),
        pmcid=str(result.get("pmcid") or ""),
        doi=str(result.get("doi") or ""),
        title=str(result.get("title") or ""),
        abstract=str(result.get("abstractText") or ""),
        authors=str(result.get("authorString") or ""),
        journal=str(journal),
        year=year,
        pub_types=[str(p) for p in pub_types],
        keywords=[str(k) for k in keywords],
        citations=int(result.get("citedByCount") or 0),
        is_oa=result.get("isOpenAccess") == "Y",
        pdf_url=pdf,
        fulltext_url=html,
        has_fulltext=result.get("inEPMC") == "Y" or result.get("inPMC") == "Y",
        sources=["europepmc"],
    )


def build_query(query: str, author: str = "", journal: str = "",
                filters: list[str] | None = None, recent_years: int | None = None,
                only_oa: bool = False) -> str:
    parts = [f"({query})"]
    if author:
        parts.append(f'AUTH:"{author}"')
    if journal:
        parts.append(f'JOURNAL:"{journal}"')
    if only_oa:
        parts.append("OPEN_ACCESS:y")
    for f in filters or []:
        term = FILTER_TERMS.get(f)
        if term:
            parts.append(term)
    if recent_years:
        now = datetime.now().year
        parts.append(f"(FIRST_PDATE:[{now - recent_years} TO {now}])")
    return " AND ".join(parts)


def search(query: str, author: str = "", journal: str = "", max_results: int = 20,
           recent_years: int | None = 10, only_oa: bool = False,
           filters: list[str] | None = None,
           sort_by_citations: bool = False) -> Hits:
    term = build_query(query, author, journal, filters, recent_years, only_oa)

    params = {"query": term, "format": "json", "pageSize": min(max_results, 100),
              "resultType": "core"}
    if sort_by_citations:
        params["sort"] = "CITED desc"
    data = get_json(f"{BASE}/search", params)
    out = Hits()
    if not data:
        return out
    out.total = int(data.get("hitCount") or 0)
    for res in (data.get("resultList") or {}).get("result", []):
        art = _parse(res)
        if art.title:
            out.append(art)
    return out


def citation_count(doi: str = "", pmid: str = "") -> int:
    q = f'DOI:"{doi}"' if doi else f"EXT_ID:{pmid}"
    data = get_json(f"{BASE}/search", {"query": q, "format": "json", "pageSize": 1, "resultType": "lite"})
    try:
        return int(data["resultList"]["result"][0].get("citedByCount") or 0)
    except Exception:
        return 0
