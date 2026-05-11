from __future__ import annotations

import asyncio
import logging
from datetime import datetime
from time import monotonic

from aiogram import F, Router
from aiogram.exceptions import TelegramBadRequest, TelegramRetryAfter
from aiogram.filters import Command
from aiogram.types import BufferedInputFile, BusinessMessagesDeleted, InlineKeyboardMarkup, Message

from src.bot.keyboards import control_keyboard, model_selection_keyboard, stop_only_keyboard
from src.bot.runtime import generation_control, model_router, provider, session_manager
from src.config import settings
from src.llm.reasoning import post_process_reasoning
from src.llm.tools import execute_builtin_tool
from src.routing.router import RouteResult
from src.session.manager import TopicKey

logger = logging.getLogger(__name__)
router = Router(name="handlers")

TELEGRAM_RENDER_LIMIT = 3500
PREVIEW_CHARS_ON_OVERFLOW = 1800


async def _safe_edit_markdown(
    message: Message,
    text: str,
    retry_on_flood: bool = False,
    reply_markup: InlineKeyboardMarkup | None = None,
) -> None:
    """Edit a message with Markdown; falls back to plain text on parse error.

    During streaming (retry_on_flood=False) rate-limit errors are silently
    skipped so the generator can continue.  For the final edit pass
    retry_on_flood=True to wait and retry up to a few times.
    """
    max_flood_attempts = 4 if retry_on_flood else 1
    keyboard = reply_markup if reply_markup is not None else control_keyboard()
    for flood_attempt in range(max_flood_attempts):
        try:
            await message.edit_text(text, reply_markup=keyboard, parse_mode="Markdown")
            return
        except TelegramRetryAfter as exc:
            if retry_on_flood and flood_attempt < max_flood_attempts - 1:
                await asyncio.sleep(exc.retry_after)
                continue
            return  # skip this update if not retrying or attempts exhausted
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
                await message.edit_text(text, reply_markup=keyboard)
                return
            except TelegramRetryAfter as fallback_retry_exc:
                if retry_on_flood and flood_attempt < max_flood_attempts - 1:
                    await asyncio.sleep(fallback_retry_exc.retry_after)
                    continue
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


def _compact_query_for_status(query: str, max_len: int = 60) -> str:
    compact = " ".join(query.split())
    if len(compact) <= max_len:
        return compact
    return compact[: max_len - 1] + "…"


def _format_elapsed(seconds: float) -> str:
    total_seconds = max(0, int(seconds))
    minutes, remaining_seconds = divmod(total_seconds, 60)
    if minutes > 0:
        return f"{minutes} m {remaining_seconds} s"
    return f"{remaining_seconds} s"


def _extract_command_argument(raw_text: str, command: str) -> str:
    if not raw_text:
        return ""
    stripped = raw_text.strip()
    if not stripped.startswith("/"):
        return ""
    first_token, _, remainder = stripped.partition(" ")
    token_base = first_token.split("@", maxsplit=1)[0].lower()
    if token_base != f"/{command}":
        return ""
    return remainder.strip()


def _private_topic_key_from_message(message: Message) -> TopicKey | None:
    user_id = message.from_user.id if message.from_user else None
    if user_id is None:
        return None
    return TopicKey(chat_id=user_id, message_thread_id=0)


async def _ensure_ai_permission(message: Message) -> bool:
    user_id = message.from_user.id if message.from_user else None
    if user_id is None or user_id not in settings.allowed_user_ids:
        await message.answer("❌ 无权限使用 AI（仅限 TELEGRAM_ALLOWED_USER_IDS 中的用户）")
        return False
    return True


def _is_private_chat(message: Message) -> bool:
    return message.chat.type == "private"


def _split_telegram_text(text: str, limit: int = 3500) -> list[str]:
    if len(text) <= limit:
        return [text]
    parts: list[str] = []
    start = 0
    while start < len(text):
        end = min(start + limit, len(text))
        split_at = text.rfind("\n", start, end)
        if split_at <= start:
            split_at = end
        parts.append(text[start:split_at].strip())
        start = split_at
    return [p for p in parts if p]


def _clip_stream_render(text: str) -> str:
    if len(text) <= TELEGRAM_RENDER_LIMIT:
        return text
    return _truncate_with_dynamic_omission(text, TELEGRAM_RENDER_LIMIT, "消息较长，仍在生成中")


