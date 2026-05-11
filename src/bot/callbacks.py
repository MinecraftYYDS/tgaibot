from __future__ import annotations

import logging
from datetime import datetime

from aiogram import Router
from aiogram.types import BufferedInputFile, CallbackQuery

from src.bot.keyboards import control_keyboard
from src.bot.runtime import generation_control, session_manager
from src.session.manager import TopicKey

logger = logging.getLogger(__name__)
router = Router(name="callbacks")

PREVIEW_CHARS_ON_OVERFLOW = 1800


def _split_preview_and_tail(text: str, head_chars: int = PREVIEW_CHARS_ON_OVERFLOW) -> tuple[str, str]:
    if len(text) <= head_chars:
        return text, ""
    return text[:head_chars], text[head_chars:]


async def _send_refresh_text(callback: CallbackQuery, key: TopicKey, text: str) -> None:
    if callback.message is None:
        return
    preview, overflow = _split_preview_and_tail(text)
    if overflow:
        await callback.message.answer(preview + "\n\n...(内容过长，剩余内容见txt附件)", reply_markup=control_keyboard())
        file_name = f"ai_refresh_full_{key.message_thread_id}_{datetime.utcnow().strftime('%Y%m%d_%H%M%S')}.txt"
        payload = BufferedInputFile(text.encode("utf-8"), filename=file_name)
        await callback.message.answer_document(payload, caption="📎 完整内容（txt）")
        return
    await callback.message.answer(preview, reply_markup=control_keyboard())


@router.callback_query(lambda c: c.data and c.data.startswith("model:"))
async def on_model_switch(callback: CallbackQuery) -> None:
    selected = callback.data.split(":", maxsplit=1)[1]
    if callback.message and callback.message.message_thread_id is not None:
        key = TopicKey(chat_id=callback.message.chat.id, message_thread_id=callback.message.message_thread_id)
        if selected == "auto":
            session_manager.set_topic_model_selection(key, mode="auto", model_name="", reason="user_button")
        else:
            session_manager.set_topic_model_selection(key, mode="manual", model_name=selected, reason="user_button")
    await callback.answer(f"✅ 已切换模型: {selected}")
    if callback.message:
        await callback.message.answer(f"📝 模型已设置: {selected}", reply_markup=control_keyboard())


@router.callback_query(lambda c: c.data and c.data == "control:stop")
async def on_stop(callback: CallbackQuery) -> None:
    if callback.message and callback.message.message_thread_id is not None:
        key = TopicKey(chat_id=callback.message.chat.id, message_thread_id=callback.message.message_thread_id)
        generation_control.stop(key.value)
    await callback.answer("⏹️ 已请求停止生成")


@router.callback_query(lambda c: c.data and c.data == "control:refresh")
async def on_refresh(callback: CallbackQuery) -> None:
    if callback.message and callback.message.message_thread_id is not None:
        from src.bot import runtime

        key = TopicKey(chat_id=callback.message.chat.id, message_thread_id=callback.message.message_thread_id)
        generation_control.stop(key.value)
        runtime.user_takeover_topics.add(key.value)

        snapshot = runtime.active_stream_snapshots.get(key.value)
        latest_text = snapshot.latest_render_text if snapshot is not None else ""
        if not latest_text:
            latest_assistant = session_manager.get_latest_assistant_content(key)
            latest_text = latest_assistant if latest_assistant else ""

        if not latest_text:
            await callback.answer("⚠️ 暂无可刷新的内容", show_alert=False)
            return

        try:
            await callback.message.delete()
        except Exception:  # noqa: BLE001
            logger.debug("refresh delete old message skipped", exc_info=True)

        runtime.active_stream_snapshots.pop(key.value, None)

        await _send_refresh_text(callback, key, latest_text)
        await callback.answer("🔄 已刷新并重发最新结果")
        return
    await callback.answer("❌ 无话题上下文")


@router.callback_query(lambda c: c.data and c.data == "control:summarize")
async def on_summarize(callback: CallbackQuery) -> None:
    if callback.message and callback.message.message_thread_id is not None:
        key = TopicKey(chat_id=callback.message.chat.id, message_thread_id=callback.message.message_thread_id)
        session_manager.enqueue_job(key=key, job_type="summarize", payload={"source": "button"})
        await callback.answer("📝 已开始生成本话题总结")
        await callback.message.answer("🧾 正在整理本话题的关键信息：结论、要点与待办事项。")
        return
    await callback.answer("❌ 无话题上下文")


@router.callback_query(lambda c: c.data and c.data == "control:rename_topic")
async def on_rename_topic(callback: CallbackQuery) -> None:
    if callback.message and callback.message.message_thread_id is not None:
        key = TopicKey(chat_id=callback.message.chat.id, message_thread_id=callback.message.message_thread_id)
        try:
            session_manager.enqueue_job(key=key, job_type="rename_topic", payload={"source": "button"})
        except Exception:  # noqa: BLE001
            logger.exception("enqueue rename_topic failed chat=%s thread=%s", key.chat_id, key.message_thread_id)
            await callback.answer("❌ 标题生成任务提交失败，请稍后重试")
            return
        await callback.answer("📝 已开始生成话题标题")
        await callback.message.answer("🏷️ 正在根据当前话题内容生成新标题。")
        return
    await callback.answer("❌ 无话题上下文")


@router.callback_query(lambda c: c.data and c.data == "control:clear")
async def on_clear_removed(callback: CallbackQuery) -> None:
    await callback.answer("该功能已移除", show_alert=False)
