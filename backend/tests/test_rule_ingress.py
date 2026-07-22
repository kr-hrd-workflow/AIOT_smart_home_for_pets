from __future__ import annotations

import threading
import time
from datetime import UTC, datetime, timedelta

import pytest

from app.events import EVENT_QUEUE_MAXSIZE, SensorReadingCommitted
from app.rule_ingress import IngressTombstone, RuleEnvelope, RuleIngress


NOW = datetime(2026, 7, 16, 0, 0, tzinfo=UTC)
EVENT = SensorReadingCommitted(
    reading_id=1,
    device_id="entrance-01",
    sensor_type="temperature",
    observed_at=NOW,
)


class FakeClock:
    def __init__(self) -> None:
        self.utc = NOW
        self.mono = 10.0

    def utc_now(self) -> datetime:
        return self.utc

    def monotonic(self) -> float:
        return self.mono


def test_begin_captures_receipt_and_allows_one_outstanding_ticket_per_source() -> None:
    clock = FakeClock()
    ingress = RuleIngress(clock)

    first = ingress.begin("mqtt")
    clock.utc += timedelta(seconds=1)
    clock.mono += 1
    second = ingress.begin("camera")

    assert (first.ticket_id, first.received_at_utc, first.received_at_monotonic) == (1, NOW, 10.0)
    assert second.ticket_id == 2
    with pytest.raises(RuntimeError, match="outstanding"):
        ingress.begin("mqtt")
    with pytest.raises(ValueError, match="source"):
        ingress.begin("other")  # type: ignore[arg-type]


def test_reverse_resolution_releases_committed_items_in_ticket_order() -> None:
    ingress = RuleIngress(FakeClock())
    first = ingress.begin("mqtt")
    second = ingress.begin("camera")
    later_done = threading.Event()

    def resolve_later() -> None:
        ingress.resolve_committed(second, EVENT.model_copy(update={"reading_id": 2}))
        later_done.set()

    thread = threading.Thread(target=resolve_later)
    thread.start()
    time.sleep(0.02)
    assert not later_done.is_set()

    ingress.resolve_committed(first, EVENT)
    thread.join(1)
    assert later_done.is_set()
    items = [ingress.get(timeout=0.1), ingress.get(timeout=0.1)]
    assert [item.ticket_id for item in items] == [1, 2]
    assert all(isinstance(item, RuleEnvelope) for item in items)


def test_tombstone_advances_order_and_ticket_resolves_once() -> None:
    ingress = RuleIngress(FakeClock())
    ticket = ingress.begin("mqtt")
    ingress.resolve_tombstone(ticket, "validation_error")

    item = ingress.get(timeout=0.1)
    assert item == IngressTombstone(ticket_id=1, reason="validation_error")
    assert ingress.last_released_ticket_id == 1
    with pytest.raises(RuntimeError, match="resolved"):
        ingress.resolve_tombstone(ticket, "again")


def test_tombstone_capacity_blocks_next_producer_until_worker_pops() -> None:
    ingress = RuleIngress(FakeClock(), capacity=1)
    first = ingress.begin("mqtt")
    ingress.resolve_tombstone(first, "validation_error")
    retained = ingress.begin("mqtt")
    admitted = threading.Event()

    def resolve_retained() -> None:
        ingress.resolve_tombstone(retained, "duplicate")
        admitted.set()

    thread = threading.Thread(target=resolve_retained)
    thread.start()
    time.sleep(0.27)

    assert ingress.queue_full
    assert not admitted.is_set()
    assert ingress.get(timeout=0.1) == IngressTombstone(ticket_id=1, reason="validation_error")
    thread.join(1)
    assert admitted.is_set()
    assert ingress.get(timeout=0.1) == IngressTombstone(ticket_id=2, reason="duplicate")


def test_exact_capacity_retains_same_ticket_until_space_is_available() -> None:
    ingress = RuleIngress(FakeClock())
    assert ingress.capacity == EVENT_QUEUE_MAXSIZE == 1024

    for reading_id in range(1, EVENT_QUEUE_MAXSIZE + 1):
        ticket = ingress.begin("mqtt")
        ingress.resolve_committed(ticket, EVENT.model_copy(update={"reading_id": reading_id}))

    retained = ingress.begin("mqtt")
    admitted = threading.Event()

    def resolve_retained() -> None:
        ingress.resolve_committed(retained, EVENT.model_copy(update={"reading_id": 1025}))
        admitted.set()

    thread = threading.Thread(target=resolve_retained)
    thread.start()
    time.sleep(0.27)
    assert ingress.queue_full
    assert not admitted.is_set()
    with pytest.raises(RuntimeError, match="outstanding"):
        ingress.begin("mqtt")

    first = ingress.get(timeout=0.1)
    thread.join(1)
    assert isinstance(first, RuleEnvelope) and first.ticket_id == 1
    assert admitted.is_set()
    assert ingress.ready_event_count == EVENT_QUEUE_MAXSIZE


def test_graceful_stop_rejects_new_intake_and_waits_for_retained_admission() -> None:
    ingress = RuleIngress(FakeClock(), capacity=1)
    first = ingress.begin("mqtt")
    ingress.resolve_committed(first, EVENT)
    retained = ingress.begin("mqtt")
    thread = threading.Thread(target=ingress.resolve_committed, args=(retained, EVENT))
    thread.start()
    time.sleep(0.02)

    ingress.stop_accepting()
    with pytest.raises(RuntimeError, match="stopped"):
        ingress.begin("camera")
    assert not ingress.wait_until_admitted(timeout=0.02)

    ingress.get(timeout=0.1)
    thread.join(1)
    assert ingress.wait_until_admitted(timeout=0.1)
    assert ingress.get(timeout=0.1).ticket_id == 2
