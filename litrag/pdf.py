"""PDF export: the synthesis, its sections and the numeric findings tables.

The layout mirrors the on-screen report so a reader gets the same document offline.
Fonts are bundled (DejaVu Sans) so Turkish, German and French characters as well as
medical symbols render correctly on any server.
"""
from __future__ import annotations

import io
import re
from datetime import datetime

from reportlab.lib import colors
from reportlab.lib.enums import TA_JUSTIFY
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import (BaseDocTemplate, Frame, KeepTogether, PageTemplate,
                                Paragraph, Spacer, Table, TableStyle)

from .config import BASE_DIR
from .exporters import format_findings, format_sample

ACCENT = colors.HexColor("#0f766e")
INK = colors.HexColor("#101828")
INK_2 = colors.HexColor("#344054")
MUTED = colors.HexColor("#667085")
BORDER = colors.HexColor("#e4e7ec")
SOFT = colors.HexColor("#f2f4f7")

HEADING_RE = re.compile(r"^(#{1,4})\s+(.*)$")
BULLET_RE = re.compile(r"^\s*[-*•]\s+(.*)$")
NUMBERED_RE = re.compile(r"^\s*\d+\.\s+(.*)$")

_fonts_ready = False


from . import exporters  # noqa: E402  (caveats paylaşımı)


def _register_fonts() -> tuple[str, str, str]:
    """Registers the bundled Unicode fonts, falling back to reportlab's own."""
    global _fonts_ready
    regular, bold, italic = "LitSans", "LitSans-Bold", "LitSans-Italic"
    if _fonts_ready:
        return regular, bold, italic

    font_dir = BASE_DIR / "assets" / "fonts"
    faces = {
        regular: font_dir / "DejaVuSans.ttf",
        bold: font_dir / "DejaVuSans-Bold.ttf",
        italic: font_dir / "DejaVuSans-Oblique.ttf",
    }
    if all(path.exists() for path in faces.values()):
        for name, path in faces.items():
            pdfmetrics.registerFont(TTFont(name, str(path)))
        pdfmetrics.registerFontFamily(regular, normal=regular, bold=bold, italic=italic,
                                      boldItalic=bold)
        _fonts_ready = True
        return regular, bold, italic

    return "Helvetica", "Helvetica-Bold", "Helvetica-Oblique"


