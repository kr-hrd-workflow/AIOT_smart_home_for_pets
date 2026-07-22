from __future__ import annotations

import time
from collections import OrderedDict, deque
from concurrent.futures import Future
from dataclasses import dataclass
from datetime import UTC, datetime
from threading import Condition
from typing import Literal, Protocol, TypeAlias

from .contracts import BedCalibrationSuccess
from .events import CalibrateBedCommand, DomainEvent, EVENT_QUEUE_MAXSIZE


IngressSource = Literal["mqtt", "camera"]
ADMISSION_RETRY_SECONDS = 0.25


class RuleClock(Protocol):
    def utc_now(self) -> datetime: ...

    def monotonic(self) -> float: ...


class SystemRuleClock:
    def utc_now(self) -> datetime:
        return datetime.now(UTC)

    def monotonic(self) -> float:
        return time.monotonic()


@dataclass(frozen=True, slots=True)
class IngressTicket:
    ticket_id: int
    received_at_utc: datetime
    received_at_monotonic: float


@dataclass(frozen=True, slots=True)
class RuleEnvelope:
    ticket_id: int
    event: DomainEvent
    received_at_utc: datetime
    received_at_monotonic: float


@dataclass(frozen=True, slots=True)
class IngressTombstone:
    ticket_id: int
    reason: str


@dataclass(frozen=True, slots=True)
class IngressCommand:
    ticket_id: int
    command: CalibrateBedCommand
    future: Future[BedCalibrationSuccess]
    received_at_utc: datetime
    received_at_monotonic: float


@dataclass(frozen=True, slots=True)
class DeadlineBarrier:
    due_monotonic: float
    last_ticket_id: int


@dataclass(frozen=True, slots=True)
class StopMarker:
    last_ticket_id: int
    received_at_utc: datetime
    received_at_monotonic: float


IngressItem: TypeAlias = RuleEnvelope | IngressTombstone | IngressCommand
WorkerItem: TypeAlias = IngressItem | DeadlineBarrier | StopMarker


@dataclass(slots=True)
class _Pending:
    source: IngressSource | None
    ticket: IngressTicket
    item: IngressItem | None = None


