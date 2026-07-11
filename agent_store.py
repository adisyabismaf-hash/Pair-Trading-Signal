"""Watchlist Agent Signal berbasis database (shared Postgres).

Kenapa DB, bukan watchlist.yaml: di cloud (Streamlit Cloud + GitHub Actions)
filesystem tidak permanen dan tidak sinkron antar-proses. Aset & protokol
disimpan di tabel `agent_watch_*`; yaml hanya seed awal + fallback untuk
wallets/exchange_labels yang belum dikelola dari dashboard.

Pemakaian: panggil `effective_watchlist()` sebagai pengganti `load_watchlist()`.
"""
from __future__ import annotations

import sys
from pathlib import Path

from sqlalchemy import Boolean, Integer, String, select
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

ROOT = Path(__file__).resolve().parent
if str(ROOT / "agent") not in sys.path:
    sys.path.insert(0, str(ROOT / "agent"))

from app.config import (WatchAsset, Watchlist, WatchProtocol,  # noqa: E402
                        load_watchlist)
from app.db import get_engine, session_scope  # noqa: E402

MAX_ASSETS = 30      # kuota CoinGecko — sama dengan batas watchlist.yaml
MAX_PROTOCOLS = 20


class StoreBase(DeclarativeBase):
    pass


class AgentWatchAsset(StoreBase):
    __tablename__ = "agent_watch_assets"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    symbol: Mapped[str] = mapped_column(String(16), unique=True)
    coingecko_id: Mapped[str] = mapped_column(String(64))
    tier: Mapped[str] = mapped_column(String(16), default="satellite")  # core|satellite
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)


class AgentWatchProtocol(StoreBase):
    __tablename__ = "agent_watch_protocols"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(64), unique=True)
    llama_slug: Mapped[str] = mapped_column(String(64))
    track_tvl: Mapped[bool] = mapped_column(Boolean, default=True)
    track_fees: Mapped[bool] = mapped_column(Boolean, default=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)


def init_store() -> None:
    """Buat tabel (idempotent) lalu seed sekali dari watchlist.yaml bila kosong."""
    StoreBase.metadata.create_all(get_engine())
    yaml_wl = load_watchlist()
    with session_scope() as s:
        if not s.execute(select(AgentWatchAsset.id).limit(1)).first():
            for a in yaml_wl.assets:
                s.add(AgentWatchAsset(symbol=a.symbol.upper(),
                                      coingecko_id=a.coingecko_id, tier=a.tier))
        if not s.execute(select(AgentWatchProtocol.id).limit(1)).first():
            for p in yaml_wl.protocols:
                s.add(AgentWatchProtocol(name=p.name, llama_slug=p.llama_slug,
                                         track_tvl="tvl" in p.track,
                                         track_fees="fees" in p.track))


def effective_watchlist() -> Watchlist:
    """Watchlist final: assets+protocols dari DB, wallets/exchange dari yaml."""
    yaml_wl = load_watchlist()
    with session_scope() as s:
        assets = [WatchAsset(symbol=r.symbol, coingecko_id=r.coingecko_id, tier=r.tier)
                  for r in s.execute(select(AgentWatchAsset)
                                     .where(AgentWatchAsset.is_active)
                                     .order_by(AgentWatchAsset.id)).scalars()]
        protocols = []
        for r in s.execute(select(AgentWatchProtocol)
                           .where(AgentWatchProtocol.is_active)
                           .order_by(AgentWatchProtocol.id)).scalars():
            track = [t for t, on in (("tvl", r.track_tvl), ("fees", r.track_fees)) if on]
            protocols.append(WatchProtocol(name=r.name, llama_slug=r.llama_slug,
                                           track=track or ["tvl"]))
    return Watchlist(assets=assets or yaml_wl.assets,
                     protocols=protocols or yaml_wl.protocols,
                     wallets=yaml_wl.wallets,
                     exchange_labels=yaml_wl.exchange_labels)
