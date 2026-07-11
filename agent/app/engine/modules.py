"""M1–M7 sebagai fungsi murni: input snapshot -> list[SignalEvent].
Semua threshold dibaca dari action_rules.yaml (cfg = rules.module('Mx_...')) —
TIDAK ada angka trigger yang di-hardcode di file ini."""
from __future__ import annotations

import statistics
from datetime import datetime
from typing import Any

from app.engine.events import SignalEvent


def _pct(old: float, new: float) -> float | None:
    if old is None or new is None or old == 0:
        return None
    return (new - old) / abs(old) * 100.0


# ------------------------------------------------------------------ M1 — TVL
def m1_tvl(entity: str, tvl_now: float, tvl_24h_ago: float | None,
           tvl_7d_ago: float | None, cfg: dict[str, Any],
           source_url: str = "", ts: datetime | None = None) -> list[SignalEvent]:
    trig = cfg["triggers"]
    inv = cfg["invalidation_default"]
    events: list[SignalEvent] = []
    kw = {"source_url": source_url, **({"ts": ts} if ts else {})}

    d24 = _pct(tvl_24h_ago, tvl_now)
    if d24 is not None and abs(d24) >= trig["delta_24h_pct"]:
        # severity_map yaml: "8-15" -> MONITOR, ">15" -> REVIEW_CANDIDATE
        sev = "REVIEW_CANDIDATE" if abs(d24) > 15 else "MONITOR"
        events.append(SignalEvent(
            module="M1", type="tvl_delta_24h", entity=entity, metric="tvl_usd",
            old_value=tvl_24h_ago, new_value=tvl_now, delta_pct=round(d24, 2),
            severity=sev, direction="bearish" if d24 < 0 else "bullish",
            invalidation=inv, **kw))

    d7 = _pct(tvl_7d_ago, tvl_now)
    if d7 is not None and abs(d7) >= trig["delta_7d_pct"]:
        events.append(SignalEvent(
            module="M1", type="tvl_delta_7d", entity=entity, metric="tvl_usd",
            old_value=tvl_7d_ago, new_value=tvl_now, delta_pct=round(d7, 2),
            severity="MONITOR", direction="bearish" if d7 < 0 else "bullish",
            invalidation=inv, **kw))
    return events


# --------------------------------------------------------- M2 — Revenue/Fees
def m2_fees(entity: str, fees_daily: float, fees_7d_history: list[float],
            cfg: dict[str, Any], source_url: str = "",
            ts: datetime | None = None) -> list[SignalEvent]:
    """fees_7d_history: fees harian 7 hari sebelumnya (tanpa hari ini).
    Pakai median jika ada outlier (max > 3x median) — catatan di yaml."""
    if not fees_7d_history or fees_daily is None:
        return []
    med = statistics.median(fees_7d_history)
    baseline = med if (med > 0 and max(fees_7d_history) > 3 * med) \
        else statistics.fmean(fees_7d_history)
    d = _pct(baseline, fees_daily)
    thr = cfg["triggers"]["daily_vs_7d_avg_pct"]
    if d is None or abs(d) < thr:
        return []
    kw = {"source_url": source_url, **({"ts": ts} if ts else {})}
    return [SignalEvent(
        module="M2", type="fees_vs_7d_avg", entity=entity, metric="fees_24h_usd",
        old_value=round(baseline, 2), new_value=fees_daily, delta_pct=round(d, 2),
        severity="MONITOR", direction="bullish" if d > 0 else "bearish",
        invalidation=f"fees harian kembali dalam ±{thr}% dari rata-rata 7 hari dalam 48 jam",
        **kw)]


