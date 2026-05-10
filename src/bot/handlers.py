from __future__ import annotations

import logging
from time import monotonic

from aiogram import F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command
from aiogram.types import BusinessMessagesDeleted, Message

from src.bot.keyboards import control_keyboard, model_selection_keyboard
from src.bot.runtime import generation_control, model_router, provider, session_manager
from src.config import settings
from src.llm.reasoning import post_process_reasoning
from src.llm.tools import execute_builtin_tool
from src.session.manager import TopicKey

logger = logging.getLogger(__name__)
router = Router(name="handlers")


async def _safe_edit_markdown(message: Message, text: str) -> None:
    try:
        await message.edit_text(text, reply_markup=control_keyboard(), parse_mode="Markdown")
    except TelegramBadRequest as exc:
        error_text = str(exc).lower()
        if "message is not modified" in error_text:
            return
        if "parse entities" not in error_text:
            raise
        try:
            await message.edit_text(text, reply_markup=control_keyboard())
        except TelegramBadRequest as fallback_exc:
            if "message is not modified" in str(fallback_exc).lower():
                return
            raise


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
    from src.bot.runtime import bot
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
            await message.answer(
                f"✅ 已创建新话题：[点我进入新话题]({topic_link})",
                parse_mode="Markdown",
                disable_web_page_preview=True,
            )
            # Send initialization message in the new topic, not in the main forum
            await bot.send_message(
                chat_id=message.chat.id,
                message_thread_id=topic_id,
                text="📌 新话题已创建，请选择此话题的模型模式：",
                reply_markup=model_selection_keyboard()
            )
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


@router.message(Command("models"))
async def on_models(message: Message) -> None:
    lines = ["📚 可用模型列表：\n"]
    for model in settings.model_catalog:
        lines.append(f"• {model.id}: {model.label}")
    await message.answer("\n".join(lines))


@router.message(Command("search"))
async def on_search(message: Message) -> None:
    raw = (message.text or "").strip()
    query = raw[len("/search") :].strip() if raw.startswith("/search") else ""
    if not query:
        await message.answer("🔎 用法：/search 关键词 或 /search 关键词 | 结果数(1-10)")
        return
    await message.answer("🔎 正在搜索，请稍候...")
    result = await execute_builtin_tool("search", query)
    await message.answer(f"🔎 搜索结果：\n\n{result}")


@router.message(F.text)
async def on_text(message: Message) -> None:
    if message.message_thread_id is None or message.message_thread_id == 0:
        await message.answer("⚠️ 请在论坛话题内聊天")
        return
    if settings.allowed_chat_ids and message.chat.id not in settings.allowed_chat_ids:
        await message.answer("❌ 此群组未被授权使用此机器人")
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

    prompt_for_model = incoming_text

    header = f"🤖 模型: {route.model}\n📋 原因: {route.reason}\n\n"
    try:
        sent = await message.answer(header + "⏳ 思考中...", reply_markup=control_keyboard(), parse_mode="Markdown")
    except TelegramBadRequest:
        sent = await message.answer(header + "⏳ 思考中...", reply_markup=control_keyboard())

    context_text = session_manager.collect_context_for_response(topic_key)
    generation_control.begin(topic_key.value)

    tool_status = ""

    def _current_stream_render() -> str:
        parts: list[str] = [header]
        if tool_status:
            parts.append(tool_status + "\n\n")
        parts.append(built_answer or "⏳ 思考中...")
        return "".join(parts)

    async def _tool_event_to_chat(event: str) -> None:
        nonlocal tool_status
        if event.startswith("start:"):
            tool_name = event.split(":", maxsplit=1)[1].strip()
            if tool_name == "search":
                tool_status = "🛠️ 正在调用工具：联网搜索..."
            elif tool_name in {"", "undefined", "unknown_tool"}:
                tool_status = "🛠️ 正在调用工具：未知工具"
            else:
                tool_status = f"🛠️ 正在调用工具：{tool_name}"
            await _safe_edit_markdown(sent, _current_stream_render())
        elif event.startswith("done:"):
            tool_name = event.split(":", maxsplit=1)[1].strip()
            if tool_name == "search":
                tool_status = "✅ 联网搜索完成，正在生成最终回答..."
            elif tool_name in {"", "undefined", "unknown_tool"}:
                tool_status = "✅ 工具调用完成，正在生成最终回答..."
            else:
                tool_status = f"✅ 工具 {tool_name} 调用完成，正在生成最终回答..."
            await _safe_edit_markdown(sent, _current_stream_render())

    last_edit = monotonic()
    built_answer = ""
    stop_reason = "completed"
    try:
        async for chunk in provider.stream_generate(
            prompt=prompt_for_model,
            model=route.model,
            context_messages=context_text,
            on_tool_event=_tool_event_to_chat,
        ):
            if generation_control.should_stop(topic_key.value):
                stop_reason = "user_stop"
                break
            built_answer += chunk
            now = monotonic()
            if now - last_edit >= settings.stream_edit_interval_seconds:
                await _safe_edit_markdown(sent, _current_stream_render())
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
                ):
                    if generation_control.should_stop(topic_key.value):
                        stop_reason = "user_stop"
                        break
                    built_answer += chunk
                    now = monotonic()
                    if now - last_edit >= settings.stream_edit_interval_seconds:
                        await _safe_edit_markdown(sent, _current_stream_render())
                        last_edit = now
            except Exception as fallback_exc:  # noqa: BLE001
                stop_reason = "error"
                built_answer += f"\n\n❌ 错误: {type(fallback_exc).__name__}: {fallback_exc}"
        else:
            stop_reason = "error"
            built_answer += f"\n\n❌ 错误: {type(exc).__name__}: {exc}"
    finally:
        generation_control.end(topic_key.value)

    reasoning_text = "route_decision -> stream_generate"
    reasoning_raw = provider._last_reasoning_content or reasoning_text
    reasoning = post_process_reasoning(settings.reasoning_mode, reasoning_raw)
    final_text = built_answer or "(无响应)"
    if stop_reason == "user_stop":
        final_text += "\n\n⏹️ 已停止"
    elif stop_reason == "error":
        final_text += "\n\n（含错误）"

    final_render = header + final_text

    await _safe_edit_markdown(sent, final_render)
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
