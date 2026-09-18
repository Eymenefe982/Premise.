"""Dil modeli katmanı: Gemini anahtar havuzu, çeviri ve sentez."""
from __future__ import annotations

import logging
import json
import re
import threading
import warnings

warnings.filterwarnings("ignore", category=FutureWarning, module="google.generativeai")
warnings.filterwarnings("ignore", message=".*google.generativeai.*")

import google.generativeai as genai  # noqa: E402
from google.generativeai import client as genai_client  # noqa: E402

from . import modes  # noqa: E402
from .config import GEMINI_API_KEYS, GEMINI_FAST_MODEL  # noqa: E402

log = logging.getLogger("premise.llm")

# Son çalışan anahtar. Yeni çağrılar buradan başlar; kotası dolan anahtar bir sonrakine
# devreder ve havuz bir çağrıda en fazla bir kez dolaşılır.
_key_index = 0
# genai.configure() süreç genelindedir; yalnızca anahtara bağlı istemcinin üretimi bu
# kilidin altında yapılır. Ağ çağrısının kendisi kilidin dışındadır: önceden kilit çağrı
# boyunca tutuluyor, bütün kullanıcıların bütün model çağrıları sıraya giriyordu.
_api_lock = threading.Lock()
_clients: dict[str, object] = {}

if GEMINI_API_KEYS:
    genai.configure(api_key=GEMINI_API_KEYS[0])


class LLMError(RuntimeError):
    pass


def _client_for(key: str):
    """Anahtara bağlı, iş parçacıkları arasında paylaşılabilen istemci (anahtar başına bir)."""
    with _api_lock:
        cli = _clients.get(key)
        if cli is None:
            genai.configure(api_key=key)
            cli = genai_client.get_default_generative_client()
            _clients[key] = cli
        return cli


def _is_quota_error(exc: Exception) -> bool:
    msg = str(exc).lower()
    return any(t in msg for t in ("429", "quota", "exhausted", "rate limit", "resource_exhausted"))


def _gen_config(stage: modes.Stage, json_mode: bool) -> dict:
    """Aşamanın üretim ayarı.

    Thinking burada ayrıca ayarlanmaz: kullanılan `google.generativeai` sürümü
    `thinking_level`/`thinking_budget` alanlarını reddediyor. Pratikte gerek de yok —
    thinking token'ları çıktı sınırının içinden harcanır, dolayısıyla maliyet freni
    `max_output_tokens`'tır.
    """
    config: dict = {"max_output_tokens": stage.max_output_tokens}
    if json_mode:
        config["response_mime_type"] = "application/json"
    return config


def _response_text(response) -> str:
    """Yanıt metnini parçalardan toplar.

    `response.text` çıktı sınırına takılan yanıtlarda istisna atar. Sınır bu sürümle
    birlikte geldiği için o durum artık mümkün: kesilmiş de olsa eldeki metin,
    aramanın tamamen boşa gitmesinden iyidir.
    """
    try:
        candidates = getattr(response, "candidates", None) or []
        parts = getattr(getattr(candidates[0], "content", None), "parts", None) or []
        return "".join(getattr(p, "text", "") or "" for p in parts).strip()
    except (AttributeError, IndexError, TypeError):
        return ""


def _generate_with_model(model_name: str, prompt: str, system: str, config: dict):
    """Verilen modeli havuzdaki anahtarlarla dener; her anahtar en fazla bir kez.

    Önceki sürüm her turda ilk anahtara dönüyordu: ilk anahtarın günlük kotası
    dolduğunda diğerleri hiç denenmiyor, döngü sonsuza kadar sürüyordu. Hepsi
    doluysa None döner ve çağıran yedek modele geçer.
    """
    global _key_index
    count = len(GEMINI_API_KEYS)
    start = _key_index % count if count else 0

    for step in range(count):
        index = (start + step) % count
        try:
            model = genai.GenerativeModel(model_name=model_name,
                                          system_instruction=system or None)
            # Modeli anahtarın kendi istemcisine bağla: çağrı sırasında başka bir
            # iş parçacığı genai.configure() çağırsa bile bu model etkilenmez.
            model._client = _client_for(GEMINI_API_KEYS[index])
            response = model.generate_content(prompt, generation_config=config)
        except Exception as exc:
            if _is_quota_error(exc):
                if step + 1 < count:
                    log.warning(f"quota hit on Gemini key #{index + 1}, "
                          f"trying key #{(index + 1) % count + 1}")
                continue
            log.warning(f"Gemini error ({model_name}): {exc}")
            return None
        _key_index = index
        return response
    return None


