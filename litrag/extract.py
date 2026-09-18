"""Makalelerden sayısal sonuçların yapılandırılmış çıkarımı.

Etki büyüklüğü, güven aralığı, denek sayısı ve p değeri makalenin kendi metninden
birebir alınır. Model bir sayı uydurursa doğrulama katmanı onu eler: her değer,
modele verilen kaynak metinde birebir aranır. Bulunamayan bulgu raporlanmaz.
"""
from __future__ import annotations

import json
import re

from . import modes
from .llm import generate

EXTRACT_SYSTEM = """You extract quantitative results from biomedical article texts.

Return ONLY a JSON object: {"articles": [ ... ]}
Each element:
{"pmid": "<the exact PMID given>",
 "design": "study design in English (randomised controlled trial, meta-analysis, cohort, case-control, cross-sectional, review)",
 "population": "studied population in English, max 10 words",
 "n": <total number of participants as an integer, 0 if not stated>,
 "findings": [
   {"outcome": "outcome name in English, max 8 words",
    "measure": "HR | OR | RR | MD | SMD | % | mean | other",
    "value": "the number exactly as printed, e.g. 0.63 or -136.03",
    "ci": "95% CI exactly as printed, e.g. 0.52 to 0.76, empty string if absent",
    "p": "p value exactly as printed, e.g. <0.001, empty string if absent"}
 ]}

ABSOLUTE RULES
1. Copy numbers character by character from the supplied text. Never round, convert,
   estimate or infer a number that is not printed there.
2. SIGNS MATTER. If a value is printed as negative (e.g. -136.03, meaning a reduction),
   keep the minus sign. Never drop it and never add one.
3. In a confidence interval the dash is a separator, not a minus sign: "0.52-0.76" has two
   positive bounds. Write intervals as "lower to upper" so the bounds stay unambiguous.
4. If an article reports no usable numeric result, return "findings": [].
5. At most 3 findings per article, the clinically most important ones.
6. Always echo the PMID exactly as supplied. Return one element per supplied article."""

# Oran ölçütlerinde güven aralığı sınırları negatif olamaz
RATIO_MEASURES = {"hr", "or", "rr", "irr", "shr", "aor", "ahr", "hazard", "odds"}

# "0.52 to 0.76", "-253.36, -18.70", "0.71–0.87" gibi aralıkları iki sınıra ayırır
INTERVAL_RE = re.compile(
    r"^\s*(-?\s*\d+(?:\.\d+)?)\s*(?:to|and|ile|[,;]|-)\s*(-?\s*\d+(?:\.\d+)?)\s*$",
    re.IGNORECASE)


def _normalize(text: str) -> str:
    """Dergilerin farklı ondalık ve tire karakterlerini tek biçime indirger."""
    replacements = {
        "·": ".",   # Lancet tarzı orta nokta: 0·79
        "−": "-",   # eksi işareti
        "–": "-",   # en tire
        "—": "-",   # em tire
        " ": "",    # ince boşluk
        " ": " ",   # kırılmaz boşluk
    }
    for old, new in replacements.items():
        text = text.replace(old, new)
    return re.sub(r"\s+", " ", text)


def _coerce_int(value) -> int:
    try:
        return max(0, int(re.sub(r"[.,\s]", "", str(value)).strip()))
    except (TypeError, ValueError):
        return 0


def _clean_finding(raw: dict) -> dict | None:
    outcome = str(raw.get("outcome") or "").strip()
    value = str(raw.get("value") or "").strip()
    if not outcome or not value:
        return None
    return {
        "outcome": outcome[:80],
        "measure": str(raw.get("measure") or "").strip()[:12],
        "value": value[:24],
        "ci": str(raw.get("ci") or "").strip()[:48],
        "p": str(raw.get("p") or "").strip()[:20],
    }


def _parse_json(raw: str) -> dict:
    """Model yanıtındaki kod bloğu işaretlerini temizleyip JSON'a çevirir."""
    text = re.sub(r"^```(?:json)?|```$", "", raw.strip(), flags=re.MULTILINE).strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", text, flags=re.DOTALL)
        if not match:
            return {}
        try:
            return json.loads(match.group(0))
        except json.JSONDecodeError:
            return {}


