"""Performance analytics computed directly from the signals table."""
from sqlalchemy import select
from sqlalchemy.orm import Session

from core.models import Signal, SignalStatus, Strategy

CLOSED_STATUSES = {
    SignalStatus.CLOSED_TP, SignalStatus.CLOSED_SL,
    SignalStatus.CLOSED_EXIT, SignalStatus.CLOSED_MANUAL, SignalStatus.EXPIRED,
}


def _stats(label: str, signals: list[Signal]) -> dict:
    closed = [s for s in signals if s.status in CLOSED_STATUSES and s.pnl_pct is not None]
    open_count = sum(1 for s in signals if s.status == SignalStatus.OPEN)
    wins = [s for s in closed if s.pnl_pct > 0]
    losses = [s for s in closed if s.pnl_pct <= 0]
    pnls = [s.pnl_pct for s in closed]
    return {
        "strategy": label,
        "total": len(signals),
        "open": open_count,
        "closed": len(closed),
        "wins": len(wins),
        "losses": len(losses),
        "winrate_pct": round(len(wins) / len(closed) * 100, 2) if closed else None,
        "total_pnl_pct": round(sum(pnls), 4) if pnls else 0.0,
        "avg_pnl_pct": round(sum(pnls) / len(pnls), 4) if pnls else None,
        "best_pnl_pct": round(max(pnls), 4) if pnls else None,
        "worst_pnl_pct": round(min(pnls), 4) if pnls else None,
    }


def performance(db: Session) -> dict:
    signals = db.execute(select(Signal).order_by(Signal.opened_at)).scalars().all()
    per_strategy = [
        _stats("PAIR_TRADING", [s for s in signals if s.strategy == Strategy.PAIR_TRADING]),
        _stats("MA50_RETEST", [s for s in signals if s.strategy == Strategy.MA50_RETEST]),
    ]
    closed_sorted = sorted(
        (s for s in signals if s.status in CLOSED_STATUSES and s.pnl_pct is not None and s.closed_at),
        key=lambda s: s.closed_at,
    )
    curve = []
    cum = 0.0
    for s in closed_sorted:
        cum += s.pnl_pct
        curve.append({"t": s.closed_at, "cum_pnl_pct": round(cum, 4)})
    return {
        "overall": _stats("OVERALL", signals),
        "per_strategy": per_strategy,
        "equity_curve": curve,
    }
