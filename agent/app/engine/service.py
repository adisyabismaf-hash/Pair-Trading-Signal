"""Orkestrasi L3: baca snapshot dari DB -> panggil modul murni -> simpan event
-> jalankan eskalasi. Dedupe: event dengan module+type+entity sama dalam 24 jam
terakhir tidak dibuat ulang (mencegah spam saat kondisi bertahan)."""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import desc, select
from sqlalchemy.orm import Session

from app import models
from app.config import ActionRules, Position, Watchlist, position_entities
from app.engine import modules as mod
from app.engine.escalation import escalate
from app.engine.events import SignalEvent, SignalGroup
from app.engine.regime import RegimeInputs, regime_score

logger = logging.getLogger(__name__)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _as_utc(dt: datetime | None) -> datetime | None:
    """SQLite mengembalikan datetime naive — anggap UTC (semua kolom disimpan UTC)."""
    if dt is not None and dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt


def _tvl_at(session: Session, entity: str, hours_ago: float,
            tolerance_hours: float = 6.0) -> float | None:
    """Snapshot TVL terdekat dari (sekarang - hours_ago), dalam toleransi."""
    target = _utcnow() - timedelta(hours=hours_ago)
    row = session.execute(
        select(models.TvlSnapshot)
        .where(models.TvlSnapshot.entity == entity,
               models.TvlSnapshot.fetched_at <= target + timedelta(hours=tolerance_hours),
               models.TvlSnapshot.fetched_at >= target - timedelta(hours=tolerance_hours * 4))
        .order_by(desc(models.TvlSnapshot.fetched_at)).limit(1)).scalars().first()
    return row.tvl_usd if row else None


def _latest_tvl(session: Session, entity: str) -> tuple[float, str] | None:
    row = session.execute(
        select(models.TvlSnapshot).where(models.TvlSnapshot.entity == entity)
        .order_by(desc(models.TvlSnapshot.fetched_at)).limit(1)).scalars().first()
    return (row.tvl_usd, row.source_url) if row else None


def run_m1(session: Session, rules: ActionRules, watchlist: Watchlist) -> list[SignalEvent]:
    cfg = rules.module("M1_tvl_monitor")
    events: list[SignalEvent] = []
    for p in watchlist.protocols:
        if "tvl" not in p.track:
            continue
        latest = _latest_tvl(session, p.llama_slug)
        if not latest:
            continue
        tvl_now, url = latest
        events += mod.m1_tvl(p.llama_slug, tvl_now,
                             _tvl_at(session, p.llama_slug, 24),
                             _tvl_at(session, p.llama_slug, 24 * 7),
                             cfg, source_url=url)
    return events


def run_m2(session: Session, rules: ActionRules, watchlist: Watchlist) -> list[SignalEvent]:
    cfg = rules.module("M2_revenue_fees")
    events: list[SignalEvent] = []
    for p in watchlist.protocols:
        if "fees" not in p.track:
            continue
        rows = session.execute(
            select(models.FeesSnapshot).where(models.FeesSnapshot.entity == p.llama_slug)
            .order_by(desc(models.FeesSnapshot.fetched_at)).limit(200)).scalars().all()
        if not rows:
            continue
        # 1 nilai per hari (snapshot terbaru hari itu); rows sudah desc
        daily: dict[str, float] = {}
        for r in rows:
            day = r.fetched_at.strftime("%Y-%m-%d")
            if day not in daily and r.fees_24h_usd is not None:
                daily[day] = r.fees_24h_usd
        days = sorted(daily.keys(), reverse=True)
        if len(days) < 4:  # butuh riwayat minimal agar baseline bermakna
            continue
        today, history = daily[days[0]], [daily[d] for d in days[1:8]]
        events += mod.m2_fees(p.llama_slug, today, history, cfg, source_url=rows[0].source_url)
    return events


def run_m3(session: Session, rules: ActionRules, watchlist: Watchlist,
           lookback_minutes: int | None = None) -> list[SignalEvent]:
    cfg = rules.module("M3_wallet_tracker")
    lookback = lookback_minutes or cfg["interval_minutes"]
    since = _utcnow() - timedelta(minutes=lookback * 2)  # overlap 1 interval agar tak bolong
    ex_addrs = watchlist.exchange_addresses()
    events: list[SignalEvent] = []
    for w in watchlist.wallets:
        txs = session.execute(
            select(models.WalletTx)
            .where(models.WalletTx.wallet_address == w.address.lower(),
                   models.WalletTx.block_time >= since)
            .order_by(models.WalletTx.block_time)).scalars().all()
        for tx in txs:
            prev = session.execute(
                select(models.WalletTx.block_time)
                .where(models.WalletTx.wallet_address == w.address.lower(),
                       models.WalletTx.block_time < tx.block_time)
                .order_by(desc(models.WalletTx.block_time)).limit(1)).scalar()
            events += mod.m3_wallet(
                {"wallet_label": tx.wallet_label, "wallet_address": tx.wallet_address,
                 "tx_hash": tx.tx_hash, "direction": tx.direction,
                 "counterparty": tx.counterparty, "token_symbol": tx.token_symbol,
                 "amount": tx.amount, "amount_usd": tx.amount_usd,
                 "block_time": _as_utc(tx.block_time), "source_url": tx.source_url},
                ex_addrs, w.category, cfg,
                threshold_usd_override=w.threshold_usd_override,
                last_activity_before=_as_utc(prev))
    return events


