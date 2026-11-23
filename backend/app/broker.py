"""Thread-safe fan-out to subscribers on their own asyncio event loops."""

import asyncio
import threading
from collections.abc import AsyncIterator

from app.events import StoredEvent


class Subscription(AsyncIterator[StoredEvent]):
    def __init__(self, broker: "Broker", check_id: str) -> None:
        self.broker, self.check_id = broker, check_id
        self.loop = asyncio.get_running_loop()
        self.queue: asyncio.Queue[StoredEvent] = asyncio.Queue()
        self.closed = False

    def __aiter__(self) -> "Subscription":
        return self

    async def __anext__(self) -> StoredEvent:
        if self.closed:
            raise StopAsyncIteration
        event = await self.queue.get()
        if event.event.type in ("done", "failed"):
            await self.aclose()
        return event

    async def aclose(self) -> None:
        self.closed = True
        self.broker.unsubscribe(self)


class Broker:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._subscribers: dict[str, set[Subscription]] = {}
        self._terminal: dict[str, StoredEvent] = {}

    def subscribe(self, check_id: str) -> Subscription:
        subscription = Subscription(self, check_id)
        with self._lock:
            self._subscribers.setdefault(check_id, set()).add(subscription)
            terminal = self._terminal.get(check_id)
            if terminal is not None:
                subscription.queue.put_nowait(terminal)
        return subscription

    def unsubscribe(self, subscription: Subscription) -> None:
        with self._lock:
            group = self._subscribers.get(subscription.check_id)
            if group is not None:
                group.discard(subscription)
                if not group:
                    self._subscribers.pop(subscription.check_id, None)

    def publish(self, check_id: str, stored_event: StoredEvent) -> None:
        with self._lock:
            if stored_event.event.type in ("done", "failed"):
                self._terminal[check_id] = stored_event
            subscribers = list(self._subscribers.get(check_id, ()))
        for subscription in subscribers:
            try:
                subscription.loop.call_soon_threadsafe(subscription.queue.put_nowait, stored_event)
            except RuntimeError:
                self.unsubscribe(subscription)
