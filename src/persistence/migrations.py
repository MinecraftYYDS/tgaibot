from __future__ import annotations

from src.persistence.db import enable_sqlite_wal, ensure_data_dir
from src.persistence.models import Base
from src.persistence.db import engine


def run_migrations() -> None:
    ensure_data_dir()
    enable_sqlite_wal()
    Base.metadata.create_all(bind=engine)
