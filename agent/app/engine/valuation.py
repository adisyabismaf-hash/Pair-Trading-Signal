"""Token valuation for HYPE / LIT: revenue, fees, TVL, and three price multiples
(P/F, P/S, P/E) with an indicative undervalue/overvalue read.

Definitions (annualized from DeFiLlama's trailing 7-day totals for stability):
  Fees      = total fees paid by users        -> P/F = market cap / fees
  Revenue   = protocol revenue (its cut)       -> P/S = market cap / revenue
  Earnings  = revenue accruing to token holders (DeFiLlama dailyHoldersRevenue)
                                               -> P/E = market cap / earnings

The undervalue/overvalue label is a rough heuristic on P/S, not financial advice.
"""
from __future__ import annotations

import logging
from typing import Any

from app.fetchers.base import get_json

logger = logging.getLogger(__name__)

_BASE = "https://api.llama.fi"
_ANNUALIZE = 365.0 / 7.0  # from trailing-7d total


def _annual(data: dict) -> float | None:
    wk = data.get("total7d")
    if wk:
        return float(wk) * _ANNUALIZE
    d1 = data.get("total24h")
    return float(d1) * 365.0 if d1 else None


def fetch_economics(slug: str) -> dict[str, float | None]:
    """Annualized fees / revenue / earnings for a DeFiLlama protocol slug."""
    econ: dict[str, float | None] = {"fees": None, "revenue": None, "earnings": None}
    for key, data_type in (("fees", "dailyFees"), ("revenue", "dailyRevenue"),
                           ("earnings", "dailyHoldersRevenue")):
        try:
            data, _ = get_json(f"{_BASE}/summary/fees/{slug}", params={"dataType": data_type})
            econ[key] = _annual(data)
        except Exception as exc:
            logger.warning("valuation: %s %s failed: %s", slug, data_type, exc)
    return econ


def _ratio(mcap: float | None, denom: float | None) -> float | None:
    if not mcap or not denom or denom <= 0:
        return None
    return round(mcap / denom, 1)


def _verdict(ps: float | None) -> str:
    """Indicative read from P/S (price-to-revenue). Rough, not advice."""
    if ps is None:
        return "data belum lengkap"
    if ps < 15:
        return "cenderung UNDERVALUE"
    if ps <= 45:
        return "WAJAR"
    return "cenderung OVERVALUE"


def valuation(slug: str, market_cap: float | None, tvl: float | None) -> dict[str, Any]:
    """Full valuation dict for one token. market_cap & tvl come from the agent DB
    (CoinGecko / DeFiLlama snapshots); economics are fetched live."""
    econ = fetch_economics(slug)
    pf = _ratio(market_cap, econ["fees"])
    ps = _ratio(market_cap, econ["revenue"])
    pe = _ratio(market_cap, econ["earnings"])
    return {
        "slug": slug,
        "market_cap": market_cap,
        "tvl": tvl,
        "fees_annual": econ["fees"],
        "revenue_annual": econ["revenue"],
        "earnings_annual": econ["earnings"],
        "pf": pf, "ps": ps, "pe": pe,
        "mc_tvl": _ratio(market_cap, tvl),
        "verdict": _verdict(ps),
    }
