"""Orkestrasi L5: daily outlook, real-time alert (cap harian), digest sore,
weekly review, dan follow-up invalidation H+1. Semua pesan tercatat di alert_log."""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from sqlalchemy import desc, func, select
from sqlalchemy.orm import Session

from app import models
from app.config import ActionRules, Position, Settings, Watchlist
from app.engine.events import SignalGroup
from app.engine.service import (build_regime_inputs, compute_regime,
                                run_signal_engine, _as_utc)
from app.llm import narrator
from app.notify import formatter
from app.notify.notifier import Notifier

logger = logging.getLogger(__name__)

WIB = ZoneInfo("Asia/Jakarta")
LEVEL_ORDER = {"INFO": 0, "MONITOR": 1, "REVIEW": 2, "RISK_OFF": 3, "OPPORTUNITY": 3}


def _now_wib() -> datetime:
    return datetime.now(WIB)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def build_snapshot(session: Session, watchlist: Watchlist) -> dict:
    """Angka murni untuk Blok B (tanpa interpretasi)."""
    inp = build_regime_inputs(session, watchlist)
    prices: dict[str, dict] = {}
    greens = reds = 0
    for a in watchlist.assets:
        row = session.execute(
            select(models.PriceSnapshot).where(models.PriceSnapshot.coingecko_id == a.coingecko_id)
            .order_by(desc(models.PriceSnapshot.fetched_at)).limit(1)).scalars().first()
        if not row:
            continue
        if a.tier == "core":
            prices[a.symbol] = {"price": row.price_usd, "change_24h": row.change_24h_pct or 0}
        if row.change_24h_pct is not None:
            greens += row.change_24h_pct > 0
            reds += row.change_24h_pct <= 0
    fng = session.execute(select(models.SentimentFng)
                          .order_by(desc(models.SentimentFng.fng_timestamp))
                          .limit(1)).scalars().first()
    return {
        "prices": prices,
        "fng": fng.value if fng else None,
        "fng_label": fng.value_classification if fng else "",
        "stablecoin_delta_7d": inp.stablecoin_delta_7d_pct,
        "funding_btc": inp.funding_btc_8h_pct,
        "breadth_green": greens if (greens + reds) else None,
        "breadth_red": reds,
    }


# Assets that get a fundamentals/valuation block (symbol -> DeFiLlama slug).
FUNDAMENTAL_ASSETS = {"HYPE": "hyperliquid", "LIT": "lighter"}


def _latest_price(session: Session, coingecko_id: str):
    return session.execute(
        select(models.PriceSnapshot).where(models.PriceSnapshot.coingecko_id == coingecko_id)
        .order_by(desc(models.PriceSnapshot.fetched_at)).limit(1)).scalars().first()


def build_market_rows(session: Session, watchlist: Watchlist) -> list[dict]:
    """Per watchlist asset: price + 24h change (CoinGecko DB), OI (Extended), and
    Stochastic + market-structure trend on 4H/1D/1W (Extended candles)."""
    from app.engine import technicals
    from app.fetchers import extended

    rows = []
    for a in watchlist.assets:
        market = f"{a.symbol}-USD"
        pr = _latest_price(session, a.coingecko_id)
        oi_usd = None
        try:
            stats, _url, _s = extended.fetch_market_stats(market)
            oi_usd = float(stats.get("openInterest") or 0) or None
        except Exception as exc:
            logger.warning("OI fetch failed for %s: %s", market, exc)
        tech = technicals.analyze_asset(market)
        rows.append({
            "symbol": a.symbol,
            "price": pr.price_usd if pr else None,
            "change_24h": pr.change_24h_pct if pr else None,
            "oi_usd": oi_usd,
            "stoch_k": tech.get("stoch_k"), "stoch_label": tech.get("stoch_label", "—"),
            "trend_4h": tech.get("trend_4h", "—"), "trend_1d": tech.get("trend_1d", "—"),
            "trend_1w": tech.get("trend_1w", "—"),
        })
    return rows


def build_fundamentals(session: Session, watchlist: Watchlist) -> list[dict]:
    """Valuation block for HYPE & LIT: market cap (CoinGecko DB) + TVL (DeFiLlama DB) +
    live fees/revenue/earnings -> P/F, P/S, P/E."""
    from app.engine import valuation

    out = []
    for a in watchlist.assets:
        slug = FUNDAMENTAL_ASSETS.get(a.symbol.upper())
        if not slug:
            continue
        pr = _latest_price(session, a.coingecko_id)
        mcap = pr.market_cap if pr else None
        tvl_row = session.execute(
            select(models.TvlSnapshot).where(models.TvlSnapshot.entity == slug)
            .order_by(desc(models.TvlSnapshot.fetched_at)).limit(1)).scalars().first()
        tvl = tvl_row.tvl_usd if tvl_row else None
        v = valuation.valuation(slug, mcap, tvl)
        v["symbol"] = a.symbol
        out.append(v)
    return out


