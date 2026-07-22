from __future__ import annotations

import asyncio
import importlib
from concurrent.futures import Future
from datetime import UTC, datetime
from threading import Event
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.routing import APIRoute, APIWebSocketRoute
from fastapi.testclient import TestClient
from paho.mqtt.packettypes import PacketTypes
from paho.mqtt.reasoncodes import ReasonCode
from sqlalchemy import create_engine
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.api import DEFAULT_ALLOWED_ORIGINS, install_api
from app.contracts import (
    BedCalibrationChannel,
    BedCalibrationError,
    BedCalibrationSuccess,
    BedChannelStatus,
    BedStatus,
    CameraStatus,
    SevenDayComparison,
)
from app.rules import BedCalibrationRejected
from app.rules import RestMetrics, RuleEngine
from app.rule_worker import RuleQueueUnavailable
from app.vision import CameraUnavailable
from app.bed import CalibrationSnapshot, CameraFact, PressureFact
from app.config import AppConfig
from app.mqtt_ingest import MqttEndpoint, MqttIngestor


NOW = datetime(2026, 7, 20, 12, 0, tzinfo=UTC)


class Clock:
    def utc_now(self) -> datetime:
        return NOW


class Worker:
    def __init__(self, result: object | BaseException | None = None) -> None:
        self.thread = SimpleNamespace(is_alive=lambda: True)
        self.last_error = None
        self.result = result or calibration_success()
        self.submitted: list[object] = []

    def submit(self, command: object) -> Future[BedCalibrationSuccess]:
        self.submitted.append(command)
        if isinstance(self.result, RuleQueueUnavailable):
            raise self.result
        future: Future[BedCalibrationSuccess] = Future()
        if isinstance(self.result, BaseException):
            future.set_exception(self.result)
        else:
            future.set_result(self.result)
        return future


class Camera:
    def __init__(self, chunks: list[bytes] | None = None) -> None:
        self.status = CameraStatus(
            state="online",
            fps=5.0,
            inference_ms=20.0,
            last_frame_at=NOW,
            reason=None,
        )
        self.chunks = list(
            [b"--frame\r\nContent-Type: image/jpeg\r\n\r\nabc\r\n"] if chunks is None else chunks
        )
        self.zone_updates: list[dict[str, tuple[int, int, int, int]]] = []

    def replace_zones(self, zones: dict[str, tuple[int, int, int, int]]) -> None:
        self.zone_updates.append(zones)

    def mjpeg_chunk(self) -> bytes:
        if not self.chunks:
            raise CameraUnavailable("camera_unavailable")
        return self.chunks.pop(0)


def bed_status() -> BedStatus:
    return BedStatus(
        device_id="petzone-01",
        sensor_state="ready",
        pressure_state="empty",
        fusion_state="empty",
        camera_confirmed=False,
        channels=[
            BedChannelStatus(
                channel=channel,
                raw=100,
                baseline=100.0,
                delta=0.0,
                polarity=1,
                available=True,
                observed_at=NOW,
            )
            for channel in ("left", "center", "right")
        ],
        current_rest_seconds=0,
        today_rest_seconds=0,
        nighttime_exit_count=0,
        seven_day=SevenDayComparison(
            status="insufficient_data",
            today_seconds=0,
            baseline_seconds=None,
            difference_seconds=None,
            percent_change=None,
            complete_days=0,
        ),
        calibrated_at=NOW,
    )


def calibration_success() -> BedCalibrationSuccess:
    return BedCalibrationSuccess(
        device_id="petzone-01",
        calibrated_at=NOW,
        window_start=datetime(2026, 7, 20, 11, 59, tzinfo=UTC),
        window_end=NOW,
        channels=[
            BedCalibrationChannel(channel=channel, sample_count=60, baseline=100.0, polarity=1)
            for channel in ("left", "center", "right")
        ],
    )


