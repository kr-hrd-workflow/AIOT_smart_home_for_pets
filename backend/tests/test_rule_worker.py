from __future__ import annotations

import threading
import time
import importlib
from datetime import UTC, datetime

import pytest
from fastapi import FastAPI

import app.main as main_module
from app.config import AppConfig
from app.events import CalibrateBedCommand, SensorReadingCommitted
from app.rule_ingress import IngressTombstone, RuleIngress


NOW = datetime(2026, 7, 20, 0, 0, tzinfo=UTC)
EVENT = SensorReadingCommitted(
    reading_id=1,
    device_id="petzone-01",
    sensor_type="food_weight",
    observed_at=NOW,
)


class FakeClock:
    def __init__(self) -> None:
        self.utc = NOW
        self.mono = 20.0

    def utc_now(self) -> datetime:
        return self.utc

    def monotonic(self) -> float:
        return self.mono


@pytest.mark.parametrize("receipt", (19.999, 20.0))
def test_pre_boundary_ticket_blocks_barrier_until_tombstone_progress(receipt: float) -> None:
    clock = FakeClock()
    clock.mono = receipt
    ingress = RuleIngress(clock)
    ticket = ingress.begin("mqtt")
    received: list[object] = []

    worker = threading.Thread(target=lambda: received.append(ingress.get_for_worker(20.0, timeout=1.0)))
    worker.start()
    time.sleep(0.02)

    assert worker.is_alive()
    clock.mono = 20.0
    ingress.resolve_tombstone(ticket, "validation_error")
    worker.join(1)

    assert received == [IngressTombstone(ticket_id=1, reason="validation_error")]
    barrier = ingress.get_for_worker(20.0, timeout=0.1)
    assert type(barrier).__name__ == "DeadlineBarrier"
    assert barrier.due_monotonic == 20.0
    assert barrier.last_ticket_id == 1


def test_rule_envelope_runs_after_earlier_and_before_valid_exact_boundary_deadlines() -> None:
    rule_worker = importlib.import_module("app.rule_worker")
    clock = FakeClock()
    ingress = RuleIngress(clock)
    ticket = ingress.begin("mqtt")
    ingress.resolve_committed(ticket, EVENT)
    calls: list[str] = []

    class Session:
        def close(self) -> None:
            pass

    class Engine:
        def startup(self, _session: object, scheduler: object, _now: datetime) -> None:
            scheduler.schedule("proof", "earlier", 19.0, NOW.replace(second=19))
            scheduler.schedule("proof", "exact-first", 20.0, NOW.replace(second=20))
            scheduler.schedule("proof", "cancelled", 20.0, NOW.replace(second=20))
            scheduler.schedule("proof", "exact-second", 20.0, NOW.replace(second=20))
            scheduler.cancel("proof", "cancelled")

        def apply(self, *_args: object) -> None:
            calls.append("envelope")

        def deadline(self, _session: object, _kind: str, key: str, *_args: object) -> None:
            calls.append(key)

        def controlled_shutdown(self, *_args: object) -> None:
            calls.append("shutdown")

    worker = rule_worker.RuleWorker(ingress=ingress, clock=clock, session_factory=Session, engine=Engine())
    worker.start()
    deadline = time.monotonic() + 1
    while "exact-second" not in calls and time.monotonic() < deadline:
        time.sleep(0.01)
    worker.shutdown()

    assert calls == ["earlier", "envelope", "exact-first", "exact-second", "shutdown"]


