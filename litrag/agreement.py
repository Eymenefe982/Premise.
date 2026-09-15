"""Çalışmalar arası yön uyuşması.

`extract.py` her makaleden ölçüt, değer ve güven aralığını kaynak metinde doğrulayarak
çıkarır. Aynı sonlanımı bildiren makaleler bir araya getirildiğinde, sonucun yönü
(azalma / artış / anlamsız) tamamen aritmetikle belirlenebilir. Bu tablo hiçbir model
çağrısı kullanmaz: dil modeli çalışmalar arasında olmayan bir uzlaşı uyduramaz, çünkü
uzlaşıyı model değil bu modül hesaplar.
"""
from __future__ import annotations

import re

# Etkisizlik noktası ölçüte göre değişir: oranlarda 1, farklarda 0.
RATIO_MEASURES = {"hr", "or", "rr", "irr", "shr", "aor", "ahr", "rate ratio", "odds ratio"}
DIFF_MEASURES = {"md", "smd", "wmd", "mean", "mean difference", "rd"}

_WORD_RE = re.compile(r"[a-z]+")
_OUTCOME_STOPWORDS = {"the", "of", "in", "and", "for", "rate", "rates", "risk", "total"}

# Dergiler aynı sonlanımı farklı adlandırır. Eşanlamlılar tek biçime indirgenmezse
# "all-cause mortality" ile "all-cause death" ayrı gruplara düşer.
_SYNONYMS = {
    "mortality": "death", "deaths": "death", "died": "death", "fatal": "death",
    "hospitalization": "hospitalisation", "hospitalizations": "hospitalisation",
    "hospitalisations": "hospitalisation", "admission": "hospitalisation",
    "admissions": "hospitalisation", "hospitalised": "hospitalisation",
    "cv": "cardiovascular", "hf": "failure", "mace": "cardiovascular",
    "worsening": "worsening", "events": "event",
}


def _number(text: str) -> float | None:
    match = re.search(r"-?\d+(?:\.\d+)?", (text or "").replace(",", "."))
    return float(match.group()) if match else None


def _bounds(ci: str) -> tuple[float, float] | None:
    numbers = re.findall(r"-?\d+(?:\.\d+)?", (ci or "").replace(",", "."))
    if len(numbers) < 2:
        return None
    low, high = float(numbers[0]), float(numbers[1])
    return (low, high) if low <= high else (high, low)


def direction(finding: dict) -> str:
    """decrease / increase / null / unknown — sonucun yönü ve anlamlılığı."""
    measure = (finding.get("measure") or "").strip().lower()
    value = _number(finding.get("value", ""))
    if value is None:
        return "unknown"

    if measure in RATIO_MEASURES:
        null_point = 1.0
    elif measure in DIFF_MEASURES:
        null_point = 0.0
    else:
        return "unknown"

    bounds = _bounds(finding.get("ci", ""))
    if bounds and bounds[0] <= null_point <= bounds[1]:
        return "null"          # güven aralığı etkisizlik noktasını kesiyor

    if value < null_point:
        return "decrease"
    if value > null_point:
        return "increase"
    return "null"


def _key_tokens(outcome: str) -> set[str]:
    return {_SYNONYMS.get(w, w) for w in _WORD_RE.findall((outcome or "").lower())
            if len(w) > 1 and w not in _OUTCOME_STOPWORDS}


def _same_outcome(a: set[str], b: set[str]) -> bool:
    """Jaccard benzerliği. Kapsama oranı kullanılamaz: bileşik bir sonlanımın
    ("kardiyovasküler ölüm veya kalp yetmezliği yatışı") bileşenleri onu tamamen
    kapsar ve iki ayrı sonlanım tek grupta birleşerek uyuşmayı olduğundan güçlü
    gösterirdi."""
    if not a or not b:
        return False
    return len(a & b) / len(a | b) >= 0.7


def build(articles: list) -> list[dict]:
    """Aynı sonlanımı en az iki makalenin bildirdiği grupları döndürür."""
    groups: list[dict] = []

    for art in articles:
        for finding in (art.findings or []):
            way = direction(finding)
            if way == "unknown":
                continue
            tokens = _key_tokens(finding.get("outcome", ""))
            if not tokens:
                continue

            entry = {
                "pmid": art.pmid or art.pmcid,
                "year": art.year,
                "design": art.design or art.evidence_label,
                "measure": finding.get("measure", ""),
                "value": finding.get("value", ""),
                "ci": finding.get("ci", ""),
                "direction": way,
            }
            slot = next((g for g in groups if _same_outcome(g["tokens"], tokens)), None)
            name = finding.get("outcome", "")
            if slot is None:
                groups.append({"outcome": name, "tokens": tokens, "entries": [entry]})
            else:
                slot["entries"].append(entry)
                # Grup adı en sade yazımda kalsın; birleştirme token kümesini büyütmez,
                # yoksa grup her yeni üyeyle biraz daha gevşer ve alakasızları çeker.
                if name and len(name) < len(slot["outcome"]):
                    slot["outcome"] = name

    out = []
    for group in groups:
        entries = group["entries"]
        if len(entries) < 2:
            continue
        counts = {"decrease": 0, "increase": 0, "null": 0}
        for entry in entries:
            counts[entry["direction"]] += 1
        # En az iki farklı yön varsa çalışmalar çelişiyor demektir
        directions_seen = [k for k, v in counts.items() if v]
        out.append({
            "outcome": group["outcome"],
            "entries": sorted(entries, key=lambda e: e["year"], reverse=True),
            "counts": counts,
            "conflicting": len([k for k in directions_seen if k != "null"]) > 1,
        })

    out.sort(key=lambda g: len(g["entries"]), reverse=True)
    return out[:6]
