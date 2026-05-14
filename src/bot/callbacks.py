from __future__ import annotations

import asyncio
import logging
import re

from aiogram import Router
from aiogram.exceptions import TelegramBadRequest, TelegramRetryAfter
from aiogram.types import CallbackQuery, InlineKeyboardMarkup

from src.bot.keyboards import control_keyboard
from src.bot.runtime import generation_control, session_manager
from src.config import settings
from src.session.manager import TopicKey

logger = logging.getLogger(__name__)
router = Router(name="callbacks")

TELEGRAM_CODE_FENCE_LANG_RE = re.compile(r"```[A-Za-z0-9_.+-]+[ \t]*\r?\n")


def _normalize_markdown_for_telegram(text: str) -> str:
    # Telegram legacy Markdown does not reliably support fenced code language hints (```python).
    # Strip language tags while preserving fenced blocks.
    return TELEGRAM_CODE_FENCE_LANG_RE.sub("```\n", text)


async def _safe_edit_markdown(
    callback_message: any,
    text: str,
    reply_markup: InlineKeyboardMarkup | None = None,
) -> None:
    """Edit a message with Markdown; falls back to plain text on parse error."""
    normalized_text = _normalize_markdown_for_telegram(text)
    keyboard = reply_markup if reply_markup is not None else control_keyboard()
    
    try:
        await callback_message.edit_text(normalized_text, reply_markup=keyboard, parse_mode="Markdown")
        return
    except TelegramBadRequest as exc:
        error_text = str(exc).lower()
        if "message is not modified" in error_text:
            return
        if "message_too_long" in error_text:
            return
        if "message to edit not found" in error_text or "message can't be edited" in error_text:
            return
        if "parse entities" not in error_text:
            raise
        try:
            await callback_message.edit_text(normalized_text, reply_markup=keyboard)
            return
        except TelegramBadRequest as fallback_exc:
            fallback_text = str(fallback_exc).lower()
            if "message is not modified" in fallback_text:
                return
            if "message_too_long" in fallback_text:
                return
            if "message to edit not found" in fallback_text or "message can't be edited" in fallback_text:
                return
            raise
    except TelegramRetryAfter:
        return  # Silently skip rate limit errors


async def _ensure_ai_permission(callback: CallbackQuery) -> bool:
    message = callback.message
    user_id = callback.from_user.id if callback.from_user else None

    if message is not None and message.chat.type == "private":
        if user_id is None or user_id not in settings.allowed_user_ids:
            await callback.answer("❌ 私聊无权限", show_alert=True)
            return False
        return True

    if message is not None and settings.allowed_chat_ids and message.chat.id in settings.allowed_chat_ids:
        return True

    if user_id is not None and user_id in settings.allowed_user_ids:
        return True

    await callback.answer("❌ 无权限使用 AI", show_alert=True)
    return False


@router.callback_query(lambda c: c.data and c.data.startswith("model:"))
async def on_model_switch(callback: CallbackQuery) -> None:
    if not await _ensure_ai_permission(callback):
        return
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
    if not await _ensure_ai_permission(callback):
        return
    if callback.message and callback.message.message_thread_id is not None:
        key = TopicKey(chat_id=callback.message.chat.id, message_thread_id=callback.message.message_thread_id)
        generation_control.stop(key.value)
    await callback.answer("⏹️ 已请求停止生成")


@router.callback_query(lambda c: c.data and c.data == "control:summarize")
async def on_summarize(callback: CallbackQuery) -> None:
    if not await _ensure_ai_permission(callback):
        return
    if callback.message and callback.message.message_thread_id is not None:
        key = TopicKey(chat_id=callback.message.chat.id, message_thread_id=callback.message.message_thread_id)
        session_manager.enqueue_job(key=key, job_type="summarize", payload={"source": "button"})
        await callback.answer("📝 已开始生成本话题总结")
        await callback.message.answer("🧾 正在整理本话题的关键信息：结论、要点与待办事项。")
        return
    await callback.answer("❌ 无话题上下文")


@router.callback_query(lambda c: c.data and c.data == "control:rename_topic")
async def on_rename_topic(callback: CallbackQuery) -> None:
    if not await _ensure_ai_permission(callback):
        return
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


@router.callback_query(lambda c: c.data and c.data == "control:refresh")
async def on_refresh(callback: CallbackQuery) -> None:
    if not await _ensure_ai_permission(callback):
        return
    
    if callback.message is None or callback.message.message_thread_id is None:
        await callback.answer("❌ 无话题上下文", show_alert=True)
        return
    
    from src.bot import runtime
    
    key = TopicKey(chat_id=callback.message.chat.id, message_thread_id=callback.message.message_thread_id)
    
    # 获取当前话题的流式快照或最后一条消息的完整文本
    latest_text = ""
    snapshot = runtime.active_stream_snapshots.get(key.value)
    
    if snapshot is not None and snapshot.assistant_message_id == callback.message.message_id:
        # 流式输出中的消息
        latest_text = snapshot.latest_answer_text or snapshot.latest_render_text or ""
    
    if not latest_text:
        # 从存储中获取消息的完整文本
        latest_text = runtime.assistant_full_text_by_message.get((key.chat_id, callback.message.message_id), "")
    
    if not latest_text:
        # 从数据库获取
        latest_text = session_manager.get_assistant_content_by_telegram_message_id(key, callback.message.message_id)
    
    if not latest_text:
        await callback.answer("⚠️ 无法获取消息内容", show_alert=True)
        return
    
    # 直接编辑消息
    try:
        await _safe_edit_markdown(callback.message, latest_text, reply_markup=control_keyboard())
        await callback.answer("✅ 消息已刷新")
    except Exception as exc:
        logger.exception("refresh message failed", exc_info=True)
        await callback.answer("❌ 刷新消息失败", show_alert=True)
