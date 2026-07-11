"""CoinGecko Demo API — sumber utama data market (M4 + Blok B).
Header x-cg-demo-api-key; 1 call /coins/markets untuk seluruh watchlist."""
from __future__ import annotations

from typing import Any

from app.fetchers.base import get_json

BASE_URL = "https://api.coingecko.com/api/v3"


def fetch_markets(coingecko_ids: list[str], api_key: str) -> tuple[list[dict[str, Any]], str, int]:
    """Return (rows, source_url, status). Satu call untuk semua id watchlist."""
    url = f"{BASE_URL}/coins/markets"
    params = {
        "vs_currency": "usd",
        "ids": ",".join(coingecko_ids),
        "price_change_percentage": "24h,7d",
        "per_page": 250,
    }
    headers = {"x-cg-demo-api-key": api_key} if api_key else {}
    data, status = get_json(url, params=params, headers=headers)
    return data, url, status


def fetch_derivatives(api_key: str) -> tuple[list[dict[str, Any]], str, int]:
    """Fallback M6 saat fapi.binance.com tidak terjangkau (blokir ISP):
    /derivatives memuat funding_rate (%/8j) + open_interest (USD) Binance Futures."""
    url = f"{BASE_URL}/derivatives"
    headers = {"x-cg-demo-api-key": api_key} if api_key else {}
    data, status = get_json(url, headers=headers)
    return data, url, status


def parse_derivatives(data: list[dict[str, Any]], symbols: list[str],
                      market: str = "Binance (Futures)") -> list[dict[str, Any]]:
    wanted = set(symbols)
    out = []
    for t in data:
        if (t.get("market") == market and t.get("symbol") in wanted
                and t.get("contract_type") == "perpetual"):
            out.append({
                "symbol": t["symbol"],
                "funding_rate_8h_pct": t.get("funding_rate"),  # sudah dalam %
                "open_interest": None,
                "open_interest_usd": t.get("open_interest"),
            })
    return out


def parse_markets(data: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Bentuk seragam untuk PriceSnapshot."""
    out = []
    for row in data:
        out.append({
            "coingecko_id": row["id"],
            "symbol": (row.get("symbol") or "").upper(),
            "price_usd": row.get("current_price"),
            "market_cap": row.get("market_cap"),
            "volume_24h": row.get("total_volume"),
            "change_24h_pct": row.get("price_change_percentage_24h_in_currency",
                                      row.get("price_change_percentage_24h")),
            "change_7d_pct": row.get("price_change_percentage_7d_in_currency"),
        })
    return [r for r in out if r["price_usd"] is not None]
