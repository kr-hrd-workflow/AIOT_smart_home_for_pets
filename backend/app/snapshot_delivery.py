from __future__ import annotations

import threading
import time
from collections.abc import Callable
from typing import Protocol

from .agent_client import SNAPSHOT_MAX_BYTES


class SnapshotClient(Protocol):
    def upload(self, summary_bytes: bytes) -> None: ...

    def close(self) -> None: ...


class SnapshotDeliveryWorker:
    def __init__(
        self,
        client: SnapshotClient,
        summary_supplier: Callable[[], bytes],
        *,
        cadence_seconds: float = 2.0,
    ) -> None:
        if cadence_seconds <= 0:
            raise ValueError("cadence_seconds must be positive")
        self._client = client
        self._summary_supplier = summary_supplier
        self._cadence_seconds = cadence_seconds
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._closed = False
        self.last_error: str | None = None

    def _run(self) -> None:
        try:
            next_attempt = time.monotonic()
            while not self._stop.is_set():
                remaining = next_attempt - time.monotonic()
                if remaining > 0 and self._stop.wait(remaining):
                    break
                try:
                    summary_bytes = self._summary_supplier()
                    if (
                        type(summary_bytes) is not bytes
                        or not summary_bytes
                        or len(summary_bytes) > SNAPSHOT_MAX_BYTES
                    ):
                        raise ValueError("invalid snapshot body")
                    self._client.upload(summary_bytes)
                    self.last_error = None
                except Exception:
                    self.last_error = "snapshot_unavailable"
                next_attempt += self._cadence_seconds
        finally:
            self._close_client()

    def _close_client(self) -> None:
        with self._lock:
            if self._closed:
                return
            self._closed = True
        self._client.close()

    def start(self) -> None:
        with self._lock:
            if self._thread is not None or self._closed:
                return
            self._thread = threading.Thread(
                target=self._run,
                name="petcare-snapshot-delivery",
                daemon=True,
            )
            self._thread.start()

    def stop(self, *, timeout_seconds: float) -> None:
        if timeout_seconds < 0:
            raise ValueError("timeout_seconds must be nonnegative")
        with self._lock:
            if self._closed:
                return
            thread = self._thread
            self._stop.set()
        if thread is not None:
            thread.join(timeout_seconds)
            if thread.is_alive():
                raise TimeoutError("snapshot delivery shutdown timed out")
        self._close_client()