def _escape(text: str) -> str:
    return (str(text or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"))


def _inline(text: str) -> str:
    """Markdown emphasis and PMID citations to reportlab's inline markup."""
    out = _escape(text)
    out = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", out)
    out = re.sub(
        r"\[PMID:\s*(\d+)([^\]]*)\]",
        lambda m: (f'<link href="https://pubmed.ncbi.nlm.nih.gov/{m.group(1)}/" '
                   f'color="#0f766e">[PMID {m.group(1)}'
                   f'{" !" if "doğrulanamadı" in m.group(2) or "unverified" in m.group(2) else ""}]</link>'),
        out)
    return out


def _styles() -> dict:
    regular, bold, italic = _register_fonts()
    base = getSampleStyleSheet()["Normal"]

    def make(**kw):
        kw.setdefault("fontName", regular)
        return ParagraphStyle(parent=base, **kw)

    return {
        "title": make(name="t", fontName=bold, fontSize=19, leading=24, textColor=INK,
                      spaceAfter=5),
        "meta": make(name="m", fontSize=8.5, leading=12, textColor=MUTED, spaceAfter=3),
        "h2": make(name="h2", fontName=bold, fontSize=12.5, leading=16, textColor=INK,
                   spaceBefore=15, spaceAfter=6),
        "h3": make(name="h3", fontName=bold, fontSize=10.5, leading=14, textColor=INK_2,
                   spaceBefore=10, spaceAfter=4),
        "body": make(name="b", fontSize=9.6, leading=14.6, textColor=INK_2,
                     alignment=TA_JUSTIFY, spaceAfter=7),
        "bullet": make(name="li", fontSize=9.6, leading=14.6, textColor=INK_2,
                       leftIndent=11, bulletIndent=2, spaceAfter=4),
        "bibtitle": make(name="bt", fontName=bold, fontSize=9.6, leading=13, textColor=INK),
        "bibmeta": make(name="bm", fontSize=8.3, leading=11.5, textColor=MUTED, spaceBefore=2),
        "bibtags": make(name="bg", fontSize=8.3, leading=11.5, textColor=ACCENT, spaceBefore=2),
        "cell": make(name="c", fontSize=8.2, leading=11, textColor=INK_2),
        "cellnum": make(name="cn", fontName=bold, fontSize=8.2, leading=11, textColor=INK),
        "note": make(name="n", fontName=italic, fontSize=8.2, leading=11.5, textColor=MUTED),
    }


def _page_furniture(canvas, doc, heading: str) -> None:
    canvas.saveState()
    regular, _, _ = _register_fonts()
    canvas.setFont(regular, 7.5)
    canvas.setFillColor(MUTED)
    canvas.drawString(18 * mm, 12 * mm, heading[:95])
    canvas.drawRightString(A4[0] - 18 * mm, 12 * mm, str(canvas.getPageNumber()))
    canvas.setStrokeColor(BORDER)
    canvas.setLineWidth(0.5)
    canvas.line(18 * mm, 15.5 * mm, A4[0] - 18 * mm, 15.5 * mm)
    canvas.restoreState()


def _report_flowables(report: str, st: dict) -> list:
    flow: list = []
    for raw in (report or "").splitlines():
        line = raw.strip()
        if not line:
            continue
        heading = HEADING_RE.match(line)
        if heading:
            level = min(max(len(heading.group(1)), 2), 3)
            flow.append(Paragraph(_inline(heading.group(2)), st["h2" if level == 2 else "h3"]))
            continue
        bullet = BULLET_RE.match(line) or NUMBERED_RE.match(line)
        if bullet:
            flow.append(Paragraph(_inline(bullet.group(1)), st["bullet"], bulletText="•"))
            continue
        flow.append(Paragraph(_inline(line), st["body"]))
    return flow


def _findings_table(article: dict, st: dict) -> Table | None:
    findings = article.get("findings") or []
    if not findings:
        return None
    rows = []
    for f in findings:
        value = " ".join(x for x in (f.get("measure", ""), f.get("value", "")) if x)
        interval = f"95% CI {f['ci']}" if f.get("ci") else ""
        p_value = f"p {f['p']}" if f.get("p") else ""
        rows.append([Paragraph(_escape(f.get("outcome", "")), st["cell"]),
                     Paragraph(_escape(value), st["cellnum"]),
                     Paragraph(_escape(interval), st["cell"]),
                     Paragraph(_escape(p_value), st["cell"])])

    table = Table(rows, colWidths=[72 * mm, 26 * mm, 40 * mm, 24 * mm], hAlign="LEFT")
    table.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("TOPPADDING", (0, 0), (-1, -1), 3),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
        ("LEFTPADDING", (0, 0), (0, -1), 0),
        ("LINEABOVE", (0, 0), (-1, 0), 0.5, BORDER),
        ("LINEBELOW", (0, 0), (-1, -2), 0.25, BORDER),
        ("BACKGROUND", (0, 0), (-1, -1), colors.Color(0, 0, 0, 0)),
    ]))
    return table


