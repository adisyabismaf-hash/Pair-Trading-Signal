"""DefiLlama Free API (api.llama.fi, tanpa key) — TVL (M1), fees (M2),
stablecoin supply (M5). Endpoint sesuai defillama-api-reference.md."""
from __future__ import annotations

from typing import Any

from app.fetchers.base import get_json

BASE_URL = "https://api.llama.fi"
STABLECOINS_BASE_URL = "https://stablecoins.llama.fi"


def fetch_protocols() -> tuple[list[dict], str, int]:
    """Semua protokol + TVL + change_1d/change_7d — 1 call untuk seluruh watchlist."""
    url = f"{BASE_URL}/protocols"
    data, status = get_json(url)
    return data, url, status


def parse_protocol_tvl(data: list[dict], slugs: list[str]) -> list[dict[str, Any]]:
    """Match slug langsung; kalau slug adalah parent protocol (mis. 'hyperliquid'),
    /protocols hanya memuat anak-anaknya (parentProtocol='parent#<slug>') — TVL
    anak dijumlahkan ke parent."""
    out = []
    for slug in slugs:
        s = slug.lower()
        direct = [p for p in data if (p.get("slug") or "").lower() == s and p.get("tvl") is not None]
        if direct:
            p = direct[0]
            out.append({"entity": slug, "entity_type": "protocol",
                        "tvl_usd": float(p["tvl"]),
                        "change_1d": p.get("change_1d"), "change_7d": p.get("change_7d")})
            continue
        children = [p for p in data
                    if (p.get("parentProtocol") or "").lower() == f"parent#{s}"
                    and p.get("tvl") is not None]
        if children:
            out.append({"entity": slug, "entity_type": "protocol",
                        "tvl_usd": float(sum(p["tvl"] for p in children)),
                        "change_1d": None, "change_7d": None})
    return out


def fetch_protocol_history(slug: str) -> tuple[dict, str, int]:
    """Historical TVL 1 protokol — untuk backfill saat cold start."""
    url = f"{BASE_URL}/protocol/{slug}"
    data, status = get_json(url)
    return data, url, status


def parse_protocol_history(data: dict, days: int = 9) -> list[dict[str, Any]]:
    """Ambil N titik harian terakhir dari field 'tvl': [{date, totalLiquidityUSD}]."""
    series = data.get("tvl") or []
    return [{"ts_unix": int(pt["date"]), "tvl_usd": float(pt["totalLiquidityUSD"])}
            for pt in series[-days:]]


def fetch_chains() -> tuple[list[dict], str, int]:
    url = f"{BASE_URL}/v2/chains"
    data, status = get_json(url)
    return data, url, status


def fetch_fees_summary(slug: str) -> tuple[dict, str, int]:
    """Fees & revenue 1 protokol: total24h + total7d (untuk rata2/median 7h)."""
    url = f"{BASE_URL}/summary/fees/{slug}"
    data, status = get_json(url, params={"dataType": "dailyFees"})
    return data, url, status


def parse_fees_summary(data: dict) -> dict[str, Any]:
    return {
        "entity": data.get("slug") or data.get("name", ""),
        "fees_24h_usd": data.get("total24h"),
        "fees_7d_usd": data.get("total7d"),
        # riwayat harian bila tersedia -> dipakai median 7h (catatan M2 soal outlier)
        "daily": [
            {"ts_unix": int(d[0]), "fees_usd": float(d[1])}
            for d in (data.get("totalDataChart") or [])[-8:]
        ],
    }


def fetch_stablecoins() -> tuple[dict, str, int]:
    url = f"{STABLECOINS_BASE_URL}/stablecoins"
    data, status = get_json(url, params={"includePrices": "false"})
    return data, url, status


def parse_stablecoins(data: dict) -> dict[str, Any]:
    """Total circulating USD semua stablecoin (peggedUSD) + breakdown top 10."""
    total = 0.0
    detail = []
    for coin in data.get("peggedAssets") or []:
        circ = (coin.get("circulating") or {}).get("peggedUSD")
        if circ is None:
            continue
        total += float(circ)
        detail.append({"symbol": coin.get("symbol"), "circulating_usd": float(circ)})
    detail.sort(key=lambda d: -d["circulating_usd"])
    return {"total_supply_usd": total, "detail": detail[:10]}