def _truncate_with_dynamic_omission(text: str, limit: int, label: str) -> str:
    if len(text) <= limit:
        return text

    omitted_chars = max(0, len(text) - limit)
    for _ in range(4):
        suffix = f"\n\n...({label}...已省略{omitted_chars}字)"
        visible_len = max(0, limit - len(suffix))
        omitted_chars = max(0, len(text) - visible_len)

    suffix = f"\n\n...({label}...已省略{omitted_chars}字)"
    visible_len = max(0, limit - len(suffix))
    return text[:visible_len] + suffix


def _split_preview_and_tail(text: str, head_chars: int = PREVIEW_CHARS_ON_OVERFLOW) -> tuple[str, str]:
    if len(text) <= head_chars:
        return text, ""
    return text[:head_chars], text[head_chars:]


async def _send_tail_as_txt(message: Message, topic_key: TopicKey, tail_text: str) -> None:
    if not tail_text:
        return
    file_name = f"ai_reply_full_{topic_key.message_thread_id}_{datetime.utcnow().strftime('%Y%m%d_%H%M%S')}.txt"
    payload = BufferedInputFile(tail_text.encode("utf-8"), filename=file_name)
    await message.answer_document(payload, caption="📎 完整内容（txt）")


async def _send_refreshed_content(message: Message, topic_key: TopicKey, full_text: str) -> None:
    preview, overflow = _split_preview_and_tail(full_text)
    if overflow:
        preview_text = _truncate_with_dynamic_omission(full_text, PREVIEW_CHARS_ON_OVERFLOW, "消息较长，已经放入txt请查看txt文件")
        preview_msg = await message.answer(preview_text, reply_markup=control_keyboard())
        from src.bot import runtime
        runtime.assistant_full_text_by_message[(topic_key.chat_id, preview_msg.message_id)] = full_text
        await _send_tail_as_txt(message, topic_key, full_text)
        return
    preview_msg = await message.answer(preview, reply_markup=control_keyboard())
    from src.bot import runtime
    runtime.assistant_full_text_by_message[(topic_key.chat_id, preview_msg.message_id)] = full_text


async def _cleanup_new_topic_seed_messages(topic_key: TopicKey) -> None:
    from src.bot import runtime

    pair = runtime.pending_new_topic_cleanup.pop(topic_key.value, None)
    if not pair or runtime.bot is None:
        return
    new_cmd_msg_id, created_msg_id = pair
    for message_id in (new_cmd_msg_id, created_msg_id):
        try:
            await runtime.bot.delete_message(chat_id=topic_key.chat_id, message_id=message_id)
        except Exception:
            logger.debug(
                "skip delete seed message chat=%s thread=%s message_id=%s",
                topic_key.chat_id,
                topic_key.message_thread_id,
                message_id,
            )


def _extract_main_forum_mention_prompt(message: Message) -> str:
    from src.bot import runtime

    text = (message.text or "").strip()
    bot_username = runtime.bot_username.strip().lower()
    if not text or not bot_username:
        return ""

    mention = f"@{bot_username}"
    lowered = text.lower()
    idx = lowered.find(mention)
    if idx < 0:
        return ""

    prompt = (text[:idx] + text[idx + len(mention) :]).strip(" \t\n,:，：")
    return prompt


@router.message(Command("start"))
async def on_start(message: Message) -> None:
    await message.answer(
        "🤖 机器人已在线。\n\n"
        "在论坛群组中：\n"
        "1️⃣ 在主话题发送 /new 自动创建新话题\n"
        "2️⃣ 进入新话题后开始聊天\n"
        "3️⃣ 可用 /search 关键词 进行联网搜索\n\n"
        "按钮说明：\n"
        "- 对话总结：提炼当前话题的结论、要点和待办\n"
        "- 停止生成：中断当前回复"
    )


