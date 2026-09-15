"""Açık erişim zenginleştirme: OpenAlex (atıf sayısı) ve Unpaywall (ücretsiz PDF)."""
from __future__ import annotations

import concurrent.futures

from ..config import CONTACT_EMAIL
from ..http import get_json
from ..models import Article

OPENALEX = "https://api.openalex.org/works"
UNPAYWALL = "https://api.unpaywall.org/v2/{doi}"


def openalex_lookup(doi: str) -> dict:
    if not doi:
        return {}
    data = get_json(OPENALEX, {"filter": f"doi:{doi}", "mailto": CONTACT_EMAIL, "per_page": 1})
    results = (data or {}).get("results") or []
    if not results:
        return {}
    work = results[0]
    oa = work.get("open_access") or {}
    return {
        "citations": int(work.get("cited_by_count") or 0),
        "is_oa": bool(oa.get("is_oa")),
        "oa_url": oa.get("oa_url") or "",
        "oa_status": oa.get("oa_status") or "",
    }


def unpaywall_lookup(doi: str) -> dict:
    if not doi or not CONTACT_EMAIL:
        return {}
    data = get_json(UNPAYWALL.format(doi=doi), {"email": CONTACT_EMAIL})
    if not data:
        return {}
    best = data.get("best_oa_location") or {}
    return {
        "is_oa": bool(data.get("is_oa")),
        "pdf_url": best.get("url_for_pdf") or "",
        "landing_url": best.get("url_for_landing_page") or "",
        "oa_status": data.get("oa_status") or "",
    }


def _enrich_one(article: Article, use_unpaywall: bool) -> Article:
    if not article.doi:
        return article
    info = openalex_lookup(article.doi)
    if info:
        article.citations = max(article.citations, info.get("citations", 0))
        article.is_oa = article.is_oa or info.get("is_oa", False)
        if not article.pdf_url and info.get("oa_url"):
            article.pdf_url = info["oa_url"]
        if "openalex" not in article.sources:
            article.sources.append("openalex")

    if use_unpaywall and not article.pdf_url:
        up = unpaywall_lookup(article.doi)
        if up:
            article.is_oa = article.is_oa or up.get("is_oa", False)
            article.pdf_url = up.get("pdf_url") or article.pdf_url
            if not article.fulltext_url:
                article.fulltext_url = up.get("landing_url", "")
            if up.get("is_oa") and "unpaywall" not in article.sources:
                article.sources.append("unpaywall")
    return article


def enrich(articles: list[Article], use_unpaywall: bool = True, max_workers: int = 8) -> list[Article]:
    """Makaleleri paralel olarak atıf sayısı ve ücretsiz PDF bağlantısıyla zenginleştirir."""
    if not articles:
        return articles
    with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as pool:
        list(pool.map(lambda a: _enrich_one(a, use_unpaywall), articles))
    return articles
