"""PubMed Central: açık erişim tam metinlerin çekilmesi ve okunabilir hale getirilmesi."""
from __future__ import annotations

import re
from xml.etree import ElementTree as ET

from defusedxml import DefusedXmlException
from defusedxml.ElementTree import fromstring as safe_fromstring

from ..config import FULLTEXT_CHAR_LIMIT
from ..http import get_text

EPMC_FULLTEXT = "https://www.ebi.ac.uk/europepmc/webservices/rest/{pmcid}/fullTextXML"

# RAG için değerli bölümler; kaynakça/teşekkür gibi gürültü dışarıda bırakılır
WANTED = ("introduction", "background", "method", "material", "result", "finding",
          "discussion", "conclusion", "implication", "limitation")
SKIP = ("reference", "acknowledg", "funding", "conflict", "supplementary",
        "author contribution", "competing interest", "abbreviation")


def _text_of(node: ET.Element) -> str:
    return re.sub(r"\s+", " ", "".join(node.itertext())).strip()


def fetch_fulltext(pmcid: str, char_limit: int = FULLTEXT_CHAR_LIMIT) -> str:
    """PMC açık erişim tam metnini düz metne çevirir. Erişilemezse boş döner."""
    if not pmcid:
        return ""
    pmcid = pmcid if pmcid.upper().startswith("PMC") else f"PMC{pmcid}"
    xml = get_text(EPMC_FULLTEXT.format(pmcid=pmcid))
    if not xml or "<article" not in xml:
        return ""
    # Dış kaynaktan gelen XML: varlık genişletme bombalarına karşı defusedxml.
    try:
        root = safe_fromstring(xml.encode("utf-8"))
    except (ET.ParseError, DefusedXmlException):
        return ""

    body = root.find(".//body")
    if body is None:
        return ""

    chunks: list[str] = []
    for sec in body.findall(".//sec"):
        title_node = sec.find("title")
        title = _text_of(title_node) if title_node is not None else ""
        low = title.lower()
        if any(s in low for s in SKIP):
            continue
        paragraphs = [_text_of(p) for p in sec.findall("./p")]
        text = " ".join(t for t in paragraphs if t)
        if not text:
            continue
        if title and not any(w in low for w in WANTED) and len(text) < 220:
            continue
        chunks.append(f"## {title}\n{text}" if title else text)

    if not chunks:                       # bölümsüz makaleler için kaba geri dönüş
        chunks = [_text_of(body)]

    full = "\n\n".join(chunks)
    return full[:char_limit]


def has_open_fulltext(pmcid: str) -> bool:
    return bool(pmcid)