@router.message(Command("new"))
async def on_new(message: Message) -> None:
    from src.bot import runtime

    if not await _ensure_ai_permission(message):
        return

    if _is_private_chat(message):
        private_key = _private_topic_key_from_message(message)
        if private_key is None:
            await message.answer("❌ 无法识别用户身份，无法清空私聊记忆")
            return
        generation_control.stop(private_key.value)
        session_manager.get_or_create_topic(private_key)
        changed = session_manager.clear_topic_memory(private_key)
        runtime.active_stream_snapshots.pop(private_key.value, None)
        runtime.user_takeover_topics.discard(private_key.value)
        await message.answer(
            "🧹 私聊记忆已清空\n"
            f"- 清理消息: {changed.get('messages', 0)} 条\n"
            f"- 清理总结: {changed.get('summary', 0)} 条\n"
            f"- 清理检查点: {changed.get('checkpoints', 0)} 条\n"
            f"- 清理任务: {changed.get('jobs', 0)} 条"
        )
        return

    bot = runtime.bot
    # If in main forum, auto-create a new topic
    if message.message_thread_id is None or message.message_thread_id == 0:
        if bot is None:
            await message.answer("❌ 机器人初始化失败，请稍后重试")
            return
        try:
            # Create new forum topic
            topic = await bot.create_forum_topic(
                chat_id=message.chat.id,
                name="💬 新对话"
            )
            topic_id = topic.message_thread_id
            topic_key = TopicKey(chat_id=message.chat.id, message_thread_id=topic_id)
            session_manager.get_or_create_topic(topic_key)
            topic_link = f"https://t.me/c/{str(message.chat.id)[4:]}/{topic_id}"
            created_message_id = 0
            for attempt in range(4):
                try:
                    created_notice = await message.answer(
                        f"✅ 已创建新话题：[点我进入新话题]({topic_link})",
                        parse_mode="Markdown",
                        disable_web_page_preview=True,
                        reply_to_message_id=message.message_id,
                    )
                    created_message_id = created_notice.message_id
                    break
                except TelegramRetryAfter as exc:
                    if attempt < 3:
                        await asyncio.sleep(exc.retry_after)
                    else:
                        raise
            # Send initialization message in the new topic, not in the main forum
            for attempt in range(4):
                try:
                    await bot.send_message(
                        chat_id=message.chat.id,
                        message_thread_id=topic_id,
                        text="📌 新话题已创建，请选择此话题的模型模式：",
                        reply_markup=model_selection_keyboard()
                    )
                    break
                except TelegramRetryAfter as exc:
                    if attempt < 3:
                        await asyncio.sleep(exc.retry_after)
                    else:
                        raise
            if created_message_id:
                runtime.pending_new_topic_cleanup[topic_key.value] = (message.message_id, created_message_id)
        except Exception as e:
            await message.answer(f"❌ 创建话题失败: {e}")
        return

    # If already in a topic, just initialize session
    topic_key = TopicKey(chat_id=message.chat.id, message_thread_id=message.message_thread_id)
    session_manager.get_or_create_topic(topic_key)
    await message.answer("📌 请选择此话题的模型模式：", reply_markup=model_selection_keyboard())


@router.message(Command("stop"))
async def on_stop(message: Message) -> None:
    if message.message_thread_id is None or message.message_thread_id == 0:
        await message.answer("⚠️ 请在话题内运行此命令")
        return
    topic_key = TopicKey(chat_id=message.chat.id, message_thread_id=message.message_thread_id)
    generation_control.stop(topic_key.value)
    await message.answer("⏹️ 已请求停止本话题的生成")


@router.message(Command("re"))
async def on_refresh_by_command(message: Message) -> None:
    from src.bot import runtime

    if message.message_thread_id is None or message.message_thread_id == 0:
        await message.answer("⚠️ 请在论坛话题内使用 /re")
        return
    if message.reply_to_message is None:
        await message.answer("⚠️ 请引用一条 AI 消息后再发送 /re")
        return

    topic_key = TopicKey(chat_id=message.chat.id, message_thread_id=message.message_thread_id)
    replied_message_id = message.reply_to_message.message_id

    generation_control.stop(topic_key.value)
    runtime.user_takeover_topics.add(topic_key.value)

    latest_text = ""
    snapshot = runtime.active_stream_snapshots.get(topic_key.value)
    if snapshot is not None and snapshot.assistant_message_id == replied_message_id:
        latest_text = snapshot.latest_answer_text or snapshot.latest_render_text or ""
    if not latest_text:
        latest_text = runtime.assistant_full_text_by_message.get((topic_key.chat_id, replied_message_id), "")
    if not latest_text:
        latest_text = session_manager.get_assistant_content_by_telegram_message_id(topic_key, replied_message_id)

    if not latest_text:
        await message.answer("⚠️ 你引用的不是可刷新的 AI 消息")
        return

    if snapshot is not None and snapshot.assistant_message_id == replied_message_id and runtime.bot is not None:
        try:
            await runtime.bot.delete_message(chat_id=topic_key.chat_id, message_id=replied_message_id)
        except Exception:
            logger.debug("/re delete target message skipped", exc_info=True)
        runtime.active_stream_snapshots.pop(topic_key.value, None)

    await _send_refreshed_content(message, topic_key, latest_text)