def run_m4(session: Session, rules: ActionRules, watchlist: Watchlist) -> list[SignalEvent]:
    cfg = rules.module("M4_price_volume_anomaly")
    events: list[SignalEvent] = []
    for a in watchlist.assets:
        rows = session.execute(
            select(models.PriceSnapshot).where(models.PriceSnapshot.coingecko_id == a.coingecko_id)
            .order_by(desc(models.PriceSnapshot.fetched_at)).limit(3000)).scalars().all()
        if not rows:
            continue
        latest = rows[0]
        # volume harian 30 hari (tanpa hari ini)
        daily: dict[str, float] = {}
        for r in rows:
            day = r.fetched_at.strftime("%Y-%m-%d")
            if day not in daily and r.volume_24h is not None:
                daily[day] = r.volume_24h
        days = sorted(daily.keys(), reverse=True)
        volumes_30d = [daily[d] for d in days[1:31]]
        # pergerakan 4 jam dari snapshot terdekat 4 jam lalu
        target = _utcnow() - timedelta(hours=4)
        past = next((r for r in rows if _as_utc(r.fetched_at) <= target), None)
        move_4h = None
        if past and past.price_usd:
            move_4h = (latest.price_usd - past.price_usd) / past.price_usd * 100
        events += mod.m4_price_volume(a.symbol, latest.volume_24h, volumes_30d,
                                      move_4h, cfg, source_url=latest.source_url)
    return events


def run_m5(session: Session, rules: ActionRules) -> list[SignalEvent]:
    cfg = rules.module("M5_stablecoin_flow")
    latest = session.execute(select(models.StablecoinSupply)
                             .order_by(desc(models.StablecoinSupply.fetched_at))
                             .limit(1)).scalars().first()
    if not latest:
        return []
    target = _utcnow() - timedelta(days=7)
    past = session.execute(
        select(models.StablecoinSupply)
        .where(models.StablecoinSupply.fetched_at <= target + timedelta(hours=18))
        .order_by(desc(models.StablecoinSupply.fetched_at)).limit(1)).scalars().first()
    return mod.m5_stablecoin(latest.total_supply_usd,
                             past.total_supply_usd if past else None,
                             cfg, source_url=latest.source_url)


def run_m6(session: Session, rules: ActionRules) -> list[SignalEvent]:
    cfg = rules.module("M6_derivatives")
    events: list[SignalEvent] = []
    symbols = session.execute(select(models.DerivativesSnapshot.symbol).distinct()).scalars().all()
    for sym in symbols:
        latest = session.execute(
            select(models.DerivativesSnapshot).where(models.DerivativesSnapshot.symbol == sym)
            .order_by(desc(models.DerivativesSnapshot.fetched_at)).limit(1)).scalars().first()
        target = _utcnow() - timedelta(hours=24)
        past = session.execute(
            select(models.DerivativesSnapshot)
            .where(models.DerivativesSnapshot.symbol == sym,
                   models.DerivativesSnapshot.fetched_at <= target + timedelta(hours=6))
            .order_by(desc(models.DerivativesSnapshot.fetched_at)).limit(1)).scalars().first()
        oi_now = latest.open_interest_usd or latest.open_interest
        oi_past = (past.open_interest_usd or past.open_interest) if past else None
        events += mod.m6_derivatives(sym, latest.funding_rate_8h_pct, oi_now, oi_past,
                                     cfg, source_url=latest.source_url)
    return events


def run_m7(session: Session, rules: ActionRules) -> list[SignalEvent]:
    cfg = rules.module("M7_sentiment")
    latest = session.execute(select(models.SentimentFng)
                             .order_by(desc(models.SentimentFng.fng_timestamp))
                             .limit(1)).scalars().first()
    if not latest:
        return []
    return mod.m7_sentiment(latest.value, cfg,
                            source_url="https://api.alternative.me/fng/")


MODULE_RUNNERS = {
    "M1": lambda s, r, w: run_m1(s, r, w),
    "M2": lambda s, r, w: run_m2(s, r, w),
    "M3": lambda s, r, w: run_m3(s, r, w),
    "M4": lambda s, r, w: run_m4(s, r, w),
    "M5": lambda s, r, w: run_m5(s, r),
    "M6": lambda s, r, w: run_m6(s, r),
    "M7": lambda s, r, w: run_m7(s, r),
}