def test_worker_silence_deadline_keeps_scheduled_utc_after_boundary_ticket() -> None:
    rule_worker = importlib.import_module("app.rule_worker")
    clock = FakeClock()
    clock.mono = 10.0
    ingress = RuleIngress(clock)
    calls: list[tuple[object, ...]] = []

    class Session:
        def close(self) -> None:
            calls.append(("session:close",))

    class Engine:
        def startup(self, _session: object, scheduler: object, _now: datetime) -> None:
            calls.append(("startup",))
            scheduler.schedule("proof", "one", 20.0, NOW.replace(second=10))

        def apply(self, *_args: object) -> None:
            raise AssertionError("tombstones must not reach the engine")

        def deadline(self, _session: object, kind: str, key: str, effective_at: datetime, _scheduler: object) -> None:
            calls.append(("deadline", kind, key, effective_at))

        def controlled_shutdown(self, *_args: object) -> None:
            calls.append(("shutdown",))

    worker = rule_worker.RuleWorker(ingress=ingress, clock=clock, session_factory=Session, engine=Engine())
    worker.start()
    clock.mono = 20.0
    ticket = ingress.begin("mqtt")
    ingress.resolve_tombstone(ticket, "duplicate")
    ingress.notify_clock_advanced()

    deadline = time.monotonic() + 1
    while not any(call[0] == "deadline" for call in calls) and time.monotonic() < deadline:
        time.sleep(0.01)
    worker.shutdown()

    assert ("deadline", "proof", "one", NOW.replace(second=10)) in calls
    assert calls.index(("deadline", "proof", "one", NOW.replace(second=10))) < calls.index(("shutdown",))


