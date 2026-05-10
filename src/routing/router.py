from __future__ import annotations

from dataclasses import dataclass


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
            return RouteResult(mode="auto", model="strong-reasoning", reason="coding_or_reasoning")
        if len(text) > 1800:
            return RouteResult(mode="auto", model="long-context", reason="long_input")
        if any(k in normalized for k in ["search", "网页", "web", "tool", "调用"]):
            return RouteResult(mode="auto", model="tool-use", reason="tool_intent")
        return RouteResult(mode="auto", model="small-fast", reason="simple_qa")
