"""Dışa aktarma: Word, PDF, RIS (Zotero/Mendeley/EndNote), BibTeX, Markdown ve CSV."""
from __future__ import annotations

import csv
import io
import re
import unicodedata
from datetime import datetime
from urllib.parse import quote

from docx import Document
from docx.shared import Pt

HEADING_RE = re.compile(r"^(#{1,4})\s+(.*)$")
BULLET_RE = re.compile(r"^\s*[-*•]\s+(.*)$")


TR_MAP = str.maketrans("çğıİöşüÇĞÖŞÜ", "cgiIosuCGOSU")


def _slug(text: str, limit: int = 40) -> str:
    text = re.sub(r"[^\w\s-]", "", text, flags=re.UNICODE).strip()
    return re.sub(r"[\s_]+", "-", text)[:limit] or "rapor"


def _ascii(text: str) -> str:
    """HTTP başlıklarında kullanılabilecek ASCII dosya adı."""
    folded = unicodedata.normalize("NFKD", text.translate(TR_MAP))
    return folded.encode("ascii", "ignore").decode("ascii") or "litrag-rapor"


def content_disposition(name: str) -> str:
    """Türkçe karakterli dosya adlarını RFC 5987 uyumlu biçimde başlığa koyar."""
    return f"attachment; filename=\"{_ascii(name)}\"; filename*=UTF-8''{quote(name)}"


def format_findings(article: dict) -> str:
    """Sayısal bulguları tek satırlık okunur metne çevirir."""
    parts = []
    for f in article.get("findings") or []:
        chunk = f"{f.get('outcome', '')}: {f.get('measure', '')} {f.get('value', '')}".strip()
        if f.get("ci"):
            chunk += f" (95% CI {f['ci']})"
        if f.get("p"):
            chunk += f", p {f['p']}"
        parts.append(chunk)
    return " | ".join(parts)


def format_sample(article: dict) -> str:
    n = article.get("sample_size") or 0
    return f"n = {n:,}" if n else ""


def filename(result: dict, extension: str) -> str:
    topic = (result.get("translation") or {}).get("topic") or result.get("query", "rapor")
    return f"{_slug(topic)}-{datetime.now():%Y%m%d-%H%M}.{extension}"


# ------------------------------------------------------------------ Markdown
def caveats(result: dict) -> list[str]:
    """Raporun ne kadar sağlam olduğunu söyleyen uyarılar.

    Dışa aktarılan belge uygulamadan koparak dolaşır: bir meslektaşa yollanır, dosyaya
    konur, aylar sonra okunur. Ekranda görünen "sınırlı cevap" uyarısı PDF'e geçmezse
    belge olduğundan daha güvenilir görünür, bu yüzden uyarılar her biçime yazılır.
    """
    out: list[str] = []
    mode = result.get("answer_mode")
    if mode == "none":
        out.append("No synthesis was written for this search: none of the records found "
                   "address the question directly.")
    elif mode == "limited":
        out.append(f"LIMITED ANSWER: only {result.get('relevant_count', 0)} article(s) address "
                   f"this question directly, which is not enough to support a clinical "
                   f"recommendation. No clinical implications section was written.")
    if result.get("low_evidence"):
        out.append(f"This answer rests on {len(result.get('articles', []))} article(s) - too few "
                   f"to settle the question on their own.")
    if result.get("broadened"):
        relaxed = " and ".join(result.get("relaxed") or []) or "some limits"
        out.append(f"The first search returned too little, so it was repeated without the "
                   f"{relaxed} you set. Some listed articles fall outside your criteria.")
    if result.get("off_filter_count"):
        out.append(f"{result['off_filter_count']} of the listed articles do not match the "
                   f"publication type filter you chose; they were ranked down, not removed.")
    if result.get("dropped_findings"):
        out.append(f"{result['dropped_findings']} extracted number(s) could not be found "
                   f"verbatim in the source text and were dropped before this report.")
    for flag in result.get("flagged_claims") or []:
        out.append(f"Short answer sentence flagged as \"{flag.get('verdict')}\" "
                   f"({flag.get('reason', '')}): {flag.get('sentence', '')}")
    return out


