from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import select

from src.persistence.models import Message, Topic
from src.sync.consistency import topic_transaction


@dataclass(frozen=True)
class TopicKey:
    chat_id: int
    message_thread_id: int

    @property
    def value(self) -> str:
        return f"{self.chat_id}:{self.message_thread_id}"


class TopicSessionManager:
    def get_or_create_topic(self, key: TopicKey) -> Topic:
        with topic_transaction(key.value) as db:
            stmt = select(Topic).where(
                Topic.chat_id == key.chat_id,
                Topic.message_thread_id == key.message_thread_id,
            )
            topic = db.execute(stmt).scalar_one_or_none()
            if topic is None:
                topic = Topic(chat_id=key.chat_id, message_thread_id=key.message_thread_id)
                db.add(topic)
                db.flush()
            db.refresh(topic)
            return topic

    def append_message(
        self,
        key: TopicKey,
        telegram_message_id: int,
        role: str,
        content: str,
        reasoning: str = "",
    ) -> Message:
        with topic_transaction(key.value) as db:
            topic = self._fetch_topic_for_update(db, key)
            message = Message(
                topic_id=topic.id,
                telegram_message_id=telegram_message_id,
                role=role,
                content=content,
                reasoning=reasoning,
            )
            db.add(message)
            db.flush()
            db.refresh(message)
            return message

    def mark_deleted(self, key: TopicKey, telegram_message_id: int) -> bool:
        with topic_transaction(key.value) as db:
            topic = self._fetch_topic_for_update(db, key)
            stmt = select(Message).where(
                Message.topic_id == topic.id,
                Message.telegram_message_id == telegram_message_id,
                Message.deleted.is_(False),
            )
            message = db.execute(stmt).scalar_one_or_none()
            if not message:
                return False
            message.deleted = True
            db.add(message)
            return True

    @staticmethod
    def _fetch_topic_for_update(db, key: TopicKey) -> Topic:
        stmt = select(Topic).where(
            Topic.chat_id == key.chat_id,
            Topic.message_thread_id == key.message_thread_id,
        )
        topic = db.execute(stmt).scalar_one_or_none()
        if topic is None:
            topic = Topic(chat_id=key.chat_id, message_thread_id=key.message_thread_id)
            db.add(topic)
            db.flush()
            db.refresh(topic)
        return topic
