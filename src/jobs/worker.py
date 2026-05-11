from __future__ import annotations

import asyncio
import json
import logging

from aiogram import Bot

from src.bot.runtime import provider, session_manager
from src.config import settings

logger = logging.getLogger(__name__)


async def run_job_worker(bot: Bot) -> None:
    while True:
        try:
            claimed = session_manager.claim_next_pending_job()
            if not claimed:
                await asyncio.sleep(1.0)
                continue
            job_id, topic_id, job_type, payload = claimed
            try:
                await _execute_job(bot=bot, job_id=job_id, topic_id=topic_id, job_type=job_type, payload=payload)
            except Exception:  # noqa: BLE001
                session_manager.fail_job(job_id)
                raise
            else:
                session_manager.finish_job(job_id)
        except Exception:  # noqa: BLE001
            logger.exception("Job worker tick failed")
            await asyncio.sleep(1.0)


async def _execute_job(bot: Bot, job_id: int, topic_id: int, job_type: str, payload: dict[str, object]) -> None:
    key = session_manager.topic_key_by_topic_id(topic_id)
    if key is None:
        return

    if job_type == "summarize":
        context = session_manager.collect_context_for_summary(key, max_messages=40)
        if not context.strip():
            await bot.send_message(
                chat_id=key.chat_id,
                message_thread_id=key.message_thread_id,
                text="🧾 当前话题还没有足够内容可总结。",
            )
            return
        prompt = (
            "请将以下对话提炼为结构化 JSON，并且只输出 JSON，不要输出任何额外文字。"
            "JSON schema: "
            "{\"summary\":string,\"user_goals\":string[],\"requirements\":string[],\"decisions\":string[],"
            "\"constraints\":string[],\"important_details\":string[],\"open_tasks\":string[]}。\n\n"
            + context
        )
        result = await provider.generate(prompt=prompt, model=settings.auto_reasoning_model_id)
        raw = result.final_text.strip()
        summary_text = raw
        summary_json = ""
        try:
            parsed = json.loads(raw)
            if isinstance(parsed, dict):
                summary_json = json.dumps(parsed, ensure_ascii=False)
                summary_text = str(parsed.get("summary") or "").strip() or summary_text
        except json.JSONDecodeError:
            summary_json = json.dumps(
                {
                    "summary": summary_text,
                    "user_goals": [],
                    "requirements": [],
                    "decisions": [],
                    "constraints": [],
                    "important_details": [],
                    "open_tasks": [],
                },
                ensure_ascii=False,
            )

        session_manager.set_topic_summary(key, summary_text)
        session_manager.set_scope_summary_json(key, summary_json)
        if summary_text:
            await bot.send_message(
                chat_id=key.chat_id,
                message_thread_id=key.message_thread_id,
                text=f"🧾 本话题总结：\n\n{summary_text}",
            )
        else:
            await bot.send_message(
                chat_id=key.chat_id,
                message_thread_id=key.message_thread_id,
                text="⚠️ 总结生成完成，但未返回可展示内容，请稍后重试。",
            )
        return

    if job_type == "rename_topic":
        context = session_manager.collect_context_for_summary(key, max_messages=12)
        if not context.strip():
            return
        prompt = "为该话题生成一个简洁中文标题，12字以内，不要标点。\n\n" + context
        result = await provider.generate(prompt=prompt, model=settings.auto_simple_model_id)
        title = result.final_text.strip().splitlines()[0][:12]
        if title:
            session_manager.set_topic_title(key, title)
            try:
                await bot.edit_forum_topic(chat_id=key.chat_id, message_thread_id=key.message_thread_id, name=title)
            except Exception:  # noqa: BLE001
                logger.warning("edit_forum_topic failed for chat=%s thread=%s", key.chat_id, key.message_thread_id)
        return

    if job_type == "delete_sync":
        message_id = int(payload.get("telegram_message_id", 0)) if payload else 0
        if message_id <= 0:
            return
        session_manager.mark_deleted(key, message_id)
        return

    logger.info("skip unknown job type=%s job_id=%s", job_type, job_id)
