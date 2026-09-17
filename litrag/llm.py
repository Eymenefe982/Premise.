"""Dil modeli katmanı: Gemini anahtar havuzu, çeviri ve sentez."""
from __future__ import annotations

import json
import re
import time
import warnings

warnings.filterwarnings("ignore", category=FutureWarning, module="google.generativeai")
warnings.filterwarnings("ignore", message=".*google.generativeai.*")

import google.generativeai as genai  # noqa: E402

from .config import GEMINI_API_KEYS, GEMINI_FAST_MODEL, GEMINI_MODEL

_key_index = 0
if GEMINI_API_KEYS:
    genai.configure(api_key=GEMINI_API_KEYS[0])


class LLMError(RuntimeError):
    pass


def _switch_key() -> None:
    """Kota dolunca havuzdaki bir sonraki Gemini anahtarına geçer."""
    global _key_index
    _key_index += 1
    if _key_index >= len(GEMINI_API_KEYS):
        raise LLMError("All Gemini keys in the pool are out of quota.")
    genai.configure(api_key=GEMINI_API_KEYS[_key_index])
    print(f"[llm] quota hit, switched to Gemini key #{_key_index + 1}")


def _is_quota_error(exc: Exception) -> bool:
    msg = str(exc).lower()
    return any(t in msg for t in ("429", "quota", "exhausted", "rate limit", "resource_exhausted"))


def _generate_with_model(model_name: str, prompt: str, system: str, config: dict) -> str | None:
    """Verilen modeli, havuzdaki her anahtarla kota bitene kadar dener."""
    global _key_index
    _key_index = 0
    if GEMINI_API_KEYS:
        genai.configure(api_key=GEMINI_API_KEYS[0])

    while GEMINI_API_KEYS:
        try:
            model = genai.GenerativeModel(model_name=model_name,
                                          system_instruction=system or None)
            response = model.generate_content(prompt, generation_config=config)
            return response.text
        except Exception as exc:
            if _is_quota_error(exc):
                try:
                    _switch_key()
                    time.sleep(1.2)
                    continue
                except LLMError:
                    return None
            print(f"[llm] Gemini error ({model_name}): {exc}")
            return None
    return None


def generate(prompt: str, system: str = "", json_mode: bool = False, fast: bool = False) -> str:
    """Gemini ile üretir; kota biterse anahtar değiştirir, tüm anahtarlar biterse
    (ana model kullanılıyorsa) hızlı modele düşer."""
    model_name = GEMINI_FAST_MODEL if fast else GEMINI_MODEL
    config = {"response_mime_type": "application/json"} if json_mode else {}

    result = _generate_with_model(model_name, prompt, system, config)
    if result is not None:
        return result

    if model_name != GEMINI_FAST_MODEL:
        print(f"[llm] '{model_name}' exhausted, falling back to fast model '{GEMINI_FAST_MODEL}'")
        result = _generate_with_model(GEMINI_FAST_MODEL, prompt, system, config)
        if result is not None:
            return result

    raise LLMError("All Gemini keys/models are out of quota.")


# --------------------------------------------------------------------------- çeviri
TRANSLATE_SYSTEM = """You are a biomedical search strategist.
The user writes a clinical or research question, often in Turkish.
Return ONLY a JSON object with this exact schema:
{"english": "plain English version of the question",
 "academic": "concise academic phrasing",
 "pubmed_query": "a valid PubMed boolean query using MeSH terms, AND/OR and quotes",
 "mesh": ["MeSH term", "..."],
 "topic": "3-6 word short title of the topic in the user's own language"}
Keep pubmed_query broad enough to return results: 2-4 concepts maximum."""


def translate_query(query: str) -> dict:
    fallback = {"original": query, "english": query, "academic": query,
                "pubmed_query": query, "mesh": [], "topic": query[:60]}
    try:
        raw = generate(query, system=TRANSLATE_SYSTEM, json_mode=True, fast=True)
        data = json.loads(raw)
        return {
            "original": query,
            "english": (data.get("english") or query).strip(),
            "academic": (data.get("academic") or query).strip(),
            "pubmed_query": (data.get("pubmed_query") or query).strip(),
            "mesh": [str(m) for m in (data.get("mesh") or [])][:10],
            "topic": (data.get("topic") or query)[:80],
        }
    except Exception as exc:
        print(f"[llm] translation failed: {exc}")
        return fallback


# --------------------------------------------------------------------------- triyaj
TRIAGE_SYSTEM = """You judge whether biomedical articles actually address a given question.

Return ONLY a JSON object: {"articles": [{"pmid": "<exact pmid given>", "relevance": 0|1|2}]}

2 = the article directly investigates the question (same population, same intervention or
    exposure, and it reports an outcome the question asks about).
1 = related and useful as background, but it does not directly answer the question
    (different population, adjacent intervention, or it only touches the topic).
0 = not relevant to the question at all.

Be strict. An article that merely mentions the topic in passing is 0, not 1. Judge only
from the title and abstract shown. Return one element for every article supplied."""


