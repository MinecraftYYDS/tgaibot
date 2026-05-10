from __future__ import annotations

from dataclasses import dataclass

from src.config import settings


@dataclass
class RouteResult:
    mode: str
    model: str
    reason: str


class ModelRouter:
    def route(self, text: str, mode: str = "auto") -> RouteResult:
        normalized = text.lower().strip()
        if mode != "auto":
            return RouteResult(mode=mode, model=mode, reason="manual_selection")

        if any(k in normalized for k in ["python", "code", "debug", "算法", "推理"]):
            return RouteResult(mode="auto", model=settings.auto_reasoning_model_id, reason="coding_or_reasoning")
        if len(text) > 1800:
            return RouteResult(mode="auto", model=settings.auto_long_model_id, reason="long_input")
        if any(k in normalized for k in ["search", "网页", "web", "tool", "调用"]):
            return RouteResult(mode="auto", model=settings.auto_tool_model_id, reason="tool_intent")
        return RouteResult(mode="auto", model=settings.auto_simple_model_id, reason="simple_qa")
