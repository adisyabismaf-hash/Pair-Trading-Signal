"""Skema database — time-series snapshot per sumber + events + output agent.
Setiap snapshot menyimpan source_url + fetched_at untuk data provenance."""
from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import (JSON, Boolean, DateTime, Float, Index, Integer,
                        SmallInteger, String, Text, UniqueConstraint)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    pass


class RawResponse(Base):
    """Audit trail: raw response API — kalau sinyal salah, cek datanya dulu."""
    __tablename__ = "raw_responses"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    source: Mapped[str] = mapped_column(String(32))          # coingecko|defillama|etherscan|binance|alternativeme
    endpoint: Mapped[str] = mapped_column(Text)
    status_code: Mapped[int] = mapped_column(Integer)
    payload: Mapped[dict] = mapped_column(JSON)
    fetched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class PriceSnapshot(Base):
    __tablename__ = "price_snapshots"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    coingecko_id: Mapped[str] = mapped_column(String(64), index=True)
    symbol: Mapped[str] = mapped_column(String(16))
    price_usd: Mapped[float] = mapped_column(Float)
    market_cap: Mapped[float | None] = mapped_column(Float, nullable=True)
    volume_24h: Mapped[float | None] = mapped_column(Float, nullable=True)
    change_24h_pct: Mapped[float | None] = mapped_column(Float, nullable=True)
    change_7d_pct: Mapped[float | None] = mapped_column(Float, nullable=True)
    source_url: Mapped[str] = mapped_column(Text, default="")
    fetched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)


class TvlSnapshot(Base):
    __tablename__ = "tvl_snapshots"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    entity: Mapped[str] = mapped_column(String(64), index=True)   # llama_slug atau nama chain
    entity_type: Mapped[str] = mapped_column(String(16), default="protocol")  # protocol|chain
    tvl_usd: Mapped[float] = mapped_column(Float)
    source_url: Mapped[str] = mapped_column(Text, default="")
    fetched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)


class FeesSnapshot(Base):
    __tablename__ = "fees_snapshots"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    entity: Mapped[str] = mapped_column(String(64), index=True)
    fees_24h_usd: Mapped[float | None] = mapped_column(Float, nullable=True)
    revenue_24h_usd: Mapped[float | None] = mapped_column(Float, nullable=True)
    source_url: Mapped[str] = mapped_column(Text, default="")
    fetched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)


class WalletTx(Base):
    __tablename__ = "wallet_txs"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    wallet_label: Mapped[str] = mapped_column(String(64), index=True)
    wallet_address: Mapped[str] = mapped_column(String(64), index=True)
    tx_hash: Mapped[str] = mapped_column(String(80))
    direction: Mapped[str] = mapped_column(String(8))            # in|out
    counterparty: Mapped[str] = mapped_column(String(64), default="")
    token_symbol: Mapped[str] = mapped_column(String(32), default="ETH")
    amount: Mapped[float] = mapped_column(Float)
    amount_usd: Mapped[float | None] = mapped_column(Float, nullable=True)
    block_time: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    source_url: Mapped[str] = mapped_column(Text, default="")
    fetched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    __table_args__ = (UniqueConstraint("tx_hash", "wallet_address", "token_symbol", "direction",
                                       name="uq_wallet_tx"),)


class StablecoinSupply(Base):
    __tablename__ = "stablecoin_supply"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    total_supply_usd: Mapped[float] = mapped_column(Float)
    detail: Mapped[dict | None] = mapped_column(JSON, nullable=True)  # breakdown per stablecoin
    source_url: Mapped[str] = mapped_column(Text, default="")
    fetched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)


class DerivativesSnapshot(Base):
    __tablename__ = "derivatives_snapshots"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    symbol: Mapped[str] = mapped_column(String(24), index=True)   # mis. BTCUSDT
    funding_rate_8h_pct: Mapped[float | None] = mapped_column(Float, nullable=True)
    open_interest: Mapped[float | None] = mapped_column(Float, nullable=True)
    open_interest_usd: Mapped[float | None] = mapped_column(Float, nullable=True)
    source_url: Mapped[str] = mapped_column(Text, default="")
    fetched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)


class SentimentFng(Base):
    """Sesuai schema_fng.sql — Fear & Greed alternative.me, 1x/hari."""
    __tablename__ = "sentiment_fng"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    value: Mapped[int] = mapped_column(SmallInteger)
    value_classification: Mapped[str] = mapped_column(Text)
    fng_timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True), unique=True)
    fetched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    __table_args__ = (Index("idx_fng_timestamp_desc", fng_timestamp.desc()),)


class Event(Base):
    """Output signal engine (L3) — satu baris per trigger modul."""
    __tablename__ = "events"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    module: Mapped[str] = mapped_column(String(32), index=True)       # M1..M7
    type: Mapped[str] = mapped_column(String(64))
    entity: Mapped[str] = mapped_column(String(64), index=True)
    metric: Mapped[str] = mapped_column(String(64))
    old_value: Mapped[float | None] = mapped_column(Float, nullable=True)
    new_value: Mapped[float | None] = mapped_column(Float, nullable=True)
    delta_pct: Mapped[float | None] = mapped_column(Float, nullable=True)
    severity: Mapped[str] = mapped_column(String(24))                 # INFO|MONITOR|REVIEW_CANDIDATE
    direction: Mapped[str] = mapped_column(String(8), default="neutral")  # bearish|bullish|neutral
    invalidation: Mapped[str] = mapped_column(Text)                   # E5: wajib terisi
    source_url: Mapped[str] = mapped_column(Text, default="")
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)
    final_level: Mapped[str | None] = mapped_column(String(24), nullable=True)  # hasil eskalasi
    signal_group: Mapped[str | None] = mapped_column(String(64), nullable=True)


class DailyOutlook(Base):
    __tablename__ = "daily_outlooks"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    outlook_date: Mapped[str] = mapped_column(String(10), unique=True)   # YYYY-MM-DD (WIB)
    regime_score: Mapped[int] = mapped_column(Integer)
    regime_label: Mapped[str] = mapped_column(String(24))
    payload: Mapped[dict] = mapped_column(JSON)         # data mentah yang dipakai (audit LLM)
    message_text: Mapped[str] = mapped_column(Text)
    llm_used: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class AlertLog(Base):
    """Semua pesan keluar — bahan weekly review & cap 5 alert real-time/hari."""
    __tablename__ = "alert_log"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    kind: Mapped[str] = mapped_column(String(24), index=True)  # realtime|daily_outlook|digest|weekly|followup
    level: Mapped[str | None] = mapped_column(String(24), nullable=True)
    entity: Mapped[str | None] = mapped_column(String(64), nullable=True)
    message_text: Mapped[str] = mapped_column(Text)
    event_ids: Mapped[list | None] = mapped_column(JSON, nullable=True)
    invalidation: Mapped[str | None] = mapped_column(Text, nullable=True)
    sent_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)
    delivered: Mapped[bool] = mapped_column(Boolean, default=False)
    channel: Mapped[str] = mapped_column(String(24), default="console")
    followup_status: Mapped[str | None] = mapped_column(String(16), nullable=True)  # valid|batal|None
    followup_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
