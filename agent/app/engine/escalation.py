"""Matriks eskalasi E1–E5 — deterministik, bukan opini LLM.
Sumber aturan: action_rules.yaml bagian action_bias.escalation_rules.

E1: 1 modul trigger tunggal            -> maks MONITOR
E2: 2 modul independen, entitas sama,
    dalam 24 jam                       -> min REVIEW
E3: >=2 modul searah + konfirmasi M6
    searah                             -> boleh RISK_OFF / OPPORTUNITY
E4: entitas ada di positions.yaml      -> naik 1 tingkat (maks RISK_OFF/OPPORTUNITY)
E5: event tanpa invalidation           -> DROP (tidak boleh dikirim)
"""
from __future__ import annotations

from collections import defaultdict
from datetime import timedelta

from app.engine.events import SignalEvent, SignalGroup

# tangga level: RISK_OFF dan OPPORTUNITY setingkat (terminal, arah yang membedakan)
_ORDER = {"INFO": 0, "MONITOR": 1, "REVIEW": 2, "RISK_OFF": 3, "OPPORTUNITY": 3}

# modul yang max_level-nya INFO (dari yaml) tidak ikut hitungan eskalasi —
# mereka masuk regime score / konteks, bukan alert (M5, M7)
_SEVERITY_TO_BASE = {"INFO": "INFO", "MONITOR": "MONITOR", "REVIEW_CANDIDATE": "MONITOR"}


def _module_max_levels(rules_modules: dict) -> dict[str, str]:
    """Peta 'M1'.. -> max_level dari yaml (jika ada)."""
    out = {}
    for name, cfg in rules_modules.items():
        mid = name.split("_")[0]  # 'M4_price_volume_anomaly' -> 'M4'
        if "max_level" in cfg:
            out[mid] = cfg["max_level"]
    return out


def _cap(level: str, cap_level: str) -> str:
    return level if _ORDER[level] <= _ORDER[cap_level] else cap_level


def _bump(level: str, direction: str) -> str:
    """E4: naik 1 tingkat; tingkat 3 memakai arah grup."""
    nxt = min(_ORDER[level] + 1, 3)
    if nxt < 3:
        return {0: "INFO", 1: "MONITOR", 2: "REVIEW"}[nxt]
    return "OPPORTUNITY" if direction == "bullish" else "RISK_OFF"


def _group_direction(events: list[SignalEvent]) -> str:
    dirs = [e.direction for e in events if e.direction != "neutral"]
    if not dirs:
        return "neutral"
    bear = sum(1 for d in dirs if d == "bearish")
    bull = len(dirs) - bear
    if bear == bull:
        return "neutral"
    return "bearish" if bear > bull else "bullish"


def escalate(events: list[SignalEvent], rules_modules: dict,
             position_entities: set[str],
             window_hours: int = 24) -> list[SignalGroup]:
    """Input: semua event kandidat (biasanya 24 jam terakhir).
    Output: SignalGroup per entitas dengan final level E1–E5."""
    max_levels = _module_max_levels(rules_modules)

    # E5 — drop event tanpa invalidation
    valid = [e for e in events if (e.invalidation or "").strip()]

    by_entity: dict[str, list[SignalEvent]] = defaultdict(list)
    for e in valid:
        by_entity[e.entity.lower()].append(e)

    groups: list[SignalGroup] = []
    for entity, evs in by_entity.items():
        evs.sort(key=lambda e: e.ts)
        # jendela 24 jam (E2): pakai event dalam window dari event terbaru
        newest = evs[-1].ts
        evs = [e for e in evs if newest - e.ts <= timedelta(hours=window_hours)]

        # modul max_level INFO tidak ikut eskalasi (konteks saja)
        escalatable = [e for e in evs if max_levels.get(e.module) != "INFO"]
        context_only = [e for e in evs if max_levels.get(e.module) == "INFO"]

        if not escalatable:
            if context_only:
                groups.append(SignalGroup(entity=evs[0].entity, level="INFO",
                                          direction=_group_direction(context_only),
                                          events=context_only))
            continue

        modules = {e.module for e in escalatable}
        direction = _group_direction(escalatable)

        # level dasar dari severity terberat
        base = "INFO"
        for e in escalatable:
            cand = _SEVERITY_TO_BASE[e.severity]
            if _ORDER[cand] > _ORDER[base]:
                base = cand
        level = base

        if len(modules) == 1:
            # E1 — trigger tunggal: maks MONITOR (+ max_level modul, mis. M4)
            level = _cap(level, "MONITOR")
            only = next(iter(modules))
            if only in max_levels:
                level = _cap(level, max_levels[only])
        else:
            # E2 — >=2 modul independen: min REVIEW
            if _ORDER[level] < _ORDER["REVIEW"]:
                level = "REVIEW"
            # E3 — >=2 modul non-M6 searah + konfirmasi M6 searah
            non_m6 = [e for e in escalatable if e.module != "M6" and e.direction == direction
                      and direction != "neutral"]
            m6_confirm = any(e.module == "M6" and e.direction == direction for e in escalatable)
            if len({e.module for e in non_m6}) >= 2 and m6_confirm:
                level = "OPPORTUNITY" if direction == "bullish" else "RISK_OFF"

        touches = entity in position_entities
        if touches:
            level = _bump(level, direction)  # E4

        groups.append(SignalGroup(entity=evs[0].entity, level=level,
                                  direction=direction, events=evs,
                                  touches_position=touches))

    order = {"RISK_OFF": 0, "OPPORTUNITY": 0, "REVIEW": 1, "MONITOR": 2, "INFO": 3}
    groups.sort(key=lambda g: order[g.level])
    return groups
