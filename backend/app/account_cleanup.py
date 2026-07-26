from __future__ import annotations

import re
import threading
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from .models import ActivityCleanupState, ActivityObservation


COMMAND_ID_PATTERN = re.compile(r"clc_[0-9a-f]{32}\Z")
AGENT_ID_PATTERN = re.compile(r"[A-Za-z0-9._:-]{1,128}\Z")


class ActivityCleanupError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class ActivityCleanupCommand:
    command_id: str
    type: str


class ActivityCleanupClient(Protocol):
    def poll(self) -> ActivityCleanupCommand | None: ...

    def ack(self, command_id: str) -> None: ...

    def close(self) -> None: ...


def _agent_id(value: str) -> str:
    if type(value) is not str or AGENT_ID_PATTERN.fullmatch(value) is None:
        raise ActivityCleanupError("invalid cleanup agent")
    return value


def _command_id(value: str) -> str:
    if type(value) is not str or COMMAND_ID_PATTERN.fullmatch(value) is None:
        raise ActivityCleanupError("invalid cleanup command")
    return value


def activity_collection_enabled(session: Session) -> bool:
    with session.no_autoflush:
        state = session.execute(
            select(ActivityCleanupState)
            .where(ActivityCleanupState.singleton == 1)
            .with_for_update(read=True)
        ).scalar_one_or_none()
    if state is None:
        raise ActivityCleanupError("activity cleanup state is unavailable")
    return bool(state.activity_enabled)


class ActivityCleanupRepository:
    def __init__(self, session_factory: Callable[[], Session]) -> None:
        if not callable(session_factory):
            raise TypeError("session_factory must be callable")
        self._session_factory = session_factory

    @staticmethod
    def _locked_state(session: Session) -> ActivityCleanupState:
        state = session.execute(
            select(ActivityCleanupState)
            .where(ActivityCleanupState.singleton == 1)
            .with_for_update()
        ).scalar_one_or_none()
        if state is None:
            raise ActivityCleanupError("activity cleanup state is unavailable")
        return state

    def bind_agent(self, agent_id: str, now: datetime) -> None:
        agent_id = _agent_id(agent_id)
        if now.tzinfo is None or now.utcoffset() is None:
            raise ActivityCleanupError("cleanup clock must be timezone-aware")
        with self._session_factory.begin() as session:
            state = self._locked_state(session)
            if state.agent_id == agent_id:
                return
            state.agent_id = agent_id
            state.activity_enabled = True
            state.command_id = None
            state.applied_at = None
            state.updated_at = now

    def apply(self, agent_id: str, command_id: str, now: datetime) -> bool:
        agent_id = _agent_id(agent_id)
        command_id = _command_id(command_id)
        if now.tzinfo is None or now.utcoffset() is None:
            raise ActivityCleanupError("cleanup clock must be timezone-aware")
        with self._session_factory.begin() as session:
            state = self._locked_state(session)
            if state.agent_id != agent_id:
                raise ActivityCleanupError("cleanup command does not match active agent")
            if state.command_id == command_id and not state.activity_enabled:
                return False
            if state.command_id is not None or not state.activity_enabled:
                raise ActivityCleanupError("conflicting cleanup command")
            session.execute(delete(ActivityObservation))
            state.activity_enabled = False
            state.command_id = command_id
            state.applied_at = now
            state.updated_at = now
            return True


class ActivityCleanupWorker:
    def __init__(
        self,
        repository: ActivityCleanupRepository,
        client: ActivityCleanupClient,
        *,
        agent_id: str,
        now: Callable[[], datetime],
        poll_seconds: float = 30.0,
    ) -> None:
        if poll_seconds <= 0:
            raise ValueError("poll_seconds must be positive")
        self._repository = repository
        self._client = client
        self._agent_id = _agent_id(agent_id)
        self._now = now
        self._poll_seconds = poll_seconds
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._lock = threading.Lock()
        self._closed = False
        self.last_error: str | None = None

    def bind_agent(self) -> None:
        self._repository.bind_agent(self._agent_id, self._now())

    def process_once(self) -> None:
        command = self._client.poll()
        if command is None:
            return
        if command.type != "delete_activity_observations":
            raise ActivityCleanupError("unsupported cleanup command")
        self._repository.apply(self._agent_id, command.command_id, self._now())
        self._client.ack(command.command_id)

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                self.process_once()
                self.last_error = None
            except Exception:
                self.last_error = "activity_cleanup_unavailable"
            self._stop.wait(self._poll_seconds)

    def start(self) -> None:
        with self._lock:
            if self._thread is not None or self._closed:
                return
            self.bind_agent()
            self._thread = threading.Thread(
                target=self._run,
                name="petcare-activity-cleanup",
                daemon=True,
            )
            self._thread.start()

    def stop(self, *, timeout_seconds: float) -> None:
        if timeout_seconds < 0:
            raise ValueError("timeout_seconds must be nonnegative")
        with self._lock:
            thread = self._thread
            if self._closed:
                return
            self._stop.set()
        if thread is not None:
            thread.join(timeout_seconds)
            if thread.is_alive():
                raise TimeoutError("activity cleanup worker shutdown timed out")
        with self._lock:
            if not self._closed:
                self._client.close()
                self._closed = True
