from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import math
import re

from sqlalchemy import asc, func, select

from src.config import settings
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
    def _tokenize_for_embedding(text: str) -> list[str]:
        lowered = (text or "").lower()
        return re.findall("[A-Za-z0-9_]+|[\u4e00-\u9fff]+", lowered)

    @staticmethod
    def _text_to_embedding(text: str, dim: int = 64) -> list[float]:
        vec = [0.0] * dim
        tokens = TopicSessionManager._tokenize_for_embedding(text)
        if not tokens:
            return vec
        for tok in tokens:
            digest = hashlib.md5(tok.encode("utf-8")).digest()
            idx = int.from_bytes(digest[:4], "little", signed=False) % dim
            vec[idx] += 1.0
        norm = math.sqrt(sum(v * v for v in vec))
        if norm <= 0:
            return vec
        return [v / norm for v in vec]

    @staticmethod
    def _cosine_similarity(a: list[float], b: list[float]) -> float:
        if not a or not b:
            return 0.0
        size = min(len(a), len(b))
        return float(sum(a[i] * b[i] for i in range(size)))

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
            if settings.embedding_enable and content.strip():
                scope_type, scope_id = self._scope_for_key(key)
                vec = self._text_to_embedding(content)
                db.add(
                    MessageEmbedding(
                        scope_type=scope_type,
                        scope_id=scope_id,
                        message_id=int(message.id),
                        content=content,
                        embedding_json=json.dumps(vec, ensure_ascii=True),
                    )
                )
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
            scope_type, scope_id = self._scope_for_key(key)
            emb_stmt = select(MessageEmbedding).where(
                MessageEmbedding.scope_type == scope_type,
                MessageEmbedding.scope_id == scope_id,
                MessageEmbedding.message_id == int(message.id),
            )
            emb_rows = db.execute(emb_stmt).scalars().all()
            for row in emb_rows:
                db.delete(row)
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

    def estimate_topic_tokens(self, key: TopicKey) -> int:
        """Estimate token usage with a lightweight chars/4 heuristic."""
        with topic_transaction(key.value) as db:
            topic = self._fetch_topic_for_update(db, key)
            stmt = select(Message).where(Message.topic_id == topic.id, Message.deleted.is_(False))
            items = db.execute(stmt).scalars().all()
            total_chars = 0
            for item in items:
                total_chars += len(item.content or "")
                total_chars += len(item.reasoning or "")
            return max(1, total_chars // 4)

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

    @staticmethod
    def _summary_json_to_text(raw: str) -> str:
        payload = (raw or "").strip()
        if not payload:
            return ""
        try:
            parsed = json.loads(payload)
        except json.JSONDecodeError:
            return payload
        if not isinstance(parsed, dict):
            return payload

        pieces: list[str] = []
        main = str(parsed.get("summary") or "").strip()
        if main:
            pieces.append(main)

        for key in ("user_goals", "requirements", "decisions", "constraints", "important_details", "open_tasks"):
            value = parsed.get(key)
            if isinstance(value, list) and value:
                line = "、".join(str(item).strip() for item in value if str(item).strip())
                if line:
                    pieces.append(f"{key}: {line}")
        return "\n".join(pieces).strip()

    def set_scope_summary_json(self, key: TopicKey, summary_json: str) -> None:
        scope_type, scope_id = self._scope_for_key(key)
        with topic_transaction(key.value) as db:
            stmt = (
                select(ConversationSummary)
                .where(ConversationSummary.scope_type == scope_type, ConversationSummary.scope_id == scope_id)
                .order_by(ConversationSummary.id.desc())
                .limit(1)
            )
            row = db.execute(stmt).scalar_one_or_none()
            if row is None:
                row = ConversationSummary(scope_type=scope_type, scope_id=scope_id, summary_json=summary_json)
            else:
                row.summary_json = summary_json
            db.add(row)

    def get_scope_summary_text(self, key: TopicKey) -> str:
        scope_type, scope_id = self._scope_for_key(key)
        with topic_transaction(key.value) as db:
            topic = self._fetch_topic_for_update(db, key)
            stmt = (
                select(ConversationSummary)
                .where(ConversationSummary.scope_type == scope_type, ConversationSummary.scope_id == scope_id)
                .order_by(ConversationSummary.id.desc())
                .limit(1)
            )
            row = db.execute(stmt).scalar_one_or_none()
            if row is not None:
                text = self._summary_json_to_text(str(row.summary_json or ""))
                if text:
                    return text
            return str(topic.summary or "").strip()

    def add_pinned_memory(self, key: TopicKey, content: str, created_by: int) -> int:
        scope_type, scope_id = self._scope_for_key(key)
        clean = content.strip()
        if not clean:
            return 0
        with topic_transaction(key.value) as db:
            row = PinnedMemory(scope_type=scope_type, scope_id=scope_id, content=clean, created_by=created_by)
            db.add(row)
            db.flush()
            db.refresh(row)
            return int(row.id)

    def list_pinned_memories(self, key: TopicKey, limit: int = 20) -> list[PinnedMemory]:
        scope_type, scope_id = self._scope_for_key(key)
        with topic_transaction(key.value) as db:
            stmt = (
                select(PinnedMemory)
                .where(PinnedMemory.scope_type == scope_type, PinnedMemory.scope_id == scope_id)
                .order_by(PinnedMemory.id.asc())
                .limit(limit)
            )
            return list(db.execute(stmt).scalars().all())

    def remove_pinned_memory(self, key: TopicKey, pin_id: int) -> bool:
        scope_type, scope_id = self._scope_for_key(key)
        with topic_transaction(key.value) as db:
            stmt = select(PinnedMemory).where(
                PinnedMemory.id == pin_id,
                PinnedMemory.scope_type == scope_type,
                PinnedMemory.scope_id == scope_id,
            )
            row = db.execute(stmt).scalar_one_or_none()
            if row is None:
                return False
            db.delete(row)
            return True

    def add_long_term_memory(self, user_id: int, memory: str, importance: int = 1) -> int:
        clean = memory.strip()
        if not clean:
            return 0
        key = TopicKey(chat_id=user_id, message_thread_id=0)
        with topic_transaction(key.value) as db:
            row = LongTermMemory(user_id=user_id, memory=clean, importance=max(1, int(importance)))
            db.add(row)
            db.flush()
            db.refresh(row)
            return int(row.id)

    def list_long_term_memories(self, user_id: int, limit: int = 10) -> list[LongTermMemory]:
        key = TopicKey(chat_id=user_id, message_thread_id=0)
        with topic_transaction(key.value) as db:
            stmt = (
                select(LongTermMemory)
                .where(LongTermMemory.user_id == user_id)
                .order_by(LongTermMemory.importance.desc(), LongTermMemory.updated_at.desc(), LongTermMemory.id.desc())
                .limit(limit)
            )
            return list(db.execute(stmt).scalars().all())

    def remove_long_term_memory(self, user_id: int, memory_id: int) -> bool:
        key = TopicKey(chat_id=user_id, message_thread_id=0)
        with topic_transaction(key.value) as db:
            stmt = select(LongTermMemory).where(
                LongTermMemory.id == memory_id,
                LongTermMemory.user_id == user_id,
            )
            row = db.execute(stmt).scalar_one_or_none()
            if row is None:
                return False
            db.delete(row)
            return True

    def clear_long_term_memories(self, user_id: int) -> int:
        key = TopicKey(chat_id=user_id, message_thread_id=0)
        with topic_transaction(key.value) as db:
            stmt = select(LongTermMemory).where(LongTermMemory.user_id == user_id)
            rows = db.execute(stmt).scalars().all()
            for row in rows:
                db.delete(row)
            return len(rows)

    def collect_augmented_context_for_response(
        self,
        key: TopicKey,
        max_messages: int | None = None,
        recent_window_size: int = 10,
        query_text: str = "",
        retrieval_top_k: int = 6,
    ) -> list[dict]:
        scope_type, scope_id = self._scope_for_key(key)
        with topic_transaction(key.value) as db:
            topic = self._fetch_topic_for_update(db, key)
            limit_value = max_messages if (max_messages is not None and max_messages > 0) else max(1, int(recent_window_size))

            context: list[dict] = []

            summary_stmt = (
                select(ConversationSummary)
                .where(ConversationSummary.scope_type == scope_type, ConversationSummary.scope_id == scope_id)
                .order_by(ConversationSummary.id.desc())
                .limit(1)
            )
            summary_row = db.execute(summary_stmt).scalar_one_or_none()
            summary_text = ""
            if summary_row is not None:
                summary_text = self._summary_json_to_text(str(summary_row.summary_json or ""))
            if not summary_text:
                summary_text = str(topic.summary or "").strip()
            if summary_text:
                context.append({"role": "system", "content": "[结构化摘要]\n" + summary_text})

            pinned_stmt = (
                select(PinnedMemory)
                .where(PinnedMemory.scope_type == scope_type, PinnedMemory.scope_id == scope_id)
                .order_by(PinnedMemory.id.asc())
                .limit(20)
            )
            pinned_items = db.execute(pinned_stmt).scalars().all()
            if pinned_items:
                pinned_text = "\n".join(f"- {item.content}" for item in pinned_items if item.content.strip())
                if pinned_text:
                    context.append({"role": "system", "content": "[钉住内容]\n" + pinned_text})

            if scope_type == "private":
                long_stmt = (
                    select(LongTermMemory)
                    .where(LongTermMemory.user_id == key.chat_id)
                    .order_by(LongTermMemory.importance.desc(), LongTermMemory.updated_at.desc(), LongTermMemory.id.desc())
                    .limit(8)
                )
                long_items = db.execute(long_stmt).scalars().all()
                if long_items:
                    long_text = "\n".join(f"- {item.memory}" for item in long_items if item.memory.strip())
                    if long_text:
                        context.append({"role": "system", "content": "[长期记忆]\n" + long_text})

            msg_stmt = (
                select(Message)
                .where(Message.topic_id == topic.id, Message.deleted.is_(False))
                .order_by(asc(Message.id))
            )
            all_items = db.execute(msg_stmt).scalars().all()
            recent_items = all_items[-limit_value:]
            recent_message_ids = {int(item.id) for item in recent_items}

            if settings.embedding_enable and query_text.strip() and retrieval_top_k > 0:
                query_vec = self._text_to_embedding(query_text)
                emb_stmt = (
                    select(MessageEmbedding)
                    .where(MessageEmbedding.scope_type == scope_type, MessageEmbedding.scope_id == scope_id)
                    .order_by(MessageEmbedding.id.desc())
                    .limit(400)
                )
                emb_items = db.execute(emb_stmt).scalars().all()
                scored: list[tuple[float, str]] = []
                for emb in emb_items:
                    if int(emb.message_id or 0) in recent_message_ids:
                        continue
                    raw = str(emb.embedding_json or "[]")
                    try:
                        vec_raw = json.loads(raw)
                    except json.JSONDecodeError:
                        continue
                    if not isinstance(vec_raw, list):
                        continue
                    try:
                        vec = [float(v) for v in vec_raw]
                    except (TypeError, ValueError):
                        continue
                    sim = self._cosine_similarity(query_vec, vec)
                    if sim <= 0.12:
                        continue
                    snippet = str(emb.content or "").strip()
                    if not snippet:
                        continue
                    scored.append((sim, snippet))
                scored.sort(key=lambda x: x[0], reverse=True)
                seen: set[str] = set()
                picked: list[str] = []
                for _, text in scored:
                    key_text = text[:120]
                    if key_text in seen:
                        continue
                    seen.add(key_text)
                    picked.append(text)
                    if len(picked) >= retrieval_top_k:
                        break
                if picked:
                    retrieve_text = "\n".join(f"- {item}" for item in picked)
                    context.append({"role": "system", "content": "[相关历史检索]\n" + retrieve_text})

            for item in recent_items:
                msg = {
                    "role": "assistant" if item.role == "assistant" else "user",
                    "content": item.content,
                }
                if item.reasoning and item.reasoning.strip():
                    msg["reasoning_content"] = item.reasoning
                context.append(msg)
            return context

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
