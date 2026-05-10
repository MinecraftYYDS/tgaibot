from __future__ import annotations


def post_process_reasoning(reasoning_mode: str, reasoning_text: str) -> str:
    if reasoning_mode == "visible_then_hide":
        return reasoning_text
    if reasoning_mode == "disabled":
        return ""
    return reasoning_text