def _bibliography_flowables(articles: list[dict], st: dict) -> list:
    flow: list = [Paragraph("References and numeric findings", st["h2"]),
                  Paragraph("Every number is copied verbatim from the source article and "
                            "verified against its text. Nothing is calculated or inferred.",
                            st["note"]),
                  Spacer(1, 5)]

    for index, a in enumerate(articles, 1):
        block: list = [Paragraph(f"{index}. {_escape(a.get('title', ''))}", st["bibtitle"])]

        authors = a.get("authors") or "Authors not listed in the record"
        journal = " · ".join(x for x in (a.get("journal", ""), str(a.get("year") or "")) if x)
        block.append(Paragraph(_escape(f"{authors}{' · ' + journal if journal else ''}"),
                               st["bibmeta"]))

        tags = [a.get("evidence_label", ""), a.get("design", ""), format_sample(a),
                f"{a.get('citations', 0)} citations" if a.get("citations") else "",
                "free full text" if (a.get("is_oa") or a.get("pmcid")) else "subscription"]
        block.append(Paragraph(_escape(" · ".join(t for t in tags if t)), st["bibtags"]))

        table = _findings_table(a, st)
        if table is not None:
            block += [Spacer(1, 3), table]

        links = []
        if a.get("pmid"):
            links.append(f'<link href="{a["pubmed_url"]}" color="#0f766e">PMID {a["pmid"]}</link>')
        if a.get("doi"):
            links.append(f'<link href="{a["doi_url"]}" color="#0f766e">DOI {_escape(a["doi"])}</link>')
        if a.get("pdf_url"):
            links.append(f'<link href="{_escape(a["pdf_url"])}" color="#0f766e">Free PDF</link>')
        if links:
            block.append(Paragraph(" · ".join(links), st["bibmeta"]))

        block.append(Spacer(1, 9))
        flow.append(KeepTogether(block))
    return flow


def to_pdf(result: dict) -> bytes:
    """Renders the whole result - question, synthesis, references, findings - as a PDF."""
    st = _styles()
    translation = result.get("translation") or {}
    heading = translation.get("topic") or result.get("query", "Literature report")

    buffer = io.BytesIO()
    doc = BaseDocTemplate(buffer, pagesize=A4,
                          leftMargin=18 * mm, rightMargin=18 * mm,
                          topMargin=16 * mm, bottomMargin=20 * mm,
                          title=heading, author="Premise")
    frame = Frame(doc.leftMargin, doc.bottomMargin, doc.width, doc.height, id="main")
    doc.addPageTemplates([PageTemplate(id="all", frames=[frame],
                                       onPage=lambda c, d: _page_furniture(c, d, heading))])

    articles = result.get("articles", [])
    generated_at = result.get("generated_at") or datetime.now().strftime("%Y-%m-%d %H:%M")
    summary = (f"{len(articles)} articles selected · "
               f"{result.get('pool_size', 0)} records screened · "
               f"{result.get('fulltext_count', 0)} full texts read · {generated_at}")
    flow: list = [
        Paragraph(_escape(result.get("query", "")), st["title"]),
        Paragraph(_escape(summary), st["meta"]),
    ]
    if translation.get("pubmed_query"):
        flow.append(Paragraph("PubMed query: " + _escape(translation["pubmed_query"]), st["meta"]))
    flow.append(Spacer(1, 8))

    # Uyarılar raporun ÜSTÜNDE durur: belgeyi açan kişi cevabı okumadan önce ne kadar
    # güvenebileceğini görmeli, dipnotta aramak zorunda kalmamalı.
    notes = exporters.caveats(result)
    if notes:
        flow.append(Paragraph("How far this answer can be trusted", st["h3"]))
        for note in notes:
            flow.append(Paragraph(_escape(note), st["bullet"], bulletText="•"))
        flow.append(Spacer(1, 6))

    flow += _report_flowables(result.get("report", ""), st)
    flow.append(Spacer(1, 10))
    flow += _bibliography_flowables(articles, st)

    flow += [Spacer(1, 6),
             Paragraph("Generated by Premise from PubMed, PubMed Central and Europe PMC. "
                       "This document is not clinical decision support: verify every claim "
                       "against the cited articles before acting on it.", st["note"])]

    doc.build(flow)
    return buffer.getvalue()