def _dedupe_and_store(session: Session, events: list[SignalEvent]) -> list[SignalEvent]:
    """Simpan event baru; skip jika module+type+entity sama sudah ada dalam 24 jam."""
    fresh: list[SignalEvent] = []
    cutoff = _utcnow() - timedelta(hours=24)
    for e in events:
        dup = session.execute(
            select(models.Event.id).where(
                models.Event.module == e.module, models.Event.type == e.type,
                models.Event.entity == e.entity,
                models.Event.ts >= cutoff)).first()
        if dup:
            continue
        session.add(models.Event(**e.as_dict() | {"ts": e.ts}))
        fresh.append(e)
    return fresh


def run_signal_engine(session: Session, rules: ActionRules, watchlist: Watchlist,
                      positions: list[Position],
                      only_modules: list[str] | None = None) -> list[SignalGroup]:
    """Jalankan modul -> simpan event baru -> eskalasi atas SEMUA event 24 jam
    (baru + lama) supaya E2 lintas-waktu tetap terdeteksi."""
    events: list[SignalEvent] = []
    for mid, runner in MODULE_RUNNERS.items():
        if only_modules and mid not in only_modules:
            continue
        try:
            events += runner(session, rules, watchlist)
        except Exception:
            logger.exception("modul %s gagal", mid)

    fresh = _dedupe_and_store(session, events)
    logger.info("signal engine: %d event baru dari %d kandidat", len(fresh), len(events))

    cutoff = _utcnow() - timedelta(hours=24)
    stored = session.execute(select(models.Event).where(models.Event.ts >= cutoff)).scalars().all()
    window_events = [SignalEvent(
        module=r.module, type=r.type, entity=r.entity, metric=r.metric,
        old_value=r.old_value, new_value=r.new_value, delta_pct=r.delta_pct,
        severity=r.severity, direction=r.direction, invalidation=r.invalidation,
        source_url=r.source_url, ts=_as_utc(r.ts)) for r in stored]

    groups = escalate(window_events, rules.modules, position_entities(positions))

    # tulis balik final_level ke tabel events
    for g in groups:
        for e in g.events:
            session.execute(
                models.Event.__table__.update()
                .where(models.Event.module == e.module, models.Event.type == e.type,
                       models.Event.entity == e.entity, models.Event.ts >= cutoff)
                .values(final_level=g.level, signal_group=g.entity))
    return groups


def build_regime_inputs(session: Session, watchlist: Watchlist) -> RegimeInputs:
    fng = session.execute(select(models.SentimentFng)
                          .order_by(desc(models.SentimentFng.fng_timestamp))
                          .limit(1)).scalars().first()

    stab_now = session.execute(select(models.StablecoinSupply)
                               .order_by(desc(models.StablecoinSupply.fetched_at))
                               .limit(1)).scalars().first()
    stab_delta = None
    if stab_now:
        target = _utcnow() - timedelta(days=7)
        stab_past = session.execute(
            select(models.StablecoinSupply)
            .where(models.StablecoinSupply.fetched_at <= target + timedelta(hours=18))
            .order_by(desc(models.StablecoinSupply.fetched_at)).limit(1)).scalars().first()
        if stab_past and stab_past.total_supply_usd:
            stab_delta = (stab_now.total_supply_usd - stab_past.total_supply_usd) \
                / stab_past.total_supply_usd * 100

    # breadth: % aset watchlist dengan change_7d_pct > 0 (snapshot terbaru per aset)
    greens, total = 0, 0
    for a in watchlist.assets:
        row = session.execute(
            select(models.PriceSnapshot).where(models.PriceSnapshot.coingecko_id == a.coingecko_id)
            .order_by(desc(models.PriceSnapshot.fetched_at)).limit(1)).scalars().first()
        if row and row.change_7d_pct is not None:
            total += 1
            if row.change_7d_pct > 0:
                greens += 1
    breadth = (greens / total * 100) if total else None

    btc = session.execute(
        select(models.DerivativesSnapshot).where(models.DerivativesSnapshot.symbol == "BTCUSDT")
        .order_by(desc(models.DerivativesSnapshot.fetched_at)).limit(1)).scalars().first()

    agg_now = _latest_tvl(session, "_aggregate_chains")
    tvl_delta = None
    if agg_now:
        agg_past = _tvl_at(session, "_aggregate_chains", 24 * 7, tolerance_hours=18)
        if agg_past:
            tvl_delta = (agg_now[0] - agg_past) / agg_past * 100

    return RegimeInputs(
        fear_greed=fng.value if fng else None,
        stablecoin_delta_7d_pct=stab_delta,
        breadth_green_pct=breadth,
        funding_btc_8h_pct=btc.funding_rate_8h_pct if btc else None,
        tvl_aggregate_delta_7d_pct=tvl_delta)


def compute_regime(session: Session, rules: ActionRules,
                   watchlist: Watchlist) -> tuple[int, str, dict]:
    inp = build_regime_inputs(session, watchlist)
    return regime_score(inp, rules.regime, rules.modules)
