from __future__ import annotations

import logging

from aiogram import Router
from aiogram.types import CallbackQuery

from src.bot.keyboards import control_keyboard
from src.bot.runtime import generation_control, session_manager
from src.session.manager import TopicKey

logger = logging.getLogger(__name__)
router = Router(name="callbacks")


@router.callback_query(lambda c: c.data and c.data.startswith("model:"))
async def on_model_switch(callback: CallbackQuery) -> None:
    selected = callback.data.split(":", maxsplit=1)[1]
    if callback.message and callback.message.message_thread_id is not None:
        key = TopicKey(chat_id=callback.message.chat.id, message_thread_id=callback.message.message_thread_id)
        if selected == "auto":
            session_manager.set_topic_model_selection(key, mode="auto", model_name="", reason="user_button")
        else:
            session_manager.set_topic_model_selection(key, mode="manual", model_name=selected, reason="user_button")
    await callback.answer(f"Model switched to {selected}")
    if callback.message:
        await callback.message.answer(f"Model set: {selected}", reply_markup=control_keyboard())


@router.callback_query(lambda c: c.data and c.data == "control:stop")
async def on_stop(callback: CallbackQuery) -> None:
    if callback.message and callback.message.message_thread_id is not None:
        key = TopicKey(chat_id=callback.message.chat.id, message_thread_id=callback.message.message_thread_id)
        generation_control.stop(key.value)
    await callback.answer("Generation stop requested")


@router.callback_query(lambda c: c.data and c.data == "control:summarize")
async def on_summarize(callback: CallbackQuery) -> None:
    if callback.message and callback.message.message_thread_id is not None:
        key = TopicKey(chat_id=callback.message.chat.id, message_thread_id=callback.message.message_thread_id)
        session_manager.enqueue_job(key=key, job_type="summarize", payload={"source": "button"})
        await callback.answer("Summary job queued")
        return
    await callback.answer("No topic context")


@router.callback_query(lambda c: c.data and c.data == "control:clear")
async def on_clear(callback: CallbackQuery) -> None:
    if callback.message and callback.message.message_thread_id is not None:
        key = TopicKey(chat_id=callback.message.chat.id, message_thread_id=callback.message.message_thread_id)
        changed = session_manager.clear_topic_messages(key)
        await callback.answer(f"Context cleared: {changed} messages")
        if callback.message:
            await callback.message.answer(f"Cleared context: {changed} messages")
        return
    await callback.answer("No topic context")