# -------------------------------------------------------- M3 — Wallet Tracker
def m3_wallet(tx: dict[str, Any], exchange_addresses: set[str],
              wallet_category: str, cfg: dict[str, Any],
              threshold_usd_override: float | None = None,
              last_activity_before: datetime | None = None) -> list[SignalEvent]:
    """tx: dict hasil parse_transfers (1 transfer). Return 0-2 event."""
    trig = cfg["triggers"]
    inv = cfg["invalidation_default"]
    amount_usd = tx.get("amount_usd")
    events: list[SignalEvent] = []

    base_thr = threshold_usd_override or trig["transfer_usd_min"]
    to_exchange = (tx.get("counterparty") or "").lower() in exchange_addresses \
        and tx.get("direction") == "out"
    thr = base_thr * trig["to_exchange_multiplier"] if to_exchange else base_thr

    if amount_usd is not None and amount_usd >= thr:
        if to_exchange:
            etype, sev, direction = "wallet_to_exchange", cfg["severity_map"]["to_exchange"], "bearish"
        elif wallet_category == "treasury" and tx.get("direction") == "out":
            etype, sev, direction = "treasury_outflow", cfg["severity_map"]["treasury_outflow"], "bearish"
        else:
            etype, sev, direction = "wallet_large_transfer", cfg["severity_map"]["to_unknown_address"], "neutral"
        events.append(SignalEvent(
            module="M3", type=etype, entity=tx["wallet_label"], metric="transfer_usd",
            old_value=float(thr), new_value=round(amount_usd, 2),
            delta_pct=None, severity=sev, direction=direction,
            invalidation=inv, source_url=tx.get("source_url", ""),
            ts=tx.get("block_time") or datetime.now().astimezone()))

    if last_activity_before is not None and tx.get("block_time"):
        dormant_days = (tx["block_time"] - last_activity_before).days
        if dormant_days >= trig["dormant_days_reactivated"]:
            events.append(SignalEvent(
                module="M3", type="dormant_wallet_reactivated", entity=tx["wallet_label"],
                metric="dormant_days", old_value=float(trig["dormant_days_reactivated"]),
                new_value=float(dormant_days), delta_pct=None,
                severity="MONITOR", direction="neutral", invalidation=inv,
                source_url=tx.get("source_url", ""), ts=tx["block_time"]))
    return events


# ------------------------------------------------- M4 — Price/Volume Anomaly
def m4_price_volume(entity: str, volume_24h: float, volumes_30d: list[float],
                    price_move_4h_pct: float | None, cfg: dict[str, Any],
                    source_url: str = "", ts: datetime | None = None) -> list[SignalEvent]:
    """volumes_30d: volume harian 30 hari (tanpa hari ini). Modul paling berisik:
    severity selalu MONITOR — max_level di yaml dijaga engine eskalasi."""
    trig = cfg["triggers"]
    events: list[SignalEvent] = []
    kw = {"source_url": source_url, **({"ts": ts} if ts else {})}

    if volume_24h is not None and len(volumes_30d) >= 10:
        mean = statistics.fmean(volumes_30d)
        stdev = statistics.pstdev(volumes_30d)
        if stdev > 0:
            z = (volume_24h - mean) / stdev
            if z >= trig["volume_zscore_30d"]:
                events.append(SignalEvent(
                    module="M4", type="volume_zscore", entity=entity, metric="volume_24h_zscore",
                    old_value=round(mean, 2), new_value=volume_24h, delta_pct=round(z, 2),
                    severity="MONITOR", direction="neutral",
                    invalidation=f"Z-score volume kembali < {trig['volume_zscore_30d']} dalam 24 jam",
                    **kw))

    if price_move_4h_pct is not None and abs(price_move_4h_pct) >= trig["price_move_4h_pct"]:
        events.append(SignalEvent(
            module="M4", type="price_move_4h", entity=entity, metric="price_change_4h_pct",
            old_value=None, new_value=round(price_move_4h_pct, 2),
            delta_pct=round(price_move_4h_pct, 2), severity="MONITOR",
            direction="bearish" if price_move_4h_pct < 0 else "bullish",
            invalidation=f"pergerakan 4 jam kembali < {trig['price_move_4h_pct']}% pada periode berikutnya",
            **kw))
    return events