def test_rule_engine_exposes_a_deep_copied_worker_owned_bed_snapshot() -> None:
    published: list[object] = []
    engine = RuleEngine(
        config=AppConfig(database_url="postgresql+psycopg://u:p@127.0.0.1:55432/petcare_test"),
        camera_service=SimpleNamespace(),
    )
    engine.dashboard_publisher = published.append
    engine.bed.load_calibration(
        CalibrationSnapshot(
            window_start=datetime(2026, 7, 20, 11, 59, tzinfo=UTC),
            window_end=NOW,
            counts=(60, 60, 60),
            baselines=(100.0, 100.0, 100.0),
            polarities=(1, 1, 1),
            stability_limits=(40, 40, 40),
            entry_threshold=450,
            exit_threshold=250,
        ),
        restart=False,
    )
    for index, channel in enumerate(("left", "center", "right"), start=1):
        engine.bed.observe_pressure(PressureFact(index, channel, 100, NOW, NOW, 10.0))
    engine.bed.observe_camera(CameraFact(NOW, NOW, 10.0, (), None, {}))
    engine.rest_metrics = lambda _session, _now: RestMetrics(
        today_seconds=0,
        nighttime_exit_count=0,
        seven_day=SevenDayComparison(
            status="insufficient_data",
            today_seconds=0,
            baseline_seconds=None,
            difference_seconds=None,
            percent_change=None,
            complete_days=0,
        ),
    )

    class EmptyResult:
        def scalar_one_or_none(self) -> None:
            return None

    class Session:
        def execute(self, _statement: object) -> EmptyResult:
            return EmptyResult()

    engine.refresh_dashboard_snapshot(Session(), NOW, 10.0)
    first = engine.bed_status_snapshot
    assert first is not None
    first.channels[0].raw = 999
    second = engine.bed_status_snapshot
    assert second is not None
    assert second.channels[0].raw == 100
    assert published == [{"type": "bed_status", "payload": second}]


def test_post_commit_dashboard_snapshot_failure_does_not_retry_core_event() -> None:
    engine = RuleEngine(
        config=AppConfig(database_url="postgresql+psycopg://u:p@127.0.0.1:55432/petcare_test"),
        camera_service=SimpleNamespace(),
    )
    engine._evaluate_and_commit = lambda *_args: ([], [])
    engine._sync_state_deadlines = lambda _scheduler: None

    def fail_snapshot(*_args: object) -> None:
        raise SQLAlchemyError("dashboard snapshot read failed")

    engine.refresh_dashboard_snapshot = fail_snapshot
    engine._evaluate(SimpleNamespace(), NOW, 10.0, SimpleNamespace())


def test_mqtt_health_tracks_real_connection_callbacks() -> None:
    ingestor = MqttIngestor(
        ingress=SimpleNamespace(),
        session_factory=lambda: None,
        endpoint=MqttEndpoint("127.0.0.1", 18883),
        username="user",
        password="password",
    )
    client = SimpleNamespace(subscribe=lambda _topics: None)
    assert not ingestor.connected
    ingestor._on_connect(client, None, None, ReasonCode(PacketTypes.CONNACK), None)
    assert ingestor.connected
    ingestor._on_connect(
        client,
        None,
        None,
        ReasonCode(PacketTypes.CONNACK, "Not authorized"),
        None,
    )
    assert not ingestor.connected
    assert ingestor.last_error == "MQTT connection failed: Not authorized"
    ingestor._on_connect(client, None, None, ReasonCode(PacketTypes.CONNACK), None)
    assert ingestor.connected
    ingestor._on_disconnect(client, None, None, 0, None)
    assert not ingestor.connected


