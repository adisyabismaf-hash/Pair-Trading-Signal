"""L1/L5 scheduling — APScheduler, timezone Asia/Jakarta.
Interval ingest per modul dari action_rules.yaml (interval_minutes);
jadwal notifikasi dari bagian notifications. Tidak ada angka jadwal hardcode."""
from __future__ import annotations

import logging

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger

from app import ingest, reporting
from app.config import (ActionRules, Settings, load_action_rules,
                        load_positions, load_settings, load_watchlist)
from app.db import session_scope
from app.notify.notifier import get_notifier

logger = logging.getLogger(__name__)


def _ctx():
    """Config dibaca ulang tiap job — edit yaml/positions berlaku tanpa restart."""
    return load_action_rules(), load_watchlist(), load_positions(), load_settings()


# ------------------------------------------------------------- job functions
def job_ingest_prices() -> None:
    rules, wl, _, settings = _ctx()
    with session_scope() as s:
        ingest.ingest_prices(s, wl, settings)


def job_ingest_tvl() -> None:
    _, wl, _, _ = _ctx()
    with session_scope() as s:
        ingest.ingest_tvl(s, wl)


def job_ingest_fees() -> None:
    _, wl, _, _ = _ctx()
    with session_scope() as s:
        ingest.ingest_fees(s, wl)


def job_ingest_wallets() -> None:
    _, wl, _, settings = _ctx()
    with session_scope() as s:
        ingest.ingest_wallet_txs(s, wl, settings)


def job_ingest_stablecoins() -> None:
    with session_scope() as s:
        ingest.ingest_stablecoins(s)


def job_ingest_derivatives() -> None:
    _, _, _, settings = _ctx()
    with session_scope() as s:
        ingest.ingest_derivatives(s, settings=settings)


def job_ingest_fng() -> None:
    with session_scope() as s:
        ingest.ingest_fng(s)


def job_realtime_scan() -> None:
    """Setelah data segar masuk: jalankan engine + kirim alert real-time bila perlu."""
    rules, wl, pos, settings = _ctx()
    notifier = get_notifier(settings)
    with session_scope() as s:
        sent = reporting.process_realtime_alerts(s, rules, wl, pos, settings, notifier)
        if sent:
            logger.info("realtime: %d pesan terkirim", len(sent))


def job_daily_outlook() -> None:
    rules, wl, pos, settings = _ctx()
    notifier = get_notifier(settings)
    with session_scope() as s:
        reporting.send_daily_outlook(s, rules, wl, pos, settings, notifier)


def job_evening_digest() -> None:
    rules, wl, pos, settings = _ctx()
    notifier = get_notifier(settings)
    with session_scope() as s:
        reporting.send_evening_digest(s, rules, wl, pos, notifier)


def job_weekly_review() -> None:
    rules, _, _, settings = _ctx()
    notifier = get_notifier(settings)
    with session_scope() as s:
        reporting.send_weekly_review(s, rules, notifier)


def job_followup_check() -> None:
    rules, _, _, settings = _ctx()
    notifier = get_notifier(settings)
    with session_scope() as s:
        reporting.check_followups(s, rules, notifier)


# ------------------------------------------------------------- wiring
INGEST_JOBS = {
    # modul yaml -> (job, nama)
    "M1_tvl_monitor": (job_ingest_tvl, "ingest_tvl"),
    "M2_revenue_fees": (job_ingest_fees, "ingest_fees"),
    "M3_wallet_tracker": (job_ingest_wallets, "ingest_wallets"),
    "M4_price_volume_anomaly": (job_ingest_prices, "ingest_prices"),
    "M5_stablecoin_flow": (job_ingest_stablecoins, "ingest_stablecoins"),
    "M6_derivatives": (job_ingest_derivatives, "ingest_derivatives"),
    "M7_sentiment": (job_ingest_fng, "ingest_fng"),
}


def build_scheduler(rules: ActionRules | None = None,
                    settings: Settings | None = None) -> BackgroundScheduler:
    rules = rules or load_action_rules()
    settings = settings or load_settings()
    tz = settings.timezone
    sched = BackgroundScheduler(timezone=tz)

    for module_name, (fn, name) in INGEST_JOBS.items():
        minutes = rules.module(module_name)["interval_minutes"]
        sched.add_job(fn, IntervalTrigger(minutes=minutes, timezone=tz),
                      id=name, name=name, max_instances=1, coalesce=True)

    # scan real-time mengikuti irama modul tercepat (M3/M4) — engine dedupe sendiri
    fastest = min(rules.module(m)["interval_minutes"] for m in INGEST_JOBS)
    sched.add_job(job_realtime_scan, IntervalTrigger(minutes=fastest, timezone=tz),
                  id="realtime_scan", max_instances=1, coalesce=True)

    notif = rules.notifications
    h, m = notif["daily_outlook_time"].split(":")
    sched.add_job(job_daily_outlook, CronTrigger(hour=int(h), minute=int(m), timezone=tz),
                  id="daily_outlook")
    h, m = notif["evening_digest_time"].split(":")
    sched.add_job(job_evening_digest, CronTrigger(hour=int(h), minute=int(m), timezone=tz),
                  id="evening_digest")
    day, hm = notif["weekly_review"].split()
    h, m = hm.split(":")
    sched.add_job(job_weekly_review, CronTrigger(day_of_week=day.lower()[:3],
                                                 hour=int(h), minute=int(m), timezone=tz),
                  id="weekly_review")
    sched.add_job(job_followup_check, IntervalTrigger(hours=1, timezone=tz),
                  id="followup_check", max_instances=1, coalesce=True)
    return sched