class RuleIngress:
    def __init__(self, clock: RuleClock | None = None, *, capacity: int = EVENT_QUEUE_MAXSIZE) -> None:
        if capacity <= 0:
            raise ValueError("capacity must be positive")
        self._clock = clock or SystemRuleClock()
        self._condition = Condition()
        self._capacity = capacity
        self._next_ticket_id = 1
        self._pending: OrderedDict[int, _Pending] = OrderedDict()
        self._outstanding_sources: set[IngressSource] = set()
        self._ready: deque[IngressItem] = deque()
        self._ready_receipts: dict[int, float] = {}
        self._ready_event_count = 0
        self._last_released_ticket_id = 0
        self._sealed_deadlines: dict[float, int] = {}
        self._stop_marker: StopMarker | None = None
        self._accepting = True

    @property
    def condition(self) -> Condition:
        return self._condition

    @property
    def capacity(self) -> int:
        return self._capacity

    @property
    def ready_event_count(self) -> int:
        with self._condition:
            return self._ready_event_count

    @property
    def queue_full(self) -> bool:
        with self._condition:
            return len(self._ready) >= self._capacity

    @property
    def last_released_ticket_id(self) -> int:
        with self._condition:
            return self._last_released_ticket_id

    def begin(self, source: IngressSource) -> IngressTicket:
        if source not in {"mqtt", "camera"}:
            raise ValueError("invalid ingress source")
        with self._condition:
            if not self._accepting:
                raise RuntimeError("ingress intake is stopped")
            if source in self._outstanding_sources:
                raise RuntimeError(f"{source} already has an outstanding ticket")
            ticket = IngressTicket(
                self._next_ticket_id,
                self._clock.utc_now(),
                self._clock.monotonic(),
            )
            self._next_ticket_id += 1
            self._pending[ticket.ticket_id] = _Pending(source, ticket)
            self._outstanding_sources.add(source)
            return ticket

    def resolve_committed(self, ticket: IngressTicket, event: DomainEvent) -> None:
        envelope = RuleEnvelope(
            ticket.ticket_id,
            event,
            ticket.received_at_utc,
            ticket.received_at_monotonic,
        )
        self._resolve_and_wait(ticket, envelope)

    def resolve_tombstone(self, ticket: IngressTicket, reason: str) -> None:
        if not reason:
            raise ValueError("tombstone reason must not be empty")
        self._resolve_and_wait(ticket, IngressTombstone(ticket.ticket_id, reason))

    def _resolve_and_wait(self, ticket: IngressTicket, item: IngressItem) -> None:
        with self._condition:
            pending = self._pending.get(ticket.ticket_id)
            if pending is None:
                raise RuntimeError("ticket is already resolved or unknown")
            if pending.ticket is not ticket:
                raise RuntimeError("ticket identity does not match registration")
            if pending.item is not None:
                raise RuntimeError("ticket is already resolved")
            pending.item = item
            self._condition.notify_all()
            self._release_ready_locked()
            while ticket.ticket_id in self._pending:
                self._condition.wait(ADMISSION_RETRY_SECONDS)
                self._release_ready_locked()

    def _release_ready_locked(self, *, ignore_capacity: bool = False) -> None:
        changed = False
        while self._pending:
            ticket_id, pending = next(iter(self._pending.items()))
            if pending.item is None:
                break
            if (
                not ignore_capacity
                and len(self._ready) >= self._capacity
            ):
                break
            item = pending.item
            self._ready.append(item)
            self._ready_receipts[ticket_id] = pending.ticket.received_at_monotonic
            if isinstance(item, (RuleEnvelope, IngressCommand)):
                self._ready_event_count += 1
            self._pending.pop(ticket_id)
            if pending.source is not None:
                self._outstanding_sources.remove(pending.source)
            self._last_released_ticket_id = ticket_id
            changed = True
        if changed:
            self._condition.notify_all()

    def get(self, *, timeout: float | None = None) -> IngressItem:
        deadline = None if timeout is None else time.monotonic() + timeout
        with self._condition:
            while not self._ready:
                remaining = None if deadline is None else deadline - time.monotonic()
                if remaining is not None and remaining <= 0:
                    raise TimeoutError("no ingress item available")
                self._condition.wait(remaining)
            return self._pop_ready_locked()

    def get_for_worker(self, due_monotonic: float | None, *, timeout: float | None = None) -> WorkerItem:
        timeout_at = None if timeout is None else time.monotonic() + timeout
        with self._condition:
            while True:
                already_sealed = due_monotonic is not None and due_monotonic in self._sealed_deadlines
                if self._ready:
                    item = self._ready[0]
                    receipt = self._ready_receipts[item.ticket_id]
                    if due_monotonic is None or already_sealed or receipt <= due_monotonic:
                        return self._pop_ready_locked()
                now = self._clock.monotonic()
                if due_monotonic is not None and now >= due_monotonic and not already_sealed:
                    first_pending = next(iter(self._pending.values()), None)
                    if first_pending is None or first_pending.ticket.received_at_monotonic > due_monotonic:
                        last_ticket_id = self._next_ticket_id - 1
                        self._sealed_deadlines[due_monotonic] = last_ticket_id
                        return DeadlineBarrier(due_monotonic, last_ticket_id)
                if self._stop_marker is not None and not self._ready and not self._pending:
                    marker = self._stop_marker
                    self._stop_marker = None
                    return marker
                remaining = None if timeout_at is None else timeout_at - time.monotonic()
                if remaining is not None and remaining <= 0:
                    raise TimeoutError("no worker item available")
                if due_monotonic is not None and now < due_monotonic:
                    until_due = due_monotonic - now
                    remaining = until_due if remaining is None else min(remaining, until_due)
                self._condition.wait(remaining)

    def _pop_ready_locked(self) -> IngressItem:
        item = self._ready.popleft()
        self._ready_receipts.pop(item.ticket_id, None)
        if isinstance(item, (RuleEnvelope, IngressCommand)):
            self._ready_event_count -= 1
        self._release_ready_locked()
        self._condition.notify_all()
        return item

    def try_submit_command(
        self,
        command: CalibrateBedCommand,
        future: Future[BedCalibrationSuccess],
        *,
        timeout: float = ADMISSION_RETRY_SECONDS,
    ) -> bool:
        timeout_at = time.monotonic() + timeout
        with self._condition:
            while self._accepting and (self._pending or len(self._ready) >= self._capacity):
                remaining = timeout_at - time.monotonic()
                if remaining <= 0:
                    return False
                self._condition.wait(remaining)
            if not self._accepting:
                return False
            ticket = IngressTicket(self._next_ticket_id, self._clock.utc_now(), self._clock.monotonic())
            self._next_ticket_id += 1
            item = IngressCommand(
                ticket.ticket_id,
                command,
                future,
                ticket.received_at_utc,
                ticket.received_at_monotonic,
            )
            self._ready.append(item)
            self._ready_receipts[ticket.ticket_id] = ticket.received_at_monotonic
            self._ready_event_count += 1
            self._last_released_ticket_id = ticket.ticket_id
            self._condition.notify_all()
            return True

    def stop_accepting(self) -> None:
        with self._condition:
            self._accepting = False
            self._condition.notify_all()

    def seal_stop(self) -> StopMarker:
        with self._condition:
            if self._accepting:
                raise RuntimeError("ingress intake must be stopped before sealing")
            if self._pending:
                raise RuntimeError("cannot seal stop with unresolved or retained tickets")
            if self._stop_marker is not None:
                raise RuntimeError("stop marker is already sealed")
            self._stop_marker = StopMarker(
                self._last_released_ticket_id,
                self._clock.utc_now(),
                self._clock.monotonic(),
            )
            self._condition.notify_all()
            return self._stop_marker

    def notify_clock_advanced(self) -> None:
        with self._condition:
            self._condition.notify_all()

    def wait_until_admitted(self, *, timeout: float | None = None) -> bool:
        deadline = None if timeout is None else time.monotonic() + timeout
        with self._condition:
            while self._pending:
                remaining = None if deadline is None else deadline - time.monotonic()
                if remaining is not None and remaining <= 0:
                    return False
                self._condition.wait(remaining)
            return True

    def admit_retained_for_shutdown(self, *, timeout: float | None = None) -> bool:
        deadline = None if timeout is None else time.monotonic() + timeout
        with self._condition:
            if self._accepting:
                raise RuntimeError("ingress intake must be stopped before shutdown admission")
            while self._pending:
                self._release_ready_locked(ignore_capacity=True)
                if not self._pending:
                    return True
                remaining = None if deadline is None else deadline - time.monotonic()
                if remaining is not None and remaining <= 0:
                    return False
                self._condition.wait(remaining)
            return True