# ------------------------------------------------- M5 — Stablecoin Flow
def m5_stablecoin(total_now: float, total_7d_ago: float | None,
                  cfg: dict[str, Any], source_url: str = "",
                  ts: datetime | None = None) -> list[SignalEvent]:
    d = _pct(total_7d_ago, total_now)
    thr = cfg["triggers"]["total_supply_delta_7d_pct"]
    if d is None or abs(d) < thr:
        return []
    kw = {"source_url": source_url, **({"ts": ts} if ts else {})}
    return [SignalEvent(
        module="M5", type="stablecoin_supply_delta_7d", entity="stablecoins",
        metric="total_supply_usd", old_value=total_7d_ago, new_value=total_now,
        delta_pct=round(d, 3), severity="INFO",
        direction="bullish" if d > 0 else "bearish",
        invalidation=f"delta supply 7 hari kembali < {thr}% pada pengukuran harian berikutnya",
        **kw)]


# ------------------------------------------------- M6 — Derivatives
def m6_derivatives(symbol: str, funding_8h_pct: float | None,
                   oi_now: float | None, oi_24h_ago: float | None,
                   cfg: dict[str, Any], source_url: str = "",
                   ts: datetime | None = None) -> list[SignalEvent]:
    trig = cfg["triggers"]
    inv = cfg["invalidation_default"]
    events: list[SignalEvent] = []
    kw = {"source_url": source_url, **({"ts": ts} if ts else {})}

    if funding_8h_pct is not None:
        if funding_8h_pct >= trig["funding_8h_extreme"]:
            events.append(SignalEvent(
                module="M6", type="funding_extreme", entity=symbol, metric="funding_8h_pct",
                old_value=trig["funding_8h_extreme"], new_value=round(funding_8h_pct, 4),
                delta_pct=None, severity="REVIEW_CANDIDATE", direction="bearish",
                invalidation=inv, **kw))
        elif funding_8h_pct > trig["funding_8h_hot"]:
            events.append(SignalEvent(
                module="M6", type="funding_hot", entity=symbol, metric="funding_8h_pct",
                old_value=trig["funding_8h_hot"], new_value=round(funding_8h_pct, 4),
                delta_pct=None, severity="MONITOR", direction="bearish",
                invalidation=inv, **kw))
        elif funding_8h_pct <= trig["funding_8h_negative"]:
            events.append(SignalEvent(
                module="M6", type="funding_negative", entity=symbol, metric="funding_8h_pct",
                old_value=trig["funding_8h_negative"], new_value=round(funding_8h_pct, 4),
                delta_pct=None, severity="MONITOR", direction="bullish",
                invalidation=inv, **kw))

    d_oi = _pct(oi_24h_ago, oi_now)
    if d_oi is not None and abs(d_oi) >= trig["oi_delta_24h_pct"]:
        events.append(SignalEvent(
            module="M6", type="oi_delta_24h", entity=symbol, metric="open_interest",
            old_value=oi_24h_ago, new_value=oi_now, delta_pct=round(d_oi, 2),
            severity="MONITOR", direction="bearish" if d_oi > 0 else "neutral",
            invalidation=inv, **kw))
    return events


# ------------------------------------------------- M7 — Sentiment (F&G)
def m7_sentiment(value: int, cfg: dict[str, Any], source_url: str = "",
                 ts: datetime | None = None) -> list[SignalEvent]:
    trig = cfg["triggers"]
    kw = {"source_url": source_url, **({"ts": ts} if ts else {})}
    if value < trig["extreme_fear_below"]:
        return [SignalEvent(
            module="M7", type="extreme_fear", entity="market", metric="fear_greed",
            old_value=float(trig["extreme_fear_below"]), new_value=float(value),
            delta_pct=None, severity="INFO", direction="bearish",
            invalidation=f"F&G kembali >= {trig['extreme_fear_below']} pada update harian berikutnya",
            **kw)]
    if value > trig["extreme_greed_above"]:
        return [SignalEvent(
            module="M7", type="extreme_greed", entity="market", metric="fear_greed",
            old_value=float(trig["extreme_greed_above"]), new_value=float(value),
            delta_pct=None, severity="INFO", direction="bearish",
            invalidation=f"F&G kembali <= {trig['extreme_greed_above']} pada update harian berikutnya",
            **kw)]
    return []
