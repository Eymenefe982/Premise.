"""Premise terminal arayüzü.

Örnekler:
    python cli.py "What is the levothyroxine target in pregnancy?"
    python cli.py "atrial fibrillation ablation" --sayi 20 --yil 5 --filtre meta rct
    python cli.py "vitamin C in sepsis" --disa-aktar pdf docx --dil Türkçe
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from litrag import exporters
from litrag.pdf import to_pdf
from litrag.pipeline import SearchRequest, run_search
from litrag.store import save_report

# Windows konsolunda Türkçe karakterler için
if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

C = {"dim": "\033[2m", "bold": "\033[1m", "teal": "\033[36m", "green": "\033[32m",
     "yellow": "\033[33m", "red": "\033[31m", "off": "\033[0m"}


def paint(text: str, color: str) -> str:
    return f"{C[color]}{text}{C['off']}"


def progress_printer(message: str, pct: float) -> None:
    filled = int(pct * 28)
    bar = "█" * filled + "░" * (28 - filled)
    sys.stdout.write(f"\r  {paint(bar, 'teal')} {int(pct * 100):3d}%  {message[:60]:<60}")
    sys.stdout.flush()
    if pct >= 1.0:
        sys.stdout.write("\n")


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Evidence-based literature search across PubMed, PubMed Central and Europe PMC.")
    parser.add_argument("sorgu", help="Clinical question or research topic")
    parser.add_argument("--sayi", type=int, default=15, help="How many articles to read (default 15)")
    parser.add_argument("--yil", type=int, default=10, help="Published within the last N years (0 = no limit)")
    parser.add_argument("--dil", default="English", help="Answer language (default English)")
    parser.add_argument("--yazar", default="", help="Author filter, e.g. 'Smith J'")
    parser.add_argument("--dergi", default="", help="Journal filter, e.g. 'Lancet'")
    parser.add_argument("--filtre", nargs="*", default=[],
                        choices=["meta", "rct", "guideline", "review", "free_fulltext",
                                 "humans", "turkish_authors"],
                        help="PubMed publication type filters")
    parser.add_argument("--sadece-acik", action="store_true", help="Open access articles only")
    parser.add_argument("--tam-metin-yok", action="store_true", help="Skip downloading PMC full texts")
    parser.add_argument("--sentez-yok", action="store_true", help="Skip the AI synthesis, just list the articles")
    parser.add_argument("--sayisal-yok", action="store_true",
                        help="Skip effect size and confidence interval extraction (faster)")
    parser.add_argument("--disa-aktar", nargs="*", default=[],
                        choices=["pdf", "docx", "md", "ris", "bib", "csv"], help="Formats to save")
    parser.add_argument("--klasor", default="exports", help="Export folder")
    args = parser.parse_args()

    request = SearchRequest.from_dict({
        "query": args.sorgu, "author": args.yazar, "journal": args.dergi,
        "language": args.dil, "max_articles": args.sayi, "recent_years": args.yil,
        "filters": args.filtre, "only_open_access": args.sadece_acik,
        "use_fulltext": not args.tam_metin_yok, "synthesize": not args.sentez_yok,
        "extract_stats": not args.sayisal_yok,
    })

    print(paint("\n  LitRAG", "bold") + paint("  ·  PubMed · PubMed Central · Europe PMC\n", "dim"))
    try:
        result = run_search(request, progress=progress_printer)
    except Exception as exc:
        print(paint(f"\n  Error: {exc}\n", "red"))
        return 1

    report_id = save_report(result, request.language)
    translation = result["translation"]

    print(paint(f"\n  PubMed query: {translation['pubmed_query']}", "dim"))
    matched = max(result.get("coverage", {}).get("pubmed", 0),
                  result.get("coverage", {}).get("europepmc", 0))
    coverage = f"{matched:,} records matched · " if matched else ""
    print(paint(f"  {coverage}{result['pool_size']} records screened · "
                f"{len(result['articles'])} articles selected · "
                f"{result['fulltext_count']} full texts read · {result['elapsed']} s "
                f"· saved as #{report_id}\n", "dim"))

    if result.get("answer_mode") == "none":
        print(paint("  NO ARTICLE IN THE RESULTS ANSWERS THIS QUESTION", "bold"))
        print(paint(f"  The search returned {result['pool_size']} records, but none of them "
                    f"address your question directly.\n  No synthesis was written: a confident, "
                    f"cited answer built on unrelated papers would mislead more\n  than an empty "
                    f"result. The records found are listed below.\n", "yellow"))
        for tip in result.get("suggestions", []):
            print(paint(f"   · {tip}", "dim"))
        print()
    elif result.get("answer_mode") == "limited":
        print(paint(f"  LIMITED ANSWER — only {result.get('relevant_count', 0)} article(s) "
                    f"address this question directly.\n  No clinical recommendation is drawn; "
                    f"the studies are reported one by one.\n", "yellow"))

    if result["report"]:
        print(result["report"])
        print()

    for flag in result.get("flagged_claims", []):
        print(paint(f"  ! Short answer sentence flagged as \"{flag['verdict']}\": "
                    f"{flag['reason']}", "yellow"))
        print(paint(f"    {flag['sentence'][:160]}", "dim"))
    if result.get("flagged_claims"):
        print()

    for group in result.get("agreement", []):
        counts = " · ".join(f"{v} {k}" for k, v in group["counts"].items() if v)
        head = f"  {group['outcome']}: {counts}"
        print(paint(head + ("  [conflicting]" if group["conflicting"] else ""), "bold"))
        for e in group["entries"]:
            ci = f" (95% CI {e['ci']})" if e.get("ci") else ""
            print(paint(f"      PMID {e['pmid']:<10} {e['measure']} {e['value']}{ci}"
                        f"  -> {e['direction']}", "dim"))
    if result.get("agreement"):
        print()

    print(paint("  REFERENCES AND NUMERIC FINDINGS", "bold"))
    if result.get("dropped_findings"):
        print(paint(f"  {result['dropped_findings']} extracted number(s) could not be found "
                    f"verbatim in the source text and were dropped.", "dim"))
    for i, a in enumerate(result["articles"], 1):
        access = (paint("free", "green") if (a["is_oa"] or a["pmcid"])
                  else paint("subscription", "yellow"))
        extra = " · ".join(x for x in (a.get("design", ""),
                                       f"n = {a['sample_size']:,}"
                                       if a.get("sample_size") else "") if x)
        print(f"  {i:2d}. {a['title'][:88]}")
        print(paint(f"      {a['journal']} {a['year']} · {a['evidence_label']} · "
                    f"{a['citations']} citations · ", "dim") + access +
              paint(f" · PMID {a['pmid'] or '-'}" + (f" · {extra}" if extra else ""), "dim"))
        for f in a.get("findings") or []:
            ci = f" (95% CI {f['ci']})" if f.get("ci") else ""
            pv = f", p {f['p']}" if f.get("p") else ""
            print(f"      → {f['outcome']}: " +
                  paint(f"{f.get('measure', '')} {f['value']}", "teal") + paint(ci + pv, "dim"))
        print(paint(f"      {a['best_free_url']}", "dim"))

    chapters = result["clinical"]["statpearls"]
    if chapters:
        print(paint("\n  FREE POINT-OF-CARE CHAPTERS (StatPearls)", "bold"))
        for c in chapters:
            print(f"   · {c['title'][:70]}  {paint(c['url'], 'dim')}")

    links = [l for l in result["clinical"]["links"] if not l["free"]]
    if links:
        print(paint("\n  SUBSCRIPTION RESOURCES (open with your own institutional access)", "bold"))
        for l in links:
            print(f"   · {l['source']}: {paint(l['url'], 'dim')}")

    if args.disa_aktar:
        folder = Path(args.klasor)
        folder.mkdir(parents=True, exist_ok=True)
        print(paint("\n  EXPORTED", "bold"))
        for fmt in args.disa_aktar:
            path = folder / exporters.filename(result, fmt)
            if fmt in ("docx", "pdf"):
                path.write_bytes(exporters.to_docx(result) if fmt == "docx" else to_pdf(result))
            else:
                body = {
                    "md": lambda: exporters.to_markdown(result),
                    "ris": lambda: exporters.to_ris(result["articles"]),
                    "bib": lambda: exporters.to_bibtex(result["articles"]),
                    "csv": lambda: "﻿" + exporters.to_csv(result["articles"]),
                }[fmt]()
                path.write_text(body, encoding="utf-8")
            print(f"   · {path}")

    print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
