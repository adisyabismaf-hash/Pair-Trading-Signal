"""Regime score (Blok A daily outlook) — rule-based, bobot dari action_rules.yaml.
Setiap komponen dinormalisasi ke 0–100 lalu dibobot. Formula normalisasi adalah
titik awal kalibrasi (sama seperti threshold modul) — direview mingguan."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any


def _clamp(x: float, lo: float = 0.0, hi: float = 100.0) -> float:
    return max(lo, min(hi, x))


@dataclass
class RegimeInputs:
    fear_greed: int | None                    # 0-100
    stablecoin_delta_7d_pct: float | None     # %
    breadth_green_pct: float | None           # % aset watchlist hijau 7 hari (0-100)
    funding_btc_8h_pct: float | None          # %/8j
    tvl_aggregate_delta_7d_pct: float | None  # % delta TVL agregat chain 7 hari


def component_scores(inp: RegimeInputs, modules_cfg: dict[str, Any]) -> dict[str, float]:
    """Komponen hilang -> netral 50 (tidak menghukum saat data belum lengkap)."""
    s: dict[str, float] = {}

    s["fear_greed"] = float(inp.fear_greed) if inp.fear_greed is not None else 50.0

    # +0.5% per 7h (threshold M5) = 75; +1% = 100; -0.5% = 25
    thr_m5 = modules_cfg["M5_stablecoin_flow"]["triggers"]["total_supply_delta_7d_pct"]
    d = inp.stablecoin_delta_7d_pct
    s["stablecoin_supply_delta_7d"] = _clamp(50 + (d / thr_m5) * 25) if d is not None else 50.0

    s["watchlist_breadth_7d"] = _clamp(inp.breadth_green_pct) if inp.breadth_green_pct is not None else 50.0

    # funding moderat positif = sehat; >= extreme (M6) = overheat -> skor rendah
    m6 = modules_cfg["M6_derivatives"]["triggers"]
    f = inp.funding_btc_8h_pct
    if f is None:
        s["funding_aggregate"] = 50.0
    elif f >= m6["funding_8h_extreme"]:
        s["funding_aggregate"] = 30.0
    else:
        s["funding_aggregate"] = _clamp(50 + (f / m6["funding_8h_hot"]) * 25)

    # TVL agregat: ±5% per 7 hari = ujung skala
    t = inp.tvl_aggregate_delta_7d_pct
    s["tvl_aggregate_delta"] = _clamp(50 + t * 10) if t is not None else 50.0
    return s


def regime_score(inp: RegimeInputs, rules_regime: dict[str, Any],
                 modules_cfg: dict[str, Any]) -> tuple[int, str, dict[str, float]]:
    weights = rules_regime["weights"]
    scores = component_scores(inp, modules_cfg)
    total = sum(scores[k] * w for k, w in weights.items()) / 100.0
    score = int(round(total))
    label = _label(score, rules_regime["labels"])
    return score, label, scores


def _label(score: int, labels: dict[str, dict]) -> str:
    for name, bounds in labels.items():
        lo = bounds.get("min", -10**9)
        hi = bounds.get("max", 10**9)
        if lo <= score <= hi:
            return name
    return "NETRAL"