# --------------------------------------------------------------- doğrulama
def _positions(number: str, haystack: str) -> list[int]:
    """Sayının metinde tek başına geçtiği konumlar.

    Başka bir sayının parçası olan eşleşme sayılmaz: "1.3", "61.3" ya da "1.35"
    içinde bulunmuş sayılmaz. Önceden düz alt dize aranıyordu ve metinde hiç geçmeyen
    bir değer, benzeyen bir sayının içinde "doğrulanmış" oluyordu."""
    pattern = rf"(?<![\d.]){re.escape(number)}(?!\d|\.\d)"
    return [m.start() for m in re.finditer(pattern, haystack)]


def _signed_value(value: str, haystack: str) -> str | None:
    """Sayıyı kaynak metinde arar. Metinde eksiyle geçiyorsa işareti geri koyar."""
    bare = value.lstrip("-").strip()
    if not bare:
        return None

    positions = _positions(bare, haystack)
    if not positions:
        return None

    def is_negative(index: int) -> bool:
        """Sayının önünde eksi var mı. Tirenin solunda bir rakam varsa tire eksi değil
        aralık ayırıcısıdır: "12.4-15.8" ya da "45 - 67" içindeki ikinci sayı pozitiftir."""
        j = index - 1
        while j >= 0 and haystack[j] == " ":
            j -= 1
        if j < 0 or haystack[j] != "-":
            return False
        k = j - 1
        while k >= 0 and haystack[k] == " ":
            k -= 1
        return not (k >= 0 and haystack[k].isdigit())

    # Tüm geçişlerde eksiyle yazılmışsa değer negatiftir
    if all(is_negative(i) for i in positions):
        return "-" + bare
    return bare


# "95% CI", "95%CI:" gibi ön ekler aralığın sayılarından değildir
_CI_PREFIX_RE = re.compile(r"^\s*(?:95\s*%\s*)?(?:ci|confidence interval)?\s*[:=]?\s*",
                           re.IGNORECASE)


def _numbers_in_source(text: str, haystack: str, allow_bare_decimal: bool = False) -> bool:
    """Metindeki her sayı kaynakta tek başına geçiyor mu (en az bir sayı olmalı)."""
    numbers = re.findall(r"\d+(?:\.\d+)?", text)
    if not numbers:
        return False
    for number in numbers:
        if _positions(number, haystack):
            continue
        # p değerleri çoğu dergide başındaki sıfır olmadan yazılır: p<.001
        if allow_bare_decimal and number.startswith("0.") and _positions(number[1:], haystack):
            continue
        return False
    return True


def _fix_interval(ci: str, measure: str) -> str:
    """Ayırıcı tireden gelen sahte eksileri temizler, sınırları küçükten büyüğe sıralar."""
    match = INTERVAL_RE.match(_normalize(ci))
    if not match:
        return _normalize(ci).strip()

    low, high = (re.sub(r"\s+", "", group) for group in match.groups())
    if measure.lower() in RATIO_MEASURES:
        low, high = low.lstrip("-"), high.lstrip("-")
    try:
        if float(low) > float(high):
            low, high = high, low
    except ValueError:
        pass
    return f"{low} – {high}"


def _verify(finding: dict, haystack: str) -> dict | None:
    """Kaynak metinde doğrulanamayan bulguyu eler, doğrulananın işaretini düzeltir.

    Güven aralığının iki sınırı da kaynakta aranır; bulunamazsa bulgu tamamen elenir.
    Yalnız aralığı silip değeri bırakmak tehlikeli olurdu: yön tablosu aralık yokken
    sonucu "anlamlı" sayar (bkz. agreement.direction). Kaynakta bulunmayan p değeri
    ise yalnızca boşaltılır; değer ve aralık doğrulanmışsa bulgu kullanılabilir kalır."""
    corrected = _signed_value(_normalize(finding["value"]).replace(",", "."), haystack)
    if corrected is None:
        return None
    finding["value"] = corrected
    if finding.get("ci"):
        ci = _fix_interval(_CI_PREFIX_RE.sub("", finding["ci"]), finding.get("measure", ""))
        if not re.search(r"\d", ci):
            ci = ""                          # "not reported" gibi: aralık değil
        elif not _numbers_in_source(ci, haystack):
            return None
        finding["ci"] = ci
    p = _normalize(finding.get("p") or "")
    if re.search(r"\d", p) and not _numbers_in_source(p, haystack, allow_bare_decimal=True):
        finding["p"] = ""
    return finding