def _log_alert(session: Session, kind: str, text: str, notifier: Notifier,
               level: str | None = None, entity: str | None = None,
               invalidation: str | None = None) -> models.AlertLog:
    delivered = notifier.send(text, kind=kind)
    row = models.AlertLog(kind=kind, level=level, entity=entity, message_text=text,
                          invalidation=invalidation, delivered=delivered,
                          channel=notifier.channel)
    session.add(row)
    return row


# ------------------------------------------------------------ daily outlook
def send_daily_outlook(session: Session, rules: ActionRules, watchlist: Watchlist,
                       positions: list[Position], settings: Settings,
                       notifier: Notifier) -> str:
    now = _now_wib()
    score, label, comps = compute_regime(session, rules, watchlist)
    snapshot = build_snapshot(session, watchlist)
    market_rows = build_market_rows(session, watchlist)
    fundamentals = build_fundamentals(session, watchlist)

    prev = session.execute(select(models.DailyOutlook)
                           .order_by(desc(models.DailyOutlook.outlook_date))
                           .limit(1)).scalars().first()
    prev_score = prev.regime_score if prev and prev.outlook_date != now.strftime("%Y-%m-%d") else \
        (prev.regime_score if prev else None)

    text = formatter.format_daily_outlook(now, (score, label), prev_score,
                                          snapshot, market_rows, fundamentals)
    payload = {"score": score, "label": label, "market_rows": market_rows,
               "fundamentals": fundamentals}
    llm_used = False

    date_str = now.strftime("%Y-%m-%d")
    existing = session.execute(select(models.DailyOutlook)
                               .where(models.DailyOutlook.outlook_date == date_str)
                               ).scalars().first()
    if existing:
        existing.regime_score, existing.regime_label = score, label
        existing.payload, existing.message_text, existing.llm_used = payload, text, llm_used
    else:
        session.add(models.DailyOutlook(outlook_date=date_str, regime_score=score,
                                        regime_label=label, payload=payload,
                                        message_text=text, llm_used=llm_used))
    _log_alert(session, "daily_outlook", text, notifier)
    return text


# ------------------------------------------------------------ real-time alert
def process_realtime_alerts(session: Session, rules: ActionRules, watchlist: Watchlist,
                            positions: list[Position], settings: Settings,
                            notifier: Notifier) -> list[str]:
    """Dipanggil setelah tiap siklus ingest. Kirim hanya level >= realtime_min_level,
    dedupe per entitas+level 24 jam, cap harian -> bundel digest darurat."""
    cfg = rules.notifications
    min_level = LEVEL_ORDER[cfg["realtime_min_level"]]
    cap = cfg["max_realtime_alerts_per_day"]
    now = _now_wib()

    groups = run_signal_engine(session, rules, watchlist, positions)
    candidates = [g for g in groups if LEVEL_ORDER[g.level] >= min_level]

    to_send: list[SignalGroup] = []
    for g in candidates:
        dup = session.execute(
            select(models.AlertLog.id).where(
                models.AlertLog.kind.in_(["realtime", "emergency_digest"]),
                models.AlertLog.entity == g.entity,
                models.AlertLog.level == g.level,
                models.AlertLog.sent_at >= _utcnow() - timedelta(hours=24))).first()
        if not dup:
            to_send.append(g)
    if not to_send:
        return []

    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0).astimezone(timezone.utc)
    sent_today = session.execute(
        select(func.count()).select_from(models.AlertLog)
        .where(models.AlertLog.kind == "realtime",
               models.AlertLog.sent_at >= today_start)).scalar() or 0

    messages: list[str] = []
    room = max(0, cap - sent_today)
    direct, overflow = to_send[:room], to_send[room:]

    for g in direct:
        payload = narrator.build_payload([g], compute_regime(session, rules, watchlist),
                                         {})
        narasi, _ = narrator.narrate(payload, rules, settings)
        arti = narasi["arti"].get(g.entity) or narrator.template_arti(g)
        text = formatter.format_realtime_alert(g, arti, now)
        _log_alert(session, "realtime", text, notifier, level=g.level,
                   entity=g.entity, invalidation=g.invalidation)
        messages.append(text)

    if overflow:
        text = formatter.format_emergency_digest(overflow, now)
        row = _log_alert(session, "emergency_digest", text, notifier)
        # catat entitas overflow agar tidak dikirim ulang
        row.event_ids = [g.entity for g in overflow]
        for g in overflow:
            session.add(models.AlertLog(kind="emergency_digest", level=g.level,
                                        entity=g.entity, message_text="(dibundel)",
                                        invalidation=g.invalidation, delivered=True,
                                        channel=notifier.channel))
        messages.append(text)
    return messages


