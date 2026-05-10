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
            return RouteResult(mode=mode, model=mode, reason="手动选择模型")

        if any(k in normalized for k in ["python", "code", "debug", "算法", "推理"]):
            return RouteResult(mode="auto", model=settings.auto_reasoning_model_id, reason="编程或推理问题")
        if len(text) > 1800:
            return RouteResult(mode="auto", model=settings.auto_long_model_id, reason="长文本输入")
        if any(k in normalized for k in ["search", "网页", "web", "tool", "调用"]):
            return RouteResult(mode="auto", model=settings.auto_tool_model_id, reason="工具调用意图")
        return RouteResult(mode="auto", model=settings.auto_simple_model_id, reason="普通问答")
