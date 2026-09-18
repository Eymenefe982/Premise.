"""Arama başına gerçek token harcamasını ölçer ve TL'ye çevirir.

Bu modül gelmeden önce maliyet hiç ölçülmüyordu: `usage_metadata` okunmuyor, çıktı
token'ına sınır konmuyordu. Kredi de gerçek gidere değil, makale sayısına bakan bir
formüle dayanıyordu. Artık kullanıcıdan düşülen kredi, aramanın bize gerçekten
maliyetinden hesaplanır.
"""
from __future__ import annotations

import threading

from .config import CREDIT_TRY, MODEL_PRICES, UNKNOWN_MODEL_PRICE, USD_TRY


def price_of(model: str) -> tuple[float, float]:
    """(girdi, çıktı) USD / 1M token."""
    return MODEL_PRICES.get(model, UNKNOWN_MODEL_PRICE)


def cost_try(model: str, in_tokens: int, out_tokens: int) -> float:
    price_in, price_out = price_of(model)
    usd = (in_tokens * price_in + out_tokens * price_out) / 1_000_000
    return usd * USD_TRY


def credits_for(amount_try: float) -> int:
    """TL gideri krediye çevirir. Sıfır olmayan her gider en az 1 kredidir."""
    if amount_try <= 0:
        return 0
    return max(1, round(amount_try / CREDIT_TRY))


def _usage_tokens(usage) -> tuple[int, int]:
    """Gemini `usage_metadata` -> (girdi, faturalanan çıktı) token.

    Thinking token'ları çıktı fiyatından faturalanır ama kullandığımız SDK bunları
    `thoughts_token_count` alanında bildirmiyor: alan sıfır gelirken toplam token
    sayısı bunları içeriyor. Ölçülen örnek — gemini-3.6-flash, 7.792 token girdi:
    candidates 538, thoughts_field 0, total 9.504; aradaki 1.174 token görünmeyen
    düşünme. Toplamdan hesaplamazsak fatura ölçümün üstünde çıkar.
    """
    if usage is None:
        return 0, 0
    prompt = int(getattr(usage, "prompt_token_count", 0) or 0)
    reported = (int(getattr(usage, "candidates_token_count", 0) or 0)
                + int(getattr(usage, "thoughts_token_count", 0) or 0))
    total = int(getattr(usage, "total_token_count", 0) or 0)
    return prompt, max(reported, total - prompt)


class Meter:
    """Tek bir aramanın sayacı. Hattın iki adımı paralel koştuğu için kilitli."""

    def __init__(self, ceiling_try: float) -> None:
        self.ceiling_try = ceiling_try
        self._lock = threading.Lock()
        self._stages: dict[str, dict] = {}
        self._total_try = 0.0
        self._degraded: list[str] = []

    def record(self, stage: str, model: str, usage) -> None:
        in_tokens, out_tokens = _usage_tokens(usage)
        if not in_tokens and not out_tokens:
            return
        amount = cost_try(model, in_tokens, out_tokens)
        with self._lock:
            row = self._stages.setdefault(
                stage, {"model": model, "calls": 0, "in": 0, "out": 0, "try": 0.0})
            row["model"] = model
            row["calls"] += 1
            row["in"] += in_tokens
            row["out"] += out_tokens
            row["try"] += amount
            self._total_try += amount

    @property
    def total_try(self) -> float:
        with self._lock:
            return self._total_try

    @property
    def tokens(self) -> dict[str, int]:
        with self._lock:
            return {"in": sum(r["in"] for r in self._stages.values()),
                    "out": sum(r["out"] for r in self._stages.values())}

    @property
    def degraded(self) -> list[str]:
        with self._lock:
            return list(self._degraded)

    def allow(self, stage: str, projected_try: float = 0.0) -> bool:
        """Bu aşama tavanı aşmadan çalışabilir mi?"""
        with self._lock:
            return self._total_try + projected_try <= self.ceiling_try

    def note_degraded(self, stage: str) -> None:
        with self._lock:
            if stage not in self._degraded:
                self._degraded.append(stage)
                print(f"[meter] budget ceiling reached, skipping '{stage}' "
                      f"({self._total_try:.2f} / {self.ceiling_try:.2f} TL)")

    def summary(self) -> dict:
        with self._lock:
            return {
                "cost_try": round(self._total_try, 4),
                "credits": credits_for(self._total_try),
                "ceiling_try": self.ceiling_try,
                "tokens": {"in": sum(r["in"] for r in self._stages.values()),
                           "out": sum(r["out"] for r in self._stages.values())},
                "stages": {name: {"model": r["model"], "calls": r["calls"],
                                  "in": r["in"], "out": r["out"],
                                  "try": round(r["try"], 4)}
                           for name, r in self._stages.items()},
                "degraded": list(self._degraded),
            }