@router.message(Command("models"))
async def on_models(message: Message) -> None:
    if not await _ensure_ai_permission(message):
        return
    lines = ["📚 可用模型列表：\n"]
    for model in settings.model_catalog:
        lines.append(f"• {model.id}: {model.label}")
    await message.answer("\n".join(lines))


@router.message(Command("model"))
async def on_model(message: Message) -> None:
    if not await _ensure_ai_permission(message):
        return
    if not _is_private_chat(message):
        await message.answer("⚠️ /model 仅支持私聊。群话题请使用按钮切换模型。")
        return

    private_key = _private_topic_key_from_message(message)
    if private_key is None:
        await message.answer("❌ 无法识别用户身份")
        return
    session_manager.get_or_create_topic(private_key)

    raw = (message.text or "").strip()
    selected = _extract_command_argument(raw, "model")
    if not selected:
        mode, selected_model = session_manager.get_topic_model_selection(private_key)
        current = selected_model if mode == "manual" and selected_model else "auto"
        await message.answer(
            "🧭 用法：/model auto 或 /model 模型ID\n"
            f"当前私聊模型：{current}"
        )
        return

    candidate = selected.strip()
    if candidate == "auto":
        session_manager.set_topic_model_selection(private_key, mode="auto", model_name="", reason="user_private_command")
        await message.answer("✅ 私聊模型已切换为自动路由")
        return

    model_ids = {m.id for m in settings.model_catalog if "tts" not in m.tags}
    if candidate not in model_ids:
        await message.answer("❌ 模型不存在，请先使用 /models 查看可用模型")
        return

    session_manager.set_topic_model_selection(private_key, mode="manual", model_name=candidate, reason="user_private_command")
    await message.answer(f"✅ 私聊模型已切换为: {candidate}")


@router.message(Command("search"))
async def on_search(message: Message) -> None:
    if not await _ensure_ai_permission(message):
        return
    raw = (message.text or "").strip()
    query = _extract_command_argument(raw, "search")
    if not query:
        await message.answer("🔎 用法：/search 关键词 或 /search 关键词 | 结果数(1-10)")
        return
    display_query = _compact_query_for_status(query)
    await message.answer(f"🔎 正在联网搜索：{display_query}")
    result = await execute_builtin_tool("search", query)
    if result.startswith("tool_error:"):
        await message.answer(f"❌ 联网搜索失败：{result}")
        return
    await message.answer(f"✅ 联网搜索完成：{display_query}")
    for idx, part in enumerate(_split_telegram_text(result), start=1):
        prefix = f"📄 搜索结果（第 {idx} 段）\n\n" if len(result) > 3500 else "📄 搜索结果\n\n"
        await message.answer(prefix + part)


# ---------------------------------------------------------------------------
# /ping helpers
# ---------------------------------------------------------------------------

def _build_ping_text(
    models: list,
    results: dict[str, bool | None],
    finished: bool = False,
) -> str:
    """Build a clean progress/result text for /ping."""
    total = len(models)
    done_count = sum(1 for v in results.values() if v is not None)

    if finished:
        header_line = f"✅ 模型测试完成 [{done_count}/{total}]"
    else:
        header_line = f"🔍 正在测试模型连通性 [{done_count}/{total}]"

    lines = [header_line, ""]
    for model in models:
        status = results.get(model.id)
        if status is True:
            icon = "✅"
        elif status is False:
            icon = "❌"
        else:
            # Find the first untested model – that's the one currently running
            untested = [m for m in models if results.get(m.id) is None]
            icon = "⏳" if untested and untested[0].id == model.id else "⬜"
        lines.append(f"{icon} {model.label}")

    if finished:
        failed_count = sum(1 for ok in results.values() if ok is False)
        lines.append("")
        if failed_count:
            lines.append(f"已标记 {failed_count} 个暂不可用模型，可重新运行 /ping 重新检测状态")
        else:
            lines.append("所有模型均可用 ✅")
    return "\n".join(lines)


