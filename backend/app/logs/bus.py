"""Bounded in-process fan-out for live system-log events.

The current deployment runs one application process. Subscribers get bounded
queues (slow/closed browsers cannot accumulate unbounded memory); publishing
never blocks the request path. Reconnection/replay always goes through the
database, so the bus is not the source of truth.
"""
from __future__ import annotations

import asyncio
from typing import Any

MAX_SUBSCRIBER_QUEUE = 500


class EventBus:
    def __init__(self) -> None:
        self._subscribers: set[asyncio.Queue[dict[str, Any]]] = set()

    def subscribe(self) -> asyncio.Queue[dict[str, Any]]:
        queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=MAX_SUBSCRIBER_QUEUE)
        self._subscribers.add(queue)
        return queue

    def unsubscribe(self, queue: asyncio.Queue[dict[str, Any]]) -> None:
        self._subscribers.discard(queue)

    def publish(self, event: dict[str, Any]) -> None:
        for queue in list(self._subscribers):
            if queue.full():
                try:
                    queue.get_nowait()
                except asyncio.QueueEmpty:  # pragma: no cover - race only
                    pass
            try:
                queue.put_nowait(event)
            except asyncio.QueueFull:  # pragma: no cover - defensive
                pass

    @property
    def subscriber_count(self) -> int:
        return len(self._subscribers)

    def reset(self) -> None:
        self._subscribers.clear()


bus = EventBus()
