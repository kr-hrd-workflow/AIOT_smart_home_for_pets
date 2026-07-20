from __future__ import annotations

import threading
import time
import importlib
from datetime import UTC, datetime

import pytest
from fastapi import FastAPI

import app.main as main_module
from app.config import AppConfig
from app.rule_ingress import IngressTombstone, RuleIngress


NOW = datetime(2026, 7, 20, 0, 0, tzinfo=UTC)


class FakeClock:
    def __init__(self) -> None:
        self.utc = NOW
        self.mono = 20.0

    def utc_now(self) -> datetime:
        return self.utc

    def monotonic(self) -> float:
        return self.mono


def test_exact_boundary_ticket_blocks_barrier_until_tombstone_progress() -> None:
    clock = FakeClock()
    ingress = RuleIngress(clock)
    ticket = ingress.begin("mqtt")
    received: list[object] = []

    worker = threading.Thread(target=lambda: received.append(ingress.get_for_worker(20.0, timeout=1.0)))
    worker.start()
    time.sleep(0.02)

    assert worker.is_alive()
    ingress.resolve_tombstone(ticket, "validation_error")
    worker.join(1)

    assert received == [IngressTombstone(ticket_id=1, reason="validation_error")]
    barrier = ingress.get_for_worker(20.0, timeout=0.1)
    assert type(barrier).__name__ == "DeadlineBarrier"
    assert barrier.due_monotonic == 20.0
    assert barrier.last_ticket_id == 1


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


def test_deadline_failure_is_recorded_and_does_not_skip_controlled_shutdown() -> None:
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
            scheduler.schedule("proof", "fails", 20.0, NOW.replace(second=10))

        def deadline(self, *_args: object) -> None:
            calls.append("deadline")
            raise RuntimeError("database write failed")

        def controlled_shutdown(self, *_args: object) -> None:
            calls.append("shutdown")

    worker = rule_worker.RuleWorker(ingress=ingress, clock=clock, session_factory=Session, engine=Engine())
    worker.start()
    clock.mono = 20.0
    worker.shutdown()

    assert calls == ["deadline", "shutdown"]
    assert isinstance(worker.last_error, RuntimeError)