async def _ping_single_model(model_id: str, timeout: float = 30.0) -> bool:
    """Send a minimal prompt to a model and return True if it responds."""
    try:
        async def _collect() -> str:
            parts: list[str] = []
            async for chunk in provider.stream_generate(prompt="hi", model=model_id):
                parts.append(chunk)
            return "".join(parts)

        response = await asyncio.wait_for(_collect(), timeout=timeout)
        stripped = response.strip()
        # Three conditions for a "live" response:
        # 1. non-empty, 2. not the echo fallback (no API key configured),
        # 3. not an upstream HTTP error message returned by the provider.
        return bool(stripped) and not stripped.startswith("[model=") and "上游接口请求失败" not in stripped
    except Exception:
        return False


@router.message(Command("ping"))
async def on_ping(message: Message) -> None:
    # Allowed only in private chat or the default (non-thread) topic.
    is_private = message.chat.type == "private"
    is_default_topic = message.message_thread_id is None or message.message_thread_id == 0

    if not (is_private or is_default_topic):
        await message.answer("⚠️ /ping 只能在私聊或群组默认话题中使用")
        return

    if not await _ensure_ai_permission(message):
        return

    models = [m for m in settings.model_catalog if "tts" not in m.tags]
    if not models:
        await message.answer("❌ 没有可测试的模型")
        return

    results: dict[str, bool | None] = {m.id: None for m in models}
    status_msg = await message.answer(_build_ping_text(models, results))

    for model in models:
        ok = await _ping_single_model(model.id)
        results[model.id] = ok
        try:
            await status_msg.edit_text(_build_ping_text(models, results))
        except (TelegramBadRequest, TelegramRetryAfter):
            pass

    # Update the global failed set used by model_selection_keyboard.
    # We import the module (not the name) so the assignment mutates the
    # actual module-level variable rather than a stale local binding.
    from src.bot import runtime as _rt
    _rt.failed_ping_models = {m_id for m_id, ok in results.items() if ok is False}

    try:
        await status_msg.edit_text(_build_ping_text(models, results, finished=True))
    except (TelegramBadRequest, TelegramRetryAfter):
        pass


@router.message(F.text)
async def on_text(message: Message) -> None:
    if not await _ensure_ai_permission(message):
        return

    is_private = _is_private_chat(message)
    is_default_topic = message.message_thread_id is None or message.message_thread_id == 0
    if not is_private and settings.allowed_chat_ids and message.chat.id not in settings.allowed_chat_ids:
        await message.answer("❌ 此群组未被授权使用此机器人")
        return

    if is_private:
        incoming_text = (message.text or "").strip()
        if not incoming_text:
            return
        topic_key = _private_topic_key_from_message(message)
        if topic_key is None:
            await message.answer("❌ 无法识别用户身份")
            return
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
        await _run_generation(
            message=message,
            topic_key=topic_key,
            prompt_for_model=incoming_text,
            route=route,
            reply_markup=stop_only_keyboard(),
        )
        return

    if is_default_topic:
        prompt = _extract_main_forum_mention_prompt(message)
        if not prompt:
            return

        topic_key = TopicKey(chat_id=message.chat.id, message_thread_id=0)
        session_manager.get_or_create_topic(topic_key)
        session_manager.append_message(
            key=topic_key,
            telegram_message_id=message.message_id,
            role="user",
            content=prompt,
        )

        route = RouteResult(mode="manual", model=settings.auto_simple_model_id, reason="主话题 @bot 默认模型")
        await _run_generation(
            message=message,
            topic_key=topic_key,
            prompt_for_model=prompt,
            route=route,
            context_message_limit=15,
            reply_markup=stop_only_keyboard(),
        )
        return

    incoming_text = message.text or ""
    topic_key = TopicKey(chat_id=message.chat.id, message_thread_id=message.message_thread_id)
    session_manager.get_or_create_topic(topic_key)
    await _cleanup_new_topic_seed_messages(topic_key)
    session_manager.append_message(
        key=topic_key,
        telegram_message_id=message.message_id,
        role="user",
        content=incoming_text,
    )

    mode, selected_model = session_manager.get_topic_model_selection(topic_key)
    requested_mode = selected_model if mode == "manual" and selected_model else settings.default_model_mode
    route = model_router.route(incoming_text, mode=requested_mode)

    await _run_generation(
        message=message,
        topic_key=topic_key,
        prompt_for_model=incoming_text,
        route=route,
    )


