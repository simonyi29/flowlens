"""Tenant-filtered, process-local live event fan-out for the trusted site proxy."""
from __future__ import annotations

import asyncio
from collections import defaultdict


class RemoteEventHub:
    def __init__(self):
        self._subscribers: dict[str, set[asyncio.Queue]] = defaultdict(set)

    def subscribe(self, user_id: str) -> asyncio.Queue:
        queue: asyncio.Queue = asyncio.Queue(maxsize=200)
        self._subscribers[user_id].add(queue)
        return queue

    def unsubscribe(self, user_id: str, queue: asyncio.Queue) -> None:
        self._subscribers[user_id].discard(queue)
        if not self._subscribers[user_id]: self._subscribers.pop(user_id, None)

    def publish(self, user_id: str, event: dict) -> None:
        for queue in tuple(self._subscribers.get(user_id, ())):
            if queue.full():
                try: queue.get_nowait()
                except asyncio.QueueEmpty: pass
            queue.put_nowait(event)


remote_event_hub = RemoteEventHub()
