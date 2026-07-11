"""Binance Futures public REST — funding rate & open interest (M6).
Tanpa API key; rate limit per IP."""
from __future__ import annotations

from typing import Any

from app.fetchers.base import get_json

BASE_URL = "https://fapi.binance.com"


def fetch_premium_index(symbol: str) -> tuple[dict, str, int]:
    """lastFundingRate = funding periode 8 jam berjalan (fraksi, bukan %)."""
    url = f"{BASE_URL}/fapi/v1/premiumIndex"
    data, status = get_json(url, params={"symbol": symbol})
    return data, url, status


def fetch_open_interest(symbol: str) -> tuple[dict, str, int]:
    url = f"{BASE_URL}/fapi/v1/openInterest"
    data, status = get_json(url, params={"symbol": symbol})
    return data, url, status


def parse_derivatives(symbol: str, premium: dict, oi: dict,
                      mark_price: float | None = None) -> dict[str, Any]:
    funding_pct = float(premium.get("lastFundingRate", 0)) * 100  # -> %/8j
    mark = mark_price or float(premium.get("markPrice") or 0)
    oi_qty = float(oi.get("openInterest") or 0)
    return {
        "symbol": symbol,
        "funding_rate_8h_pct": funding_pct,
        "open_interest": oi_qty,
        "open_interest_usd": oi_qty * mark if mark else None,
    }
