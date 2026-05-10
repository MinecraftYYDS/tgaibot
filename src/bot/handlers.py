from __future__ import annotations

import logging

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.types import Message

from src.bot.keyboards import control_keyboard, model_selection_keyboard
from src.config import settings
from src.llm.provider import LLMProvider
from src.llm.reasoning import post_process_reasoning
from src.routing.router import ModelRouter
from src.session.manager import TopicKey, TopicSessionManager

logger = logging.getLogger(__name__)
router = Router(name="handlers")
session_manager = TopicSessionManager()
model_router = ModelRouter()
provider = LLMProvider()


@router.message(Command("start"))
async def on_start(message: Message) -> None:
    await message.answer("Bot is online. Use /new in a forum topic to start a session.")


@router.message(Command("new"))
async def on_new(message: Message) -> None:
    if message.message_thread_id is None:
        await message.answer("Please run /new inside a forum topic.")
        return

    topic_key = TopicKey(chat_id=message.chat.id, message_thread_id=message.message_thread_id)
    session_manager.get_or_create_topic(topic_key)
    await message.answer("Select model mode for this topic:", reply_markup=model_selection_keyboard())


@router.message(Command("stop"))
async def on_stop(message: Message) -> None:
    await message.answer("Stop requested for current topic generation.")


@router.message(F.text)
async def on_text(message: Message) -> None:
    if message.message_thread_id is None:
        await message.answer("Please chat inside a forum topic.")
        return
    if settings.allowed_chat_ids and message.chat.id not in settings.allowed_chat_ids:
        await message.answer("This chat is not allowed for this bot instance.")
        return

    incoming_text = message.text or ""
    topic_key = TopicKey(chat_id=message.chat.id, message_thread_id=message.message_thread_id)
    session_manager.get_or_create_topic(topic_key)
    session_manager.append_message(
        key=topic_key,
        telegram_message_id=message.message_id,
        role="user",
        content=incoming_text,
    )

    route = model_router.route(incoming_text, mode=settings.default_model_mode)
    generated = await provider.generate(prompt=incoming_text, model=route.model)
    reasoning = post_process_reasoning(settings.reasoning_mode, generated.reasoning)

    reply_parts = [
        f"Model: {route.model}",
        f"Reason: {route.reason}",
        "",
        generated.final_text,
    ]
    if reasoning:
        reply_parts.extend(["", "[reasoning]", reasoning])

    sent = await message.answer("\n".join(reply_parts), reply_markup=control_keyboard())
    session_manager.append_message(
        key=topic_key,
        telegram_message_id=sent.message_id,
        role="assistant",
        content=generated.final_text,
        reasoning=reasoning,
    )
