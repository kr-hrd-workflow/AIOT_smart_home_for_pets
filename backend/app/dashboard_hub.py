from __future__ import annotations

import asyncio
from queue import Empty, Full, Queue
from threading import Event, Lock

from pydantic import TypeAdapter, ValidationError
from starlette.concurrency import run_in_threadpool
from starlette.websockets import WebSocket

from .contracts import AnomalyAlert, DashboardMessage


_MESSAGE_ADAPTER = TypeAdapter(DashboardMessage)


class DashboardHub:
    def __init__(self, *, capacity: int = 1024) -> None:
        if capacity <= 0:
            raise ValueError("capacity must be positive")
        self._queue: Queue[DashboardMessage] = Queue(maxsize=capacity)
        self._coalesced: dict[str, DashboardMessage] = {}
        self._stop = Event()
        self._lock = Lock()
        self._subscribers: set[WebSocket] = set()
        self._broadcaster: asyncio.Task[None] | None = None

    @property
    def queue_full(self) -> bool:
        with self._lock:
            return self._queue.full() or bool(self._coalesced)

    @property
    def subscriber_count(self) -> int:
        with self._lock:
            return len(self._subscribers)

    def publish_from_worker(self, message: DashboardMessage | object) -> bool:
        try:
            validated = _MESSAGE_ADAPTER.validate_python(message, strict=True)
        except ValidationError as error:
            raise ValueError("dashboard message is outside the closed union") from error
        if self._stop.is_set():
            return False
        if isinstance(validated, AnomalyAlert):
            while not self._stop.is_set():
                try:
                    self._queue.put(validated, timeout=0.25)
                    return True
                except Full:
                    continue
            return False
        with self._lock:
            if validated.type in self._coalesced:
                self._coalesced[validated.type] = validated
                return False
            try:
                self._queue.put_nowait(validated)
                return True
            except Full:
                self._coalesced[validated.type] = validated
                return False

    def get_for_broadcast(self, *, timeout: float | None = None) -> DashboardMessage | None:
        try:
            item = self._queue.get(timeout=timeout)
        except Empty:
            if self._stop.is_set():
                return None
            raise
        self._promote_coalesced()
        return item

    def _promote_coalesced(self) -> None:
        with self._lock:
            while self._coalesced and not self._queue.full():
                message_type = next(iter(self._coalesced))
                self._queue.put_nowait(self._coalesced.pop(message_type))

    def subscribe(self, websocket: WebSocket) -> None:
        with self._lock:
            self._subscribers.add(websocket)

    def unsubscribe(self, websocket: WebSocket) -> None:
        with self._lock:
            self._subscribers.discard(websocket)

    def start_broadcaster(self) -> asyncio.Task[None]:
        if self._broadcaster is not None:
            return self._broadcaster
        self._broadcaster = asyncio.create_task(self._broadcast(), name="petcare-dashboard-broadcaster")
        return self._broadcaster

    async def _broadcast(self) -> None:
        while True:
            try:
                message = await run_in_threadpool(self.get_for_broadcast, timeout=0.25)
            except Empty:
                continue
            if message is None:
                return
            payload = message.model_dump(mode="json")
            with self._lock:
                subscribers = tuple(self._subscribers)
            for websocket in subscribers:
                try:
                    await websocket.send_json(payload)
                except Exception:
                    self.unsubscribe(websocket)

    def shutdown(self) -> None:
        self._stop.set()