def triage(question: str, articles: list) -> int:
    """Her makaleye 0-2 arasi alaka notu yazar. Degerlendirilen makale sayisini dondurur.

    Sentezden once, ucuz ve hizli modelle calisir. Amaci hattin en sessiz hata bicimini
    yakalamaktir: sorgu cevirisi bozuldugunda sistem alakasiz ama gercek makaleler getirir
    ve model onlardan kendinden emin, kaynakli, tamamen yanlis bir cevap yazar.
    """
    if not articles:
        return 0
    blocks = []
    for art in articles:
        blocks.append(f"PMID: {art.pmid or art.pmcid}\nTITLE: {art.title}\n"
                      f"ABSTRACT: {art.abstract[:400]}")
    prompt = f"QUESTION: {question}\n\nARTICLES:\n\n" + "\n\n---\n\n".join(blocks)

    try:
        raw = generate(prompt, system=TRIAGE_SYSTEM, json_mode=True, fast=True)
        data = json.loads(re.sub(r"^```(?:json)?|```$", "", raw.strip(), flags=re.MULTILINE))
    except Exception as exc:
        print(f"[llm] triage failed: {exc}")
        return 0

    by_pmid = {(a.pmid or a.pmcid): a for a in articles}
    graded = 0
    for item in (data.get("articles") or []):
        art = by_pmid.get(str(item.get("pmid", "")).strip())
        if art is None:
            continue
        try:
            art.relevance = max(0, min(2, int(item.get("relevance", 1))))
            graded += 1
        except (TypeError, ValueError):
            continue
    return graded


# --------------------------------------------------------------------------- sentez
LOW_EVIDENCE_RULE = """
5. THE EVIDENCE BASE IS THIN: only a handful of articles matched this question. Open the
   "Short Answer" by saying plainly, in the answer language, how many articles the answer
   rests on and that this is too few to settle the question. Do not generalise beyond what
   these few articles show, do not present a single study as established practice, and name
   in "Disputed and Missing" what kind of study would actually be needed to answer it."""


LIMITED_STRUCTURE = """## Short Answer           - open by stating how many articles this rests on and that it
                           is too thin to settle the question, then the most defensible reading
## What Each Study Found - one bullet per article: design, n, the numbers, what it showed
## Disputed and Missing  - disagreements, and what study would actually answer this"""

FULL_STRUCTURE = """## Short Answer          - 3-5 sentence direct answer
## Evidence Summary      - grouped bullet points, strongest evidence first
## Clinical Implications - what this means at the bedside, practical and cautious
## Disputed and Missing  - disagreements between studies and open questions"""

LIMITED_RULE = """
5. THE EVIDENCE BASE IS TOO THIN FOR A RECOMMENDATION. Do not write a "Clinical
   Implications" section and do not tell the reader what to do at the bedside: there is
   not enough evidence here to support that. Report what each study found, separately,
   and let the reader judge. Never present a single study as established practice and
   never generalise beyond the population that was actually studied."""


def synthesis_system(language: str, low_evidence: bool = False, mode: str = "full") -> str:
    limited = mode == "limited"
    extra = LIMITED_RULE if limited else (LOW_EVIDENCE_RULE if low_evidence else "")
    return f"""You are an expert biomedical evidence analyst writing for physicians.
Write the entire answer in {language}.

STRICT RULES
1. Use ONLY the supplied article texts. Never add outside knowledge or invent numbers.
2. Cite every factual sentence inline as [PMID: 12345678]. Use the PMIDs given to you verbatim.
3. Prefer higher-level evidence (guidelines, meta-analyses, RCTs) and say so when evidence is weak,
   conflicting, or based only on small/observational studies.
4. Give concrete numbers (effect size, CI, p, n) when the text contains them.
{extra}

STRUCTURE (translate these headings into {language}, keep the order and the ## level):
{LIMITED_STRUCTURE if limited else FULL_STRUCTURE}

Do NOT write a bibliography or reference list: the application appends a verified one,
complete with effect sizes and confidence intervals, below your text. End after the
"Disputed and Missing" section.

Never claim an article says something it does not. If the supplied texts cannot answer the
question, say so explicitly in the first section."""


def build_context(articles: list, fulltexts: dict[str, str] | None = None) -> str:
    fulltexts = fulltexts or {}
    blocks = []
    for i, a in enumerate(articles, 1):
        head = (f"[{i}] PMID: {a.pmid or 'yok'} | DOI: {a.doi or 'yok'} | YEAR: {a.year} | "
                f"JOURNAL: {a.journal} | TYPE: {', '.join(a.pub_types) or 'n/a'} | "
                f"CITATIONS: {a.citations}")
        body = [head, f"AUTHORS: {a.authors}", f"TITLE: {a.title}", f"ABSTRACT: {a.abstract}"]
        ft = fulltexts.get(a.pmid or a.pmcid)
        if ft:
            body.append(f"FULL TEXT EXCERPT:\n{ft}")
        blocks.append("\n".join(body))
    return "\n\n---\n\n".join(blocks)


