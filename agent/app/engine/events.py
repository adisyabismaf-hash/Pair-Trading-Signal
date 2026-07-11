"""Kontrak event L3 — output seragam semua modul M1–M7."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone

SEVERITIES = ["INFO", "MONITOR", "REVIEW_CANDIDATE"]
DIRECTIONS = ["bearish", "bullish", "neutral"]


@dataclass
class SignalEvent:
    module: str                 # M1..M7
    type: str                   # mis. tvl_drop_24h, wallet_to_exchange
    entity: str                 # symbol/slug/label wallet
    metric: str
    old_value: float | None
    new_value: float | None
    delta_pct: float | None
    severity: str               # INFO | MONITOR | REVIEW_CANDIDATE
    invalidation: str           # E5: wajib — event tanpa ini di-drop
    source_url: str = ""
    direction: str = "neutral"  # bearish | bullish | neutral (untuk E3)
    ts: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    def as_dict(self) -> dict:
        return {
            "module": self.module, "type": self.type, "entity": self.entity,
            "metric": self.metric, "old_value": self.old_value,
            "new_value": self.new_value, "delta_pct": self.delta_pct,
            "severity": self.severity, "direction": self.direction,
            "invalidation": self.invalidation, "source_url": self.source_url,
            "ts": self.ts.isoformat(),
        }


@dataclass
class SignalGroup:
    """Hasil eskalasi: 1 kartu sinyal per entitas (Blok C / real-time alert)."""
    entity: str
    level: str                  # INFO | MONITOR | REVIEW | RISK_OFF | OPPORTUNITY
    direction: str
    events: list[SignalEvent]
    touches_position: bool = False

    @property
    def invalidation(self) -> str:
        return " DAN ".join(dict.fromkeys(e.invalidation for e in self.events))

    @property
    def modules(self) -> list[str]:
        return sorted({e.module for e in self.events})
