"""Arama hattı: çeviri -> çok kaynaklı tarama -> birleştirme -> triyaj -> tam metin -> sentez."""
from __future__ import annotations

import concurrent.futures
from dataclasses import dataclass, field
from datetime import datetime

from . import agreement, cache, modes, selection, tables
from .config import (CACHE_ENABLED, CLAIM_CHECK_ENABLED, DEFAULT_MAX_ARTICLES,
                     DEFAULT_RECENT_YEARS, LOW_EVIDENCE_FLOOR, MIN_EVIDENCE_POOL,
                     MIN_RELEVANT_ARTICLES, SOURCE_EXCERPT_LIMIT, TOPIC_QUOTA,
                     TRIAGE_ENABLED)
from .extract import extract_findings
from .meter import Meter
from .llm import (check_claims, short_answer_citations, synthesize, translate_query,
                  triage, validate_citations)
from .models import Article, ClinicalResource
from .sources import clinical, europepmc, openaccess, pmc, pubmed


@dataclass
class SearchRequest:
    query: str
    author: str = ""
    journal: str = ""
    language: str = "Türkçe"
    max_articles: int = DEFAULT_MAX_ARTICLES
    recent_years: int | None = DEFAULT_RECENT_YEARS
    filters: list[str] = field(default_factory=list)      # rct / meta / guideline / free_fulltext
    only_open_access: bool = False
    use_fulltext: bool = True
    use_clinical: bool = True
    extract_stats: bool = True
    synthesize: bool = True
    refresh: bool = False          # önbelleği atlayıp taramayı baştan çalıştır
    power_mode: str = modes.DEFAULT_MODE   # low / medium / high (bkz. modes.py)

    @classmethod
    def from_dict(cls, data: dict) -> "SearchRequest":
        years = data.get("recent_years", DEFAULT_RECENT_YEARS)
        return cls(
            query=(data.get("query") or "").strip(),
            author=(data.get("author") or "").strip(),
            journal=(data.get("journal") or "").strip(),
            language=data.get("language") or "Türkçe",
            max_articles=max(3, min(int(data.get("max_articles") or DEFAULT_MAX_ARTICLES), 40)),
            recent_years=None if not years or int(years) <= 0 else int(years),
            filters=[str(f) for f in (data.get("filters") or [])],
            only_open_access=bool(data.get("only_open_access")),
            use_fulltext=bool(data.get("use_fulltext", True)),
            use_clinical=bool(data.get("use_clinical", True)),
            extract_stats=bool(data.get("extract_stats", True)),
            synthesize=bool(data.get("synthesize", True)),
            refresh=bool(data.get("refresh", False)),
            power_mode=modes.profile(data.get("power_mode")).name,
        )


class SearchCancelled(RuntimeError):
    """Kullanıcı taramayı durdurduğunda yükseltilir."""


def _noop(*_args, **_kwargs) -> None:
    pass


def _dedupe(batches: list[list[Article]]) -> list[Article]:
    """Aynı makalenin DOI / PMID / PMCID üzerinden gelen kopyalarını tek kayıtta birleştirir."""
    pool: dict[str, Article] = {}
    alias: dict[str, str] = {}

    for batch in batches:
        for art in batch:
            keys = [k for k in (
                f"doi:{art.doi.lower()}" if art.doi else "",
                f"pmid:{art.pmid}" if art.pmid else "",
                f"pmc:{art.pmcid}" if art.pmcid else "",
            ) if k]
            existing = next((alias[k] for k in keys if k in alias), None)
            if existing and existing in pool:
                pool[existing].merge(art)
                for k in keys:
                    alias[k] = existing
            else:
                main = art.key
                pool[main] = art
                for k in keys + [main]:
                    alias[k] = main
    return list(pool.values())


def _is_relevant(art: Article) -> bool:
    """Triyaj notu varsa ona, yoksa kelime örtüşmesine bakar."""
    if art.relevance >= 0:
        return art.relevance >= 1
    return art.topic_overlap >= 0.15


def _is_direct(art: Article) -> bool:
    """Soruyu doğrudan araştıran makale."""
    if art.relevance >= 0:
        return art.relevance == 2
    return art.topic_overlap >= 0.40


