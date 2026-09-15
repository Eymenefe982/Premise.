"""PubMed (NCBI Entrez) kaynağı."""
from __future__ import annotations

from datetime import datetime

from Bio import Entrez

from ..config import NCBI_API_KEY, NCBI_EMAIL
from ..models import Article, Hits
from ..ratelimit import with_retry

Entrez.email = NCBI_EMAIL or "anonymous@example.com"
if NCBI_API_KEY:
    Entrez.api_key = NCBI_API_KEY   # hız limitini 3/sn'den 10/sn'ye çıkarır

# Kullanıcı arayüzündeki filtrelerin PubMed karşılıkları
FILTER_TERMS = {
    "free_fulltext": "free full text[Filter]",
    "humans": "humans[MeSH Terms]",
    "rct": "randomized controlled trial[Publication Type]",
    "meta": "(meta-analysis[Publication Type] OR systematic review[Publication Type])",
    "guideline": "(practice guideline[Publication Type] OR guideline[Publication Type])",
    "review": "review[Publication Type]",
    "turkish_authors": "Turkey[Affiliation]",
}


def build_query(query: str, author: str = "", journal: str = "", filters: list[str] | None = None) -> str:
    parts = [f"({query})"]
    if author:
        parts.append(f"{author}[Author]")
    if journal:
        parts.append(f'"{journal}"[Journal]')
    for f in filters or []:
        term = FILTER_TERMS.get(f)
        if term:
            parts.append(term)
    return " AND ".join(parts)


def _year_from(medline: dict) -> int:
    try:
        pub_date = medline["Article"]["Journal"]["JournalIssue"]["PubDate"]
        if pub_date.get("Year"):
            return int(pub_date["Year"])
        md = pub_date.get("MedlineDate", "")
        if md[:4].isdigit():
            return int(md[:4])
    except Exception:
        pass
    return 0


def _parse_record(record: dict) -> Article | None:
    medline = record["MedlineCitation"]
    art = medline["Article"]
    pmid = str(medline["PMID"])

    abstract = ""
    if "Abstract" in art and "AbstractText" in art["Abstract"]:
        chunks = []
        for part in art["Abstract"]["AbstractText"]:
            label = part.attributes.get("Label") if hasattr(part, "attributes") else None
            chunks.append(f"{label}: {part}" if label else str(part))
        abstract = " ".join(chunks)

    authors = []
    for au in art.get("AuthorList", []):
        if au.get("LastName"):
            authors.append(f"{au.get('LastName', '')} {au.get('Initials', '')}".strip())
        elif au.get("CollectiveName"):          # çalışma grubu adına yayınlar
            authors.append(str(au["CollectiveName"]))

    doi, pmcid = "", ""
    for eloc in art.get("ELocationID", []):
        if eloc.attributes.get("EIdType") == "doi":
            doi = str(eloc)
    for aid in record.get("PubmedData", {}).get("ArticleIdList", []):
        idtype = aid.attributes.get("IdType")
        if idtype == "doi" and not doi:
            doi = str(aid)
        elif idtype == "pmc":
            pmcid = str(aid)

    mesh = []
    for mh in medline.get("MeshHeadingList", []):
        try:
            mesh.append(str(mh["DescriptorName"]))
        except Exception:
            pass

    journal = ""
    try:
        journal = str(art["Journal"].get("ISOAbbreviation") or art["Journal"].get("Title", ""))
    except Exception:
        pass

    return Article.sanitize(
        pmid=pmid,
        pmcid=pmcid,
        doi=doi,
        title=str(art.get("ArticleTitle", "")),
        abstract=abstract,
        authors=", ".join(authors),
        journal=journal,
        year=_year_from(medline),
        pub_types=[str(pt) for pt in art.get("PublicationTypeList", [])],
        mesh_terms=mesh[:12],
        sources=["pubmed"],
    )


def search(query: str, author: str = "", journal: str = "", max_results: int = 20,
           recent_years: int | None = 10, filters: list[str] | None = None) -> Hits:
    term = build_query(query, author, journal, filters)
    kwargs = dict(db="pubmed", term=term, retmax=max_results, sort="relevance")
    if recent_years:
        now = datetime.now().year
        kwargs.update(datetype="pdat", mindate=f"{now - recent_years}/01/01", maxdate=f"{now}/12/31")

    out = Hits()
    try:
        handle = with_retry(Entrez.esearch, **kwargs)
        found = Entrez.read(handle)
        ids = found.get("IdList", [])
        # Sorguya uyan toplam kayıt: kaç kaydın okunmadığını kullanıcıya söyleyebilmek için.
        out.total = int(found.get("Count") or 0)
        handle.close()
        if not ids:
            return out
        handle = with_retry(Entrez.efetch, db="pubmed", id=ids,
                            rettype="medline", retmode="xml")
        records = Entrez.read(handle)
        handle.close()
    except Exception as exc:
        print(f"[pubmed] error: {exc}")
        return out

    for rec in records.get("PubmedArticle", []):
        try:
            article = _parse_record(rec)
            if article and article.title:
                out.append(article)
        except Exception:
            continue
    return out