def synthesize(question: str, articles: list, fulltexts: dict[str, str] | None = None,
               language: str = "Türkçe", low_evidence: bool = False,
               mode: str = "full") -> str:
    context = build_context(articles, fulltexts)
    prompt = (f"ARTICLES ({len(articles)} in total):\n\n{context}\n\n"
              f"USER QUESTION: {question}\n\n"
              f"Answer using only the articles above, following every rule.")
    return generate(prompt, system=synthesis_system(language, low_evidence, mode))


# --------------------------------------------------------------- iddia doğrulama
CLAIM_CHECK_SYSTEM = """You check whether a claim is actually supported by the article text cited for it.

You receive claims. Each claim has an id, the sentence, and the text of the article(s) it cites.
Return ONLY a JSON object: {"claims": [{"id": <id>, "verdict": "supported"|"partial"|"unsupported",
"reason": "at most 12 words, in English"}]}

supported   = the cited text states this, or states something that plainly entails it.
partial     = the cited text is about this, but the claim overstates it: a stronger effect, a
              wider population, a causal reading of an association, or a certainty the text
              does not carry.
unsupported = the cited text does not state this at all.

Judge ONLY against the supplied text. Never use outside knowledge. A claim can be true in
medicine and still be "unsupported" here if the cited article does not say it."""

# Kullanıcının ilk okuduğu ve alıntıladığı bölüm budur; doğrulama bütçesi buraya harcanır.
_SENTENCE_RE = re.compile(r"(?<=[.!?])\s+(?=[A-ZÇĞİÖŞÜÄÉ\"\'(\[])")


def _short_answer(report: str) -> str:
    """Raporun ilk '##' bölümünün gövdesini döndürür."""
    sections = re.split(r"^##\s+", report, flags=re.MULTILINE)
    if len(sections) < 2:
        return report[:1500]
    body = sections[1]
    return body.split("\n", 1)[1] if "\n" in body else ""


def check_claims(report: str, articles: list, fulltexts: dict[str, str] | None = None,
                 max_claims: int = 8) -> list[dict]:
    """Kısa Cevap bölümündeki her cümleyi, atıf verdiği makalenin metnine karşı sınar.

    `validate_citations` yalnızca PMID'in sonuç kümesinde olup olmadığına bakar; yani
    uydurulmuş bir numarayı yakalar, ama gerçek bir makaleye yanlış bir iddia atfedilmesini
    yakalayamaz. Asıl klinik risk ikincisidir, bu katman onu hedefler.
    """
    if not report.strip() or not articles:
        return []
    fulltexts = fulltexts or {}
    by_pmid = {a.pmid: a for a in articles if a.pmid}

    claims = []
    for sentence in _SENTENCE_RE.split(_short_answer(report)):
        sentence = sentence.strip()
        pmids = re.findall(r"\[PMID:\s*(\d+)", sentence)
        cited = [by_pmid[p] for p in dict.fromkeys(pmids) if p in by_pmid]
        if not sentence or not cited:
            continue
        claims.append({"id": len(claims), "sentence": sentence, "pmids": pmids,
                       "articles": cited})
        if len(claims) >= max_claims:
            break

    if not claims:
        return []

    blocks = []
    for claim in claims:
        texts = []
        for art in claim["articles"]:
            excerpt = fulltexts.get(art.pmid or art.pmcid, "")[:2500]
            texts.append(f"[PMID {art.pmid}] TITLE: {art.title}\n"
                         f"ABSTRACT: {art.abstract}"
                         + (f"\nFULL TEXT EXCERPT: {excerpt}" if excerpt else ""))
        blocks.append(f"CLAIM id={claim['id']}\nSENTENCE: {claim['sentence']}\n\n"
                      + "\n\n".join(texts))

    try:
        raw = generate("CLAIMS TO CHECK:\n\n" + "\n\n=====\n\n".join(blocks),
                       system=CLAIM_CHECK_SYSTEM, json_mode=True, fast=True)
        data = json.loads(re.sub(r"^```(?:json)?|```$", "", raw.strip(), flags=re.MULTILINE))
    except Exception as exc:
        print(f"[llm] claim check failed: {exc}")
        return []

    flagged = []
    for item in (data.get("claims") or []):
        try:
            claim = claims[int(item.get("id"))]
        except (TypeError, ValueError, IndexError):
            continue
        verdict = str(item.get("verdict", "")).strip().lower()
        if verdict not in ("partial", "unsupported"):
            continue
        flagged.append({
            "sentence": claim["sentence"],
            "pmids": claim["pmids"],
            "verdict": verdict,
            "reason": str(item.get("reason", ""))[:120],
        })
    if flagged:
        print(f"[llm] {len(flagged)} claim(s) in the short answer flagged by the checker")
    return flagged


def validate_citations(text: str, articles: list) -> tuple[str, list[str]]:
    """Metindeki PMID'leri gerçek sonuç kümesiyle karşılaştırır, uydurmaları işaretler."""
    valid = {a.pmid for a in articles if a.pmid}
    found = set(re.findall(r"\[PMID:\s*(\d+)", text))
    fake = sorted(found - valid)
    for pmid in fake:
        text = re.sub(rf"\[PMID:\s*{pmid}\s*\]", f"[PMID: {pmid} ⚠ unverified]", text)
    return text, fake
