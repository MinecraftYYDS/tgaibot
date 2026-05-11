from __future__ import annotations

from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


class Topic(Base):
    __tablename__ = "topics"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    chat_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    message_thread_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    title: Mapped[str] = mapped_column(String(200), default="")
    model_mode: Mapped[str] = mapped_column(String(40), default="auto")
    model_name: Mapped[str] = mapped_column(String(100), default="")
    summary: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    messages: Mapped[list[Message]] = relationship(back_populates="topic", cascade="all, delete-orphan")


class Message(Base):
    __tablename__ = "messages"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    topic_id: Mapped[int] = mapped_column(ForeignKey("topics.id"), nullable=False, index=True)
    telegram_message_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    role: Mapped[str] = mapped_column(String(20), nullable=False)
    content: Mapped[str] = mapped_column(Text, default="")
    reasoning: Mapped[str] = mapped_column(Text, default="")
    deleted: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    topic: Mapped[Topic] = relationship(back_populates="messages")


class ModelState(Base):
    __tablename__ = "model_states"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    topic_id: Mapped[int] = mapped_column(ForeignKey("topics.id"), nullable=False, index=True)
    prev_model: Mapped[str] = mapped_column(String(100), default="")
    next_model: Mapped[str] = mapped_column(String(100), default="")
    reason: Mapped[str] = mapped_column(String(200), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class ToolState(Base):
    __tablename__ = "tool_states"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    topic_id: Mapped[int] = mapped_column(ForeignKey("topics.id"), nullable=False, index=True)
    tool_name: Mapped[str] = mapped_column(String(100), nullable=False)
    status: Mapped[str] = mapped_column(String(20), default="pending")
    input_json: Mapped[str] = mapped_column(Text, default="")
    output_json: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class Job(Base):
    __tablename__ = "jobs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    topic_id: Mapped[int] = mapped_column(ForeignKey("topics.id"), nullable=False, index=True)
    job_type: Mapped[str] = mapped_column(String(50), nullable=False)
    payload_json: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[str] = mapped_column(String(20), default="pending")
    retry_count: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class StreamingCheckpoint(Base):
    __tablename__ = "streaming_checkpoints"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    topic_id: Mapped[int] = mapped_column(ForeignKey("topics.id"), nullable=False, index=True)
    assistant_telegram_message_id: Mapped[int] = mapped_column(Integer, default=0)
    partial_content: Mapped[str] = mapped_column(Text, default="")
    partial_reasoning: Mapped[str] = mapped_column(Text, default="")
    stop_reason: Mapped[str] = mapped_column(String(50), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class ConversationSummary(Base):
    __tablename__ = "conversation_summaries"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    scope_type: Mapped[str] = mapped_column(String(20), nullable=False, index=True)
    scope_id: Mapped[str] = mapped_column(String(120), nullable=False, index=True)
    summary_json: Mapped[str] = mapped_column(Text, default="{}")
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class LongTermMemory(Base):
    __tablename__ = "long_term_memories"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    memory: Mapped[str] = mapped_column(Text, default="")
    importance: Mapped[int] = mapped_column(Integer, default=1)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class PinnedMemory(Base):
    __tablename__ = "pinned_memories"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    scope_type: Mapped[str] = mapped_column(String(20), nullable=False, index=True)
    scope_id: Mapped[str] = mapped_column(String(120), nullable=False, index=True)
    content: Mapped[str] = mapped_column(Text, default="")
    created_by: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class MessageEmbedding(Base):
    __tablename__ = "message_embeddings"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    scope_type: Mapped[str] = mapped_column(String(20), nullable=False, index=True)
    scope_id: Mapped[str] = mapped_column(String(120), nullable=False, index=True)
    message_id: Mapped[int] = mapped_column(Integer, default=0, index=True)
    content: Mapped[str] = mapped_column(Text, default="")
    embedding_json: Mapped[str] = mapped_column(Text, default="[]")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
