"""Alternative.me Fear & Greed Index (M7) — gratis, tanpa key, update 1x/hari.
Wajib atribusi ke alternative.me saat ditampilkan ke user."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from app.fetchers.base import get_json

FNG_URL = "https://api.alternative.me/fng/"


def fetch_fng(limit: int = 1) -> tuple[list[dict], str, int]:
    data, status = get_json(FNG_URL, params={"limit": limit, "format": "json"})
    if data.get("metadata", {}).get("error"):
        raise ValueError(f"FNG API error: {data['metadata']['error']}")
    return data["data"], FNG_URL, status


def parse_fng(records: list[dict]) -> list[dict[str, Any]]:
    return [{
        "value": int(r["value"]),
        "value_classification": r["value_classification"],
        "fng_timestamp": datetime.fromtimestamp(int(r["timestamp"]), tz=timezone.utc),
    } for r in records]