def generate(prompt: str, system: str = "", json_mode: bool = False,
             stage: str = "synthesis", fast: bool | None = None) -> str:
    """Gemini ile üretir.

    Model ve çıktı sınırı, aktif güç modunun bu aşama için tanımladığı profilden gelir
    (bkz. modes.py). Kota biterse anahtar, tüm anahtarlar biterse yedek modele düşülür.
    `fast` yalnızca eski çağıranları kırmamak için durur.
    """
    ctx = modes.current()
    cfg = ctx.profile.stage(stage)
    config = _gen_config(cfg, json_mode)

    response = _generate_with_model(cfg.model, prompt, system, config)
    used_model = cfg.model

    if response is None and cfg.model != GEMINI_FAST_MODEL:
        log.warning(f"'{cfg.model}' exhausted, falling back to '{GEMINI_FAST_MODEL}'")
        used_model = GEMINI_FAST_MODEL
        response = _generate_with_model(GEMINI_FAST_MODEL, prompt, system, config)

    if response is None:
        raise LLMError("All Gemini keys/models are out of quota.")

    if ctx.meter is not None:
        ctx.meter.record(stage, used_model, getattr(response, "usage_metadata", None))

    text = _response_text(response)
    if not text:
        raise LLMError(f"Gemini returned an empty response for stage '{stage}'.")
    return text


# --------------------------------------------------------------------------- çeviri
TRANSLATE_SYSTEM = """You are a biomedical search strategist.
The user writes a clinical or research question in any language (often Turkish).
Return ONLY a JSON object with this exact schema:
{"english": "plain English version of the question",
 "academic": "concise academic phrasing",
 "pubmed_query": "a valid PubMed boolean query using MeSH terms, AND/OR and quotes",
 "mesh": ["MeSH term", "..."],
 "topic": "3-6 word short title of the topic in {topic_language}"}
Keep pubmed_query broad enough to return results: 2-4 concepts maximum."""


def translate_query(query: str, language: str | None = None) -> dict:
    """`language` raporun dilidir. Başlık (sonuç başlığı ve dışa aktarılan dosyanın adı)
    o dilde yazılır; verilmezse sorunun kendi dilinde. Önceden model "sorular çoğunlukla
    Türkçe" ipucuna uyup İngilizce bir soruya bile Türkçe başlık veriyordu."""
    fallback = {"original": query, "english": query, "academic": query,
                "pubmed_query": query, "mesh": [], "topic": query[:60]}
    system = TRANSLATE_SYSTEM.replace(
        "{topic_language}", language or "the same language the question is written in")
    try:
        raw = generate(query, system=system, json_mode=True, stage="translate")
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
        log.warning(f"translation failed: {exc}")
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
        raw = generate(prompt, system=TRIAGE_SYSTEM, json_mode=True, stage="triage")
    except Exception as exc:
        log.warning(f"triage failed: {exc}")
        return 0
    try:
        data = json.loads(re.sub(r"^```(?:json)?|```$", "", raw.strip(), flags=re.MULTILINE))
    except ValueError:
        # Çıktı sınırına takılan yanıt JSON'un ortasında kesilir (düşük modda 20 aday
        # 600 token'ı aşabiliyor). Önceden bu, triyajı sessizce tamamen kapatıyordu;
        # tamamlanmış notlar kurtarılır, kalan makaleler kelime örtüşmesine düşer.
        items = [{"pmid": pmid, "relevance": int(grade)} for pmid, grade in re.findall(
            r'"pmid"\s*:\s*"([^"]+)"\s*,\s*"relevance"\s*:\s*(\d)', raw)]
        if not items:
            log.warning("triage failed: unreadable response")
            return 0
        log.warning(f"triage response was cut off, {len(items)} grade(s) recovered")
        data = {"articles": items}

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
                           is too thin to settle the question, then the most defensible reading;
                           every sentence about a finding must carry a [PMID: ...] citation
## What Each Study Found - one bullet per article: design, n, the numbers, what it showed
## Disputed and Missing  - disagreements, and what study would actually answer this"""

FULL_STRUCTURE = """## Short Answer          - 3-5 sentence direct answer; EVERY sentence here must
                           carry at least one [PMID: ...] citation, no exceptions
## Evidence Summary      - grouped bullet points, strongest evidence first
## Clinical Implications - what this means at the bedside, practical and cautious
## Disputed and Missing  - disagreements between studies and open questions"""

LIMITED_RULE = """
5. THE EVIDENCE BASE IS TOO THIN FOR A RECOMMENDATION. Do not write a "Clinical
   Implications" section and do not tell the reader what to do at the bedside: there is
   not enough evidence here to support that. Report what each study found, separately,
   and let the reader judge. Never present a single study as established practice and
   never generalise beyond the population that was actually studied."""


