"""Yüksek güç modunun kanıt ve bulgu tabloları.

Tablolar modelden istenmez, doğrulanmış veriden kurulur: her sayı `extract.py`'nin
makale metninde birebir bulduğu ve elemeden geçirdiği bir değerdir. Modele ikinci bir
tur attırmak hem bağlamı şişirirdi hem de tek gerçek kaynağı ikiye bölerdi — tablodaki
rakamla metindeki rakam ayrışabilirdi.
"""
from __future__ import annotations

from .agreement import direction

_DIRECTION_LABEL = {
    "decrease": "decrease",
    "increase": "increase",
    "null": "not significant",
    "unknown": "—",
}


def _evidence_rows(articles: list) -> list[list]:
    rows = []
    for i, art in enumerate(articles, 1):
        rows.append([
            i,
            art.title[:120],
            art.design or art.evidence_label,
            f"{art.sample_size:,}" if art.sample_size else "—",
            art.year or "—",
            art.citations,
            art.pmid or art.pmcid or "—",
        ])
    return rows


def _finding_rows(articles: list) -> list[list]:
    rows = []
    for art in articles:
        for finding in (art.findings or []):
            rows.append([
                art.pmid or art.pmcid or "—",
                finding.get("outcome", ""),
                finding.get("measure", "") or "—",
                finding.get("value", ""),
                finding.get("ci", "") or "—",
                finding.get("p", "") or "—",
                _DIRECTION_LABEL.get(direction(finding), "—"),
            ])
    return rows


def _stats(articles: list) -> dict:
    years = [a.year for a in articles if a.year]
    designs: dict[str, int] = {}
    for art in articles:
        label = (art.design or art.evidence_label or "unspecified").strip()
        designs[label] = designs.get(label, 0) + 1

    with_findings = sum(1 for a in articles if a.findings)
    return {
        "article_count": len(articles),
        "with_findings": with_findings,
        "finding_count": sum(len(a.findings or []) for a in articles),
        "total_n": sum(a.sample_size for a in articles if a.sample_size),
        "year_min": min(years) if years else 0,
        "year_max": max(years) if years else 0,
        "median_citations": sorted(a.citations for a in articles)[len(articles) // 2]
                            if articles else 0,
        "designs": dict(sorted(designs.items(), key=lambda kv: kv[1], reverse=True)),
    }


def build(articles: list) -> dict:
    """Seçilmiş makalelerden kanıt tablosu, bulgu tablosu ve özet istatistikler."""
    if not articles:
        return {}

    finding_rows = _finding_rows(articles)
    tables = {
        "stats": _stats(articles),
        "evidence": {
            "title": "Evidence table",
            "columns": ["#", "Study", "Design", "n", "Year", "Citations", "PMID"],
            "rows": _evidence_rows(articles),
        },
    }
    # Hiçbir makaleden doğrulanmış sayı çıkmadıysa boş bir tablo göstermeyiz: boş
    # tablo, veri yokluğunu veri varmış gibi sunar.
    if finding_rows:
        tables["findings"] = {
            "title": "Numeric findings",
            "columns": ["PMID", "Outcome", "Measure", "Value", "95% CI", "p", "Direction"],
            "rows": finding_rows,
        }
    return tables


def to_markdown(tables: dict) -> str:
    """Dışa aktarımlar için markdown tablo gösterimi."""
    if not tables:
        return ""
    out = []
    for key in ("evidence", "findings"):
        table = tables.get(key)
        if not table:
            continue
        out.append(f"### {table['title']}\n")
        out.append("| " + " | ".join(str(c) for c in table["columns"]) + " |")
        out.append("|" + "---|" * len(table["columns"]))
        for row in table["rows"]:
            cells = [str(v).replace("|", "\\|") for v in row]
            out.append("| " + " | ".join(cells) + " |")
        out.append("")
    return "\n".join(out)
