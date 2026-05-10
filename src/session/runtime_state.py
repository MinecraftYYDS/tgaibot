from __future__ import annotations

import asyncio


class GenerationControl:
    def __init__(self) -> None:
        self._stop_flags: dict[str, asyncio.Event] = {}

    def begin(self, topic_key: str) -> None:
        self._stop_flags[topic_key] = asyncio.Event()

    def stop(self, topic_key: str) -> None:
        event = self._stop_flags.get(topic_key)
        if event is not None:
            event.set()

    def should_stop(self, topic_key: str) -> bool:
        event = self._stop_flags.get(topic_key)
        return bool(event and event.is_set())

    def end(self, topic_key: str) -> None:
        self._stop_flags.pop(topic_key, None)