def to_markdown(result: dict) -> str:
    lines = [f"# {result.get('query', '')}", ""]
    tr = result.get("translation") or {}
    if tr.get("pubmed_query"):
        lines += [f"**PubMed query:** `{tr['pubmed_query']}`", ""]
    lines += [f"*{result.get('generated_at', '')} · {len(result.get('articles', []))} articles · "
              f"{result.get('fulltext_count', 0)} full texts · {result.get('elapsed', 0)} s*", "",
              result.get("report", ""), ""]
    notes = caveats(result)
    if notes:
        lines += ["## How far this answer can be trusted", ""]
        lines += [f"- {n}" for n in notes] + [""]
    for group in result.get("agreement") or []:
        if group is result.get("agreement")[0]:
            lines += ["## Direction of agreement across studies", ""]
        counts = ", ".join(f"{v} {k}" for k, v in group["counts"].items() if v)
        lines.append(f"- **{group['outcome']}**: {counts}"
                     + ("  *(conflicting)*" if group.get("conflicting") else ""))
        for e in group["entries"]:
            ci = f" (95% CI {e['ci']})" if e.get("ci") else ""
            lines.append(f"    - PMID {e['pmid']} · {e['measure']} {e['value']}{ci} "
                         f"→ {e['direction']}")
    if result.get("agreement"):
        lines.append("")
    lines += ["## References and numeric findings", ""]
    for i, a in enumerate(result.get("articles", []), 1):
        access = "open access" if a.get("is_oa") else "subscription"
        lines.append(
            f"{i}. **{a.get('title', '')}** — {a.get('authors', '')} · "
            f"{a.get('journal', '')} {a.get('year', '')} · {a.get('evidence_label', '')} · "
            f"{a.get('citations', 0)} atıf · {access}  \n"
            f"   PMID {a.get('pmid', '-')} · DOI {a.get('doi', '-')} · {a.get('best_free_url', '')}")
    return "\n".join(lines)


# ----------------------------------------------------------------------- Word
def to_docx(result: dict) -> bytes:
    doc = Document()
    style = doc.styles["Normal"]
    style.font.name = "Calibri"
    style.font.size = Pt(11)

    doc.add_heading(result.get("query", "Literature report"), level=0)
    meta = doc.add_paragraph()
    meta.add_run(
        f"{result.get('generated_at', '')} · {len(result.get('articles', []))} articles · "
        f"{result.get('fulltext_count', 0)} full texts · "
        f"sources: PubMed, PubMed Central, Europe PMC"
    ).italic = True

    notes = caveats(result)
    if notes:
        doc.add_heading("How far this answer can be trusted", level=2)
        for note in notes:
            doc.add_paragraph(note, style="List Bullet")

    for raw in (result.get("report") or "").splitlines():
        line = raw.rstrip()
        if not line:
            continue
        heading = HEADING_RE.match(line)
        if heading:
            doc.add_heading(heading.group(2).strip(), level=min(len(heading.group(1)), 4))
            continue
        bullet = BULLET_RE.match(line)
        if bullet:
            doc.add_paragraph(bullet.group(1).strip(), style="List Bullet")
            continue
        doc.add_paragraph(re.sub(r"\*\*(.+?)\*\*", r"\1", line))

    doc.add_page_break()
    doc.add_heading("References and numeric findings", level=1)
    note = doc.add_paragraph()
    note.add_run("Every number is copied verbatim from the source article, never calculated."
                 ).italic = True

    table = doc.add_table(rows=1, cols=6)
    table.style = "Light Grid Accent 1"
    headers = ["#", "Title", "Journal / year", "Type / n", "Numeric findings", "PMID / DOI"]
    for cell, text in zip(table.rows[0].cells, headers):
        cell.text = text
    for i, a in enumerate(result.get("articles", []), 1):
        cells = table.add_row().cells
        cells[0].text = str(i)
        cells[1].text = a.get("title", "")
        cells[2].text = f"{a.get('journal', '')} {a.get('year', '')}".strip()
        cells[3].text = " · ".join(x for x in (a.get("evidence_label", ""), format_sample(a)) if x)
        cells[4].text = format_findings(a)
        cells[5].text = f"{a.get('pmid', '-')} / {a.get('doi', '-')}"

    buffer = io.BytesIO()
    doc.save(buffer)
    return buffer.getvalue()


