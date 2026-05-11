from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from src.llm.provider import LLMProvider
from src.routing.router import ModelRouter
from src.session.manager import TopicSessionManager
from src.session.runtime_state import GenerationControl

if TYPE_CHECKING:
    from aiogram import Bot

session_manager = TopicSessionManager()
model_router = ModelRouter()
provider = LLMProvider()
generation_control = GenerationControl()
bot: Bot | None = None
bot_username: str = ""

# Models that failed the last /ping test; cleared/updated on each /ping run.
failed_ping_models: set[str] = set()

# Pending cleanup after /new: key is "chat_id:thread_id", value is
# (new_command_message_id, creation_notice_message_id).
pending_new_topic_cleanup: dict[str, tuple[int, int]] = {}


@dataclass
class ActiveStreamSnapshot:
    chat_id: int
    message_thread_id: int
    assistant_message_id: int
    latest_render_text: str
    is_topic_controls: bool


# Latest in-progress stream snapshot by topic key.
active_stream_snapshots: dict[str, ActiveStreamSnapshot] = {}

# Topics manually taken over by user refresh action.
user_takeover_topics: set[str] = set()