@pytest.mark.asyncio
async def test_lifespan_starts_one_shared_clock_worker_before_mqtt_and_drains_last(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[str] = []
    shared = FakeClock()

    class Ingress:
        def __init__(self, clock: object) -> None:
            assert clock is shared
            calls.append("ingress")

        def stop_accepting(self) -> None:
            calls.append("intake:stop")

    class Engine:
        def __init__(self, *, config: object, camera_service: object) -> None:
            calls.append("engine")

    class Worker:
        def __init__(self, *, ingress: object, clock: object, session_factory: object, engine: object) -> None:
            assert clock is shared
            calls.append("worker")

        def start(self) -> None:
            calls.append("worker:start")

        def shutdown(self) -> None:
            calls.append("worker:shutdown")

    class Mqtt:
        @classmethod
        def disabled(cls) -> "Mqtt":
            return cls()

        def __init__(self, **_kwargs: object) -> None:
            pass

        def start(self) -> None:
            calls.append("mqtt:start")

        def stop(self) -> None:
            calls.append("mqtt:stop")

    class Camera:
        pipeline = None

        @classmethod
        def disabled(cls) -> "Camera":
            calls.append("camera")
            return cls()

        def shutdown(self) -> None:
            calls.append("camera:shutdown")

    monkeypatch.setattr(
        main_module,
        "load_config",
        lambda: AppConfig(
            database_url="postgresql+psycopg://petcare:x@127.0.0.1:55432/petcare",
            mqtt_profile="local_live",
            mqtt_username="petcare",
            mqtt_password="x",
            camera_source="disabled",
        ),
    )
    monkeypatch.setattr(main_module, "configure_database", lambda _url: calls.append("database"))
    monkeypatch.setattr(main_module, "dispose_database", lambda: calls.append("dispose"))
    monkeypatch.setattr(main_module, "SystemRuleClock", lambda: shared)
    monkeypatch.setattr(main_module, "RuleIngress", Ingress)
    monkeypatch.setattr(main_module, "RuleEngine", Engine)
    monkeypatch.setattr(main_module, "RuleWorker", Worker)
    monkeypatch.setattr(main_module, "MqttIngestor", Mqtt)
    monkeypatch.setattr(main_module, "CameraService", Camera)
    monkeypatch.setattr(main_module, "build_camera_service", lambda *_args: Camera.disabled())
    monkeypatch.setattr(main_module, "load_mqtt_endpoint", lambda *_args: object())

    application = FastAPI()
    async with main_module.lifespan(application):
        calls.append("yield")

    assert calls == [
        "database",
        "ingress",
        "camera",
        "engine",
        "worker",
        "worker:start",
        "mqtt:start",
        "yield",
        "intake:stop",
        "mqtt:stop",
        "camera:shutdown",
        "worker:shutdown",
        "dispose",
    ]


def test_stop_marker_does_not_skip_a_deadline_already_due_at_shutdown() -> None:
    rule_worker = importlib.import_module("app.rule_worker")
    clock = FakeClock()
    clock.mono = 10.0
    ingress = RuleIngress(clock)
    calls: list[str] = []

    class Session:
        def close(self) -> None:
            pass

    class Engine:
        def startup(self, _session: object, scheduler: object, _now: datetime) -> None:
            scheduler.schedule("proof", "due", 20.0, NOW.replace(second=10))

        def deadline(self, *_args: object) -> None:
            calls.append("deadline")

        def controlled_shutdown(self, *_args: object) -> None:
            calls.append("shutdown")

    worker = rule_worker.RuleWorker(ingress=ingress, clock=clock, session_factory=Session, engine=Engine())
    worker.start()
    clock.mono = 20.0
    worker.shutdown()

    assert calls == ["deadline", "shutdown"]
    assert worker.thread is None
    assert worker.last_error is None


def test_persistent_deadline_shutdown_times_out_then_recovery_reaches_controlled_shutdown() -> None:
    rule_worker = importlib.import_module("app.rule_worker")
    clock = FakeClock()
    clock.mono = 10.0
    ingress = RuleIngress(clock)
    calls: list[str] = []
    attempted = threading.Event()
    recover = threading.Event()

    class Session:
        def close(self) -> None:
            pass

    class Engine:
        def startup(self, _session: object, scheduler: object, _now: datetime) -> None:
            scheduler.schedule("proof", "fails", 20.0, NOW.replace(second=10))

        def deadline(self, *_args: object) -> None:
            calls.append("deadline")
            attempted.set()
            if not recover.is_set():
                raise RuntimeError("database write failed")

        def controlled_shutdown(self, *_args: object) -> None:
            calls.append("shutdown")

    worker = rule_worker.RuleWorker(ingress=ingress, clock=clock, session_factory=Session, engine=Engine())
    worker.start()
    clock.mono = 20.0
    ingress.notify_clock_advanced()
    assert attempted.wait(1)
    with pytest.raises(rule_worker.RuleShutdownUnavailable):
        worker.shutdown(timeout=0.2)

    assert set(calls) == {"deadline"}
    assert isinstance(worker.last_error, RuntimeError)
    assert worker.thread is not None and worker.thread.is_alive()

    recover.set()
    deadline = time.monotonic() + 2
    while worker.thread is not None and worker.thread.is_alive() and time.monotonic() < deadline:
        time.sleep(0.01)
    worker.shutdown(timeout=0.5)

    assert list(dict.fromkeys(calls)) == ["deadline", "shutdown"]
    assert worker.thread is None


def test_failed_envelope_retries_before_a_later_ticket_under_silence() -> None:
    rule_worker = importlib.import_module("app.rule_worker")
    clock = FakeClock()
    ingress = RuleIngress(clock)
    first_failed = threading.Event()
    calls: list[tuple[str, int | None]] = []
    committed: list[int] = []
    published: list[int] = []

    class Session:
        pending: int | None = None

        def commit(self) -> None:
            if not first_failed.is_set():
                first_failed.set()
                raise RuntimeError("commit failed")
            assert self.pending is not None
            committed.append(self.pending)

        def close(self) -> None:
            pass

    class Engine:
        def startup(self, *_args: object) -> None:
            pass

        def apply(self, session: Session, event: SensorReadingCommitted, *_args: object) -> None:
            calls.append(("apply", event.reading_id))
            session.pending = event.reading_id
            session.commit()
            published.append(event.reading_id)

        def controlled_shutdown(self, *_args: object) -> None:
            calls.append(("shutdown", None))

    worker = rule_worker.RuleWorker(ingress=ingress, clock=clock, session_factory=Session, engine=Engine())
    worker.start()
    first = ingress.begin("mqtt")
    ingress.resolve_committed(first, EVENT)
    assert first_failed.wait(1)
    second = ingress.begin("camera")
    ingress.resolve_committed(
        second,
        SensorReadingCommitted(
            reading_id=2,
            device_id="petzone-01",
            sensor_type="food_weight",
            observed_at=NOW,
        ),
    )
    deadline = time.monotonic() + 1
    while len(published) < 2 and time.monotonic() < deadline:
        time.sleep(0.01)
    worker.shutdown()

    assert calls == [("apply", 1), ("apply", 1), ("apply", 2), ("shutdown", None)]
    assert committed == published == [1, 2]


def test_failed_deadline_retries_before_a_later_deadline_under_silence() -> None:
    rule_worker = importlib.import_module("app.rule_worker")
    clock = FakeClock()
    clock.mono = 10.0
    ingress = RuleIngress(clock)
    first_failed = threading.Event()
    calls: list[str] = []
    committed: list[str] = []
    published: list[str] = []

    class Session:
        pending: str | None = None

        def commit(self) -> None:
            if not first_failed.is_set():
                first_failed.set()
                raise RuntimeError("commit failed")
            assert self.pending is not None
            committed.append(self.pending)

        def close(self) -> None:
            pass

    class Engine:
        def startup(self, _session: object, scheduler: object, _now: datetime) -> None:
            scheduler.schedule("proof", "first", 20.0, NOW.replace(second=10))
            scheduler.schedule("proof", "later", 21.0, NOW.replace(second=11))

        def deadline(self, session: Session, _kind: str, key: str, *_args: object) -> None:
            calls.append(key)
            session.pending = key
            session.commit()
            published.append(key)

        def controlled_shutdown(self, *_args: object) -> None:
            calls.append("shutdown")

    worker = rule_worker.RuleWorker(ingress=ingress, clock=clock, session_factory=Session, engine=Engine())
    worker.start()
    clock.mono = 21.0
    ingress.notify_clock_advanced()
    assert first_failed.wait(1)
    deadline = time.monotonic() + 1
    while len(committed) < 2 and time.monotonic() < deadline:
        time.sleep(0.01)
    worker.shutdown()

    assert calls == ["first", "first", "later", "shutdown"]
    assert committed == published == ["first", "later"]


def test_shutdown_timeout_preserves_failed_head_and_recovery_drains_retained_tickets_in_order() -> None:
    rule_worker = importlib.import_module("app.rule_worker")
    clock = FakeClock()
    ingress = RuleIngress(clock, capacity=1)
    first_failed = threading.Event()
    recover = threading.Event()
    calls: list[int | str] = []
    committed: list[int] = []
    resolved: list[int] = []
    shutdown_errors: list[BaseException] = []

    class Session:
        def close(self) -> None:
            pass

    class Engine:
        def startup(self, *_args: object) -> None:
            pass

        def apply(self, _session: object, event: SensorReadingCommitted, *_args: object) -> None:
            calls.append(event.reading_id)
            if not recover.is_set():
                first_failed.set()
                raise RuntimeError("persistent commit failure")
            committed.append(event.reading_id)

        def controlled_shutdown(self, *_args: object) -> None:
            calls.append("shutdown")

    def event(reading_id: int) -> SensorReadingCommitted:
        return SensorReadingCommitted(
            reading_id=reading_id,
            device_id="petzone-01",
            sensor_type="food_weight",
            observed_at=NOW,
        )

    def resolve_third() -> None:
        ingress.resolve_committed(third, event(3))
        resolved.append(3)

    def request_shutdown() -> None:
        try:
            worker.shutdown(timeout=0.2)
        except BaseException as error:
            shutdown_errors.append(error)

    worker = rule_worker.RuleWorker(ingress=ingress, clock=clock, session_factory=Session, engine=Engine())
    worker.start()
    first = ingress.begin("mqtt")
    ingress.resolve_committed(first, event(1))
    assert first_failed.wait(1)
    second = ingress.begin("mqtt")
    ingress.resolve_committed(second, event(2))
    third = ingress.begin("mqtt")
    producer = threading.Thread(target=resolve_third)
    producer.start()
    time.sleep(0.05)
    assert producer.is_alive()

    shutdown = threading.Thread(target=request_shutdown)
    shutdown.start()
    shutdown.join(1)
    producer.join(0.5)
    bounded = not shutdown.is_alive()
    producer_released_before_recovery = not producer.is_alive()
    committed_before_recovery = list(committed)
    calls_before_recovery = list(calls)

    recover.set()
    producer.join(2)
    deadline = time.monotonic() + 2
    while worker.thread is not None and worker.thread.is_alive() and time.monotonic() < deadline:
        time.sleep(0.01)
    if worker.thread is not None:
        worker.shutdown(timeout=0.5)

    assert bounded
    assert producer_released_before_recovery
    assert len(shutdown_errors) == 1
    assert isinstance(shutdown_errors[0], rule_worker.RuleShutdownUnavailable)
    assert committed_before_recovery == []
    assert set(calls_before_recovery) == {1}
    assert resolved == [3]
    assert committed == [1, 2, 3]
    assert list(dict.fromkeys(calls)) == [1, 2, 3, "shutdown"]
    assert worker.thread is None


def test_shutdown_timeout_preserves_failed_deadline_before_retained_envelopes() -> None:
    rule_worker = importlib.import_module("app.rule_worker")
    clock = FakeClock()
    clock.mono = 10.0
    ingress = RuleIngress(clock, capacity=1)
    deadline_failed = threading.Event()
    recover = threading.Event()
    calls: list[int | str] = []
    committed: list[int | str] = []
    resolved: list[int] = []
    shutdown_errors: list[BaseException] = []

    class Session:
        def close(self) -> None:
            pass

    class Engine:
        def startup(self, _session: object, scheduler: object, _now: datetime) -> None:
            scheduler.schedule("proof", "head", 20.0, NOW.replace(second=20))

        def deadline(self, *_args: object) -> None:
            calls.append("deadline")
            if not recover.is_set():
                deadline_failed.set()
                raise RuntimeError("persistent deadline commit failure")
            committed.append("deadline")

        def apply(self, _session: object, event: SensorReadingCommitted, *_args: object) -> None:
            calls.append(event.reading_id)
            committed.append(event.reading_id)

        def controlled_shutdown(self, *_args: object) -> None:
            calls.append("shutdown")

    def event(reading_id: int) -> SensorReadingCommitted:
        return SensorReadingCommitted(
            reading_id=reading_id,
            device_id="petzone-01",
            sensor_type="food_weight",
            observed_at=NOW,
        )

    worker = rule_worker.RuleWorker(ingress=ingress, clock=clock, session_factory=Session, engine=Engine())
    worker.start()
    clock.mono = 20.0
    ingress.notify_clock_advanced()
    assert deadline_failed.wait(1)
    first = ingress.begin("mqtt")
    ingress.resolve_committed(first, event(1))
    second = ingress.begin("mqtt")

    def resolve_second() -> None:
        ingress.resolve_committed(second, event(2))
        resolved.append(2)

    producer = threading.Thread(target=resolve_second)
    producer.start()
    time.sleep(0.05)
    assert producer.is_alive()

    def request_shutdown() -> None:
        try:
            worker.shutdown(timeout=0.2)
        except BaseException as error:
            shutdown_errors.append(error)

    shutdown = threading.Thread(target=request_shutdown)
    shutdown.start()
    shutdown.join(1)
    producer.join(0.5)
    bounded = not shutdown.is_alive()
    producer_released_before_recovery = not producer.is_alive()
    committed_before_recovery = list(committed)
    calls_before_recovery = list(calls)

    recover.set()
    producer.join(2)
    deadline = time.monotonic() + 2
    while worker.thread is not None and worker.thread.is_alive() and time.monotonic() < deadline:
        time.sleep(0.01)
    if worker.thread is not None:
        worker.shutdown(timeout=0.5)

    assert bounded
    assert producer_released_before_recovery
    assert len(shutdown_errors) == 1
    assert isinstance(shutdown_errors[0], rule_worker.RuleShutdownUnavailable)
    assert committed_before_recovery == []
    assert set(calls_before_recovery) == {"deadline"}
    assert resolved == [2]
    assert committed == ["deadline", 1, 2]
    assert list(dict.fromkeys(calls)) == ["deadline", 1, 2, "shutdown"]
    assert worker.thread is None


@pytest.mark.parametrize(
    "key",
    ("eating_camera_stale", "eating_dwell:dog_001", "eating_rearm"),
)
def test_consumed_rule_deadline_cannot_reinsert_the_same_due_pair_under_silence(key: str) -> None:
    rule_worker = importlib.import_module("app.rule_worker")
    clock = FakeClock()
    clock.mono = 10.0
    ingress = RuleIngress(clock)
    calls: list[str] = []

    class Session:
        def close(self) -> None:
            pass

    class Engine:
        def startup(self, _session: object, scheduler: object, _now: datetime) -> None:
            scheduler.schedule("rule_state", key, 20.0, NOW.replace(second=10))

        def deadline(self, _session: object, _kind: str, _key: str, _at: datetime, scheduler: object) -> None:
            calls.append("deadline")
            if len(calls) == 1:
                scheduler.schedule("rule_state", key, 20.0, NOW.replace(second=10))

        def controlled_shutdown(self, *_args: object) -> None:
            calls.append("shutdown")

    worker = rule_worker.RuleWorker(ingress=ingress, clock=clock, session_factory=Session, engine=Engine())
    worker.start()
    clock.mono = 20.0
    ingress.notify_clock_advanced()
    deadline = time.monotonic() + 1
    while not calls and time.monotonic() < deadline:
        time.sleep(0.01)
    worker.shutdown()

    assert calls == ["deadline", "shutdown"]


def test_cancelled_command_never_executes_or_mutates_rule_state() -> None:
    rule_worker = importlib.import_module("app.rule_worker")
    clock = FakeClock()
    ingress = RuleIngress(clock)
    entered_apply = threading.Event()
    release_apply = threading.Event()
    calls: list[str] = []

    class Session:
        def close(self) -> None:
            pass

    class Engine:
        baseline = 100

        def startup(self, *_args: object) -> None:
            pass

        def apply(self, *_args: object) -> None:
            entered_apply.set()
            release_apply.wait(1)

        def command(self, *_args: object) -> object:
            self.baseline = 200
            calls.append("command")
            return object()

        def controlled_shutdown(self, *_args: object) -> None:
            calls.append("shutdown")

    engine = Engine()
    worker = rule_worker.RuleWorker(ingress=ingress, clock=clock, session_factory=Session, engine=engine)
    worker.start()
    ticket = ingress.begin("mqtt")
    ingress.resolve_committed(ticket, EVENT)
    assert entered_apply.wait(1)
    future = worker.submit(CalibrateBedCommand(device_id="petzone-01"))
    assert future.cancel()
    release_apply.set()
    worker.shutdown()

    assert future.cancelled()
    assert engine.baseline == 100
    assert calls == ["shutdown"]


def test_command_cannot_be_cancelled_after_execution_has_started() -> None:
    rule_worker = importlib.import_module("app.rule_worker")
    clock = FakeClock()
    ingress = RuleIngress(clock)
    entered_command = threading.Event()
    release_command = threading.Event()
    result = object()
    calls: list[str] = []

    class Session:
        def close(self) -> None:
            pass

    class Engine:
        def startup(self, *_args: object) -> None:
            pass

        def command(self, *_args: object) -> object:
            entered_command.set()
            release_command.wait(1)
            calls.append("command")
            return result

        def controlled_shutdown(self, *_args: object) -> None:
            calls.append("shutdown")

    worker = rule_worker.RuleWorker(ingress=ingress, clock=clock, session_factory=Session, engine=Engine())
    worker.start()
    future = worker.submit(CalibrateBedCommand(device_id="petzone-01"))
    assert entered_command.wait(1)
    cancelled = future.cancel()
    release_command.set()
    worker.shutdown()

    assert not cancelled
    assert future.result(timeout=0.1) is result
    assert calls == ["command", "shutdown"]


def test_ticket_registered_after_a_sealed_boundary_cannot_rewind_it() -> None:
    clock = FakeClock()
    ingress = RuleIngress(clock)

    barrier = ingress.get_for_worker(20.0, timeout=0.1)
    ticket = ingress.begin("mqtt")
    ingress.resolve_tombstone(ticket, "after_boundary")

    assert type(barrier).__name__ == "DeadlineBarrier"
    assert ingress.get_for_worker(20.0, timeout=0.1) == IngressTombstone(
        ticket_id=ticket.ticket_id,
        reason="after_boundary",
    )
