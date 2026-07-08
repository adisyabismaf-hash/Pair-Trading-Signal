"""HTTP client for the Extended Exchange public REST API."""
import logging
import time

import httpx

from core.config import settings

logger = logging.getLogger(__name__)

_TIMEOUT = httpx.Timeout(20.0, connect=10.0)
_MARKETS_CACHE: dict = {"at": 0.0, "data": None}
_MARKETS_TTL_SECONDS = 10  # short TTL so dashboard prices feel live


class ExtendedApiError(Exception):
    pass


def _get(path: str, params: dict | None = None) -> dict:
    url = f"{settings.extended_base_url}{path}"
    last_err: Exception | None = None
    for attempt in range(3):
        try:
            with httpx.Client(timeout=_TIMEOUT) as client:
                resp = client.get(url, params=params)
            resp.raise_for_status()
            payload = resp.json()
            if payload.get("status") != "OK":
                raise ExtendedApiError(f"API returned status={payload.get('status')} for {path}")
            return payload
        except (httpx.HTTPError, ValueError, ExtendedApiError) as exc:
            last_err = exc
            logger.warning("Extended API call failed (attempt %d/3) %s: %s", attempt + 1, path, exc)
            time.sleep(1.0 * (attempt + 1))
    raise ExtendedApiError(f"Extended API unreachable for {path}: {last_err}")


def fetch_candles(market: str, interval: str = "P1D", limit: int = 250,
                  candle_type: str = "trades") -> list[dict]:
    """Return daily candles sorted oldest -> newest as
    [{"t": ms, "o": float, "h": float, "l": float, "c": float, "v": float}, ...]."""
    payload = _get(f"/api/v1/info/candles/{market}/{candle_type}",
                   params={"interval": interval, "limit": limit})
    raw = payload.get("data") or []
    candles = []
    for item in raw:
        try:
            candles.append({
                "t": int(item["T"]),
                "o": float(item["o"]),
                "h": float(item["h"]),
                "l": float(item["l"]),
                "c": float(item["c"]),
                "v": float(item["v"]),
            })
        except (KeyError, TypeError, ValueError):
            continue
    candles.sort(key=lambda x: x["t"])
    return candles


def fetch_markets(force: bool = False) -> list[dict]:
    """Return active markets, cached for 5 minutes."""
    now = time.monotonic()
    if not force and _MARKETS_CACHE["data"] is not None and now - _MARKETS_CACHE["at"] < _MARKETS_TTL_SECONDS:
        return _MARKETS_CACHE["data"]

    payload = _get("/api/v1/info/markets")
    markets = []
    for m in payload.get("data") or []:
        if not m.get("active"):
            continue
        stats = m.get("marketStats") or {}
        try:
            last_price = float(stats["lastPrice"]) if stats.get("lastPrice") else None
        except (TypeError, ValueError):
            last_price = None
        try:
            chg = stats.get("dailyPriceChangePercentage")
            daily_change_pct = float(chg) * 100 if chg is not None else None
        except (TypeError, ValueError):
            daily_change_pct = None
        markets.append({
            "name": m.get("name"),
            "category": m.get("category"),
            "description": m.get("description"),
            "last_price": last_price,
            "daily_change_pct": daily_change_pct,
        })
    markets.sort(key=lambda x: x["name"] or "")
    _MARKETS_CACHE["data"] = markets
    _MARKETS_CACHE["at"] = now
    return markets


def market_exists(market: str) -> bool:
    try:
        return any(m["name"] == market for m in fetch_markets())
    except ExtendedApiError:
        # If the markets endpoint is down, don't block the user from adding a symbol.
        return True

