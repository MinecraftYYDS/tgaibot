from __future__ import annotations

from dataclasses import dataclass
import json

from sqlalchemy import asc, func, select

from src.persistence.models import (
    ConversationSummary,
    Job,
    LongTermMemory,
    Message,
    MessageEmbedding,
    ModelState,
    PinnedMemory,
    StreamingCheckpoint,
    ToolState,
    Topic,
)
from src.sync.consistency import topic_transaction


@dataclass(frozen=True)
class TopicKey:
    chat_id: int
    message_thread_id: int

    @property
    def value(self) -> str:
        return f"{self.chat_id}:{self.message_thread_id}"


class TopicSessionManager:
    @staticmethod
    def _scope_for_key(key: TopicKey) -> tuple[str, str]:
        if key.chat_id > 0 and key.message_thread_id == 0:
            return "private", f"private:{key.chat_id}"
        return "topic", f"topic:{key.chat_id}:{key.message_thread_id}"

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

    def clear_topic_messages(self, key: TopicKey) -> int:
        with topic_transaction(key.value) as db:
            topic = self._fetch_topic_for_update(db, key)
            stmt = select(Message).where(Message.topic_id == topic.id, Message.deleted.is_(False))
            rows = db.execute(stmt).scalars().all()
            for row in rows:
                row.deleted = True
                db.add(row)
            return len(rows)

    def clear_topic_memory(self, key: TopicKey) -> dict[str, int]:
        """Clear persisted memory for one topic/private scope.

        This is used by private /new to reset all in-scope memory artifacts.
        """
        with topic_transaction(key.value) as db:
            topic = self._fetch_topic_for_update(db, key)

            msg_stmt = select(Message).where(Message.topic_id == topic.id, Message.deleted.is_(False))
            messages = db.execute(msg_stmt).scalars().all()
            for row in messages:
                row.deleted = True
                db.add(row)

            checkpoint_stmt = select(StreamingCheckpoint).where(StreamingCheckpoint.topic_id == topic.id)
            checkpoints = db.execute(checkpoint_stmt).scalars().all()
            for row in checkpoints:
                db.delete(row)

            job_stmt = select(Job).where(Job.topic_id == topic.id)
            jobs = db.execute(job_stmt).scalars().all()
            for row in jobs:
                db.delete(row)

            tool_stmt = select(ToolState).where(ToolState.topic_id == topic.id)
            tools = db.execute(tool_stmt).scalars().all()
            for row in tools:
                db.delete(row)

            scope_type, scope_id = self._scope_for_key(key)

            summary_stmt = select(ConversationSummary).where(
                ConversationSummary.scope_type == scope_type,
                ConversationSummary.scope_id == scope_id,
            )
            summaries = db.execute(summary_stmt).scalars().all()
            for row in summaries:
                db.delete(row)

            pinned_stmt = select(PinnedMemory).where(
                PinnedMemory.scope_type == scope_type,
                PinnedMemory.scope_id == scope_id,
            )
            pinned = db.execute(pinned_stmt).scalars().all()
            for row in pinned:
                db.delete(row)

            embedding_stmt = select(MessageEmbedding).where(
                MessageEmbedding.scope_type == scope_type,
                MessageEmbedding.scope_id == scope_id,
            )
            embeddings = db.execute(embedding_stmt).scalars().all()
            for row in embeddings:
                db.delete(row)

            long_term: list[LongTermMemory] = []
            if scope_type == "private":
                long_stmt = select(LongTermMemory).where(LongTermMemory.user_id == key.chat_id)
                long_term = db.execute(long_stmt).scalars().all()
                for row in long_term:
                    db.delete(row)

            summary_cleared = 1 if topic.summary else 0
            topic.summary = ""
            db.add(topic)

            return {
                "messages": len(messages),
                "checkpoints": len(checkpoints),
                "jobs": len(jobs),
                "tools": len(tools),
                "summary": summary_cleared,
                "scope_summaries": len(summaries),
                "pinned": len(pinned),
                "embeddings": len(embeddings),
                "long_term": len(long_term),
            }

    def topic_message_count(self, key: TopicKey) -> int:
        with topic_transaction(key.value) as db:
            topic = self._fetch_topic_for_update(db, key)
            stmt = select(func.count()).select_from(Message).where(Message.topic_id == topic.id, Message.deleted.is_(False))
            return int(db.execute(stmt).scalar_one())

    def set_topic_title(self, key: TopicKey, title: str) -> None:
        with topic_transaction(key.value) as db:
            topic = self._fetch_topic_for_update(db, key)
            topic.title = title[:200]
            db.add(topic)

    def set_topic_summary(self, key: TopicKey, summary: str) -> None:
        with topic_transaction(key.value) as db:
            topic = self._fetch_topic_for_update(db, key)
            topic.summary = summary
            db.add(topic)

    def collect_context_for_response(self, key: TopicKey, max_messages: int | None = None) -> list[dict]:
        """Collect conversation history for LLM context.

        If max_messages is set, only the last N messages are returned.
        """
        with topic_transaction(key.value) as db:
            topic = self._fetch_topic_for_update(db, key)
            stmt = (
                select(Message)
                .where(Message.topic_id == topic.id, Message.deleted.is_(False))
                .order_by(asc(Message.id))
            )
            items = db.execute(stmt).scalars().all()
            if max_messages is not None and max_messages > 0:
                items = items[-max_messages:]
            messages: list[dict] = []
            for item in items:
                msg = {
                    "role": "assistant" if item.role == "assistant" else "user",
                    "content": item.content,
                }
                if item.reasoning and item.reasoning.strip():
                    msg["reasoning_content"] = item.reasoning
                messages.append(msg)
            return messages

    def collect_context_for_summary(self, key: TopicKey, max_messages: int = 30) -> str:
        with topic_transaction(key.value) as db:
            topic = self._fetch_topic_for_update(db, key)
            stmt = (
                select(Message)
                .where(Message.topic_id == topic.id, Message.deleted.is_(False))
                .order_by(asc(Message.id))
            )
            items = db.execute(stmt).scalars().all()[-max_messages:]
            lines: list[str] = []
            for item in items:
                role = item.role.upper()
                lines.append(f"{role}: {item.content}")
            return "\n".join(lines)

    def get_latest_assistant_content(self, key: TopicKey) -> str:
        with topic_transaction(key.value) as db:
            topic = self._fetch_topic_for_update(db, key)
            stmt = (
                select(Message)
                .where(
                    Message.topic_id == topic.id,
                    Message.deleted.is_(False),
                    Message.role == "assistant",
                )
                .order_by(asc(Message.id))
            )
            items = db.execute(stmt).scalars().all()
            if not items:
                return ""
            return str(items[-1].content or "")

    def get_assistant_content_by_telegram_message_id(self, key: TopicKey, telegram_message_id: int) -> str:
        with topic_transaction(key.value) as db:
            topic = self._fetch_topic_for_update(db, key)
            stmt = select(Message).where(
                Message.topic_id == topic.id,
                Message.deleted.is_(False),
                Message.role == "assistant",
                Message.telegram_message_id == telegram_message_id,
            )
            message = db.execute(stmt).scalar_one_or_none()
            if message is None:
                return ""
            return str(message.content or "")

    def enqueue_job(self, key: TopicKey, job_type: str, payload: dict[str, object]) -> int:
        with topic_transaction(key.value) as db:
            topic = self._fetch_topic_for_update(db, key)
            job = Job(topic_id=topic.id, job_type=job_type, payload_json=json.dumps(payload, ensure_ascii=True))
            db.add(job)
            db.flush()
            db.refresh(job)
            return int(job.id)

    def claim_next_pending_job(self) -> tuple[int, int, str, dict[str, object]] | None:
        # Global claim uses a dedicated lock key to serialize worker fetches.
        with topic_transaction("__jobs__") as db:
            stmt = select(Job).where(Job.status == "pending").order_by(asc(Job.id)).limit(1)
            job = db.execute(stmt).scalar_one_or_none()
            if job is None:
                return None
            job.status = "running"
            db.add(job)
            payload: dict[str, object]
            try:
                parsed = json.loads(job.payload_json) if job.payload_json else {}
                payload = parsed if isinstance(parsed, dict) else {}
            except json.JSONDecodeError:
                payload = {}
            return int(job.id), int(job.topic_id), job.job_type, payload

    def finish_job(self, job_id: int) -> None:
        with topic_transaction("__jobs__") as db:
            stmt = select(Job).where(Job.id == job_id)
            job = db.execute(stmt).scalar_one_or_none()
            if not job:
                return
            job.status = "done"
            db.add(job)

    def fail_job(self, job_id: int, max_retry: int = 3) -> None:
        with topic_transaction("__jobs__") as db:
            stmt = select(Job).where(Job.id == job_id)
            job = db.execute(stmt).scalar_one_or_none()
            if not job:
                return
            job.retry_count = int(job.retry_count) + 1
            job.status = "pending" if job.retry_count < max_retry else "error"
            db.add(job)

    def topic_key_by_topic_id(self, topic_id: int) -> TopicKey | None:
        with topic_transaction("__jobs__") as db:
            stmt = select(Topic).where(Topic.id == topic_id)
            topic = db.execute(stmt).scalar_one_or_none()
            if topic is None:
                return None
            return TopicKey(chat_id=int(topic.chat_id), message_thread_id=int(topic.message_thread_id))

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
