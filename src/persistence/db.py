from __future__ import annotations

from pathlib import Path

from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

from src.config import settings


def _db_url() -> str:
    return f"sqlite:///{settings.db_path}"


def ensure_data_dir() -> None:
    path = Path(settings.db_path)
    path.parent.mkdir(parents=True, exist_ok=True)


engine = create_engine(_db_url(), future=True, connect_args={"check_same_thread": False})
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)


def enable_sqlite_wal() -> None:
    with engine.connect() as conn:
        conn.execute(text("PRAGMA journal_mode=WAL;"))
        conn.execute(text("PRAGMA foreign_keys=ON;"))
        conn.commit()