# ------------------------------------------------------------ evening digest
def send_evening_digest(session: Session, rules: ActionRules, watchlist: Watchlist,
                        positions: list[Position], notifier: Notifier) -> str:
    now = _now_wib()
    groups = run_signal_engine(session, rules, watchlist, positions)
    min_level = LEVEL_ORDER[rules.notifications["realtime_min_level"]]
    low = [g for g in groups if 0 < LEVEL_ORDER[g.level] < min_level]  # 🟡 saja
    text = formatter.format_evening_digest(low, now)
    _log_alert(session, "digest", text, notifier)
    return text


# ------------------------------------------------------------ weekly review
def send_weekly_review(session: Session, rules: ActionRules,
                       notifier: Notifier) -> str:
    now = _now_wib()
    week_ago = _utcnow() - timedelta(days=7)
    rows = session.execute(
        select(models.AlertLog).where(models.AlertLog.kind == "realtime",
                                      models.AlertLog.sent_at >= week_ago)).scalars().all()
    by_level: dict[str, int] = {}
    confirmed = invalidated = pending = 0
    for r in rows:
        by_level[r.level or "?"] = by_level.get(r.level or "?", 0) + 1
        if r.followup_status == "valid":
            confirmed += 1
        elif r.followup_status == "batal":
            invalidated += 1
        else:
            pending += 1

    month_ago = _utcnow() - timedelta(days=28)
    rows_4w = session.execute(
        select(models.AlertLog).where(models.AlertLog.kind == "realtime",
                                      models.AlertLog.sent_at >= month_ago,
                                      models.AlertLog.followup_status.is_not(None))
    ).scalars().all()
    fp_rate = (sum(1 for r in rows_4w if r.followup_status == "batal") / len(rows_4w) * 100) \
        if rows_4w else None

    target = rules.meta["false_positive_target_pct"]
    note = None
    if fp_rate is not None and fp_rate > target:
        note = (f"FP {formatter.fmt_num(fp_rate, 0)}% > target {target}% — review threshold "
                f"modul dengan FP terbanyak (kandidat pertama: M4 volume Z-score, "
                f"lihat action_rules.yaml)")

    stats = {"total": len(rows), "by_level": by_level, "confirmed": confirmed,
             "invalidated": invalidated, "pending": pending,
             "fp_rate_4w": fp_rate, "fp_target": target, "calibration_note": note}
    text = formatter.format_weekly_review(stats, now)
    _log_alert(session, "weekly", text, notifier)
    return text


# ------------------------------------------------------------ follow-up H+1
def check_followups(session: Session, rules: ActionRules, notifier: Notifier) -> list[str]:
    """Untuk alert RISK_OFF/OPPORTUNITY berumur >= followup_check_hours tanpa status:
    sinyal dianggap masih valid jika entitasnya masih punya event aktif 24 jam terakhir
    (kondisi bertahan); jika tidak ada lagi -> batal."""
    hours = rules.notifications["followup_check_hours"]
    cutoff_old = _utcnow() - timedelta(hours=hours)
    rows = session.execute(
        select(models.AlertLog).where(
            models.AlertLog.kind == "realtime",
            models.AlertLog.level.in_(["RISK_OFF", "OPPORTUNITY"]),
            models.AlertLog.followup_status.is_(None),
            models.AlertLog.sent_at <= cutoff_old)).scalars().all()
    out = []
    now = _now_wib()
    for r in rows:
        recent = session.execute(
            select(models.Event.id).where(
                models.Event.entity == r.entity,
                models.Event.ts >= _utcnow() - timedelta(hours=24))).first()
        still_valid = bool(recent)
        r.followup_status = "valid" if still_valid else "batal"
        r.followup_at = _utcnow()
        text = formatter.format_followup(r.entity or "?", still_valid, now,
                                         r.invalidation or "-")
        _log_alert(session, "followup", text, notifier, entity=r.entity)
        out.append(text)
    return out
