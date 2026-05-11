from __future__ import annotations

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
