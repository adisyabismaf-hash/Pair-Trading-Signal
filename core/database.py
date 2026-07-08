"""Database engine + session. Works with a cloud Postgres such as Neon.

Neon connection strings look like:
  postgresql://user:pass@ep-xxx.region.aws.neon.tech/dbname?sslmode=require
We normalize the scheme to psycopg2 and use NullPool so serverless connections
are never reused after going idle (avoids "server closed the connection").
"""
from sqlalchemy import create_engine, select
from sqlalchemy.orm import DeclarativeBase, sessionmaker
from sqlalchemy.pool import NullPool

from core.config import settings


def _normalize(url: str) -> str:
    if url.startswith("postgresql://"):
        return "postgresql+psycopg2://" + url[len("postgresql://"):]
    if url.startswith("postgres://"):
        return "postgresql+psycopg2://" + url[len("postgres://"):]
    return url


engine = create_engine(
    _normalize(settings.database_url),
    poolclass=NullPool,
    pool_pre_ping=True,
    connect_args={"connect_timeout": 15},
)
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)


class Base(DeclarativeBase):
    pass


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def init_db(seed: bool = True) -> None:
    """Create tables if missing and optionally seed a starter watchlist."""
    from core import models  # noqa: F401 (register models on Base)

    Base.metadata.create_all(bind=engine)
    if seed:
        _seed_defaults()


def _seed_defaults() -> None:
    from core.models import WatchedPair, WatchedSymbol

    db = SessionLocal()
    try:
        if not db.execute(select(WatchedPair.id).limit(1)).first():
            db.add(WatchedPair(base_market="BTC-USD", quote_market="ETH-USD"))
        if not db.execute(select(WatchedSymbol.id).limit(1)).first():
            for m in ("BTC-USD", "ETH-USD", "SOL-USD"):
                db.add(WatchedSymbol(market=m))
        db.commit()
    finally:
        db.close()