def _answer_mode(selected: list[Article]) -> str:
    """full / limited / none — cevabın hangi güvenle yazılabileceği.

    Kullanıcının kaç makale istediğinden bağımsızdır: bir cevabın güvenilirliği
    talebe değil, elde gerçekten ne olduğuna bağlıdır.
    """
    relevant = [a for a in selected if _is_relevant(a)]
    direct = [a for a in selected if _is_direct(a)]
    if len(relevant) < MIN_RELEVANT_ARTICLES:
        return "none"
    if len(direct) < 3 or len(relevant) < 5:
        return "limited"
    return "full"


def _suggestions(req: SearchRequest, pool_size: int) -> list[str]:
    """Cevap üretilemediğinde kullanıcıya verilecek somut öneriler (model çağrısı yok)."""
    tips = []
    if req.filters:
        tips.append("Yayın türü filtrelerini kaldırıp tekrar deneyin: literatürde bu soruya "
                    "yanıt veren çalışma başka bir tasarımda olabilir.")
    if req.recent_years and req.recent_years <= 10:
        tips.append(f"Tarih sınırını {req.recent_years} yıldan geniş tutun ya da tamamen kaldırın.")
    if req.only_open_access:
        tips.append("\"Yalnızca açık erişim\" seçeneğini kapatın; konunun temel çalışmaları "
                    "abonelikli dergilerde olabilir.")
    if req.author or req.journal:
        tips.append("Yazar ya da dergi kısıtını kaldırın.")
    tips.append("Soruyu daha genel yazın: tek bir ilaç ve tek bir alt grup yerine ilaç sınıfını "
                "ve ana hasta grubunu sorun.")
    if pool_size:
        tips.append(f"Tarama {pool_size} kayıt buldu ama hiçbiri sorunuzu doğrudan ele almıyor. "
                    "Aşağıdaki listeye göz atıp soruyu onların diline yaklaştırabilirsiniz.")
    return tips


def run_search(req: SearchRequest, progress=None, cancel=None) -> dict:
    """Aramayı, seçilen güç modunun profili ve maliyet sayacı altında çalıştırır."""
    prof = modes.profile(getattr(req, "power_mode", None))
    job_meter = Meter(prof.ceiling_try)
    with modes.use(prof.name, job_meter):
        result = _run_search(req, progress, cancel)

    result["power_mode"] = prof.name
    # Önbellekten gelen sonuç hiçbir model çağrısı üretmedi: sayaç sıfırdır ve
    # kullanıcıdan da ücret alınmaz (bkz. app._worker).
    result["usage"] = job_meter.summary()
    return result