@router.message(F.photo)
async def on_photo(message: Message) -> None:
    if not await _ensure_ai_permission(message):
        return
    if message.message_thread_id is None or message.message_thread_id == 0:
        return
    if settings.allowed_chat_ids and message.chat.id not in settings.allowed_chat_ids:
        await message.answer("❌ 此群组未被授权使用此机器人")
        return

    caption = (message.caption or "").strip()
    # Use caption as the prompt text; fall back to a generic placeholder.
    prompt_text = caption if caption else "请描述这张图片"

    topic_key = TopicKey(chat_id=message.chat.id, message_thread_id=message.message_thread_id)
    session_manager.get_or_create_topic(topic_key)
    await _cleanup_new_topic_seed_messages(topic_key)
    # Store the caption (or placeholder) as the user message in history.
    session_manager.append_message(
        key=topic_key,
        telegram_message_id=message.message_id,
        role="user",
        content=f"[图片] {prompt_text}",
    )

    # Download the highest-resolution photo available.
    photo = message.photo[-1]
    image_bytes: bytes | None = None
    image_mime_type = "image/jpeg"
    try:
        file = await message.bot.get_file(photo.file_id)
        downloaded = await message.bot.download_file(file.file_path)
        raw = downloaded.read() if downloaded is not None else None
        if raw:
            image_bytes = raw
            # Detect MIME type from file extension (Telegram usually serves JPEG).
            path_lower = (file.file_path or "").lower()
            if path_lower.endswith(".png"):
                image_mime_type = "image/png"
            elif path_lower.endswith(".webp"):
                image_mime_type = "image/webp"
            else:
                image_mime_type = "image/jpeg"
    except Exception as exc:  # noqa: BLE001
        logger.warning("Failed to download photo: %s", exc)
        await message.answer("⚠️ 图片下载失败，将仅使用文字内容回复")

    mode, selected_model = session_manager.get_topic_model_selection(topic_key)
    requested_mode = selected_model if mode == "manual" and selected_model else settings.default_model_mode
    # Route based on caption; default to the vision-capable simple model.
    route = model_router.route(prompt_text, mode=requested_mode)

    await _run_generation(
        message=message,
        topic_key=topic_key,
        prompt_for_model=prompt_text,
        route=route,
        image_bytes=image_bytes,
        image_mime_type=image_mime_type,
    )


