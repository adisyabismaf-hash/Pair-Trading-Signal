"""FastAPI app — advisor read-only, TANPA private key, TANPA eksekusi trading.
Jalankan: uvicorn app.main:app --port 8000"""
from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone

from fastapi import FastAPI, HTTPException
from sqlalchemy import desc, select

from app import models, reporting
from app.config import (load_action_rules, load_positions, load_settings,
                        load_watchlist)
from app.db import init_db, session_scope
from app.notify.notifier import get_notifier
from app.scheduler import INGEST_JOBS, build_scheduler

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger("agent_signal")


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    settings = load_settings()
    logger.info("konfigurasi: %s", settings.masked())
    app.state.scheduler = build_scheduler()
    app.state.scheduler.start()
    logger.info("scheduler aktif: %s",
                [j.id for j in app.state.scheduler.get_jobs()])
    yield
    app.state.scheduler.shutdown(wait=False)


app = FastAPI(title="Agent Signal — Crypto Market Signal Advisor",
              description="Read-only advisor. Bukan nasihat keuangan.",
              version="1.0.0", lifespan=lifespan)


@app.get("/health")
def health():
    jobs = [{"id": j.id, "next_run": str(j.next_run_time)}
            for j in app.state.scheduler.get_jobs()] if hasattr(app.state, "scheduler") else []
    return {"status": "ok", "jobs": jobs}


@app.get("/events")
def events(hours: int = 24):
    with session_scope() as s:
        cutoff = datetime.now(timezone.utc) - timedelta(hours=hours)
        rows = s.execute(select(models.Event).where(models.Event.ts >= cutoff)
                         .order_by(desc(models.Event.ts))).scalars().all()
        return [{
            "module": r.module, "type": r.type, "entity": r.entity,
            "metric": r.metric, "old_value": r.old_value, "new_value": r.new_value,
            "delta_pct": r.delta_pct, "severity": r.severity,
            "final_level": r.final_level, "invalidation": r.invalidation,
            "source_url": r.source_url, "ts": str(r.ts),
        } for r in rows]


@app.get("/outlook/latest")
def outlook_latest():
    with session_scope() as s:
        row = s.execute(select(models.DailyOutlook)
                        .order_by(desc(models.DailyOutlook.outlook_date))
                        .limit(1)).scalars().first()
        if not row:
            raise HTTPException(404, "belum ada outlook — jalankan POST /run/daily_outlook")
        return {"date": row.outlook_date, "regime_score": row.regime_score,
                "regime_label": row.regime_label, "llm_used": row.llm_used,
                "message": row.message_text}


@app.get("/alerts")
def alerts(days: int = 7):
    with session_scope() as s:
        cutoff = datetime.now(timezone.utc) - timedelta(days=days)
        rows = s.execute(select(models.AlertLog).where(models.AlertLog.sent_at >= cutoff)
                         .order_by(desc(models.AlertLog.sent_at))).scalars().all()
        return [{"kind": r.kind, "level": r.level, "entity": r.entity,
                 "sent_at": str(r.sent_at), "delivered": r.delivered,
                 "channel": r.channel, "followup_status": r.followup_status}
                for r in rows]


@app.post("/run/ingest/{module}")
def run_ingest(module: str):
    """Trigger manual 1 job ingest (nama modul yaml, mis. M1_tvl_monitor)."""
    if module not in INGEST_JOBS:
        raise HTTPException(404, f"modul tidak dikenal, pilih: {list(INGEST_JOBS)}")
    INGEST_JOBS[module][0]()
    return {"ok": True, "module": module}


@app.post("/run/daily_outlook")
def run_daily_outlook():
    rules, wl, pos, settings = (load_action_rules(), load_watchlist(),
                                load_positions(), load_settings())
    with session_scope() as s:
        text = reporting.send_daily_outlook(s, rules, wl, pos, settings,
                                            get_notifier(settings))
    return {"ok": True, "message": text}


@app.post("/run/realtime_scan")
def run_realtime_scan():
    rules, wl, pos, settings = (load_action_rules(), load_watchlist(),
                                load_positions(), load_settings())
    with session_scope() as s:
        sent = reporting.process_realtime_alerts(s, rules, wl, pos, settings,
                                                 get_notifier(settings))
    return {"ok": True, "alerts_sent": len(sent), "messages": sent}


@app.post("/run/evening_digest")
def run_evening_digest():
    rules, wl, pos, settings = (load_action_rules(), load_watchlist(),
                                load_positions(), load_settings())
    with session_scope() as s:
        text = reporting.send_evening_digest(s, rules, wl, pos, get_notifier(settings))
    return {"ok": True, "message": text}


@app.post("/run/weekly_review")
def run_weekly_review():
    rules, settings = load_action_rules(), load_settings()
    with session_scope() as s:
        text = reporting.send_weekly_review(s, rules, get_notifier(settings))
    return {"ok": True, "message": text}