def _run_search(req: SearchRequest, progress=None, cancel=None) -> dict:
    progress = progress or _noop
    started = datetime.now()
    prof = modes.active()
    if not req.query:
        raise ValueError("The search query cannot be empty.")

    # Kullanıcı kaydırıcıyı 40'a çekmiş olabilir; modun tavanı bağlayıcıdır.
    max_articles = min(req.max_articles, prof.max_articles)

    def checkpoint() -> None:
        """Uzun adımların arasında iptal isteğini kontrol eder."""
        if cancel is not None and cancel():
            raise SearchCancelled("Search cancelled.")

    # 0) Aynı soru daha önce yanıtlandıysa saklanan sonucu ver
    cache_key = cache.make_key(req)
    if CACHE_ENABLED and not req.refresh:
        stored = cache.get(cache_key)
        if stored is not None:
            progress("Answering from a recent identical search", 1.0)
            return stored

    # 1) Sorguyu akademik arama diline çevir
    checkpoint()
    progress("Translating your question into academic search terms", 0.06)
    tr = translate_query(req.query, req.language)
    checkpoint()
    variants = list(dict.fromkeys([v for v in (tr["pubmed_query"], tr["academic"],
                                               tr["english"], tr["original"]) if v]))[:3]
    terms = selection.topic_terms(tr)

    # 2) Kaynakları paralel tara. Yayın türü filtreleri her iki kaynağa da gider:
    #    yalnız birine uygulanırsa filtrelenmemiş kaynak havuzu sulandırır ve seçim
    #    kullanıcının koyduğu sınırı hiç görmemiş makalelerden yapılır.
    progress(f"Searching PubMed and Europe PMC with {len(variants)} query variants", 0.16)
    per_source = max(max_articles, 12)
    jobs: dict[str, concurrent.futures.Future] = {}
    results: dict[str, list] = {}

    with concurrent.futures.ThreadPoolExecutor(max_workers=10) as pool:
        for i, variant in enumerate(variants):
            jobs["pubmed-" + str(i)] = pool.submit(
                pubmed.search, variant, req.author, req.journal, per_source,
                req.recent_years, req.filters)
            jobs["epmc-" + str(i)] = pool.submit(
                europepmc.search, variant, req.author, req.journal, per_source,
                req.recent_years, req.only_open_access, req.filters)
        if req.use_clinical:
            jobs["statpearls"] = pool.submit(clinical.search_statpearls, tr["english"], 6)
            jobs["guidelines"] = pool.submit(clinical.search_guidelines, tr["english"], 6)
            jobs["cochrane"] = pool.submit(clinical.search_cochrane, tr["english"], 4)

        for name, future in jobs.items():
            try:
                results[name] = future.result()
            except Exception as exc:
                print(f"[pipeline] {name} failed: {exc}")
                results[name] = []

    article_batches = [v for k, v in results.items()
                       if k.startswith("pubmed-") or k.startswith("epmc-")]
    guidelines: list[Article] = list(results.get("guidelines", [])) + list(results.get("cochrane", []))
    statpearls: list[ClinicalResource] = list(results.get("statpearls", []))

    # Kaynakların bildirdiği toplam eşleşme: "uyan N kayıttan M'si okundu" diyebilmek için.
    coverage = {
        "pubmed": max((getattr(v, "total", 0) for k, v in results.items()
                       if k.startswith("pubmed-")), default=0),
        "europepmc": max((getattr(v, "total", 0) for k, v in results.items()
                          if k.startswith("epmc-")), default=0),
    }

    # Kılavuz/Cochrane partisi PubMed'in kendi yayın türü sorgusuyla gelir ve kullanıcının
    # filtresinden bağımsızdır. "Sadece RKÇ" denmişken bunları sentez havuzuna katmak
    # filtreyi anlamsız kılar; ayrı "Klinik kaynaklar" bölümünde görünmeye devam ederler.
    type_filters = [f for f in req.filters if f in selection.FILTER_MATCH]
    use_guidelines_in_pool = req.use_clinical and (not type_filters or "guideline" in req.filters)

    def _merge(batches: list[list[Article]]) -> list[Article]:
        pool = _dedupe(batches + ([guidelines] if use_guidelines_in_pool else []))
        pool = [a for a in pool if a.abstract]
        if req.only_open_access:
            pool = [a for a in pool if a.is_oa or a.pmcid]
        selection.annotate(pool, req.filters, terms)
        return pool

    articles = _merge(article_batches)

    # Sonuç azsa sentez eksik bilgiye dayanır. Kullanıcının koyduğu tarih ve yayın türü
    # sınırlarını bir kez kaldırıp tekrar tarar; açık erişim tercihi kullanıcının kendi
    # kararı olduğu için ona dokunulmaz. Genişletme turundan gelen kayıtlar filtreye
    # uymadıkları için `annotate` tarafından işaretlenir, sessizce karışmazlar.
    broadened, relaxed = False, []
    if len(articles) < MIN_EVIDENCE_POOL and (req.recent_years or req.filters):
        checkpoint()
        progress(f"Only {len(articles)} records matched, searching again without "
                 f"date and publication-type limits", 0.3)
        wide: dict[str, list] = {}
        with concurrent.futures.ThreadPoolExecutor(max_workers=10) as pool:
            wide_jobs = {}
            for i, variant in enumerate(variants):
                wide_jobs[f"pubmed-w{i}"] = pool.submit(
                    pubmed.search, variant, req.author, req.journal, per_source, None, [])
                wide_jobs[f"epmc-w{i}"] = pool.submit(
                    europepmc.search, variant, req.author, req.journal, per_source,
                    None, req.only_open_access, [])
            for name, future in wide_jobs.items():
                try:
                    wide[name] = future.result()
                except Exception as exc:
                    print(f"[pipeline] {name} failed: {exc}")
                    wide[name] = []

        combined = _merge(article_batches + list(wide.values()))
        if len(combined) > len(articles):
            articles, broadened = combined, True
            if req.recent_years:
                relaxed.append("date range")
            if req.filters:
                relaxed.append("publication type filters")

    if not articles:
        raise ValueError("No article matched these criteria. "
                         "Try relaxing the filters and search again.")

    # 3) Açık erişim ve atıf verisiyle zenginleştir
    checkpoint()
    progress(f"{len(articles)} unique records found, adding open access and citation data", 0.4)
    openaccess.enrich(articles[:60])
    checkpoint()

    for art in articles:
        art.compute_score()
    articles.sort(key=lambda a: a.score, reverse=True)

    # 4) Alaka triyajı. Seçimden önce çalışır: hem alakasız kayıtları listeden düşürür,
    #    hem de cevabın hangi güvenle yazılabileceğine karar veren sayıyı üretir.
    candidates = selection.select(
        articles, min(max(max_articles * 2, 20), prof.triage_candidates),
        topic_quota=TOPIC_QUOTA)
    graded = 0
    if TRIAGE_ENABLED:
        progress(f"Checking which of the {len(candidates)} best records actually "
                 f"address your question", 0.48)
        graded = triage(req.query, candidates)
        checkpoint()

    eligible = [a for a in candidates if a.relevance != 0] if graded else candidates
    if not eligible:                       # hepsi alakasız: yine de kullanıcıya gösterilir
        eligible = candidates
    selected = selection.select(eligible, max_articles)

    answer_mode = _answer_mode(selected)
    relevant_count = sum(1 for a in selected if _is_relevant(a))
    # Kullanıcı bilerek az makale istemiş olabilir, ama bir cevabın ne kadar sağlam
    # olduğu talebe değil elde gerçekten ne olduğuna bağlıdır: taban `max_articles`'tan
    # bağımsızdır.
    low_evidence = relevant_count < LOW_EVIDENCE_FLOOR

    # 5) Cevap üretilemiyorsa burada dururuz. Sentez ve sayısal çıkarım hiç çalışmaz,
    #    dolayısıyla kullanıcıdan kredi de alınmaz (bkz. app._worker).
    if answer_mode == "none" or not req.synthesize:
        fulltexts: dict[str, str] = {}
        stats_count = dropped_findings = 0
        report, fake_pmids, flagged_claims = "", [], []
        agreement_table: list[dict] = []
        table_data: dict = {}
        short_answer_uncited = False
        if answer_mode == "none":
            progress("No article in the results directly answers the question", 1.0)
    else:
        # 6) En nitelikli makalelerin PMC tam metnini indir
        fulltexts = {}
        if req.use_fulltext and prof.fulltext_top_n:
            targets = [a for a in selected if a.pmcid][:prof.fulltext_top_n]
            if targets:
                progress(f"Downloading PubMed Central full text for {len(targets)} articles", 0.58)
                limit = prof.fulltext_chars
                with concurrent.futures.ThreadPoolExecutor(max_workers=5) as pool:
                    texts = list(pool.map(lambda a: pmc.fetch_fulltext(a.pmcid, limit), targets))
                for art, text in zip(targets, texts):
                    if text:
                        fulltexts[art.pmid or art.pmcid] = text
                        art.fulltext_used = True

        # 7) Sayısal veri çıkarımı ve sentez (aynı anda çalışır, süre kaybı olmaz)
        checkpoint()
        report, fake_pmids, flagged_claims = "", [], []
        stats_count = dropped_findings = 0
        table_data = {}
        short_answer_uncited = False
        do_extract = req.extract_stats and prof.extract
        steps = []
        if do_extract:
            steps.append("extracting numeric findings")
        steps.append("writing the synthesis" if answer_mode == "full"
                     else "writing a limited, study-by-study summary")
        progress(f"Reading {len(selected)} articles: " + " and ".join(steps), 0.7)

        # Havuz görevleri `modes.submit` ile gönderilir: contextvar'lar worker
        # thread'lere kendiliğinden geçmez, geçmezse bu iki adım kullanıcının seçtiği
        # modu değil varsayılanı kullanırdı.
        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
            stats_job = modes.submit(pool, extract_findings, selected, fulltexts) if do_extract else None
            synth_job = modes.submit(pool, synthesize, req.query, selected, fulltexts,
                                     req.language, low_evidence, answer_mode)
            if stats_job is not None:
                try:
                    stats_count, dropped_findings = stats_job.result()
                except Exception as exc:
                    print(f"[pipeline] numeric extraction failed: {exc}")
            report = synth_job.result()

        # 8) Doğrulama: önce uydurma PMID, sonra iddianın kaynakta gerçekten yazıp yazmadığı
        checkpoint()
        if report:
            progress("Verifying citations and checking the short answer against the sources", 0.92)
            report, fake_pmids = validate_citations(report, selected)
            # Kısa Cevap hiç kaynak göstermiyorsa doğrulanacak iddia da yoktur ve
            # `check_claims` sessizce boş döner. Kullanıcı bunu görmeli: kaynaksız
            # bir paragraf, doğrulanmış bir paragrafla aynı görünür.
            short_answer_uncited = not short_answer_citations(report, selected)
            job_meter = modes.current().meter
            if CLAIM_CHECK_ENABLED and prof.claim_check:
                # Bütçe tavanına gelindiyse ilk kısılan adım budur: sentez zaten
                # yazıldı, doğrulama katmanının atlandığı sonuçta işaretlenir.
                if job_meter is None or job_meter.allow("claims"):
                    flagged_claims = check_claims(report, selected, fulltexts)
                else:
                    job_meter.note_degraded("claims")

        agreement_table = agreement.build(selected)
        if prof.tables:
            table_data = tables.build(selected)

    unique_guidelines = list({g.key: g for g in guidelines}.values())
    unique_guidelines.sort(key=lambda x: x.year, reverse=True)

    progress("Done", 1.0)
    result = {
        "query": req.query,
        "translation": tr,
        "report": report,
        "articles": [a.to_dict() for a in selected],
        "pool_size": len(articles),
        "coverage": coverage,
        "answer_mode": answer_mode,
        "relevant_count": relevant_count,
        "triaged": bool(graded),
        "suggestions": _suggestions(req, len(articles)) if answer_mode == "none" else [],
        "low_evidence": low_evidence,
        "broadened": broadened,
        "relaxed": relaxed,
        "off_filter_count": sum(1 for a in selected if a.filter_status == "mismatch"),
        "fulltext_count": len(fulltexts),
        "stats_count": stats_count,
        "dropped_findings": dropped_findings,
        "agreement": agreement_table,
        "tables": table_data,
        "flagged_claims": flagged_claims,
        # "Kaynağı gör" panelinin eşleştirme yapacağı tam metin parçaları
        "sources": {pmid: text[:SOURCE_EXCERPT_LIMIT] for pmid, text in fulltexts.items()},
        "clinical": {
            "statpearls": [r.to_dict() for r in statpearls],
            "guidelines": [g.to_dict() for g in unique_guidelines[:8]],
            "links": [r.to_dict() for r in clinical.quick_links(tr["english"])],
        },
        "unverified_pmids": fake_pmids,
        "short_answer_uncited": short_answer_uncited,
        "elapsed": round((datetime.now() - started).total_seconds(), 1),
        "generated_at": started.strftime("%Y-%m-%d %H:%M"),
        "cached": False,
    }

    # Cevap üretilemeyen arama önbelleğe alınmaz: kullanıcı soruyu düzeltip tekrar
    # denediğinde ya da literatür değiştiğinde aynı boş sonuca çarpmamalı.
    if CACHE_ENABLED and answer_mode != "none":
        cache.put(cache_key, req, result)
    return result
