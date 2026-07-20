from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from threading import Thread
from time import monotonic, sleep

import numpy as np
import pytest
import cv2
from fastapi import FastAPI

import app.main as main_module
from app.camera_service import CameraService
from app.config import AppConfig
from app.contracts import CameraStatus
from app.events import CameraFrameCommitted, DeviceStatusCommitted
from app.models import Camera, CameraEvent
from app.rule_ingress import IngressTicket, RuleEnvelope, RuleIngress
from app.vision import CameraUnavailable, VisionPipeline


NOW = datetime(2026, 7, 16, 12, 0, tzinfo=UTC)
FRAME = np.zeros((480, 640, 3), dtype=np.uint8)
ZONES = {"food_bowl": (40, 260, 260, 470), "pet_bed": (320, 180, 630, 470)}


class Source:
    def __init__(self, *items: object, close_error: Exception | None = None) -> None:
        self.items = list(items)
        self.reads = 0
        self.closed = False
        self.close_error = close_error

    def read(self) -> np.ndarray:
        self.reads += 1
        item = self.items.pop(0)
        if isinstance(item, Exception):
            raise item
        return item  # type: ignore[return-value]

    def close(self) -> None:
        self.closed = True
        if self.close_error is not None:
            raise self.close_error


class Detector:
    def __init__(self, detections: tuple[dict[str, object], ...] = (), error: Exception | None = None) -> None:
        self.detections = detections
        self.error = error
        self.calls = 0

    def __call__(self, _frame: np.ndarray) -> tuple[dict[str, object], ...]:
        self.calls += 1
        if self.error is not None:
            raise self.error
        return self.detections


class FakeSession:
    def __init__(self, calls: list[str], *, fail_commit: bool = False) -> None:
        self.calls = calls
        self.fail_commit = fail_commit
        self.camera: Camera | None = None
        self.events: list[CameraEvent] = []

    def get(self, model: object, _key: str) -> object | None:
        assert model is Camera
        return self.camera

    def add(self, row: object) -> None:
        if isinstance(row, Camera):
            self.camera = row
        elif isinstance(row, CameraEvent):
            self.events.append(row)
        self.calls.append(f"add:{type(row).__name__}")

    def flush(self) -> None:
        for index, row in enumerate(self.events, 101):
            row.id = index
        self.calls.append("flush")

    def commit(self) -> None:
        self.calls.append("commit")
        if self.fail_commit:
            raise RuntimeError("database down")

    def rollback(self) -> None:
        self.calls.append("rollback")

    def close(self) -> None:
        self.calls.append("close")


class RecordingIngress:
    def __init__(self, calls: list[str], times: list[datetime] | None = None) -> None:
        self.calls = calls
        self.times = list(times or [NOW])
        self.committed: list[CameraFrameCommitted] = []
        self.tombstones: list[str] = []

    def begin(self, source: str) -> IngressTicket:
        self.calls.append(f"begin:{source}")
        return IngressTicket(len(self.committed) + len(self.tombstones) + 1, self.times.pop(0), 1.0)

    def resolve_committed(self, _ticket: IngressTicket, event: CameraFrameCommitted) -> None:
        assert self.calls[-2:] == ["commit", "close"]
        self.calls.append("resolve")
        self.committed.append(event)

    def resolve_tombstone(self, _ticket: IngressTicket, reason: str) -> None:
        self.calls.append(f"tombstone:{reason}")
        self.tombstones.append(reason)


def service_for(
    source: Source,
    detector: Detector,
    *,
    times: list[datetime] | None = None,
    fail_commit: bool = False,
) -> tuple[CameraService, RecordingIngress, list[FakeSession], list[str]]:
    calls: list[str] = []
    ingress = RecordingIngress(calls, times)
    sessions: list[FakeSession] = []

    def factory() -> FakeSession:
        session = FakeSession(calls, fail_commit=fail_commit)
        sessions.append(session)
        return session

    pipeline = VisionPipeline(detector, ZONES, source=source)
    return CameraService(pipeline, ingress, factory), ingress, sessions, calls


