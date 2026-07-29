"""Extended Exchange (Starknet) public API — pengganti Binance untuk M6
(funding rate + open interest) karena fapi.binance.com terblokir di jaringan user.

Endpoint: GET {base}/api/v1/info/markets/{market}/stats  (tanpa API key)
Catatan konversi: funding Extended dibayar PER 1 JAM; threshold M6 di
action_rules.yaml berbasis %/8 jam -> nilai dikali 8 sebelum dibandingkan."""
from __future__ import annotations

from typing import Any

from app.fetchers.base import get_json

DEFAULT_BASE_URL = "https://api.starknet.extended.exchange"

# simbol internal (gaya Binance, dipakai di DB/regime) -> market Extended
MARKET_MAP = {"BTCUSDT": "BTC-USD", "ETHUSDT": "ETH-USD"}

FUNDING_PERIODS_PER_8H = 8  # funding Extended per 1 jam


def fetch_market_stats(symbol: str, base_url: str = DEFAULT_BASE_URL) -> tuple[dict, str, int]:
    market = MARKET_MAP.get(symbol, symbol)
    url = f"{base_url}/api/v1/info/markets/{market}/stats"
    data, status = get_json(url)
    if data.get("status") != "OK":
        raise ValueError(f"Extended API error untuk {market}: {str(data)[:200]}")
    return data["data"], url, status


def fetch_candles(market: str, interval: str = "P1D", limit: int = 200,
                  base_url: str = DEFAULT_BASE_URL) -> list[dict[str, float]]:
    """Daily/intraday candles for a market. interval: ISO-8601 duration Extended accepts
    (PT4H, P1D, PT1H, …; note P1W returns empty — aggregate daily instead). Returns
    oldest->newest list of {t_ms, o, h, l, c, v}."""
    market = MARKET_MAP.get(market, market)
    url = f"{base_url}/api/v1/info/candles/{market}/trades"
    data, _status = get_json(url, params={"interval": interval, "limit": min(limit, 1000)})
    rows = data.get("data") or []
    out = [{"t_ms": int(r["T"]), "o": float(r["o"]), "h": float(r["h"]),
            "l": float(r["l"]), "c": float(r["c"]), "v": float(r.get("v") or 0)}
           for r in rows]
    out.sort(key=lambda c: c["t_ms"])
    return out


def parse_derivatives(symbol: str, stats: dict[str, Any]) -> dict[str, Any]:
    funding_1h = float(stats.get("fundingRate") or 0)          # fraksi per 1 jam
    funding_8h_pct = funding_1h * FUNDING_PERIODS_PER_8H * 100  # -> %/8j
    oi_usd = float(stats.get("openInterest") or 0)              # sudah dalam USD
    oi_base = float(stats.get("openInterestBase") or 0)
    return {
        "symbol": symbol,
        "funding_rate_8h_pct": funding_8h_pct,
        "open_interest": oi_base or None,
        "open_interest_usd": oi_usd or None,
    }
