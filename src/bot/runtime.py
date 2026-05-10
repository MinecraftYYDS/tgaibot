from __future__ import annotations

from src.llm.provider import LLMProvider
from src.routing.router import ModelRouter
from src.session.manager import TopicSessionManager
from src.session.runtime_state import GenerationControl

session_manager = TopicSessionManager()
model_router = ModelRouter()
provider = LLMProvider()
generation_control = GenerationControl()