@pytest.mark.parametrize(
    "bad_frame",
    [
        np.zeros((480, 640, 3), dtype=np.float32),
        np.zeros((479, 640, 3), dtype=np.uint8),
        np.zeros((480, 639, 3), dtype=np.uint8),
    ],
)
def test_invalid_frames_tombstone_before_inference_and_publish_nothing(bad_frame: np.ndarray) -> None:
    detector = Detector()
    service, ingress, sessions, calls = service_for(Source(bad_frame), detector)

    assert service.process_once() is False

    assert calls[0] == "begin:camera"
    assert detector.calls == 0
    assert ingress.tombstones == ["invalid_frame_shape"]
    assert ingress.committed == []
    assert service.status == CameraStatus(
        state="offline", fps=0.0, inference_ms=0.0, last_frame_at=None, reason="invalid_frame_shape"
    )
    assert sessions[0].camera is not None and sessions[0].camera.status == "offline"
    with pytest.raises(CameraUnavailable, match="camera_unavailable"):
        service.mjpeg_chunk()


def test_valid_frame_persists_selected_detections_then_closes_before_resolution(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    detections = (
        {"detected_type": "dog", "confidence": 0.8, "xyxy": (330.0, 190.0, 430.0, 290.0)},
        {"detected_type": "dog", "confidence": 0.7, "xyxy": (350.0, 210.0, 450.0, 310.0)},
        {"detected_type": "cat", "confidence": 0.8, "xyxy": (500.0, 200.0, 600.0, 300.0)},
        {"detected_type": "bird", "confidence": 1.0, "xyxy": (0.0, 0.0, 10.0, 10.0)},
    )
    service, ingress, sessions, calls = service_for(Source(FRAME), Detector(detections))

    assert service.process_once() is True

    assert calls[0] == "begin:camera"
    assert len(sessions) == 1
    assert [row.detected_type for row in sessions[0].events] == ["dog", "cat"]
    assert sessions[0].camera is not None
    assert (sessions[0].camera.status, sessions[0].camera.last_frame_at) == ("online", NOW)
    assert (
        sessions[0].events[0].confidence,
        sessions[0].events[0].bbox_x,
        sessions[0].events[0].bbox_y,
        sessions[0].events[0].bbox_width,
        sessions[0].events[0].bbox_height,
        sessions[0].events[0].zone_name,
    ) == (0.8, 330, 190, 100, 100, "pet_bed")
    event = ingress.committed[0]
    assert event == CameraFrameCommitted(
        camera_id="pc-webcam-01",
        observed_at=NOW,
        detection_ids=(101, 102),
        bed_subject_ids=("dog_001", "cat_001"),
        selected_bed_subject_id="dog_001",
    )
    assert event.model_dump().keys() == {
        "camera_id", "observed_at", "detection_ids", "bed_subject_ids", "selected_bed_subject_id"
    }
    assert isinstance(service.latest_frame.detections, tuple)
    assert not list(tmp_path.rglob("*"))

    chunk = service.mjpeg_chunk()
    assert chunk.startswith(b"--frame\r\nContent-Type: image/jpeg\r\n\r\n\xff\xd8")
    assert chunk.endswith(b"\xff\xd9\r\n")
    decoded = cv2.imdecode(np.frombuffer(chunk[len(b"--frame\r\nContent-Type: image/jpeg\r\n\r\n") : -2], dtype=np.uint8), cv2.IMREAD_COLOR)
    assert decoded.shape == (480, 640, 3)
    assert not {"image", "frame", "path"} & set(CameraEvent.__table__.columns.keys())


@pytest.mark.parametrize(
    ("detections", "subjects", "selected"),
    [
        (({"detected_type": "dog", "confidence": 0.6, "xyxy": (330, 190, 430, 290)},), ("dog_001",), "dog_001"),
        (({"detected_type": "cat", "confidence": 0.6, "xyxy": (330, 190, 430, 290)},), ("cat_001",), "cat_001"),
        ((), (), None),
        (
            (
                {"detected_type": "dog", "confidence": 0.7, "xyxy": (330, 190, 430, 290)},
                {"detected_type": "cat", "confidence": 0.8, "xyxy": (500, 200, 600, 300)},
            ),
            ("dog_001", "cat_001"),
            "cat_001",
        ),
    ],
)
def test_frame_event_has_ordered_bed_facts(
    detections: tuple[dict[str, object], ...], subjects: tuple[str, ...], selected: str | None
) -> None:
    service, ingress, _sessions, _calls = service_for(Source(FRAME), Detector(detections))
    assert service.process_once()
    assert ingress.committed[0].bed_subject_ids == subjects
    assert ingress.committed[0].selected_bed_subject_id == selected


def test_missing_source_inference_and_database_failures_resolve_tombstones() -> None:
    cases = [
        (Source(CameraUnavailable("frame_unavailable")), Detector(), False, "frame_unavailable"),
        (Source(RuntimeError("usb failed")), Detector(), False, "camera_error"),
        (Source(FRAME), Detector(error=CameraUnavailable("model_unavailable")), False, "model_unavailable"),
        (Source(FRAME), Detector(error=RuntimeError("boom")), False, "inference_failed"),
        (Source(FRAME), Detector(), True, "database_rollback"),
    ]
    for source, detector, fail_commit, reason in cases:
        service, ingress, sessions, calls = service_for(source, detector, fail_commit=fail_commit)
        assert service.process_once() is False
        assert ingress.tombstones == [reason]
        assert service.status.state == "offline" and service.status.reason == reason
        assert sessions and calls.index("close") < calls.index(f"tombstone:{reason}")
        assert service.latest_frame is None
        assert not service.available_for(NOW, NOW + timedelta(seconds=1))
        with pytest.raises(CameraUnavailable, match="camera_unavailable"):
            service.mjpeg_chunk()


def test_missing_source_and_session_factory_failure_still_resolve_tickets() -> None:
    calls: list[str] = []
    ingress = RecordingIngress(calls, [NOW, NOW + timedelta(seconds=1)])
    missing_source = CameraService(VisionPipeline(Detector(), ZONES), ingress, lambda: FakeSession(calls))
    failing_database = CameraService(
        VisionPipeline(Detector(), ZONES, source=Source(FRAME)),
        ingress,
        lambda: (_ for _ in ()).throw(RuntimeError("database unavailable")),
    )

    assert missing_source.process_once() is False
    assert failing_database.process_once() is False
    assert ingress.tombstones == ["source_unavailable", "database_rollback"]


def test_availability_requires_every_second_in_open_closed_window() -> None:
    times = [NOW + timedelta(seconds=value) for value in (1, 2, 3)]
    service, _ingress, _sessions, _calls = service_for(Source(FRAME, FRAME, FRAME), Detector(), times=times)
    assert all(service.process_once() for _ in times)

    assert service.available_for(NOW, NOW + timedelta(seconds=3))
    assert service.available_for(NOW + timedelta(seconds=1), NOW + timedelta(seconds=3))
    assert not service.available_for(NOW, NOW + timedelta(seconds=4))
    hole_service, _ingress, _sessions, _calls = service_for(
        Source(FRAME, FRAME), Detector(), times=[NOW + timedelta(seconds=1), NOW + timedelta(seconds=3)]
    )
    assert hole_service.process_once() and hole_service.process_once()
    assert not hole_service.available_for(NOW, NOW + timedelta(seconds=3))
    assert not service.available_for(NOW, NOW)
    assert not service.available_for(NOW + timedelta(seconds=3), NOW + timedelta(seconds=2))


def test_each_frame_uses_a_fresh_session_and_constructor_replays_nothing() -> None:
    service, ingress, sessions, _calls = service_for(Source(FRAME, FRAME), Detector(), times=[NOW, NOW + timedelta(seconds=1)])
    assert sessions == [] and ingress.committed == []
    assert service.process_once() and service.process_once()
    assert len(sessions) == 2 and sessions[0] is not sessions[1]


def test_full_ingress_retains_same_committed_ticket_until_capacity_frees() -> None:
    ingress = RuleIngress(capacity=1)
    first = ingress.begin("mqtt")
    ingress.resolve_committed(first, DeviceStatusCommitted(device_id="entrance-01", status="online", observed_at=NOW))
    source = Source(FRAME, FRAME)
    sessions: list[FakeSession] = []
    calls: list[str] = []
    service = CameraService(
        VisionPipeline(Detector(), ZONES, source=source),
        ingress,
        lambda: sessions.append(FakeSession(calls)) or sessions[-1],
    )
    worker = Thread(target=service.process_once)
    worker.start()
    deadline = monotonic() + 2
    while (not sessions or "commit" not in calls) and monotonic() < deadline:
        sleep(0.01)

    assert worker.is_alive() and source.reads == 1
    assert isinstance(ingress.get(timeout=0.1), RuleEnvelope)
    worker.join(2)
    assert not worker.is_alive() and source.reads == 1
    queued = ingress.get(timeout=0.1)
    assert isinstance(queued, RuleEnvelope) and isinstance(queued.event, CameraFrameCommitted)


def test_worker_starts_once_and_shutdown_joins_and_closes_source() -> None:
    service, ingress, _sessions, _calls = service_for(
        Source(CameraUnavailable("frame_unavailable"), CameraUnavailable("frame_unavailable")), Detector()
    )
    source = service.pipeline.source
    service.start()
    worker = service.worker
    service.start()
    assert service.worker is worker
    deadline = monotonic() + 2
    while not ingress.tombstones and monotonic() < deadline:
        sleep(0.01)
    service.shutdown()
    assert worker is not None and not worker.is_alive()
    assert source is not None and source.closed  # type: ignore[attr-defined]


def test_shutdown_contains_source_close_failure() -> None:
    source = Source(close_error=RuntimeError("close failed"))
    service, _ingress, _sessions, _calls = service_for(source, Detector())
    service.shutdown()
    assert source.closed


def test_shutdown_waits_for_retained_committed_ticket_then_closes_source() -> None:
    ingress = RuleIngress(capacity=1)
    first = ingress.begin("mqtt")
    ingress.resolve_committed(first, DeviceStatusCommitted(device_id="entrance-01", status="online", observed_at=NOW))
    source = Source(FRAME)
    calls: list[str] = []
    service = CameraService(VisionPipeline(Detector(), ZONES, source=source), ingress, lambda: FakeSession(calls))
    service.start()
    deadline = monotonic() + 2
    while "commit" not in calls and monotonic() < deadline:
        sleep(0.01)
    stopper = Thread(target=service.shutdown)
    stopper.start()
    sleep(0.05)
    assert stopper.is_alive() and source.reads == 1
    ingress.get(timeout=0.1)
    stopper.join(2)
    assert not stopper.is_alive() and source.closed and source.reads == 1
    assert isinstance(ingress.get(timeout=0.1), RuleEnvelope)


@pytest.mark.asyncio
async def test_lifespan_owns_disabled_camera_until_rule_worker_can_drain(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[str] = []

    class FakeCameraService:
        pipeline = None

        @classmethod
        def disabled(cls) -> "FakeCameraService":
            calls.append("camera:disabled")
            return cls()

        def shutdown(self) -> None:
            calls.append("camera:shutdown")

    class FakeRuleWorker:
        def __init__(self, **_kwargs: object) -> None:
            pass

        def start(self) -> None:
            pass

        def shutdown(self) -> None:
            pass

    monkeypatch.setattr(
        main_module,
        "load_config",
        lambda: AppConfig(database_url="postgresql+psycopg://petcare:secret@127.0.0.1:55432/petcare"),
    )
    monkeypatch.setattr(main_module, "configure_database", lambda _url: calls.append("database:configure"))
    monkeypatch.setattr(main_module, "dispose_database", lambda: calls.append("database:dispose"))
    monkeypatch.setattr(main_module, "CameraService", FakeCameraService)
    monkeypatch.setattr(main_module, "RuleEngine", lambda **_kwargs: object())
    monkeypatch.setattr(main_module, "RuleWorker", FakeRuleWorker)

    application = FastAPI()
    async with main_module.lifespan(application):
        assert application.state.camera_service.__class__ is FakeCameraService
    assert calls == ["database:configure", "camera:disabled", "camera:shutdown", "database:dispose"]


@pytest.mark.asyncio
async def test_lifespan_disposes_database_when_camera_shutdown_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[str] = []

    class FailingCameraService:
        pipeline = None

        @classmethod
        def disabled(cls) -> "FailingCameraService":
            return cls()

        def shutdown(self) -> None:
            calls.append("camera:shutdown")
            raise RuntimeError("close failed")

    class FakeRuleWorker:
        def __init__(self, **_kwargs: object) -> None:
            pass

        def start(self) -> None:
            pass

        def shutdown(self) -> None:
            pass

    monkeypatch.setattr(
        main_module,
        "load_config",
        lambda: AppConfig(database_url="postgresql+psycopg://petcare:secret@127.0.0.1:55432/petcare"),
    )
    monkeypatch.setattr(main_module, "configure_database", lambda _url: calls.append("database:configure"))
    monkeypatch.setattr(main_module, "dispose_database", lambda: calls.append("database:dispose"))
    monkeypatch.setattr(main_module, "CameraService", FailingCameraService)
    monkeypatch.setattr(main_module, "RuleEngine", lambda **_kwargs: object())
    monkeypatch.setattr(main_module, "RuleWorker", FakeRuleWorker)

    async with main_module.lifespan(FastAPI()):
        pass
    assert calls == ["database:configure", "camera:shutdown", "database:dispose"]
