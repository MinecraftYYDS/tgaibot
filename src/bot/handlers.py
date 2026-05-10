from __future__ import annotations

import logging
from time import monotonic

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.types import BusinessMessagesDeleted, Message

from src.bot.keyboards import control_keyboard, model_selection_keyboard
from src.bot.runtime import generation_control, model_router, provider, session_manager
from src.config import settings
from src.llm.reasoning import post_process_reasoning
from src.session.manager import TopicKey

logger = logging.getLogger(__name__)
router = Router(name="handlers")


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
    if message.message_thread_id is None:
        await message.answer("Please run /stop inside a forum topic.")
        return
    topic_key = TopicKey(chat_id=message.chat.id, message_thread_id=message.message_thread_id)
    generation_control.stop(topic_key.value)
    await message.answer("Stop requested for current topic generation.")


@router.message(Command("models"))
async def on_models(message: Message) -> None:
    lines = ["Available models:"]
    for model in settings.model_catalog:
        lines.append(f"- {model.id}: {model.label} ({model.model_name})")
    await message.answer("\n".join(lines))


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

    mode, selected_model = session_manager.get_topic_model_selection(topic_key)
    requested_mode = selected_model if mode == "manual" and selected_model else settings.default_model_mode
    route = model_router.route(incoming_text, mode=requested_mode)

    header = f"ModelId: {route.model}\nReason: {route.reason}\n\n"
    sent = await message.answer(header + "...", reply_markup=control_keyboard())
    generation_control.begin(topic_key.value)

    last_edit = monotonic()
    built_answer = ""
    stop_reason = "completed"
    try:
        async for chunk in provider.stream_generate(prompt=incoming_text, model=route.model):
            if generation_control.should_stop(topic_key.value):
                stop_reason = "user_stop"
                break
            built_answer += chunk
            now = monotonic()
            if now - last_edit >= settings.stream_edit_interval_seconds:
                await sent.edit_text(header + (built_answer or "..."), reply_markup=control_keyboard())
                last_edit = now
    except Exception as exc:  # noqa: BLE001
        stop_reason = "error"
        built_answer += f"\n\n[error] {type(exc).__name__}: {exc}"
    finally:
        generation_control.end(topic_key.value)

    reasoning_text = "route_decision -> stream_generate"
    reasoning = post_process_reasoning(settings.reasoning_mode, reasoning_text)
    final_text = built_answer or "(empty response)"
    if stop_reason == "user_stop":
        final_text += "\n\n[stopped by user]"

    final_render = header + final_text
    if reasoning:
        final_render += "\n\n[reasoning]\n" + reasoning

    await sent.edit_text(final_render, reply_markup=control_keyboard())
    session_manager.append_message(
        key=topic_key,
        telegram_message_id=sent.message_id,
        role="assistant",
        content=final_text,
        reasoning=reasoning,
    )
    msg_count = session_manager.topic_message_count(topic_key)
    if msg_count == 2:
        session_manager.enqueue_job(topic_key, job_type="rename_topic", payload={"source": "auto"})
    if msg_count > 0 and msg_count % 10 == 0:
        session_manager.enqueue_job(topic_key, job_type="summarize", payload={"source": "auto", "count": msg_count})
    if stop_reason != "completed":
        session_manager.save_streaming_checkpoint(
            key=topic_key,
            assistant_telegram_message_id=sent.message_id,
            partial_content=final_text,
            partial_reasoning=reasoning,
            stop_reason=stop_reason,
        )


@router.deleted_business_messages()
async def on_deleted_business_messages(event: BusinessMessagesDeleted) -> None:
    # This update type is for business mode; kept for compatibility.
    if event.chat is None or event.message_ids is None:
        return
    thread_id = event.message_thread_id if event.message_thread_id is not None else 0
    key = TopicKey(chat_id=event.chat.id, message_thread_id=thread_id)
    changed = 0
    for message_id in event.message_ids:
        session_manager.enqueue_job(key=key, job_type="delete_sync", payload={"telegram_message_id": message_id})
        if session_manager.mark_deleted(key, message_id):
            changed += 1
    logger.info("Deleted sync applied chat=%s thread=%s changed=%s", event.chat.id, thread_id, changed)