# ------------------------------------------------------------------------ RIS
def to_ris(articles: list[dict]) -> str:
    out = []
    for a in articles:
        out.append("TY  - JOUR")
        out.append(f"TI  - {a.get('title', '')}")
        for author in [x.strip() for x in (a.get("authors") or "").split(",") if x.strip()]:
            out.append(f"AU  - {author}")
        if a.get("journal"):
            out.append(f"JO  - {a['journal']}")
        if a.get("year"):
            out.append(f"PY  - {a['year']}")
        if a.get("abstract"):
            out.append(f"AB  - {a['abstract']}")
        if a.get("doi"):
            out.append(f"DO  - {a['doi']}")
        if a.get("best_free_url"):
            out.append(f"UR  - {a['best_free_url']}")
        if a.get("pmid"):
            out.append(f"AN  - {a['pmid']}")
            out.append(f"C1  - PMID: {a['pmid']}")
        for kw in (a.get("mesh_terms") or [])[:8]:
            out.append(f"KW  - {kw}")
        out.append("ER  - ")
        out.append("")
    return "\n".join(out)


# --------------------------------------------------------------------- BibTeX
def _bib_key(a: dict, index: int) -> str:
    first = (a.get("authors") or "anon").split(",")[0].split(" ")[0].lower()
    return f"{re.sub(r'[^a-z]', '', first) or 'anon'}{a.get('year') or ''}{index}"


def to_bibtex(articles: list[dict]) -> str:
    out = []
    for i, a in enumerate(articles, 1):
        authors = " and ".join(x.strip() for x in (a.get("authors") or "").split(",") if x.strip())
        fields = [
            f"  title = {{{a.get('title', '')}}}",
            f"  author = {{{authors}}}",
            f"  journal = {{{a.get('journal', '')}}}",
            f"  year = {{{a.get('year', '')}}}",
        ]
        if a.get("doi"):
            fields.append(f"  doi = {{{a['doi']}}}")
        if a.get("pmid"):
            fields.append(f"  pmid = {{{a['pmid']}}}")
        if a.get("best_free_url"):
            fields.append(f"  url = {{{a['best_free_url']}}}")
        out.append("@article{" + _bib_key(a, i) + ",\n" + ",\n".join(fields) + "\n}")
    return "\n\n".join(out)


# ------------------------------------------------------------------------ CSV
def _cell(value):
    """Excel'de formül olarak çalışabilecek hücreleri (CSV enjeksiyonu) etkisizleştirir."""
    if isinstance(value, str) and value[:1] in ("=", "+", "-", "@", "\t", "\r"):
        return "'" + value
    return value


def to_csv(articles: list[dict]) -> str:
    buffer = io.StringIO()
    writer = csv.writer(buffer, delimiter=";", quoting=csv.QUOTE_MINIMAL, lineterminator="\n")
    writer.writerow(["Title", "Authors", "Journal", "Year", "Publication type", "Design",
                     "Population", "n", "Numeric findings", "Citations", "Open access",
                     "PMID", "DOI", "Link"])
    for a in articles:
        writer.writerow([_cell(v) for v in (
            a.get("title", ""), a.get("authors", ""), a.get("journal", ""),
            a.get("year", ""), a.get("evidence_label", ""), a.get("design", ""),
            a.get("population", ""), a.get("sample_size") or "",
            format_findings(a), a.get("citations", 0),
            "Yes" if a.get("is_oa") else "No", a.get("pmid", ""),
            a.get("doi", ""), a.get("best_free_url", ""))])
    return buffer.getvalue()