@pytest.mark.asyncio
async def test_lifespan_disposes_database_and_hub_when_worker_shutdown_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    main_module = importlib.import_module("app.main")
    calls: list[str] = []
    shutdown_started = Event()
    broadcaster_progress = Event()

    class Ingress:
        def __init__(self, _clock: object) -> None:
            pass

        def stop_accepting(self) -> None:
            calls.append("ingress:stop")

    class CameraService:
        pipeline = None

        def shutdown(self) -> None:
            calls.append("camera:shutdown")

    class Worker:
        def __init__(self, **_kwargs: object) -> None:
            pass

        def start(self) -> None:
            pass

        def shutdown(self) -> None:
            calls.append("worker:shutdown")
            shutdown_started.set()
            if not broadcaster_progress.wait(0.5):
                raise RuntimeError("event loop blocked")
            raise RuntimeError("shutdown failed")

    class Hub:
        def start_broadcaster(self) -> asyncio.Task[None]:
            async def done() -> None:
                while not shutdown_started.is_set():
                    await asyncio.sleep(0)
                broadcaster_progress.set()

            return asyncio.create_task(done())

        def shutdown(self) -> None:
            calls.append("hub:shutdown")

    monkeypatch.setattr(
        main_module,
        "load_config",
        lambda: AppConfig(
            database_url="postgresql+psycopg://u:p@127.0.0.1:55432/petcare_test",
            camera_source="disabled",
        ),
    )
    monkeypatch.setattr(main_module, "configure_database", lambda _url: None)
    monkeypatch.setattr(main_module, "dispose_database", lambda: calls.append("database:dispose"))
    monkeypatch.setattr(main_module, "RuleIngress", Ingress)
    monkeypatch.setattr(main_module, "build_camera_service", lambda *_args: CameraService())
    monkeypatch.setattr(main_module, "RuleEngine", lambda **_kwargs: object())
    monkeypatch.setattr(main_module, "RuleWorker", Worker)
    monkeypatch.setattr(main_module, "DashboardHub", Hub)

    with pytest.raises(RuntimeError, match="shutdown failed"):
        async with main_module.lifespan(FastAPI()):
            pass
    assert calls[-4:] == ["worker:shutdown", "camera:shutdown", "hub:shutdown", "database:dispose"]