async def _run_generation(
    message: Message,
    topic_key: TopicKey,
    prompt_for_model: str,
    route: RouteResult,
    image_bytes: bytes | None = None,
    image_mime_type: str = "image/jpeg",
    context_message_limit: int | None = None,
    reply_markup: InlineKeyboardMarkup | None = None,
) -> None:
    from src.bot import runtime

    runtime.user_takeover_topics.discard(topic_key.value)

    def _taken_over_by_user() -> bool:
        return topic_key.value in runtime.user_takeover_topics

    active_reply_markup = reply_markup if reply_markup is not None else control_keyboard()
    is_topic_controls = reply_markup is None
    started_at = monotonic()
    header = f"🤖 模型: {route.model}\n📋 原因: {route.reason}\n\n"
    try:
        sent = await message.answer(
            header + f"⏳ 思考中... {_format_elapsed(monotonic() - started_at)}",
            reply_markup=active_reply_markup,
            parse_mode="Markdown",
        )
    except TelegramBadRequest:
        sent = await message.answer(
            header + f"⏳ 思考中... {_format_elapsed(monotonic() - started_at)}",
            reply_markup=active_reply_markup,
        )

    context_text = session_manager.collect_context_for_response(topic_key, max_messages=context_message_limit)
    generation_control.begin(topic_key.value)

    runtime.active_stream_snapshots[topic_key.value] = runtime.ActiveStreamSnapshot(
        chat_id=message.chat.id,
        message_thread_id=topic_key.message_thread_id,
        assistant_message_id=sent.message_id,
        latest_render_text=header + f"⏳ 思考中... {_format_elapsed(monotonic() - started_at)}",
        latest_answer_text="",
        is_topic_controls=is_topic_controls,
    )

    tool_status = ""
    tool_call_count = 0

    def _current_stream_render() -> str:
        parts: list[str] = [header]
        if tool_status:
            parts.append(tool_status + "\n\n")
        parts.append(built_answer or f"⏳ 思考中... {_format_elapsed(monotonic() - started_at)}")
        return _clip_stream_render("".join(parts))

    async def _tool_event_to_chat(event: str) -> None:
        nonlocal tool_status, tool_call_count
        parts = event.split(":", maxsplit=2)
        action = parts[0] if parts else ""
        tool_name = parts[1].strip() if len(parts) > 1 else ""
        tool_query = parts[2].strip() if len(parts) > 2 else ""
        display_query = _compact_query_for_status(tool_query) if tool_query else ""
        if action == "start":
            tool_call_count += 1
            if tool_name == "search":
                if display_query:
                    tool_status = f"🛠️ 正在联网搜索：{display_query}"
                else:
                    tool_status = "🛠️ 正在调用工具：联网搜索..."
            elif tool_name in {"", "undefined", "unknown_tool"}:
                tool_status = "🛠️ 正在调用工具：未知工具"
            elif tool_name == "fetch_webpage":
                tool_status = "🛠️ 正在抓取网页内容..."
            else:
                tool_status = f"🛠️ 正在调用工具：{tool_name}"
            current = _current_stream_render()
            snapshot = runtime.active_stream_snapshots.get(topic_key.value)
            if snapshot is not None:
                snapshot.latest_render_text = current
            if _taken_over_by_user():
                return
            await _safe_edit_markdown(sent, current, reply_markup=active_reply_markup)
        elif action == "done":
            if tool_name == "search":
                if display_query:
                    tool_status = f"✅ 联网搜索完成（{display_query}），正在生成最终回答..."
                else:
                    tool_status = "✅ 联网搜索完成，正在生成最终回答..."
            elif tool_name == "fetch_webpage":
                tool_status = "✅ 网页抓取完成，正在生成最终回答..."
            elif tool_name in {"", "undefined", "unknown_tool"}:
                tool_status = "✅ 工具调用完成，正在生成最终回答..."
            else:
                tool_status = f"✅ 工具 {tool_name} 调用完成，正在生成最终回答..."
            current = _current_stream_render()
            snapshot = runtime.active_stream_snapshots.get(topic_key.value)
            if snapshot is not None:
                snapshot.latest_render_text = current
            if _taken_over_by_user():
                return
            await _safe_edit_markdown(sent, current, reply_markup=active_reply_markup)

    last_edit = monotonic()
    built_answer = ""
    stop_reason = "completed"
    thinking_timer_stop = asyncio.Event()

    async def _thinking_timer_loop() -> None:
        while not thinking_timer_stop.is_set():
            if built_answer:
                break
            try:
                await asyncio.wait_for(thinking_timer_stop.wait(), timeout=1)
            except asyncio.TimeoutError:
                pass
            if thinking_timer_stop.is_set() or built_answer:
                break
            current = _current_stream_render()
            snapshot = runtime.active_stream_snapshots.get(topic_key.value)
            if snapshot is not None:
                snapshot.latest_render_text = current
            if _taken_over_by_user():
                break
            await _safe_edit_markdown(sent, current, reply_markup=active_reply_markup)

    thinking_timer_task = asyncio.create_task(_thinking_timer_loop())
    try:
        async for chunk in provider.stream_generate(
            prompt=prompt_for_model,
            model=route.model,
            context_messages=context_text,
            on_tool_event=_tool_event_to_chat,
            image_bytes=image_bytes,
            image_mime_type=image_mime_type,
        ):
            if generation_control.should_stop(topic_key.value):
                stop_reason = "user_stop"
                break
            if _taken_over_by_user():
                stop_reason = "user_takeover"
                break
            built_answer += chunk
            snapshot = runtime.active_stream_snapshots.get(topic_key.value)
            if snapshot is not None:
                snapshot.latest_answer_text = built_answer
            now = monotonic()
            if now - last_edit >= settings.stream_edit_interval_seconds:
                current = _current_stream_render()
                snapshot = runtime.active_stream_snapshots.get(topic_key.value)
                if snapshot is not None:
                    snapshot.latest_render_text = current
                if _taken_over_by_user():
                    stop_reason = "user_takeover"
                    break
                await _safe_edit_markdown(sent, current, reply_markup=active_reply_markup)
                last_edit = now
    except Exception as exc:  # noqa: BLE001
        err_text = str(exc)
        should_fallback = (
            (
                "No available channel for model" in err_text
                or "HTTP 503" in err_text
                or "RemoteProtocolError" in err_text
                or "ReadTimeout" in err_text
                or "上游连接异常" in err_text
            )
            and settings.auto_simple_model_id
            and settings.auto_simple_model_id != route.model
        )
        if should_fallback:
            fallback_model = settings.auto_simple_model_id
            header = (
                f"🤖 模型: {route.model}\n"
                f"📋 原因: {route.reason}\n"
                f"⚠️ 当前模型通道不可用，已自动切换到: {fallback_model}\n\n"
            )
            built_answer = ""
            stop_reason = "completed"
            try:
                async for chunk in provider.stream_generate(
                    prompt=prompt_for_model,
                    model=fallback_model,
                    context_messages=context_text,
                    on_tool_event=_tool_event_to_chat,
                    image_bytes=image_bytes,
                    image_mime_type=image_mime_type,
                ):
                    if generation_control.should_stop(topic_key.value):
                        stop_reason = "user_stop"
                        break
                    if _taken_over_by_user():
                        stop_reason = "user_takeover"
                        break
                    built_answer += chunk
                    snapshot = runtime.active_stream_snapshots.get(topic_key.value)
                    if snapshot is not None:
                        snapshot.latest_answer_text = built_answer
                    now = monotonic()
                    if now - last_edit >= settings.stream_edit_interval_seconds:
                        current = _current_stream_render()
                        snapshot = runtime.active_stream_snapshots.get(topic_key.value)
                        if snapshot is not None:
                            snapshot.latest_render_text = current
                        if _taken_over_by_user():
                            stop_reason = "user_takeover"
                            break
                        await _safe_edit_markdown(sent, current, reply_markup=active_reply_markup)
                        last_edit = now
            except Exception as fallback_exc:  # noqa: BLE001
                stop_reason = "error"
                built_answer += f"\n\n❌ 错误: {type(fallback_exc).__name__}: {fallback_exc}"
        else:
            stop_reason = "error"
            built_answer += f"\n\n❌ 错误: {type(exc).__name__}: {exc}"
    finally:
        thinking_timer_stop.set()
        await thinking_timer_task
        generation_control.end(topic_key.value)

    reasoning_text = "route_decision -> stream_generate"
    reasoning_raw = provider._last_reasoning_content or reasoning_text
    reasoning = post_process_reasoning(settings.reasoning_mode, reasoning_raw)
    final_text = built_answer or "(无响应)"
    if stop_reason == "user_stop":
        final_text += "\n\n⏹️ 已停止"
    elif stop_reason == "error":
        final_text += "\n\n（含错误）"

    total_elapsed = _format_elapsed(monotonic() - started_at)
    stats_text = f"⌛️ 用时：{total_elapsed}\n⚒️ 调用工具：{tool_call_count} 次\n\n"
    final_render = header + stats_text + final_text
    snapshot = runtime.active_stream_snapshots.get(topic_key.value)
    if snapshot is not None:
        snapshot.latest_answer_text = final_text
        snapshot.latest_render_text = final_render
    if _taken_over_by_user():
        runtime.active_stream_snapshots.pop(topic_key.value, None)
        return

    preview, overflow = _split_preview_and_tail(final_text)
    if overflow:
        preview_body = _truncate_with_dynamic_omission(final_text, PREVIEW_CHARS_ON_OVERFLOW, "消息较长，已经放入txt请查看txt文件")
        short_render = header + stats_text + preview_body
        await _safe_edit_markdown(sent, short_render, retry_on_flood=True, reply_markup=active_reply_markup)
        await _send_tail_as_txt(message, topic_key, final_text)
    else:
        await _safe_edit_markdown(sent, final_render, retry_on_flood=True, reply_markup=active_reply_markup)

    runtime.active_stream_snapshots.pop(topic_key.value, None)
    runtime.user_takeover_topics.discard(topic_key.value)
    runtime.assistant_full_text_by_message[(topic_key.chat_id, sent.message_id)] = final_text
    session_manager.append_message(
        key=topic_key,
        telegram_message_id=sent.message_id,
        role="assistant",
        content=final_text,
        reasoning=reasoning_raw,
    )
    msg_count = session_manager.topic_message_count(topic_key)
    if msg_count == 2:
        session_manager.enqueue_job(topic_key, job_type="rename_topic", payload={"source": "auto"})
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
