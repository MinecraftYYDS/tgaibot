from __future__ import annotations

import asyncio
import base64
import json
import logging
import os
from collections.abc import Awaitable, Callable
from collections.abc import AsyncIterator
from dataclasses import dataclass
from threading import Lock

import httpx

from src.config import settings
from src.model_catalog import resolve_model
from src.llm.tools import execute_builtin_tool, execute_builtin_tool_with_context, try_extract_tool_intent

logger = logging.getLogger(__name__)


@dataclass
class GenerationResult:
    final_text: str
    reasoning: str


class LLMProvider:
    def __init__(self) -> None:
        self._rr_lock = Lock()
        self._rr_index_by_pool: dict[str, int] = {}
        self._last_reasoning_content: str = ""

    async def stream_generate(
        self,
        prompt: str,
        model: str,
        context_messages: list[dict] | None = None,
        on_tool_event: Callable[[str], Awaitable[None]] | None = None,
        image_bytes: bytes | None = None,
        image_mime_type: str = "image/jpeg",
        tool_context: dict[str, object] | None = None,
    ) -> AsyncIterator[str]:
        tool_intent = try_extract_tool_intent(prompt)
        if tool_intent is not None:
            tool_name, argument = tool_intent
            tool_output = await execute_builtin_tool(tool_name, argument)
            for chunk in self._chunk_text(f"Tool {tool_name} result: {tool_output}"):
                yield chunk
            return

        profile = resolve_model(settings.model_catalog, model)
        if profile is not None and profile.provider == "openai_compatible":
            if "tts" in profile.tags:
                raise ValueError(f"模型 {profile.id} 属于 TTS 语音模型，不能用于文本聊天。请切换到对话模型。")
            api_key = self._pick_api_key(profile.api_key_env)
            if api_key:
                if on_tool_event is not None:
                    final_text = await self._generate_with_tool_calls(
                        prompt=prompt,
                        profile_model_name=profile.model_name,
                        profile_base_url=profile.base_url,
                        api_key=api_key,
                        context_messages=context_messages or [],
                        on_tool_event=on_tool_event,
                        image_bytes=image_bytes,
                        image_mime_type=image_mime_type,
                        tool_context=tool_context,
                    )
                    for chunk in self._chunk_text(final_text):
                        yield chunk
                    return
                try:
                    async for chunk in self._stream_openai_compatible(
                        prompt=prompt,
                        profile_model_name=profile.model_name,
                        profile_base_url=profile.base_url,
                        api_key=api_key,
                        context_messages=context_messages or [],
                        image_bytes=image_bytes,
                        image_mime_type=image_mime_type,
                    ):
                        yield chunk
                    return
                except Exception as exc:  # noqa: BLE001
                    logger.warning(
                        "stream mode failed for model=%s, fallback to tool-loop mode: %s",
                        profile.model_name,
                        type(exc).__name__,
                    )
                    final_text = await self._generate_with_tool_calls(
                        prompt=prompt,
                        profile_model_name=profile.model_name,
                        profile_base_url=profile.base_url,
                        api_key=api_key,
                        context_messages=context_messages or [],
                        on_tool_event=on_tool_event,
                        image_bytes=image_bytes,
                        image_mime_type=image_mime_type,
                        tool_context=tool_context,
                    )
                    for chunk in self._chunk_text(final_text):
                        yield chunk
                    return

        # Compatibility path when old model IDs are passed directly.
        if settings.openai_key_pool:
            fallback_model = settings.openai_model if model.startswith("openai_") else model
            api_key = self._pick_from_pool(settings.openai_key_pool, "OPENAI_API_KEYS")
            if on_tool_event is not None:
                final_text = await self._generate_with_tool_calls(
                    prompt=prompt,
                    profile_model_name=fallback_model,
                    profile_base_url=settings.openai_base_url,
                    api_key=api_key,
                    context_messages=context_messages or [],
                    on_tool_event=on_tool_event,
                    image_bytes=image_bytes,
                    image_mime_type=image_mime_type,
                    tool_context=tool_context,
                )
                for chunk in self._chunk_text(final_text):
                    yield chunk
                return
            try:
                async for chunk in self._stream_openai_compatible(
                    prompt=prompt,
                    profile_model_name=fallback_model,
                    profile_base_url=settings.openai_base_url,
                    api_key=api_key,
                    context_messages=context_messages or [],
                    image_bytes=image_bytes,
                    image_mime_type=image_mime_type,
                ):
                    yield chunk
                return
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "stream mode failed for fallback model=%s, fallback to tool-loop mode: %s",
                    fallback_model,
                    type(exc).__name__,
                )
                final_text = await self._generate_with_tool_calls(
                    prompt=prompt,
                    profile_model_name=fallback_model,
                    profile_base_url=settings.openai_base_url,
                    api_key=api_key,
                    context_messages=context_messages or [],
                    on_tool_event=on_tool_event,
                    image_bytes=image_bytes,
                    image_mime_type=image_mime_type,
                    tool_context=tool_context,
                )
                for chunk in self._chunk_text(final_text):
                    yield chunk
                return

        for chunk in self._chunk_text(f"[model={model}] {prompt}"):
            yield chunk

    async def generate(self, prompt: str, model: str) -> GenerationResult:
        parts: list[str] = []
        async for chunk in self.stream_generate(prompt=prompt, model=model):
            parts.append(chunk)
        return GenerationResult(
            final_text="".join(parts),
            reasoning="route_decision -> provider_generate",
        )

    async def _generate_with_tool_calls(
        self,
        prompt: str,
        profile_model_name: str,
        profile_base_url: str,
        api_key: str,
        context_messages: list[dict] | None = None,
        max_iterations: int = 5,
        on_tool_event: Callable[[str], Awaitable[None]] | None = None,
        image_bytes: bytes | None = None,
        image_mime_type: str = "image/jpeg",
        tool_context: dict[str, object] | None = None,
    ) -> str:
        messages = self._build_messages(
            prompt=prompt,
            context_messages=context_messages,
            image_bytes=image_bytes,
            image_mime_type=image_mime_type,
        )

        tools = [
            {
                "type": "function",
                "function": {
                    "name": "search",
                    "description": "联网搜索最新信息并返回摘要。适合实时资讯、新闻、需要来源链接的问题。",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "query": {"type": "string", "description": "搜索关键词"},
                            "max_results": {"type": "integer", "description": "结果条数，1-10", "default": 5},
                        },
                        "required": ["query"],
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "time_now",
                    "description": "获取当前 UTC 时间。",
                    "parameters": {"type": "object", "properties": {}, "required": []},
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "fetch_webpage",
                    "description": "抓取网页内容并返回可读文本摘要，适合需要精读指定 URL 的场景。",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "url": {"type": "string", "description": "完整网页 URL"},
                            "max_chars": {"type": "integer", "description": "返回文本最大长度", "default": 5000},
                        },
                        "required": ["url"],
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "echo",
                    "description": "原样返回输入内容，用于调试。",
                    "parameters": {
                        "type": "object",
                        "properties": {"text": {"type": "string", "description": "要回显的文本"}},
                        "required": ["text"],
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "memory_add",
                    "description": "将关键信息写入记忆。私聊默认写长期记忆，群聊/话题默认写钉住记忆。",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "text": {"type": "string", "description": "需要写入记忆的内容"},
                            "kind": {
                                "type": "string",
                                "description": "记忆类型：auto/long_term/pinned",
                                "enum": ["auto", "long_term", "pinned"],
                                "default": "auto",
                            },
                            "importance": {"type": "integer", "description": "长期记忆重要度 1-10", "default": 1},
                        },
                        "required": ["text"],
                    },
                },
            },
        ]

        for iteration in range(max_iterations):
            logger.debug(f"[agentic-loop] iteration {iteration+1}/{max_iterations}")
            obj = await self._chat_openai_compatible(
                profile_model_name=profile_model_name,
                profile_base_url=profile_base_url,
                api_key=api_key,
                messages=messages,
                stream=False,
                tools=tools,
                tool_choice="auto",
            )
            choices = obj.get("choices") or []
            if not choices:
                return ""
            assistant = choices[0].get("message") or {}
            assistant_content = assistant.get("content") or ""
            assistant_reasoning = str(assistant.get("reasoning_content") or "").strip()
            tool_calls = assistant.get("tool_calls") or []
            logger.debug(f"[model-response] tool_calls count={len(tool_calls)} content_len={len(assistant_content)}")
            logger.debug(f"[assistant-msg-keys] {assistant.keys()}")
            if tool_calls:
                logger.debug(f"[tool-calls-raw] {tool_calls}")
            logger.debug(f"[model-content] {repr(assistant_content[:200])}")
            if not assistant_content and assistant_reasoning:
                logger.warning(
                    "empty assistant content from model=%s reasoning_len=%s reasoning_preview=%r",
                    profile_model_name,
                    len(assistant_reasoning),
                    assistant_reasoning[:200],
                )

            assistant_msg: dict[str, object] = {
                "role": "assistant",
                "content": assistant_content,
            }
            if assistant_reasoning:
                assistant_msg["reasoning_content"] = assistant_reasoning
            if tool_calls:
                assistant_msg["tool_calls"] = tool_calls
            messages.append(assistant_msg)

            if not tool_calls:
                reasoning = assistant_reasoning
                self._last_reasoning_content = reasoning
                return str(assistant_content or reasoning)

            for tool_call in tool_calls:
                fn = (tool_call.get("function") or {}) if isinstance(tool_call, dict) else {}
                name = str(fn.get("name") or "").strip()
                logger.debug(f"[tool-call-extract] raw_name={repr(fn.get('name'))} → stripped={repr(name)}")
                raw_args = str(fn.get("arguments") or "{}").strip()
                try:
                    parsed_args = json.loads(raw_args) if raw_args else {}
                except json.JSONDecodeError:
                    parsed_args = {}

                argument = ""
                search_query = ""
                if name in {"search", "web_search"}:
                    query = str(parsed_args.get("query") or "").strip()
                    max_results = int(parsed_args.get("max_results") or 5)
                    argument = f"{query} | {max_results}"
                    tool_name = "search"
                    search_query = query
                elif name == "echo":
                    argument = str(parsed_args.get("text") or "")
                    tool_name = "echo"
                elif name == "time_now":
                    tool_name = "time_now"
                elif name in {"fetch_webpage", "web_fetch", "get_webpage"}:
                    url = str(parsed_args.get("url") or "").strip()
                    max_chars = int(parsed_args.get("max_chars") or 5000)
                    argument = f"{url} | {max_chars}"
                    tool_name = "fetch_webpage"
                elif name in {"memory_add", "remember"}:
                    payload = {
                        "text": str(parsed_args.get("text") or "").strip(),
                        "kind": str(parsed_args.get("kind") or "auto").strip(),
                        "importance": int(parsed_args.get("importance") or 1),
                    }
                    argument = json.dumps(payload, ensure_ascii=False)
                    tool_name = "memory_add"
                else:
                    tool_name = name if name else "unknown_tool"

                logger.debug(f"[tool-execute] name={tool_name} argument={repr(argument[:50] if len(argument) > 50 else argument)}")
                if on_tool_event is not None:
                    event_msg = f"start:{tool_name}:{search_query}" if tool_name == "search" else f"start:{tool_name}"
                    logger.debug(f"[tool-event-callback] sending={repr(event_msg)}")
                    await on_tool_event(event_msg)
                tool_result = await execute_builtin_tool_with_context(tool_name, argument, tool_context)
                logger.debug(f"[tool-result] {tool_name}={repr(tool_result[:100] if len(tool_result) > 100 else tool_result)}")
                if on_tool_event is not None:
                    event_msg = f"done:{tool_name}:{search_query}" if tool_name == "search" else f"done:{tool_name}"
                    logger.debug(f"[tool-event-callback] sending={repr(event_msg)}")
                    await on_tool_event(event_msg)
                tool_call_id = str(tool_call.get("id") or "") if isinstance(tool_call, dict) else ""
                messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": tool_call_id,
                        "content": tool_result,
                    }
                )

        # 达到工具循环上限后，强制要求模型基于已拿到信息作答。
        messages.append(
            {
                "role": "user",
                "content": "已达到工具调用上限，请不要再调用工具，直接基于当前信息给出最终答案。",
            }
        )
        final_obj = await self._chat_openai_compatible(
            profile_model_name=profile_model_name,
            profile_base_url=profile_base_url,
            api_key=api_key,
            messages=messages,
            stream=False,
            tools=None,
            tool_choice=None,
        )
        final_choices = final_obj.get("choices") or []
        if not final_choices:
            self._last_reasoning_content = ""
            return ""
        final_message = final_choices[0].get("message") or {}
        reasoning = str(final_message.get("reasoning_content") or "").strip()
        final_content = str(final_message.get("content") or "")
        if not final_content and reasoning:
            logger.warning(
                "empty final assistant content from model=%s reasoning_len=%s reasoning_preview=%r",
                profile_model_name,
                len(reasoning),
                reasoning[:200],
            )
        self._last_reasoning_content = reasoning
        return final_content or reasoning

    async def _chat_openai_compatible(
        self,
        profile_model_name: str,
        profile_base_url: str,
        api_key: str,
        messages: list[dict],
        stream: bool,
        tools: list[dict] | None,
        tool_choice: str | None,
    ) -> dict:
        base_url = profile_base_url.rstrip("/") if profile_base_url else "https://api.openai.com/v1"
        endpoint = f"{base_url}/chat/completions"
        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }
        payload = {
            "model": profile_model_name,
            "messages": messages,
            "stream": stream,
        }
        if tools:
            payload["tools"] = tools
            logger.debug(f"[payload-tools] {json.dumps(tools, indent=2, ensure_ascii=False)}")
        retryable_status_codes = {408, 409, 425, 429, 500, 502, 503, 504}
        max_attempts = 3

        async with httpx.AsyncClient(timeout=settings.provider_timeout_seconds) as client:
            logger.debug(f"[full-payload] {json.dumps(payload, indent=2, ensure_ascii=False)[:500]}")
            for attempt in range(1, max_attempts + 1):
                try:
                    response = await client.post(endpoint, headers=headers, json=payload)
                except (
                    httpx.RemoteProtocolError,
                    httpx.ReadError,
                    httpx.ReadTimeout,
                    httpx.ConnectError,
                    httpx.ConnectTimeout,
                    httpx.WriteError,
                    httpx.PoolTimeout,
                ) as exc:
                    if attempt < max_attempts:
                        wait_seconds = 0.6 * attempt
                        logger.warning(
                            "upstream transport error, retrying attempt=%s/%s model=%s error=%s",
                            attempt,
                            max_attempts,
                            profile_model_name,
                            type(exc).__name__,
                        )
                        await asyncio.sleep(wait_seconds)
                        continue
                    raise ValueError(
                        f"上游连接异常（{type(exc).__name__}），模型={profile_model_name}，请稍后重试"
                    ) from exc

                if response.status_code in retryable_status_codes and attempt < max_attempts:
                    wait_seconds = 0.6 * attempt
                    logger.warning(
                        "upstream transient status, retrying attempt=%s/%s model=%s status=%s",
                        attempt,
                        max_attempts,
                        profile_model_name,
                        response.status_code,
                    )
                    await asyncio.sleep(wait_seconds)
                    continue

                if response.status_code >= 400:
                    error_text = response.text.strip()
                    parsed_detail = ""
                    try:
                        obj = json.loads(error_text)
                        err = obj.get("error") if isinstance(obj, dict) else None
                        if isinstance(err, dict):
                            parsed_detail = str(err.get("message") or err.get("type") or "")
                    except json.JSONDecodeError:
                        parsed_detail = ""
                    detail = parsed_detail or error_text or "无错误详情"
                    raise ValueError(
                        f"上游接口请求失败 HTTP {response.status_code}，模型={profile_model_name}，详情：{detail}"
                    )

                return response.json()

        return {}

    def _build_messages(
        self,
        prompt: str,
        context_messages: list[dict] | None = None,
        image_bytes: bytes | None = None,
        image_mime_type: str = "image/jpeg",
    ) -> list[dict]:
        messages: list[dict] = [
            {
                "role": "system",
                "content": (
                    "你是一个有帮助的AI助手。请用中文回答用户的问题。尽可能简洁、准确、有用。"
                    "当用户明确给出稳定偏好、长期约束或关键事实时，可调用 memory_add 工具写入记忆；"
                    "私聊优先写长期记忆，群话题优先写钉住记忆。"
                ),
            },
        ]
        if context_messages:
            for msg in context_messages:
                role = str(msg.get("role") or "").strip()
                content = str(msg.get("content") or "").strip()
                if role in {"system", "user", "assistant"} and content:
                    packed: dict[str, object] = {"role": role, "content": content}
                    if role == "assistant":
                        reasoning_content = str(msg.get("reasoning_content") or "").strip()
                        if reasoning_content:
                            packed["reasoning_content"] = reasoning_content
                    messages.append(packed)

        if image_bytes:
            b64 = base64.b64encode(image_bytes).decode("ascii")
            user_content: object = [
                {"type": "text", "text": prompt},
                {"type": "image_url", "image_url": {"url": f"data:{image_mime_type};base64,{b64}"}},
            ]
        else:
            user_content = prompt
        messages.append({"role": "user", "content": user_content})
        return messages

    async def _stream_openai_compatible(
        self,
        prompt: str,
        profile_model_name: str,
        profile_base_url: str,
        api_key: str,
        context_messages: list[dict] | None = None,
        image_bytes: bytes | None = None,
        image_mime_type: str = "image/jpeg",
    ) -> AsyncIterator[str]:
        base_url = profile_base_url.rstrip("/") if profile_base_url else "https://api.openai.com/v1"
        endpoint = f"{base_url}/chat/completions"
        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }
        payload = {
            "model": profile_model_name,
            "messages": self._build_messages(
                prompt=prompt,
                context_messages=context_messages,
                image_bytes=image_bytes,
                image_mime_type=image_mime_type,
            ),
            "stream": True,
        }

        collected_reasoning: list[str] = []
        got_text = False
        async with httpx.AsyncClient(timeout=settings.provider_timeout_seconds) as client:
            async with client.stream("POST", endpoint, headers=headers, json=payload) as response:
                if response.status_code >= 400:
                    error_text = (await response.aread()).decode("utf-8", errors="ignore").strip()
                    raise ValueError(
                        f"上游接口请求失败 HTTP {response.status_code}，模型={profile_model_name}，详情：{error_text or '无错误详情'}"
                    )
                async for raw_line in response.aiter_lines():
                    line = (raw_line or "").strip()
                    if not line.startswith("data:"):
                        continue
                    data = line[5:].strip()
                    if not data or data == "[DONE]":
                        continue
                    try:
                        obj = json.loads(data)
                    except json.JSONDecodeError:
                        continue
                    choices = obj.get("choices") or []
                    if not choices:
                        continue
                    delta = choices[0].get("delta") or {}
                    content = delta.get("content")
                    if isinstance(content, str) and content:
                        got_text = True
                        yield content
                    reasoning_content = delta.get("reasoning_content")
                    if isinstance(reasoning_content, str) and reasoning_content:
                        collected_reasoning.append(reasoning_content)

        self._last_reasoning_content = "".join(collected_reasoning).strip()
        if not got_text:
            raise ValueError("流式响应为空")

    def _pick_api_key(self, env_name: str) -> str:
        raw = os.getenv(env_name, "").strip()
        if not raw and env_name == "OPENAI_API_KEY":
            # Backward compatibility with OPENAI_API_KEYS from settings.
            return self._pick_from_pool(settings.openai_key_pool, env_name)
        if not raw:
            return ""
        pool = [part.strip() for part in raw.split(",") if part.strip()]
        return self._pick_from_pool(pool, env_name)

    def _pick_from_pool(self, pool: list[str], pool_name: str) -> str:
        if not pool:
            return ""
        with self._rr_lock:
            idx = self._rr_index_by_pool.get(pool_name, 0)
            chosen = pool[idx % len(pool)]
            self._rr_index_by_pool[pool_name] = (idx + 1) % len(pool)
        return chosen

    @staticmethod
    def _chunk_text(text: str, chunk_size: int = 64) -> list[str]:
        return [text[i : i + chunk_size] for i in range(0, len(text), chunk_size)]