@pytest.fixture()
def sessions() -> sessionmaker:
    engine = create_engine(
        "sqlite+pysqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    with engine.begin() as connection:
        connection.exec_driver_sql(
            "CREATE TABLE devices (device_id TEXT PRIMARY KEY, status TEXT NOT NULL, last_seen_at DATETIME, created_at DATETIME, updated_at DATETIME)"
        )
        connection.exec_driver_sql(
            "CREATE TABLE sensor_readings (id INTEGER PRIMARY KEY, device_id TEXT, sensor_type TEXT, value_number FLOAT, value_boolean BOOLEAN, unit TEXT, observed_at DATETIME, received_at DATETIME)"
        )
        connection.exec_driver_sql(
            "CREATE TABLE behavior_events (id INTEGER PRIMARY KEY, subject_id TEXT, behavior_type TEXT, source_camera_event_id INTEGER, source_sensor_reading_id INTEGER, source_key TEXT, started_at DATETIME, ended_at DATETIME, duration_seconds INTEGER, created_at DATETIME, updated_at DATETIME)"
        )
        connection.exec_driver_sql(
            "CREATE TABLE anomaly_events (id INTEGER PRIMARY KEY, subject_id TEXT, anomaly_type TEXT, severity TEXT, mismatch_kind TEXT, source_behavior_event_id INTEGER, source_key TEXT, message TEXT, occurred_at DATETIME, created_at DATETIME)"
        )
        connection.exec_driver_sql(
            "CREATE TABLE zones (zone_name TEXT PRIMARY KEY, x1 INTEGER, y1 INTEGER, x2 INTEGER, y2 INTEGER, enabled BOOLEAN, created_at DATETIME, updated_at DATETIME)"
        )
        connection.exec_driver_sql(
            "INSERT INTO devices VALUES ('petzone-01','online','2026-07-20 11:59:00',NULL,NULL),('entrance-01','offline',NULL,NULL,NULL)"
        )
        connection.exec_driver_sql(
            "INSERT INTO sensor_readings VALUES "
            "(1,'petzone-01','food_weight',100.0,NULL,'g','2026-07-20 11:58:00','2026-07-20 11:58:00'),"
            "(2,'petzone-01','food_weight',90.0,NULL,'g','2026-07-20 11:59:00','2026-07-20 11:59:00'),"
            "(3,'entrance-01','presence_moving',NULL,1,'bool','2026-07-20 11:59:30','2026-07-20 11:59:30')"
        )
        connection.exec_driver_sql(
            "INSERT INTO behavior_events VALUES "
            "(1,'dog_001','eating',1,1,'a','2026-07-20 10:00:00','2026-07-20 10:01:00',60,NULL,NULL),"
            "(2,'cat_001','resting',2,2,'b','2026-07-20 11:00:00',NULL,NULL,NULL,NULL)"
        )
        connection.exec_driver_sql(
            "INSERT INTO anomaly_events VALUES "
            "(1,'dog_001','no_meal_12h','warning',NULL,1,'a','old','2026-07-20 09:00:00',NULL),"
            "(2,NULL,'bed_sensor_mismatch','warning','unconfirmed_pressure',NULL,'b','new','2026-07-20 11:00:00',NULL)"
        )
        connection.exec_driver_sql(
            "INSERT INTO zones VALUES "
            "('food_bowl',40,260,260,470,1,NULL,'2026-07-20 10:00:00'),"
            "('pet_bed',320,180,630,470,1,NULL,'2026-07-20 10:00:00')"
        )
    yield sessionmaker(bind=engine, expire_on_commit=False)
    engine.dispose()


def make_app(sessions: sessionmaker, *, worker: Worker | None = None, camera: Camera | None = None) -> FastAPI:
    application = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
    install_api(application, allowed_origins=DEFAULT_ALLOWED_ORIGINS)
    application.state.session_factory = sessions
    application.state.clock = Clock()
    application.state.rule_ingress = SimpleNamespace(queue_full=False)
    application.state.rule_worker = worker or Worker()
    application.state.mqtt_ingestor = SimpleNamespace(enabled=False, connected=False)
    application.state.camera_service = camera or Camera()
    application.state.dashboard_hub = SimpleNamespace(queue_full=False, subscriber_count=0)
    application.state.rule_engine = SimpleNamespace(bed_status_snapshot=bed_status())
    return application


def test_route_inventory_is_exact_and_docs_are_disabled(sessions: sessionmaker) -> None:
    application = make_app(sessions)

    def nested_routes(routes: list[object]) -> list[object]:
        flattened: list[object] = []
        for route in routes:
            original = getattr(route, "original_router", None)
            if original is None:
                flattened.append(route)
            else:
                flattened.extend(nested_routes(original.routes))
        return flattened

    routes = nested_routes(application.routes)
    http_routes = {
        (next(iter(route.methods)), route.path)
        for route in routes
        if isinstance(route, APIRoute)
    }
    websocket_routes = {route.path for route in routes if isinstance(route, APIWebSocketRoute)}

    assert http_routes == {
        ("GET", "/api/health"),
        ("GET", "/api/dashboard/summary"),
        ("GET", "/api/devices"),
        ("GET", "/api/sensors/latest"),
        ("GET", "/api/behaviors"),
        ("GET", "/api/anomalies"),
        ("GET", "/api/camera/status"),
        ("GET", "/api/video_feed"),
        ("GET", "/api/bed/status"),
        ("POST", "/api/bed/calibration"),
        ("GET", "/api/zones"),
        ("PUT", "/api/zones/{zone_name}"),
    }
    assert websocket_routes == {"/ws/dashboard"}
    with TestClient(application) as client:
        assert client.get("/docs").status_code == 404
        assert client.post("/api/zones").status_code == 405
        assert client.delete("/api/zones/pet_bed").status_code == 405


def test_every_http_route_returns_exact_models_and_order(sessions: sessionmaker) -> None:
    worker = Worker()
    application = make_app(sessions, worker=worker, camera=Camera())
    with TestClient(application) as client:
        health = client.get("/api/health")
        assert health.status_code == 200
        assert list(health.json()) == ["status", "database", "mqtt", "camera", "queue", "worker"]

        assert [row["device_id"] for row in client.get("/api/devices").json()] == ["entrance-01", "petzone-01"]
        sensors = client.get("/api/sensors/latest").json()
        assert [(row["device_id"], row["sensor_type"], row["value"]) for row in sensors] == [
            ("entrance-01", "presence_moving", True),
            ("petzone-01", "food_weight", 90),
        ]
        assert type(sensors[1]["value"]) is int
        assert [row["id"] for row in client.get("/api/behaviors").json()] == [2, 1]
        anomalies = client.get("/api/anomalies").json()
        assert [row["id"] for row in anomalies] == [2, 1]
        assert anomalies[0]["subject_id"] is None
        assert client.get("/api/camera/status").json()["state"] == "online"
        assert client.get("/api/bed/status").json() == bed_status().model_dump(mode="json")
        assert [row["zone_name"] for row in client.get("/api/zones").json()] == ["food_bowl", "pet_bed"]

        summary = client.get("/api/dashboard/summary").json()
        assert list(summary) == [
            "generated_at",
            "health",
            "devices",
            "latest_sensors",
            "camera",
            "bed",
            "behaviors",
            "anomalies",
        ]
        assert [row["id"] for row in summary["behaviors"]] == [2, 1]
        assert [row["id"] for row in summary["anomalies"]] == [2, 1]

        calibration = client.post("/api/bed/calibration", json={"device_id": "petzone-01"})
        assert calibration.status_code == 200
        assert list(calibration.json()) == ["device_id", "calibrated_at", "window_start", "window_end", "channels"]
        assert len(worker.submitted) == 1

        updated = client.put(
            "/api/zones/food_bowl",
            json={"x1": 10, "y1": 10, "x2": 320, "y2": 180, "enabled": True},
        )
        assert updated.status_code == 200
        assert list(updated.json()) == ["zone_name", "x1", "y1", "x2", "y2", "enabled", "updated_at"]

        video = client.get("/api/video_feed")
        assert video.status_code == 200
        assert video.headers["content-type"].startswith("multipart/x-mixed-replace; boundary=frame")
        assert video.content.startswith(b"--frame")


@pytest.mark.parametrize(
    ("body", "status", "code"),
    [
        ({"x1": 0, "y1": 0, "x2": 0, "y2": 10, "enabled": True}, 422, "validation_error"),
        ({"x1": 0.0, "y1": 0, "x2": 10, "y2": 10, "enabled": True}, 422, "validation_error"),
        ({"x1": 0, "y1": 0, "x2": 10, "y2": 10, "enabled": "true"}, 422, "validation_error"),
    ],
)
def test_zone_geometry_is_strict(sessions: sessionmaker, body: dict[str, object], status: int, code: str) -> None:
    with TestClient(make_app(sessions)) as client:
        response = client.put("/api/zones/pet_bed", json=body)
    assert response.status_code == status
    assert response.json() == {"code": code, "message": "Request validation failed"}


def test_zone_conflict_rolls_back_but_edge_touch_is_allowed(sessions: sessionmaker) -> None:
    camera = Camera()
    with TestClient(make_app(sessions, camera=camera)) as client:
        conflict = client.put(
            "/api/zones/pet_bed",
            json={"x1": 259, "y1": 260, "x2": 630, "y2": 470, "enabled": True},
        )
        assert conflict.status_code == 409
        assert conflict.json() == {"code": "zone_conflict", "message": "Enabled zones must not overlap"}
        assert client.get("/api/zones").json()[1]["x1"] == 320

        edge = client.put(
            "/api/zones/pet_bed",
            json={"x1": 260, "y1": 180, "x2": 630, "y2": 470, "enabled": True},
        )
        assert edge.status_code == 200
        assert edge.json()["x1"] == 260
        assert camera.zone_updates == [
            {
                "food_bowl": (40, 260, 260, 470),
                "pet_bed": (260, 180, 630, 470),
            }
        ]

        missing = client.put(
            "/api/zones/unknown",
            json={"x1": 0, "y1": 0, "x2": 10, "y2": 10, "enabled": False},
        )
        assert missing.status_code == 404
        assert missing.json() == {"code": "zone_not_found", "message": "Zone was not found"}


def test_calibration_failure_mapping_and_strict_body(sessions: sessionmaker) -> None:
    rejected = BedCalibrationRejected(
        BedCalibrationError(code="occupied", message="Bed is occupied", channels=[])
    )
    with TestClient(make_app(sessions, worker=Worker(rejected))) as client:
        response = client.post("/api/bed/calibration", json={"device_id": "petzone-01"})
        assert response.status_code == 409
        assert response.json() == {"code": "occupied", "message": "Bed is occupied", "channels": []}

    with TestClient(make_app(sessions, worker=Worker(RuleQueueUnavailable()))) as client:
        response = client.post("/api/bed/calibration", json={"device_id": "petzone-01"})
        assert response.status_code == 503
        assert response.json() == {"code": "queue_unavailable", "message": "Rule queue is unavailable"}
        assert client.post(
            "/api/bed/calibration", json={"device_id": "petzone-01", "extra": True}
        ).json() == {"code": "validation_error", "message": "Request validation failed"}


def test_calibration_timeout_cancels_the_queued_command(sessions: sessionmaker) -> None:
    class TimedOutFuture:
        timeout: float | None = None
        cancelled = False

        def result(self, timeout: float | None = None) -> object:
            self.timeout = timeout
            raise TimeoutError

        def cancel(self) -> bool:
            self.cancelled = True
            return True

    class WaitingWorker(Worker):
        def __init__(self) -> None:
            super().__init__()
            self.future = TimedOutFuture()

        def submit(self, command: object) -> TimedOutFuture:
            self.submitted.append(command)
            return self.future

    worker = WaitingWorker()
    with TestClient(make_app(sessions, worker=worker)) as client:
        response = client.post("/api/bed/calibration", json={"device_id": "petzone-01"})

    assert response.status_code == 503
    assert response.json() == {"code": "worker_unavailable", "message": "Rule worker is unavailable"}
    assert worker.future.timeout == 15.0
    assert worker.future.cancelled is True


def test_mjpeg_unavailable_is_503_before_stream_headers(sessions: sessionmaker) -> None:
    camera = Camera(chunks=[])
    camera.status = CameraStatus(
        state="offline", fps=0.0, inference_ms=0.0, last_frame_at=None, reason="missing"
    )
    with TestClient(make_app(sessions, camera=camera)) as client:
        response = client.get("/api/video_feed")
    assert response.status_code == 503
    assert response.headers["content-type"].startswith("application/json")
    assert response.json() == {"code": "camera_unavailable", "message": "Camera is unavailable"}


@pytest.mark.parametrize("origin", ["https://evil.example", "null"])
def test_hostile_simple_origin_is_rejected_without_cors(sessions: sessionmaker, origin: str) -> None:
    with TestClient(make_app(sessions)) as client:
        response = client.get("/api/health", headers={"Origin": origin})
    assert response.status_code == 403
    assert response.json() == {"code": "origin_forbidden", "message": "Origin is not allowed"}
    assert "access-control-allow-origin" not in response.headers


def test_allowed_simple_and_no_origin_cli(sessions: sessionmaker) -> None:
    with TestClient(make_app(sessions)) as client:
        cli = client.get("/api/health")
        allowed = client.get("/api/health", headers={"Origin": "http://localhost:3000"})
    assert cli.status_code == 200
    assert "access-control-allow-origin" not in cli.headers
    assert allowed.status_code == 200
    assert allowed.headers["access-control-allow-origin"] == "http://localhost:3000"
    assert allowed.headers["vary"] == "Origin"
    assert "access-control-allow-credentials" not in allowed.headers


def test_preflight_headers_are_exact_and_hostile_inputs_fail(sessions: sessionmaker) -> None:
    application = make_app(sessions)
    with TestClient(application) as client:
        allowed = client.options(
            "/api/bed/calibration",
            headers={
                "Origin": "http://127.0.0.1:3000",
                "Access-Control-Request-Method": "POST",
                "Access-Control-Request-Headers": "Content-Type",
            },
        )
        assert allowed.status_code == 204
        assert allowed.headers["access-control-allow-origin"] == "http://127.0.0.1:3000"
        assert allowed.headers["access-control-allow-methods"] == "GET,POST,PUT,OPTIONS"
        assert allowed.headers["access-control-allow-headers"] == "Content-Type"
        assert allowed.headers["vary"] == "Origin"
        assert "access-control-allow-credentials" not in allowed.headers

        for headers in (
            {"Origin": "https://evil.example", "Access-Control-Request-Method": "GET"},
            {"Origin": "http://localhost:3000", "Access-Control-Request-Method": "DELETE"},
            {
                "Origin": "http://localhost:3000",
                "Access-Control-Request-Method": "GET",
                "Access-Control-Request-Headers": "Authorization",
            },
            {
                "Origin": "http://localhost:3000",
                "Access-Control-Request-Method": "GET",
                "Access-Control-Request-Headers": "*",
            },
        ):
            response = client.options("/api/health", headers=headers)
            assert response.status_code == 403
            assert response.json() == {"code": "origin_forbidden", "message": "Origin is not allowed"}
            assert "access-control-allow-origin" not in response.headers


@pytest.mark.parametrize(
    "path",
    [
        "/api/health?extra=1",
        "/api/behaviors?limit=0",
        "/api/behaviors?limit=501",
        "/api/behaviors?limit=one",
        "/api/behaviors?limit=1.0",
        "/api/behaviors?limit=%2B1",
        "/api/behaviors?limit=%201",
        "/api/behaviors?limit=001",
        "/api/behaviors?limit=1&limit=2",
        "/api/anomalies?extra=1",
        "/api/bed/status?device_id=entrance-01",
        "/api/zones?limit=1",
    ],
)
def test_query_contract_rejects_unknown_duplicate_or_out_of_range_values(
    sessions: sessionmaker,
    path: str,
) -> None:
    with TestClient(make_app(sessions)) as client:
        response = client.get(path)
    assert response.status_code == 422
    assert response.json() == {"code": "validation_error", "message": "Request validation failed"}


def test_query_contract_rejects_pathological_limit_length(sessions: sessionmaker) -> None:
    with TestClient(make_app(sessions), raise_server_exceptions=False) as client:
        response = client.get("/api/behaviors", params={"limit": "9" * 5000})
    assert response.status_code == 422
    assert response.json() == {"code": "validation_error", "message": "Request validation failed"}


def test_health_reports_component_degradation_and_database_routes_use_503(
    sessions: sessionmaker,
) -> None:
    application = make_app(sessions)
    application.state.mqtt_ingestor = SimpleNamespace(enabled=True, connected=False)
    application.state.dashboard_hub = SimpleNamespace(queue_full=True, subscriber_count=0)
    application.state.rule_worker = SimpleNamespace(thread=None, last_error=RuntimeError("stopped"))
    application.state.camera_service.status = CameraStatus(
        state="offline", fps=0.0, inference_ms=0.0, last_frame_at=None, reason="missing"
    )
    with TestClient(application) as client:
        health = client.get("/api/health")
    assert health.json() == {
        "status": "degraded",
        "database": "up",
        "mqtt": "down",
        "camera": "offline",
        "queue": "full",
        "worker": "stopped",
    }

    application = make_app(sessions)
    application.state.session_factory = lambda: (_ for _ in ()).throw(SQLAlchemyError("down"))
    with TestClient(application) as client:
        health = client.get("/api/health")
        devices = client.get("/api/devices")
    assert health.json()["database"] == "down"
    assert devices.status_code == 503
    assert devices.json() == {"code": "database_unavailable", "message": "Database is unavailable"}


def test_unconfigured_database_is_not_misreported_as_worker_failure(
    sessions: sessionmaker,
) -> None:
    application = make_app(sessions)
    application.state.session_factory = lambda: (_ for _ in ()).throw(
        RuntimeError("database is not configured")
    )
    with TestClient(application, raise_server_exceptions=False) as client:
        responses = [
            client.get("/api/dashboard/summary"),
            client.get("/api/zones"),
            client.put(
                "/api/zones/pet_bed",
                json={"x1": 320, "y1": 260, "x2": 630, "y2": 470, "enabled": True},
            ),
        ]
    for response in responses:
        assert response.status_code == 503
        assert response.json() == {
            "code": "database_unavailable",
            "message": "Database is unavailable",
        }


def test_worker_unavailable_errors_are_exact(sessions: sessionmaker) -> None:
    application = make_app(sessions)
    application.state.rule_engine = SimpleNamespace(bed_status_snapshot=None)
    application.state.rule_worker = SimpleNamespace(thread=None, last_error=None)
    with TestClient(application) as client:
        bed = client.get("/api/bed/status")
        summary_response = client.get("/api/dashboard/summary")
        calibration = client.post("/api/bed/calibration", json={"device_id": "petzone-01"})
    assert bed.status_code == 503
    assert bed.json() == {"code": "worker_unavailable", "message": "Rule worker is unavailable"}
    assert summary_response.status_code == 503
    assert summary_response.json() == {
        "code": "worker_unavailable",
        "message": "Rule worker is unavailable",
    }
    assert calibration.status_code == 503
    assert calibration.json() == {"code": "worker_unavailable", "message": "Rule worker is unavailable"}
