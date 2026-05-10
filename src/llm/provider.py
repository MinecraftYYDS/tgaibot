from __future__ import annotations

import json
import os
from collections.abc import AsyncIterator
from dataclasses import dataclass
from threading import Lock

import httpx

from src.config import settings
from src.model_catalog import resolve_model
from src.llm.tools import execute_builtin_tool, try_extract_tool_intent


@dataclass
class GenerationResult:
    final_text: str
    reasoning: str


class LLMProvider:
    def __init__(self) -> None:
        self._rr_lock = Lock()
        self._rr_index_by_pool: dict[str, int] = {}

    async def stream_generate(self, prompt: str, model: str) -> AsyncIterator[str]:
        tool_intent = try_extract_tool_intent(prompt)
        if tool_intent is not None:
            tool_name, argument = tool_intent
            tool_output = await execute_builtin_tool(tool_name, argument)
            for chunk in self._chunk_text(f"Tool {tool_name} result: {tool_output}"):
                yield chunk
            return

        profile = resolve_model(settings.model_catalog, model)
        if profile is not None and profile.provider == "openai_compatible":
            api_key = self._pick_api_key(profile.api_key_env)
            if api_key:
                async for chunk in self._stream_openai_compatible(
                    prompt=prompt,
                    profile_model_name=profile.model_name,
                    profile_base_url=profile.base_url,
                    api_key=api_key,
                ):
                    yield chunk
                return

        # Compatibility path when old model IDs are passed directly.
        if settings.openai_key_pool:
            async for chunk in self._stream_openai_compatible(
                prompt=prompt,
                profile_model_name=settings.openai_model if model.startswith("openai_") else model,
                profile_base_url=settings.openai_base_url,
                api_key=self._pick_from_pool(settings.openai_key_pool, "OPENAI_API_KEYS"),
            ):
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

    async def _stream_openai_compatible(
        self,
        prompt: str,
        profile_model_name: str,
        profile_base_url: str,
        api_key: str,
    ) -> AsyncIterator[str]:
        base_url = profile_base_url.rstrip("/") if profile_base_url else "https://api.openai.com/v1"
        endpoint = f"{base_url}/chat/completions"
        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }
        payload = {
            "model": profile_model_name,
            "messages": [{"role": "user", "content": prompt}],
            "stream": True,
        }

        async with httpx.AsyncClient(timeout=settings.provider_timeout_seconds) as client:
            async with client.stream("POST", endpoint, headers=headers, json=payload) as response:
                response.raise_for_status()
                async for line in response.aiter_lines():
                    if not line or not line.startswith("data:"):
                        continue
                    data = line[5:].strip()
                    if data == "[DONE]":
                        break
                    try:
                        obj = json.loads(data)
                    except json.JSONDecodeError:
                        continue
                    choices = obj.get("choices") or []
                    if not choices:
                        continue
                    delta = choices[0].get("delta") or {}
                    content = delta.get("content")
                    if content:
                        yield content

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
