from __future__ import annotations

import heapq
from concurrent.futures import Future
from dataclasses import dataclass, field
from datetime import datetime
from threading import Event, Thread
from typing import Callable, Protocol

from sqlalchemy.orm import Session

from .contracts import BedCalibrationSuccess
from .events import CalibrateBedCommand
from .rule_ingress import (
    DeadlineBarrier,
    IngressCommand,
    IngressTombstone,
    RuleClock,
    RuleEnvelope,
    RuleIngress,
    StopMarker,
)


class RuleQueueUnavailable(RuntimeError):
    pass


class RuleEngineProtocol(Protocol):
    def startup(self, session: Session, scheduler: RuleWorker, now: datetime) -> None: ...

    def apply(
        self,
        session: Session,
        event: object,
        received_at_utc: datetime,
        received_at_monotonic: float,
        scheduler: RuleWorker,
    ) -> None: ...

    def deadline(
        self,
        session: Session,
        kind: str,
        key: str,
        effective_at: datetime,
        scheduler: RuleWorker,
    ) -> None: ...

    def command(
        self,
        session: Session,
        command: CalibrateBedCommand,
        received_at_utc: datetime,
        received_at_monotonic: float,
        scheduler: RuleWorker,
    ) -> BedCalibrationSuccess: ...

    def controlled_shutdown(
        self,
        session: Session,
        effective_at: datetime,
        scheduler: RuleWorker,
    ) -> None: ...


@dataclass(order=True, frozen=True, slots=True)
class _Deadline:
    due_monotonic: float
    insertion_order: int
    kind: str = field(compare=False)
    key: str = field(compare=False)
    effective_at_utc: datetime = field(compare=False)


class RuleWorker:
    def __init__(
        self,
        *,
        ingress: RuleIngress,
        clock: RuleClock,
        session_factory: Callable[[], Session],
        engine: RuleEngineProtocol,
    ) -> None:
        self._ingress = ingress
        self._clock = clock
        self._session_factory = session_factory
        self._engine = engine
        self._thread: Thread | None = None
        self._started = Event()
        self._startup_error: BaseException | None = None
        self._last_error: Exception | None = None
        self._deadlines: list[_Deadline] = []
        self._active: dict[tuple[str, str], int] = {}
        self._current: dict[tuple[str, str], _Deadline] = {}
        self._next_insertion_order = 1
        self._effective_monotonic: float | None = None

    @property
    def thread(self) -> Thread | None:
        return self._thread

    @property
    def effective_monotonic(self) -> float:
        return self._clock.monotonic() if self._effective_monotonic is None else self._effective_monotonic

    @property
    def last_error(self) -> Exception | None:
        return self._last_error

    def start(self) -> None:
        if self._thread is not None:
            return
        self._thread = Thread(target=self._run, name="petcare-rule-worker", daemon=False)
        self._thread.start()
        self._started.wait()
        if self._startup_error is not None:
            raise RuntimeError("rule worker startup failed") from self._startup_error

    def submit(self, command: CalibrateBedCommand) -> Future[BedCalibrationSuccess]:
        future: Future[BedCalibrationSuccess] = Future()
        if not self._ingress.try_submit_command(command, future):
            raise RuleQueueUnavailable("rule command queue is unavailable")
        return future

    def schedule(self, kind: str, key: str, due_monotonic: float, effective_at_utc: datetime) -> None:
        if not kind or not key:
            raise ValueError("deadline kind and key must not be empty")
        identity = (kind, key)
        current = self._current.get(identity)
        if current is not None and (current.due_monotonic, current.effective_at_utc) == (
            due_monotonic,
            effective_at_utc,
        ):
            return
        order = self._next_insertion_order
        self._next_insertion_order += 1
        deadline = _Deadline(due_monotonic, order, kind, key, effective_at_utc)
        self._active[identity] = order
        self._current[identity] = deadline
        heapq.heappush(self._deadlines, deadline)

    def cancel(self, kind: str, key: str) -> None:
        self._active.pop((kind, key), None)
        self._current.pop((kind, key), None)

    def shutdown(self) -> None:
        thread = self._thread
        if thread is None:
            return
        self._ingress.stop_accepting()
        self._ingress.wait_until_admitted()
        try:
            self._ingress.seal_stop()
        except RuntimeError as error:
            if "already sealed" not in str(error):
                raise
        thread.join()
        self._thread = None

    def _run(self) -> None:
        try:
            self._with_session(lambda session: self._engine.startup(session, self, self._clock.utc_now()))
        except BaseException as error:
            self._startup_error = error
            self._started.set()
            return
        self._started.set()
        while True:
            due = self._next_deadline_due()
            item = self._ingress.get_for_worker(due)
            if isinstance(item, DeadlineBarrier):
                self._fire_through(item.due_monotonic)
            elif isinstance(item, IngressTombstone):
                continue
            elif isinstance(item, RuleEnvelope):
                self._fire_before(item.received_at_monotonic)
                try:
                    self._with_session(
                        lambda session: self._engine.apply(
                            session,
                            item.event,
                            item.received_at_utc,
                            item.received_at_monotonic,
                            self,
                        )
                    )
                except Exception as error:
                    self._last_error = error
                self._fire_through(item.received_at_monotonic)
            elif isinstance(item, IngressCommand):
                self._fire_before(item.received_at_monotonic)
                try:
                    result = self._with_session(
                        lambda session: self._engine.command(
                            session,
                            item.command,
                            item.received_at_utc,
                            item.received_at_monotonic,
                            self,
                        )
                    )
                except BaseException as error:
                    item.future.set_exception(error)
                else:
                    item.future.set_result(result)
                self._fire_through(item.received_at_monotonic)
            elif isinstance(item, StopMarker):
                self._effective_monotonic = item.received_at_monotonic
                try:
                    self._with_session(
                        lambda session: self._engine.controlled_shutdown(session, item.received_at_utc, self)
                    )
                except Exception as error:
                    self._last_error = error
                finally:
                    self._effective_monotonic = None
                return

    def _next_deadline_due(self) -> float | None:
        self._discard_cancelled()
        return self._deadlines[0].due_monotonic if self._deadlines else None

    def _fire_before(self, boundary: float) -> None:
        self._fire(boundary, inclusive=False)

    def _fire_through(self, boundary: float) -> None:
        self._fire(boundary, inclusive=True)

    def _fire(self, boundary: float, *, inclusive: bool) -> None:
        while True:
            self._discard_cancelled()
            if not self._deadlines:
                return
            deadline = self._deadlines[0]
            if deadline.due_monotonic > boundary or (deadline.due_monotonic == boundary and not inclusive):
                return
            heapq.heappop(self._deadlines)
            if self._active.get((deadline.kind, deadline.key)) != deadline.insertion_order:
                continue
            self._active.pop((deadline.kind, deadline.key), None)
            self._current.pop((deadline.kind, deadline.key), None)
            self._effective_monotonic = deadline.due_monotonic
            try:
                self._with_session(
                    lambda session: self._engine.deadline(
                        session,
                        deadline.kind,
                        deadline.key,
                        deadline.effective_at_utc,
                        self,
                    )
                )
            except Exception as error:
                self._last_error = error
            finally:
                self._effective_monotonic = None

    def _discard_cancelled(self) -> None:
        while self._deadlines:
            deadline = self._deadlines[0]
            if self._active.get((deadline.kind, deadline.key)) == deadline.insertion_order:
                return
            heapq.heappop(self._deadlines)

    def _with_session(self, operation: Callable[[Session], object]) -> object:
        session = self._session_factory()
        try:
            return operation(session)
        finally:
            session.close()
