from __future__ import annotations

import re
from asyncio import to_thread
from datetime import datetime
from html import unescape

import httpx

from src.config import settings


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

    proxy = settings.search_proxy.strip()
    try:
        if proxy:
            with DDGS(timeout=10, proxy=proxy) as client:
                results = client.text(query, max_results=max_results, region="us-en", safesearch="moderate")
        else:
            with DDGS(timeout=10) as client:
                results = client.text(query, max_results=max_results, region="us-en", safesearch="moderate")
    except TypeError:
        # Compatibility for DDGS versions that don't accept proxy parameter.
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


def _parse_web_fetch_argument(argument: str) -> tuple[str, int]:
    # Format: "url" or "url | 6000"
    if "|" not in argument:
        return argument.strip(), 5000
    url, max_chars_raw = argument.split("|", maxsplit=1)
    url = url.strip()
    try:
        max_chars = int(max_chars_raw.strip())
    except ValueError:
        max_chars = 5000
    return url, max(500, min(20000, max_chars))


def _extract_title(html: str) -> str:
    match = re.search(r"<title[^>]*>(.*?)</title>", html, flags=re.IGNORECASE | re.DOTALL)
    if not match:
        return "(no title)"
    title = unescape(match.group(1)).strip()
    return re.sub(r"\s+", " ", title) if title else "(no title)"


def _html_to_text(html: str) -> str:
    text = re.sub(r"<script\b[^<]*(?:(?!</script>)<[^<]*)*</script>", " ", html, flags=re.IGNORECASE)
    text = re.sub(r"<style\b[^<]*(?:(?!</style>)<[^<]*)*</style>", " ", text, flags=re.IGNORECASE)
    text = re.sub(r"<!--.*?-->", " ", text, flags=re.DOTALL)
    text = re.sub(r"<[^>]+>", " ", text)
    text = unescape(text)
    return re.sub(r"\s+", " ", text).strip()


def _run_web_fetch(url: str, max_chars: int) -> str:
    if not url.lower().startswith(("http://", "https://")):
        raise ValueError("url must start with http:// or https://")

    headers = {
        "User-Agent": "Mozilla/5.0 (compatible; TG-AIBot/1.0; +https://example.local)"
    }
    with httpx.Client(timeout=15.0, follow_redirects=True, headers=headers) as client:
        response = client.get(url)
        response.raise_for_status()
        content_type = (response.headers.get("content-type") or "").lower()
        raw = response.text

    if "html" in content_type or "<html" in raw.lower():
        title = _extract_title(raw)
        text = _html_to_text(raw)
    else:
        title = "(non-html content)"
        text = re.sub(r"\s+", " ", raw).strip()

    if len(text) > max_chars:
        text = text[: max_chars - 3] + "..."

    return f"title: {title}\nurl: {url}\ncontent:\n{text}"


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
    if name in {"fetch_webpage", "web_fetch", "get_webpage"}:
        url, max_chars = _parse_web_fetch_argument(argument)
        if not url:
            return "tool_error:fetch_webpage url is empty"
        try:
            return await to_thread(_run_web_fetch, url, max_chars)
        except Exception as exc:  # noqa: BLE001
            return f"tool_error:fetch_webpage {type(exc).__name__}: {exc}"
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
