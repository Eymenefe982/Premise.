"""Klinik başvuru katmanı: StatPearls, kılavuzlar, Cochrane ve hızlı erişim bağlantıları.

Not: UpToDate abonelik gerektiren, telif korumalı bir kaynaktır. İçeriği kazınmaz (scrape
edilmez); yalnızca kullanıcının kendi aboneliği veya kurumsal erişimiyle (TÜBİTAK ULAKBİM
EKUAL) açabileceği arama bağlantısı üretilir. Ücretsiz muadilleri ayrıca listelenir.
"""
from __future__ import annotations

from urllib.parse import quote_plus

from Bio import Entrez

from ..models import Article, ClinicalResource
from ..ratelimit import with_retry
from . import pubmed

BOOK_URL = "https://www.ncbi.nlm.nih.gov/books/{rid}/"


def search_statpearls(query: str, max_results: int = 6) -> list[ClinicalResource]:
    """StatPearls: NCBI Bookshelf üzerinde tamamen ücretsiz klinik başvuru bölümleri."""
    try:
        handle = with_retry(Entrez.esearch, db="books",
                            term=f"({query}) AND statpearls[book] AND chapter[Type]",
                            retmax=max_results * 2, sort="relevance")
        ids = Entrez.read(handle).get("IdList", [])
        handle.close()
        if not ids:
            return []
        handle = with_retry(Entrez.esummary, db="books", id=",".join(ids))
        summaries = Entrez.read(handle)
        handle.close()
    except Exception as exc:
        print(f"[statpearls] error: {exc}")
        return []

    out: list[ClinicalResource] = []
    for item in summaries:
        if str(item.get("RType")) != "chapter":
            continue
        rid = str(item.get("RID") or "")
        if not rid:
            continue
        out.append(ClinicalResource(
            title=str(item.get("Title") or "").strip(),
            url=BOOK_URL.format(rid=rid),
            source="StatPearls (NCBI Bookshelf)",
            kind="chapter",
            year=str(item.get("PubDate") or "")[:4],
            free=True,
            note="Free full-text point-of-care chapter",
        ))
        if len(out) >= max_results:
            break
    return out


def search_guidelines(query: str, max_results: int = 8, recent_years: int = 12) -> list[Article]:
    """PubMed'de kılavuz / uzlaşı raporu tipindeki yayınlar."""
    return pubmed.search(query, max_results=max_results, recent_years=recent_years,
                         filters=["guideline"])


def search_cochrane(query: str, max_results: int = 5) -> list[Article]:
    """Cochrane Database of Systematic Reviews kayıtları (özetler ücretsiz)."""
    term = f'({query}) AND "Cochrane database of systematic reviews"[Journal]'
    return pubmed.search(term, max_results=max_results, recent_years=15)


def quick_links(query: str) -> list[ClinicalResource]:
    """Klinik karar destek kaynaklarına doğrudan arama bağlantıları."""
    q = quote_plus(query)
    return [
        ClinicalResource(
            title=f"Search UpToDate: {query}",
            url=f"https://www.uptodate.com/contents/search?search={q}",
            source="UpToDate", kind="link", free=False,
            note="Subscription required. Opens through your hospital or university "
                 "access; this app never copies UpToDate content.",
        ),
        ClinicalResource(
            title=f"Cochrane Library: {query}",
            url=f"https://www.cochranelibrary.com/search?q={q}",
            source="Cochrane Library", kind="link", free=True,
            note="Systematic review abstracts are free to read",
        ),
        ClinicalResource(
            title=f"TRIP Database: {query}",
            url=f"https://www.tripdatabase.com/search?criteria={q}",
            source="TRIP", kind="link", free=True,
            note="Clinical search ranked by the evidence pyramid",
        ),
        ClinicalResource(
            title=f"NICE guidance: {query}",
            url=f"https://www.nice.org.uk/search?q={q}",
            source="NICE", kind="link", free=True,
            note="UK national clinical guidelines, free to read",
        ),
        ClinicalResource(
            title=f"PubMed Clinical Queries: {query}",
            url=f"https://pubmed.ncbi.nlm.nih.gov/clinical/?term={q}",
            source="PubMed", kind="link", free=True,
            note="Filtered for therapy, diagnosis and prognosis studies",
        ),
        ClinicalResource(
            title=f"Europe PMC full-text search: {query}",
            url=f"https://europepmc.org/search?query={q}%20AND%20OPEN_ACCESS%3Ay",
            source="Europe PMC", kind="link", free=True,
            note="Open access full texts only",
        ),
        ClinicalResource(
            title="Institutional full-text access (EKUAL, Turkiye)",
            url="https://cabim.ulakbim.gov.tr/ekual/",
            source="TUBITAK ULAKBIM", kind="link", free=True,
            note="National licence covering Turkish universities and hospitals; "
                 "sign in from the institution network or its proxy.",
        ),
        ClinicalResource(
            title=f"Search TR Dizin (Turkish journals): {query}",
            url=f"https://search.trdizin.gov.tr/tr/yayin/ara?q={q}",
            source="TR Dizin", kind="link", free=True,
            note="Index of Turkish peer-reviewed journals",
        ),
    ]