# Yüksek güç modunda tablolar modelden değil, doğrulanmış sayısal veriden üretilir
# (bkz. tables.py). Modelin aynı sayıları kendi tablosunda tekrar yazması hem bağlamı
# şişirir hem de tek gerçek kaynağı ikiye böler.
TABLE_RULE = """
The application also appends two verified tables below your text: an evidence table
(design, sample size, year, citations per article) and a numeric findings table
(effect sizes, confidence intervals, p values). Do NOT build these tables yourself and
do not restate their full contents. Refer to them in prose when a number matters."""


def synthesis_system(language: str, low_evidence: bool = False, mode: str = "full") -> str:
    limited = mode == "limited"
    extra = LIMITED_RULE if limited else (LOW_EVIDENCE_RULE if low_evidence else "")
    return f"""You are an expert biomedical evidence analyst writing for physicians.
Write the entire answer in {language}.

STRICT RULES
1. Use ONLY the supplied article texts. Never add outside knowledge or invent numbers.
2. Cite every factual sentence inline as [PMID: 12345678]. Copy the full number that follows
   "PMID:" in the article header, verbatim. Never cite the "ARTICLE n" position number, and
   never put more than one PMID inside a single [PMID: ...] bracket.
3. Prefer higher-level evidence (guidelines, meta-analyses, RCTs) and say so when evidence is weak,
   conflicting, or based only on small/observational studies.
4. Give concrete numbers (effect size, CI, p, n) when the text contains them.
{extra}

STRUCTURE (translate these headings into {language}, keep the order and the ## level):
{LIMITED_STRUCTURE if limited else FULL_STRUCTURE}

Do NOT write a bibliography or reference list: the application appends a verified one,
complete with effect sizes and confidence intervals, below your text. End after the
"Disputed and Missing" section.
{TABLE_RULE if modes.active().tables else ""}

Never claim an article says something it does not. If the supplied texts cannot answer the
question, say so explicitly in the first section."""


def build_context(articles: list, fulltexts: dict[str, str] | None = None) -> str:
    """Sentez bağlamı. Kırpma sınırları aktif güç modundan gelir: bu prompt hattın
    en pahalı girdisidir, mod burada uygulanmazsa seçimin maliyete etkisi olmaz."""
    fulltexts = fulltexts or {}
    prof = modes.active()
    blocks = []
    for i, a in enumerate(articles, 1):
        # Sıra numarası bilerek köşeli parantezsiz: "[1]" biçimi atıf biçimine
        # ("[PMID: 12345678]") fazla benziyordu ve ucuz modeller sıra numarasını
        # PMID sanıp uydurma atıf üretiyordu.
        head = (f"ARTICLE {i} | PMID: {a.pmid or 'yok'} | DOI: {a.doi or 'yok'} | "
                f"YEAR: {a.year} | JOURNAL: {a.journal} | "
                f"TYPE: {', '.join(a.pub_types) or 'n/a'} | CITATIONS: {a.citations}")
        body = [head, f"AUTHORS: {a.authors}", f"TITLE: {a.title}",
                f"ABSTRACT: {a.abstract[:prof.abstract_chars]}"]
        ft = fulltexts.get(a.pmid or a.pmcid)
        if ft:
            body.append(f"FULL TEXT EXCERPT:\n{ft[:prof.fulltext_chars]}")
        blocks.append("\n".join(body))
    return "\n\n---\n\n".join(blocks)


def synthesize(question: str, articles: list, fulltexts: dict[str, str] | None = None,
               language: str = "Türkçe", low_evidence: bool = False,
               mode: str = "full") -> str:
    context = build_context(articles, fulltexts)
    prompt = (f"ARTICLES ({len(articles)} in total):\n\n{context}\n\n"
              f"USER QUESTION: {question}\n\n"
              f"Answer using only the articles above, following every rule.")
    return generate(prompt, system=synthesis_system(language, low_evidence, mode),
                    stage="synthesis")


