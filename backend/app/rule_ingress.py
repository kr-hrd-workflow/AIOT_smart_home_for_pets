from __future__ import annotations

import time
from collections import OrderedDict, deque
from dataclasses import dataclass
from datetime import UTC, datetime
from threading import Condition
from typing import Literal, Protocol, TypeAlias

from .events import DomainEvent, EVENT_QUEUE_MAXSIZE


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


IngressItem: TypeAlias = RuleEnvelope | IngressTombstone


@dataclass(slots=True)
class _Pending:
    source: IngressSource
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
        self._ready_event_count = 0
        self._last_released_ticket_id = 0
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
            return self._ready_event_count >= self._capacity

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
            self._release_ready_locked()
            while ticket.ticket_id in self._pending:
                self._condition.wait(ADMISSION_RETRY_SECONDS)
                self._release_ready_locked()

    def _release_ready_locked(self) -> None:
        changed = False
        while self._pending:
            ticket_id, pending = next(iter(self._pending.items()))
            if pending.item is None:
                break
            if isinstance(pending.item, RuleEnvelope) and self._ready_event_count >= self._capacity:
                break
            item = pending.item
            self._ready.append(item)
            if isinstance(item, RuleEnvelope):
                self._ready_event_count += 1
            self._pending.pop(ticket_id)
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
            item = self._ready.popleft()
            if isinstance(item, RuleEnvelope):
                self._ready_event_count -= 1
            self._release_ready_locked()
            self._condition.notify_all()
            return item

    def stop_accepting(self) -> None:
        with self._condition:
            self._accepting = False
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
