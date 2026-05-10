from __future__ import annotations

from datetime import datetime


def execute_builtin_tool(name: str, argument: str) -> str:
    if name == "echo":
        return argument
    if name == "time_now":
        return datetime.utcnow().isoformat() + "Z"
    return f"unsupported_tool:{name}"


def try_extract_tool_intent(prompt: str) -> tuple[str, str] | None:
    # Lightweight tool loop protocol:
    # [tool:echo] your text
    # [tool:time_now]
    stripped = prompt.strip()
    if not stripped.startswith("[tool:"):
        return None
    close_idx = stripped.find("]")
    if close_idx == -1:
        return None
    tool_name = stripped[6:close_idx].strip()
    argument = stripped[close_idx + 1 :].strip()
    if not tool_name:
        return None
    return tool_name, argument