# --------------------------------------------------------------- iddia doğrulama
CLAIM_CHECK_SYSTEM = """You check whether a claim is actually supported by the article text cited for it.

You receive a SOURCES section listing each article once under its PMID, then a CLAIMS
section. Every claim has an id, a sentence, and a CITES line naming the PMIDs it relies
on. Judge each claim ONLY against the sources its own CITES line names; ignore the rest.
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


def short_answer_citations(report: str, articles: list) -> list[str]:
    """Kısa Cevap bölümünde geçen, sonuç kümesinde gerçekten bulunan PMID'ler.

    Boş dönmesi, doğrulanacak hiçbir iddia olmadığı anlamına gelir: `check_claims`
    o durumda sessizce hiçbir şey yapmaz. Kullanıcının bunu bilmesi gerekir, çünkü
    kaynaksız bir paragraf, doğrulanmış bir paragrafla aynı görünür.
    """
    valid = {a.pmid for a in articles if a.pmid}
    return [p for p in dict.fromkeys(_cited_pmids(_short_answer(report))) if p in valid]


def check_claims(report: str, articles: list, fulltexts: dict[str, str] | None = None,
                 max_claims: int | None = None) -> list[dict]:
    """Kısa Cevap bölümündeki her cümleyi, atıf verdiği makalenin metnine karşı sınar.

    `validate_citations` yalnızca PMID'in sonuç kümesinde olup olmadığına bakar; yani
    uydurulmuş bir numarayı yakalar, ama gerçek bir makaleye yanlış bir iddia atfedilmesini
    yakalayamaz. Asıl klinik risk ikincisidir, bu katman onu hedefler.
    """
    prof = modes.active()
    if max_claims is None:
        max_claims = prof.max_claims
    if not report.strip() or not articles or max_claims <= 0:
        return []
    fulltexts = fulltexts or {}
    by_pmid = {a.pmid: a for a in articles if a.pmid}

    claims = []
    for sentence in _SENTENCE_RE.split(_short_answer(report)):
        sentence = sentence.strip()
        pmids = _cited_pmids(sentence)
        cited = [by_pmid[p] for p in dict.fromkeys(pmids) if p in by_pmid]
        if not sentence or not cited:
            continue
        claims.append({"id": len(claims), "sentence": sentence, "pmids": pmids,
                       "articles": cited})
        if len(claims) >= max_claims:
            break

    if not claims:
        return []

    # Her kaynak metni bir kez gönderilir. Aynı makaleyi birkaç iddia birden
    # alıntıladığında metnini her iddia için tekrar yollamak, bu adımın girdisini
    # makale sayısı yerine iddia sayısıyla çarpıyordu.
    sources: dict[str, object] = {}
    for claim in claims:
        for art in claim["articles"]:
            sources.setdefault(art.pmid, art)

    source_blocks = []
    for pmid, art in sources.items():
        excerpt = fulltexts.get(art.pmid or art.pmcid, "")[:prof.claim_excerpt_chars]
        source_blocks.append(f"[PMID {pmid}] TITLE: {art.title}\n"
                             f"ABSTRACT: {art.abstract}"
                             + (f"\nFULL TEXT EXCERPT: {excerpt}" if excerpt else ""))

    claim_blocks = [f"CLAIM id={claim['id']}\n"
                    f"CITES: {', '.join(dict.fromkeys(claim['pmids']))}\n"
                    f"SENTENCE: {claim['sentence']}" for claim in claims]

    try:
        raw = generate("SOURCES:\n\n" + "\n\n=====\n\n".join(source_blocks)
                       + "\n\nCLAIMS TO CHECK:\n\n" + "\n\n".join(claim_blocks),
                       system=CLAIM_CHECK_SYSTEM, json_mode=True, stage="claims")
        data = json.loads(re.sub(r"^```(?:json)?|```$", "", raw.strip(), flags=re.MULTILINE))
    except Exception as exc:
        log.warning(f"claim check failed: {exc}")
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
        log.warning(f"{len(flagged)} claim(s) in the short answer flagged by the checker")
    return flagged


def _cited_pmids(text: str) -> list[str]:
    """Metindeki atıf parantezlerinde geçen bütün PMID'ler, sırasıyla.

    Modeller kuralı çiğneyip bir parantezin içine birden çok PMID sığdırabiliyor
    ("[PMID: 123, PMID: 456]"). Yalnız ilkine bakmak, uydurma bir numaranın
    kalabalığın arkasına saklanmasına izin verirdi.
    """
    out: list[str] = []
    for block in re.findall(r"\[PMID:[^\]]*\]", text):
        out.extend(re.findall(r"\d{4,}", block))
    return out


def validate_citations(text: str, articles: list) -> tuple[str, list[str]]:
    """Metindeki PMID'leri gerçek sonuç kümesiyle karşılaştırır, uydurmaları işaretler."""
    valid = {a.pmid for a in articles if a.pmid}
    fake = sorted(set(_cited_pmids(text)) - valid)
    for pmid in fake:
        text = re.sub(rf"\[PMID:\s*{pmid}\s*\]", f"[PMID: {pmid} ⚠ unverified]", text)
    return text, fake
