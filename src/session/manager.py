from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import select

from src.persistence.models import Message, ModelState, StreamingCheckpoint, Topic
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

    def get_topic_model_selection(self, key: TopicKey) -> tuple[str, str]:
        with topic_transaction(key.value) as db:
            topic = self._fetch_topic_for_update(db, key)
            return topic.model_mode, topic.model_name

    def set_topic_model_selection(self, key: TopicKey, mode: str, model_name: str, reason: str) -> None:
        with topic_transaction(key.value) as db:
            topic = self._fetch_topic_for_update(db, key)
            prev = topic.model_name if topic.model_name else topic.model_mode
            topic.model_mode = mode
            topic.model_name = model_name
            db.add(topic)
            db.add(
                ModelState(
                    topic_id=topic.id,
                    prev_model=prev,
                    next_model=model_name if model_name else mode,
                    reason=reason,
                )
            )

    def save_streaming_checkpoint(
        self,
        key: TopicKey,
        assistant_telegram_message_id: int,
        partial_content: str,
        partial_reasoning: str,
        stop_reason: str,
    ) -> None:
        with topic_transaction(key.value) as db:
            topic = self._fetch_topic_for_update(db, key)
            db.add(
                StreamingCheckpoint(
                    topic_id=topic.id,
                    assistant_telegram_message_id=assistant_telegram_message_id,
                    partial_content=partial_content,
                    partial_reasoning=partial_reasoning,
                    stop_reason=stop_reason,
                )
            )

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
