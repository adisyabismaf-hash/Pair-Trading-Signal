import enum
from datetime import datetime, timezone

from sqlalchemy import (
    Boolean, DateTime, Enum, Float, Integer, JSON, String, Text, UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from core.database import Base


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Strategy(str, enum.Enum):
    PAIR_TRADING = "PAIR_TRADING"
    MA50_RETEST = "MA50_RETEST"


class Direction(str, enum.Enum):
    LONG = "LONG"
    SHORT = "SHORT"
    LONG_SPREAD = "LONG_SPREAD"    # long base leg, short quote leg
    SHORT_SPREAD = "SHORT_SPREAD"  # short base leg, long quote leg


class SignalStatus(str, enum.Enum):
    OPEN = "OPEN"
    CLOSED_TP = "CLOSED_TP"
    CLOSED_SL = "CLOSED_SL"
    CLOSED_EXIT = "CLOSED_EXIT"      # pair trading: z-score reverted to mean
    CLOSED_MANUAL = "CLOSED_MANUAL"
    EXPIRED = "EXPIRED"              # pair trading: max holding period reached


class WatchedPair(Base):
    """A pair-trading combination, e.g. BTC-USD vs ETH-USD."""
    __tablename__ = "watched_pairs"
    __table_args__ = (UniqueConstraint("base_market", "quote_market", name="uq_pair_markets"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    base_market: Mapped[str] = mapped_column(String(32), nullable=False)
    quote_market: Mapped[str] = mapped_column(String(32), nullable=False)
    lookback: Mapped[int] = mapped_column(Integer, default=60)
    entry_zscore: Mapped[float] = mapped_column(Float, default=2.0)
    exit_zscore: Mapped[float] = mapped_column(Float, default=0.5)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    # last computed analytics (refreshed on every scan)
    last_zscore: Mapped[float | None] = mapped_column(Float, nullable=True)
    last_hedge_ratio: Mapped[float | None] = mapped_column(Float, nullable=True)
    last_correlation: Mapped[float | None] = mapped_column(Float, nullable=True)
    last_checked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class WatchedSymbol(Base):
    """A single market watched for the traditional MA50-retest strategy."""
    __tablename__ = "watched_symbols"
    __table_args__ = (UniqueConstraint("market", name="uq_symbol_market"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    market: Mapped[str] = mapped_column(String(32), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    # last computed analytics
    last_price: Mapped[float | None] = mapped_column(Float, nullable=True)
    last_ma50: Mapped[float | None] = mapped_column(Float, nullable=True)
    last_trend: Mapped[str | None] = mapped_column(String(16), nullable=True)
    last_retest_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    last_checked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class Signal(Base):
    __tablename__ = "signals"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    strategy: Mapped[Strategy] = mapped_column(Enum(Strategy), nullable=False)
    direction: Mapped[Direction] = mapped_column(Enum(Direction), nullable=False)
    status: Mapped[SignalStatus] = mapped_column(Enum(SignalStatus), default=SignalStatus.OPEN, index=True)

    # MA50_RETEST: the traded market. PAIR_TRADING: "BASE vs QUOTE" label.
    market: Mapped[str] = mapped_column(String(80), nullable=False)
    base_market: Mapped[str | None] = mapped_column(String(32), nullable=True)
    quote_market: Mapped[str | None] = mapped_column(String(32), nullable=True)

    entry_price: Mapped[float | None] = mapped_column(Float, nullable=True)   # MA50: entry. Pair: spread at entry.
    stop_loss: Mapped[float | None] = mapped_column(Float, nullable=True)
    take_profit: Mapped[float | None] = mapped_column(Float, nullable=True)
    exit_price: Mapped[float | None] = mapped_column(Float, nullable=True)

    entry_zscore: Mapped[float | None] = mapped_column(Float, nullable=True)
    exit_zscore: Mapped[float | None] = mapped_column(Float, nullable=True)
    hedge_ratio: Mapped[float | None] = mapped_column(Float, nullable=True)

    pnl_pct: Mapped[float | None] = mapped_column(Float, nullable=True)
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    details: Mapped[dict | None] = mapped_column(JSON, nullable=True)

    telegram_sent: Mapped[bool] = mapped_column(Boolean, default=False)
    opened_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class ScanLog(Base):
    __tablename__ = "scan_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    run_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)
    ok: Mapped[bool] = mapped_column(Boolean, default=True)
    pairs_scanned: Mapped[int] = mapped_column(Integer, default=0)
    symbols_scanned: Mapped[int] = mapped_column(Integer, default=0)
    signals_created: Mapped[int] = mapped_column(Integer, default=0)
    signals_closed: Mapped[int] = mapped_column(Integer, default=0)
    message: Mapped[str | None] = mapped_column(Text, nullable=True)

