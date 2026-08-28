"""In-process fan-out of run events to connected clients.

One queue per subscriber, so a browser tab that stops reading cannot slow down
the pipeline. If a subscriber falls far enough behind that its queue fills, its
events are dropped rather than blocking the publisher — the client can always
recover the full history from ``run_events`` in the database.
"""

from __future__ import annotations

import asyncio
from collections import defaultdict
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from core.logging import get_logger
from server.models import RunEvent

logger = get_logger(__name__)

# Roughly a minute of chatty output before a stalled subscriber starts losing events.
QUEUE_MAXSIZE = 500


class EventBroker:
    def __init__(self, maxsize: int = QUEUE_MAXSIZE) -> None:
        self._subscribers: dict[str, set[asyncio.Queue[RunEvent]]] = defaultdict(set)
        self._maxsize = maxsize

    def subscriber_count(self, run_id: str) -> int:
        return len(self._subscribers.get(run_id, ()))

    async def publish(self, event: RunEvent) -> None:
        dropped = 0
        for queue in tuple(self._subscribers.get(event.run_id, ())):
            try:
                queue.put_nowait(event)
            except asyncio.QueueFull:
                dropped += 1

        if dropped:
            logger.debug("Dropped an event for %d slow subscriber(s)", dropped)

    @asynccontextmanager
    async def subscribe(self, run_id: str) -> AsyncIterator[asyncio.Queue[RunEvent]]:
        queue: asyncio.Queue[RunEvent] = asyncio.Queue(maxsize=self._maxsize)
        self._subscribers[run_id].add(queue)
        try:
            yield queue
        finally:
            self._subscribers[run_id].discard(queue)
            if not self._subscribers[run_id]:
                del self._subscribers[run_id]

    def close(self) -> None:
        self._subscribers.clear()
