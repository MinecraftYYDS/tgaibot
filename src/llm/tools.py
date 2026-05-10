from __future__ import annotations

from asyncio import to_thread
from datetime import datetime


def _parse_search_argument(argument: str) -> tuple[str, int]:
    # Format: "query" or "query | 5"
    if "|" not in argument:
        return argument.strip(), 5
    query, max_results_raw = argument.split("|", maxsplit=1)
    query = query.strip()
    try:
        max_results = int(max_results_raw.strip())
    except ValueError:
        max_results = 5
    return query, max(1, min(10, max_results))


def _run_ddgs_text_search(query: str, max_results: int) -> list[dict[str, object]]:
    from ddgs import DDGS

    with DDGS(timeout=10) as client:
        results = client.text(query, max_results=max_results, region="us-en", safesearch="moderate")
    return results


def _format_search_results(results: list[dict[str, object]]) -> str:
    lines: list[str] = []
    for idx, item in enumerate(results, start=1):
        title = str(item.get("title") or "(no title)").strip()
        href = str(item.get("href") or item.get("url") or "").strip()
        body = str(item.get("body") or item.get("content") or "").strip()
        if len(body) > 180:
            body = body[:177] + "..."
        lines.append(f"{idx}. {title}\n{href}\n{body}")
    return "\n\n".join(lines)


async def execute_builtin_tool(name: str, argument: str) -> str:
    if name == "echo":
        return argument
    if name == "time_now":
        return datetime.utcnow().isoformat() + "Z"
    if name in {"web_search", "search"}:
        query, max_results = _parse_search_argument(argument)
        if not query:
            return "tool_error:web_search query is empty"
        try:
            results = await to_thread(_run_ddgs_text_search, query, max_results)
        except Exception as exc:  # noqa: BLE001
            return f"tool_error:web_search {type(exc).__name__}: {exc}"
        if not results:
            return "web_search no results"
        return _format_search_results(results)
    return f"unsupported_tool:{name}"


def try_extract_tool_intent(prompt: str) -> tuple[str, str] | None:
    # Lightweight tool loop protocol:
    # [tool:echo] your text
    # [tool:time_now]
    # [tool:web_search] OpenAI function calling | 5
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
