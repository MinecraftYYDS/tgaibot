from __future__ import annotations

from dataclasses import dataclass


@dataclass
class GenerationResult:
    final_text: str
    reasoning: str


class LLMProvider:
    async def generate(self, prompt: str, model: str) -> GenerationResult:
        # Placeholder for provider integrations and tool loop.
        return GenerationResult(
            final_text=f"[model={model}] {prompt}",
            reasoning="route_decision -> provider_stub",
        )
