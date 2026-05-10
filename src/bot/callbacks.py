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
    await callback.answer(f"✅ 已切换模型: {selected}")
    if callback.message:
        await callback.message.answer(f"📝 模型已设置: {selected}", reply_markup=control_keyboard())


@router.callback_query(lambda c: c.data and c.data == "control:stop")
async def on_stop(callback: CallbackQuery) -> None:
    if callback.message and callback.message.message_thread_id is not None:
        key = TopicKey(chat_id=callback.message.chat.id, message_thread_id=callback.message.message_thread_id)
        generation_control.stop(key.value)
    await callback.answer("⏹️ 已请求停止生成")


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
