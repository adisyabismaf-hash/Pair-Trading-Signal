"""Koneksi database. DATABASE_URL dari .env — PostgreSQL di produksi,
SQLite fallback untuk dev/test. Skema identik (SQLAlchemy Core)."""
from __future__ import annotations

from contextlib import contextmanager

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.config import load_settings

_engine = None
_SessionLocal = None


def get_engine(database_url: str | None = None):
    global _engine, _SessionLocal
    if _engine is None or database_url is not None:
        url = database_url or load_settings().database_url
        kwargs = {}
        if url.startswith("sqlite"):
            kwargs["connect_args"] = {"check_same_thread": False}
        _engine = create_engine(url, pool_pre_ping=True, **kwargs)
        _SessionLocal = sessionmaker(bind=_engine, expire_on_commit=False)
    return _engine


@contextmanager
def session_scope():
    get_engine()
    session: Session = _SessionLocal()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def init_db(database_url: str | None = None) -> None:
    """Buat semua tabel (idempotent). Pengganti migrasi formal untuk fase awal."""
    from app import models  # noqa: F401 — daftarkan semua model
    engine = get_engine(database_url)
    models.Base.metadata.create_all(engine)