# ------------------------------------------------------------------ çıkarım
def _build_prompt(articles: list, fulltexts: dict[str, str]) -> str:
    blocks = []
    for art in articles:
        parts = [f"PMID: {art.pmid or art.pmcid}",
                 f"TITLE: {art.title}",
                 f"TYPE: {', '.join(art.pub_types) or 'n/a'}",
                 f"ABSTRACT: {art.abstract}"]
        excerpt = fulltexts.get(art.pmid or art.pmcid, "")
        if excerpt:
            # Sayısal sonuçlar çoğunlukla bulgular bölümünde geçer
            parts.append(f"RESULTS EXCERPT: {excerpt[:modes.active().extract_excerpt_chars]}")
        blocks.append("\n".join(parts))
    return "ARTICLES:\n\n" + "\n\n---\n\n".join(blocks)


def _source_text(article, fulltexts: dict[str, str]) -> str:
    # Modele gönderilen metnin aynısı: doğrulama, modelin görmediği bir metinde
    # sayı aramamalı.
    excerpt = fulltexts.get(article.pmid or article.pmcid, "")[:modes.active().extract_excerpt_chars]
    return _normalize(article.abstract + " " + excerpt)


def extract_findings(articles: list, fulltexts: dict[str, str] | None = None,
                     batch_size: int = 8) -> tuple[int, int]:
    """Makaleleri yerinde zenginleştirir.

    (sayısal veri bulunan makale sayısı, kaynakta doğrulanamadığı için elenen bulgu sayısı)
    döndürür. Elenen sayı kullanıcıya da gösterilir: doğrulama katmanının çalıştığını
    görmek, aracın ürettiği rakamlara duyulan güvenin temelidir.
    """
    fulltexts = fulltexts or {}
    if not articles:
        return 0, 0

    by_pmid = {(a.pmid or a.pmcid): a for a in articles}
    enriched, dropped = 0, 0

    meter = modes.current().meter
    for start in range(0, len(articles), batch_size):
        batch = articles[start:start + batch_size]
        # Bütçe tavanına yaklaşıldıysa kalan partiler atlanır: elde edilmiş bulgular
        # korunur, sentez bütçesi harcanmaz.
        if meter is not None and not meter.allow("extract"):
            meter.note_degraded("extract")
            break
        try:
            raw = generate(_build_prompt(batch, fulltexts), system=EXTRACT_SYSTEM,
                           json_mode=True, stage="extract")
        except Exception as exc:
            print(f"[extract] numeric extraction failed: {exc}")
            continue

        data = _parse_json(raw)
        for item in (data.get("articles", []) if isinstance(data, dict) else []):
            article = by_pmid.get(str(item.get("pmid", "")).strip())
            if article is None:
                continue

            article.design = str(item.get("design") or "").strip()[:60]
            article.population = str(item.get("population") or "").strip()[:80]
            article.sample_size = _coerce_int(item.get("n"))

            haystack = _source_text(article, fulltexts)
            findings = []
            for raw_finding in (item.get("findings") or []):
                if not isinstance(raw_finding, dict):
                    continue
                cleaned = _clean_finding(raw_finding)
                if not cleaned:
                    continue
                if _verify(cleaned, haystack):
                    findings.append(cleaned)
                else:
                    dropped += 1

            article.findings = findings[:3]
            if findings or article.sample_size:
                enriched += 1

    if dropped:
        print(f"[extract] {dropped} finding(s) dropped: not verifiable in the source text")
    return enriched, dropped
