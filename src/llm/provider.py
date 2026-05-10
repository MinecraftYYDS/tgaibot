from __future__ import annotations

import json
from collections.abc import AsyncIterator
from dataclasses import dataclass

import httpx

from src.config import settings
from src.llm.tools import execute_builtin_tool, try_extract_tool_intent


@dataclass
class GenerationResult:
    final_text: str
    reasoning: str


class LLMProvider:
    async def stream_generate(self, prompt: str, model: str) -> AsyncIterator[str]:
        tool_intent = try_extract_tool_intent(prompt)
        if tool_intent is not None:
            tool_name, argument = tool_intent
            tool_output = execute_builtin_tool(tool_name, argument)
            for chunk in self._chunk_text(f"Tool {tool_name} result: {tool_output}"):
                yield chunk
            return

        if settings.openai_api_key:
            async for chunk in self._stream_openai_compatible(prompt=prompt, model=model):
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

    async def _stream_openai_compatible(self, prompt: str, model: str) -> AsyncIterator[str]:
        base_url = settings.openai_base_url.rstrip("/") if settings.openai_base_url else "https://api.openai.com/v1"
        endpoint = f"{base_url}/chat/completions"
        headers = {
            "Authorization": f"Bearer {settings.openai_api_key}",
            "Content-Type": "application/json",
        }
        payload = {
            "model": settings.openai_model if model in {"small-fast", "strong-reasoning", "long-context", "tool-use"} else model,
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

    @staticmethod
    def _chunk_text(text: str, chunk_size: int = 64) -> list[str]:
        return [text[i : i + chunk_size] for i in range(0, len(text), chunk_size)]
