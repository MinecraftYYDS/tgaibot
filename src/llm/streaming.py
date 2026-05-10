from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator


async def stream_text_chunks(text: str, chunk_size: int = 40, delay_seconds: float = 0.1) -> AsyncIterator[str]:
    start = 0
    while start < len(text):
        end = min(start + chunk_size, len(text))
        yield text[start:end]
        start = end
        await asyncio.sleep(delay_seconds)
