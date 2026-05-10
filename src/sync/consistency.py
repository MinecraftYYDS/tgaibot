from __future__ import annotations

from contextlib import contextmanager
from threading import Lock

from sqlalchemy import text

from src.persistence.db import SessionLocal


class TopicLockManager:
    _locks: dict[str, Lock] = {}
    _global_lock = Lock()

    @classmethod
    def acquire(cls, topic_key: str) -> None:
        with cls._global_lock:
            if topic_key not in cls._locks:
                cls._locks[topic_key] = Lock()
            lock = cls._locks[topic_key]
        lock.acquire()

    @classmethod
    def release(cls, topic_key: str) -> None:
        with cls._global_lock:
            lock = cls._locks.get(topic_key)
        if lock:
            lock.release()


@contextmanager
def topic_transaction(topic_key: str):
    TopicLockManager.acquire(topic_key)
    db = SessionLocal()
    try:
        db.execute(text("BEGIN IMMEDIATE"))
        yield db
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()
        TopicLockManager.release(topic_key)
