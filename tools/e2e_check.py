"""Browser-free PetCare local-live integration driver.

Secrets are read only from the child environment. CLI arguments are paths,
ports, or mode names and are therefore safe to record in process evidence.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import socket
import struct
import subprocess
import sys
import threading
import time
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence


ROOT = Path(__file__).resolve().parents[1]
BACKEND_ROOT = ROOT / "backend"
for import_root in (ROOT, BACKEND_ROOT):
    if str(import_root) not in sys.path:
        sys.path.insert(0, str(import_root))


PASS_MARKER = "PETCARE_E2E=PASS"
PHASE_NAMES = (
    "empty_calibration",
    "food_history",
    "dog_food_and_unconfirmed_pressure",
    "dog_bed",
    "dog_owner_retained",
    "cat_handoff",
    "cat_sensor_check",
    "settled_empty",
)
EXACT_THRESHOLDS = {
    "calibration_window": 60,
    "eating_dwell": 30,
    "pressure_entry": 2,
    "pressure_exit": 7,
    "owner_exit": 3,
    "mismatch": 30,
}
EXACT_ZONES = {
    "food_bowl": (40, 260, 260, 470),
    "pet_bed": (320, 180, 630, 470),
}


def _utc_text(value: datetime | None = None) -> str:
    return (value or datetime.now(UTC)).astimezone(UTC).isoformat(timespec="microseconds").replace("+00:00", "Z")


def _exact_object(value: object, keys: set[str], label: str) -> dict[str, object]:
    if type(value) is not dict or set(value) != keys:
        raise ValueError(f"invalid {label}")
    return value


@dataclass(frozen=True, slots=True)
class VisionPhase:
    name: str
    duration_seconds: int
    pressure: tuple[int, int, int]
    food_weight_g: float
    detections: tuple[dict[str, object], ...]


@dataclass(frozen=True, slots=True)
class VisionSequence:
    frame_shape: tuple[int, int, int]
    thresholds: dict[str, int]
    zones: dict[str, tuple[int, int, int, int]]
    phases: tuple[VisionPhase, ...]

    def phase(self, name: str) -> VisionPhase:
        try:
            return next(phase for phase in self.phases if phase.name == name)
        except StopIteration as exc:
            raise ValueError(f"missing vision phase: {name}") from exc


def load_vision_sequence(path: Path) -> VisionSequence:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError("invalid vision sequence JSON") from exc
    root = _exact_object(
        data,
        {"schema_version", "frame", "thresholds_seconds", "zones", "phases"},
        "vision sequence",
    )
    if root["schema_version"] != 1:
        raise ValueError("invalid vision sequence schema")
    frame = _exact_object(
        root["frame"], {"width", "height", "channels", "dtype", "color_order"}, "frame contract"
    )
    if frame != {"width": 640, "height": 480, "channels": 3, "dtype": "uint8", "color_order": "BGR"}:
        raise ValueError("vision fixture must use exact uint8 BGR 640x480 shape")
    thresholds = _exact_object(root["thresholds_seconds"], set(EXACT_THRESHOLDS), "threshold contract")
    if thresholds != EXACT_THRESHOLDS:
        raise ValueError("production timing thresholds cannot be shortened")
    zones_value = _exact_object(root["zones"], set(EXACT_ZONES), "zone contract")
    zones: dict[str, tuple[int, int, int, int]] = {}
    for name, expected in EXACT_ZONES.items():
        supplied = zones_value[name]
        if type(supplied) is not list or any(type(item) is not int for item in supplied) or tuple(supplied) != expected:
            raise ValueError("vision fixture zones must match migration seeds")
        zones[name] = expected
    phases_value = root["phases"]
    if type(phases_value) is not list or len(phases_value) != len(PHASE_NAMES):
        raise ValueError("invalid vision phase list")
    phases: list[VisionPhase] = []
    for index, supplied in enumerate(phases_value):
        phase = _exact_object(
            supplied,
            {"name", "duration_seconds", "pressure", "food_weight_g", "detections"},
            f"vision phase {index + 1}",
        )
        if phase["name"] != PHASE_NAMES[index] or type(phase["duration_seconds"]) is not int or phase["duration_seconds"] <= 0:
            raise ValueError("invalid vision phase identity or duration")
        pressure = phase["pressure"]
        if type(pressure) is not list or len(pressure) != 3 or any(type(item) is not int or not 0 <= item <= 4095 for item in pressure):
            raise ValueError("invalid vision pressure fixture")
        food = phase["food_weight_g"]
        if isinstance(food, bool) or not isinstance(food, (int, float)):
            raise ValueError("invalid food fixture")
        detections = phase["detections"]
        if type(detections) is not list:
            raise ValueError("invalid detection fixture")
        copied: list[dict[str, object]] = []
        for detection in detections:
            item = _exact_object(detection, {"detected_type", "confidence", "xyxy"}, "fixture detection")
            if item["detected_type"] not in {"dog", "cat"}:
                raise ValueError("fixture detections are limited to dog and cat")
            copied.append(dict(item))
        phases.append(
            VisionPhase(
                name=str(phase["name"]),
                duration_seconds=int(phase["duration_seconds"]),
                pressure=tuple(pressure),  # type: ignore[arg-type]
                food_weight_g=float(food),
                detections=tuple(copied),
            )
        )
    sequence = VisionSequence((480, 640, 3), dict(EXACT_THRESHOLDS), zones, tuple(phases))
    if sequence.phase("empty_calibration").duration_seconds < 60:
        raise ValueError("calibration phase is too short")
    if sequence.phase("dog_food_and_unconfirmed_pressure").duration_seconds < 32:
        raise ValueError("eating/mismatch phase is too short")
    if sequence.phase("cat_sensor_check").duration_seconds < 37:
        raise ValueError("pressure-exit/sensor-check phase is too short")
    return sequence


class FixtureDetector:
    """Mutable detector used only by the integration harness around VisionPipeline."""

    def __init__(self, sequence: VisionSequence) -> None:
        self._sequence = sequence
        self._lock = threading.Lock()
        self._detections: tuple[dict[str, object], ...] = ()

    def select(self, phase_name: str) -> None:
        with self._lock:
            self._detections = self._sequence.phase(phase_name).detections

    def __call__(self, _frame: object) -> tuple[dict[str, object], ...]:
        with self._lock:
            return tuple(dict(item) for item in self._detections)


class MqttPublisher:
    def __init__(self, host: str, port: int, username: str, password: str) -> None:
        import paho.mqtt.client as mqtt

        self._mqtt = mqtt
        self._connected = threading.Event()
        self._failed = False
        self._client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=f"petcare-e2e-{os.getpid()}")
        self._client.username_pw_set(username, password)
        self._client.on_connect = self._on_connect
        self._client.connect(host, port, 10)
        self._client.loop_start()
        if not self._connected.wait(10) or self._failed:
            self.close()
            raise RuntimeError("authenticated MQTT publisher failed to connect")

    def _on_connect(self, _client: object, _userdata: object, _flags: object, reason_code: object, _properties: object) -> None:
        self._failed = bool(getattr(reason_code, "is_failure", reason_code != 0))
        self._connected.set()

    def publish_sensor(self, device_id: str, sensor_type: str, value: object, unit: str) -> None:
        payload = {
            "device_id": device_id,
            "sensor_type": sensor_type,
            "value": value,
            "unit": unit,
            "observed_at": _utc_text(),
        }
        self._publish(f"home/pico/{device_id}/sensor/{sensor_type}", payload, retain=False)

    def publish_status(self, device_id: str, status: str) -> None:
        self._publish(
            f"home/pico/{device_id}/status",
            {"device_id": device_id, "status": status, "observed_at": _utc_text()},
            retain=True,
        )

    def publish_raw(self, topic: str, payload: bytes) -> None:
        result = self._client.publish(topic, payload, qos=1, retain=False)
        result.wait_for_publish(5)
        if not result.is_published():
            raise RuntimeError("MQTT publication timed out")

    def _publish(self, topic: str, payload: Mapping[str, object], *, retain: bool) -> None:
        raw = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        result = self._client.publish(topic, raw, qos=1, retain=retain)
        result.wait_for_publish(5)
        if not result.is_published():
            raise RuntimeError("MQTT publication timed out")

    def close(self) -> None:
        try:
            self._client.disconnect()
        finally:
            self._client.loop_stop()


def _wait_until(predicate: Callable[[], bool], timeout: float, label: str) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.05)
    raise TimeoutError(f"timed out waiting for {label}")


def _publish_profiles(publisher: MqttPublisher) -> None:
    publisher.publish_status("entrance-01", "online")
    publisher.publish_status("petzone-01", "online")
    for device_id in ("entrance-01", "petzone-01"):
        publisher.publish_sensor(device_id, "temperature", 21.5, "C")
        publisher.publish_sensor(device_id, "humidity", 48.0, "%")
        publisher.publish_sensor(device_id, "presence_moving", False, "bool")
        publisher.publish_sensor(device_id, "presence_stationary", True, "bool")
    publisher.publish_sensor("petzone-01", "food_weight", 100.0, "g")
    publisher.publish_sensor("petzone-01", "water_weight", 500.0, "g")


def run_production_handler_sequence(
    sequence: VisionSequence,
    *,
    database_url: str,
    services_manifest: Path,
    mqtt_username: str,
    mqtt_password: str,
) -> dict[str, object]:
    """Drive real handlers/engine against real services with wall-clock thresholds."""

    import numpy as np
    from sqlalchemy import create_engine, func, select
    from sqlalchemy.orm import Session, sessionmaker

    from app.camera_service import CameraService
    from app.config import AppConfig
    from app.events import CalibrateBedCommand
    from app.models import AnomalyEvent, BehaviorEvent, RestSession, SensorReading, Zone
    from app.mqtt_ingest import MqttIngestor, load_mqtt_endpoint
    from app.rule_ingress import RuleIngress, SystemRuleClock
    from app.rule_worker import RuleWorker
    from app.rules import RuleEngine
    from app.vision import MockFrameSource, VisionPipeline

    config = AppConfig(database_url=database_url, camera_source="disabled")
    sql_engine = create_engine(database_url, pool_pre_ping=True)
    sessions = sessionmaker(bind=sql_engine, expire_on_commit=False)

    def session_factory() -> Session:
        return sessions()

    with session_factory() as session:
        zone_rows = session.execute(select(Zone).order_by(Zone.zone_name)).scalars().all()
        zones = {row.zone_name: (row.x1, row.y1, row.x2, row.y2) for row in zone_rows if row.enabled}
    if zones != sequence.zones:
        sql_engine.dispose()
        raise RuntimeError("database zones do not match the vision fixture")

    detector = FixtureDetector(sequence)
    clock = SystemRuleClock()
    ingress = RuleIngress(clock)
    camera = CameraService(
        VisionPipeline(
            detector,
            zones,
            source=MockFrameSource(np.zeros(sequence.frame_shape, dtype=np.uint8)),
        ),
        ingress,
        session_factory,
    )
    messages: list[object] = []
    rules = RuleEngine(config=config, camera_service=camera, publisher=messages.append)
    worker = RuleWorker(ingress=ingress, clock=clock, session_factory=session_factory, engine=rules)
    endpoint = load_mqtt_endpoint(services_manifest, "local_live")
    if endpoint.host != "127.0.0.1" or endpoint.port != 18883:
        sql_engine.dispose()
        raise RuntimeError("local_live MQTT endpoint is not loopback-only")
    ingestor = MqttIngestor(
        ingress=ingress,
        session_factory=session_factory,
        endpoint=endpoint,
        username=mqtt_username,
        password=mqtt_password,
    )
    publisher: MqttPublisher | None = None
    worker_started = False
    elapsed: dict[str, float] = {}

    def count_pressure_samples() -> tuple[int, int, int]:
        with session_factory() as session:
            return tuple(
                int(
                    session.execute(
                        select(func.count(SensorReading.id)).where(
                            SensorReading.device_id == "petzone-01",
                            SensorReading.sensor_type == f"bed_pressure_{channel}",
                            SensorReading.observed_at > datetime.now(UTC) - timedelta(seconds=60),
                        )
                    ).scalar_one()
                )
                for channel in ("left", "center", "right")
            )  # type: ignore[return-value]

    def drive(phase: VisionPhase) -> float:
        assert publisher is not None
        detector.select(phase.name)
        started = time.monotonic()
        deadline = started + phase.duration_seconds
        next_sensor = started
        next_camera = started
        while time.monotonic() < deadline:
            now = time.monotonic()
            if now >= next_sensor:
                publisher.publish_sensor("petzone-01", "food_weight", phase.food_weight_g, "g")
                for channel, raw in zip(("left", "center", "right"), phase.pressure, strict=True):
                    publisher.publish_sensor("petzone-01", f"bed_pressure_{channel}", raw, "adc")
                next_sensor += 1.0
            if now >= next_camera:
                if not camera.process_once():
                    raise RuntimeError(
                        f"fixture camera frame was rejected: {camera.status.reason or 'unknown'}"
                    )
                next_camera += 0.2
            delay = min(next_sensor, next_camera, deadline) - time.monotonic()
            if delay > 0:
                time.sleep(delay)
        if not camera.process_once():
            raise RuntimeError(
                f"final fixture camera frame was rejected: {camera.status.reason or 'unknown'}"
            )
        return time.monotonic() - started

    try:
        worker.start()
        worker_started = True
        ingestor.start()
        _wait_until(lambda: ingestor.connected, 10, "backend MQTT subscription")
        publisher = MqttPublisher(endpoint.host, endpoint.port, mqtt_username, mqtt_password)
        _publish_profiles(publisher)

        calibration_phase = sequence.phase("empty_calibration")
        elapsed[calibration_phase.name] = drive(calibration_phase)
        _wait_until(lambda: min(count_pressure_samples()) >= 45, 10, "calibration samples")
        calibration = worker.submit(CalibrateBedCommand(device_id="petzone-01")).result(timeout=15)
        if (calibration.window_end - calibration.window_start).total_seconds() != 60:
            raise RuntimeError("calibration window was shortened")

        for name in PHASE_NAMES[1:]:
            phase = sequence.phase(name)
            elapsed[name] = drive(phase)

        def expected_rows() -> bool:
            with session_factory() as session:
                behaviors = session.execute(select(func.count(BehaviorEvent.id))).scalar_one()
                mismatches = session.execute(
                    select(func.count(AnomalyEvent.id)).where(AnomalyEvent.anomaly_type == "bed_sensor_mismatch")
                ).scalar_one()
                return behaviors >= 3 and mismatches >= 2

        _wait_until(expected_rows, 10, "behavior and mismatch persistence")
    finally:
        try:
            ingestor.stop()
        finally:
            try:
                ingress.stop_accepting()
                if worker_started:
                    worker.shutdown(timeout=15)
            finally:
                camera.shutdown()
                if publisher is not None:
                    publisher.close()

    with session_factory() as session:
        behaviors = [
            [row.behavior_type, row.subject_id]
            for row in session.execute(select(BehaviorEvent).order_by(BehaviorEvent.started_at, BehaviorEvent.id)).scalars()
        ]
        mismatches = [
            [row.mismatch_kind, row.subject_id]
            for row in session.execute(
                select(AnomalyEvent)
                .where(AnomalyEvent.anomaly_type == "bed_sensor_mismatch")
                .order_by(AnomalyEvent.occurred_at, AnomalyEvent.id)
            ).scalars()
        ]
        open_behaviors = int(
            session.execute(select(func.count(BehaviorEvent.id)).where(BehaviorEvent.ended_at.is_(None))).scalar_one()
        )
        open_rest = int(
            session.execute(select(func.count(RestSession.id)).where(RestSession.ended_at.is_(None))).scalar_one()
        )
    sql_engine.dispose()
    return {
        "calibration_seconds": elapsed["empty_calibration"],
        "eating_dwell_seconds": elapsed["dog_food_and_unconfirmed_pressure"],
        "pressure_entry_seconds": EXACT_THRESHOLDS["pressure_entry"],
        "pressure_exit_seconds": EXACT_THRESHOLDS["pressure_exit"],
        "mismatch_seconds": EXACT_THRESHOLDS["mismatch"],
        "behaviors": behaviors,
        "mismatches": mismatches,
        "open_behaviors": open_behaviors,
        "open_rest_sessions": open_rest,
        "worker_joined": worker.thread is None,
        "published_anomaly_alerts": sum(
            1 for message in messages if isinstance(message, dict) and message.get("type") == "anomaly_alert"
        ),
    }


def prepare_camera_files(valid: Path, invalid: Path) -> None:
    import cv2
    import numpy as np

    valid.parent.mkdir(parents=True, exist_ok=True)
    invalid.parent.mkdir(parents=True, exist_ok=True)
    for path, frame, label in (
        (valid, np.zeros((480, 640, 3), dtype=np.uint8), "valid"),
        (invalid, np.zeros((479, 640, 3), dtype=np.uint8), "invalid"),
    ):
        encoded, payload = cv2.imencode(".png", frame)
        if not encoded:
            raise RuntimeError(f"unable to encode {label} camera fixture")
        path.write_bytes(payload.tobytes())


def prepare_database() -> None:
    from sqlalchemy import create_engine, text
    from sqlalchemy.engine import make_url

    database_url = os.environ["DATABASE_URL"]
    target = make_url(database_url)
    if target.host not in {"127.0.0.1", "localhost"} or target.port != 55432 or target.database != "petcare_test":
        raise RuntimeError("integration database target must be dedicated loopback petcare_test")
    engine = create_engine(target.set(database="postgres"), isolation_level="AUTOCOMMIT")
    try:
        with engine.connect() as connection:
            exists = connection.execute(text("SELECT 1 FROM pg_database WHERE datname='petcare_test'"))
            if exists.scalar_one_or_none() is None:
                connection.exec_driver_sql("CREATE DATABASE petcare_test")
    finally:
        engine.dispose()
    print("PETCARE_DATABASE_PREPARE=PASS")


def serve_backend() -> int:
    import uvicorn

    from app.main import app

    server = uvicorn.Server(
        uvicorn.Config(app, host="127.0.0.1", port=8000, log_level="warning", access_log=False)
    )
    stopped = threading.Event()

    def listen() -> None:
        if sys.stdin.readline().strip() == "STOP":
            stopped.set()
            server.should_exit = True

    threading.Thread(target=listen, name="petcare-e2e-stop", daemon=True).start()
    server.run()
    if not stopped.is_set():
        return 2
    print("BACKEND_SERVER_STOPPED=GRACEFUL")
    return 0


class _RawWebSocket:
    def __init__(self, origin: str) -> None:
        key = base64.b64encode(os.urandom(16)).decode("ascii")
        self._key = key
        self._socket = socket.create_connection(("127.0.0.1", 8000), timeout=5)
        request = (
            "GET /ws/dashboard HTTP/1.1\r\n"
            "Host: 127.0.0.1:8000\r\n"
            "Upgrade: websocket\r\n"
            "Connection: Upgrade\r\n"
            f"Sec-WebSocket-Key: {key}\r\n"
            "Sec-WebSocket-Version: 13\r\n"
            f"Origin: {origin}\r\n\r\n"
        )
        self._socket.sendall(request.encode("ascii"))
        response = b""
        while b"\r\n\r\n" not in response:
            response += self._socket.recv(4096)
            if len(response) > 32768:
                raise RuntimeError("oversized WebSocket handshake")
        header, self._buffer = response.split(b"\r\n\r\n", 1)
        if not header.startswith(b"HTTP/1.1 101"):
            raise RuntimeError("WebSocket handshake failed")
        expected = base64.b64encode(
            hashlib.sha1((key + "258EAFA5-E914-47DA-95CA-C5AB0DC85B11").encode("ascii")).digest()
        )
        if b"sec-websocket-accept: " + expected.lower() not in header.lower():
            raise RuntimeError("WebSocket accept key mismatch")

    def _read(self, size: int) -> bytes:
        while len(self._buffer) < size:
            chunk = self._socket.recv(max(4096, size - len(self._buffer)))
            if not chunk:
                raise RuntimeError("WebSocket closed without a frame")
            self._buffer += chunk
        value, self._buffer = self._buffer[:size], self._buffer[size:]
        return value

    def frame(self, timeout: float) -> tuple[int, bytes]:
        self._socket.settimeout(timeout)
        first, second = self._read(2)
        opcode = first & 0x0F
        length = second & 0x7F
        if second & 0x80:
            raise RuntimeError("server WebSocket frame must not be masked")
        if length == 126:
            length = struct.unpack("!H", self._read(2))[0]
        elif length == 127:
            length = struct.unpack("!Q", self._read(8))[0]
        return opcode, self._read(length)

    def close(self) -> None:
        try:
            mask = os.urandom(4)
            payload = struct.pack("!H", 1000)
            masked = bytes(value ^ mask[index % 4] for index, value in enumerate(payload))
            self._socket.sendall(b"\x88" + bytes([0x80 | len(payload)]) + mask + masked)
        except OSError:
            pass
        finally:
            self._socket.close()


def _anonymous_mqtt_rejected(host: str, port: int) -> bool:
    import paho.mqtt.client as mqtt

    done = threading.Event()
    rejected: list[bool] = []
    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=f"petcare-e2e-anon-{os.getpid()}")

    def connected(_client: object, _userdata: object, _flags: object, reason_code: object, _properties: object) -> None:
        rejected.append(bool(getattr(reason_code, "is_failure", reason_code != 0)))
        done.set()

    client.on_connect = connected
    client.connect(host, port, 5)
    client.loop_start()
    try:
        return done.wait(5) and rejected == [True]
    finally:
        client.disconnect()
        client.loop_stop()


def _hostile_websocket_rejected(origin: str) -> bool:
    key = base64.b64encode(os.urandom(16)).decode("ascii")
    request = (
        "GET /ws/dashboard HTTP/1.1\r\n"
        "Host: 127.0.0.1:8000\r\n"
        "Upgrade: websocket\r\n"
        "Connection: Upgrade\r\n"
        f"Sec-WebSocket-Key: {key}\r\n"
        "Sec-WebSocket-Version: 13\r\n"
        f"Origin: {origin}\r\n\r\n"
    )
    with socket.create_connection(("127.0.0.1", 8000), timeout=5) as connection:
        connection.sendall(request.encode("ascii"))
        response = b""
        while b"\r\n\r\n" not in response and len(response) <= 32768:
            chunk = connection.recv(4096)
            if not chunk:
                break
            response += chunk
    return response.startswith(b"HTTP/1.1 403")


def verify_external_stack(output: Path) -> None:
    import httpx

    base = "http://127.0.0.1:8000"
    origin = "http://127.0.0.1:3000"
    manifest = Path(os.environ["PETCARE_SERVICES_MANIFEST"])
    document = json.loads(manifest.read_text(encoding="utf-8"))
    profile = document["mqtt_profiles"]["local_live"]
    if profile != {"bind_host": "127.0.0.1", "port": 18883, "client_host": "127.0.0.1"}:
        raise RuntimeError("local_live MQTT profile mismatch")
    client = httpx.Client(timeout=10)
    health = client.get(f"{base}/api/health").json()
    if health != {"status": "healthy", "database": "up", "mqtt": "up", "camera": "online", "queue": "ok", "worker": "running"}:
        raise RuntimeError("backend health is not exact")
    hostile = client.get(f"{base}/api/health", headers={"Origin": "https://evil.example"})
    if hostile.status_code != 403 or hostile.json() != {"code": "origin_forbidden", "message": "Origin is not allowed"}:
        raise RuntimeError("hostile HTTP Origin was not rejected")
    preflight = client.options(
        f"{base}/api/zones",
        headers={
            "Origin": origin,
            "Access-Control-Request-Method": "PUT",
            "Access-Control-Request-Headers": "Content-Type",
        },
    )
    if (
        preflight.status_code != 204
        or preflight.headers.get("access-control-allow-origin") != origin
        or preflight.headers.get("access-control-allow-methods") != "GET,POST,PUT,OPTIONS"
        or preflight.headers.get("access-control-allow-headers") != "Content-Type"
        or preflight.headers.get("vary") != "Origin"
    ):
        raise RuntimeError("allowed preflight contract failed")
    zones = client.get(f"{base}/api/zones", headers={"Origin": origin}).json()
    by_name = {item["zone_name"]: item for item in zones}
    if tuple(by_name) != ("food_bowl", "pet_bed"):
        raise RuntimeError("zone response is not deterministic")
    conflict = client.put(
        f"{base}/api/zones/food_bowl",
        headers={"Origin": origin},
        json={"x1": 320, "y1": 180, "x2": 400, "y2": 300, "enabled": True},
    )
    if conflict.status_code != 409 or conflict.json().get("code") != "zone_conflict":
        raise RuntimeError("zone conflict was not rejected")
    original = by_name["food_bowl"]
    restored = client.put(
        f"{base}/api/zones/food_bowl",
        headers={"Origin": origin},
        json={key: original[key] for key in ("x1", "y1", "x2", "y2", "enabled")},
    )
    if restored.status_code != 200:
        raise RuntimeError("valid zone update failed")
    behaviors = client.get(f"{base}/api/behaviors").json()
    anomalies = client.get(f"{base}/api/anomalies").json()
    behavior_pairs = [[item["behavior_type"], item["subject_id"]] for item in reversed(behaviors)]
    mismatch_pairs = [
        [item["mismatch_kind"], item["subject_id"]]
        for item in reversed(anomalies)
        if item["anomaly_type"] == "bed_sensor_mismatch"
    ]
    if behavior_pairs != [["eating", "dog_001"], ["resting", "dog_001"], ["resting", "cat_001"]]:
        raise RuntimeError("persisted behavior flow mismatch")
    if mismatch_pairs != [["unconfirmed_pressure", None], ["sensor_check", "cat_001"]]:
        raise RuntimeError("persisted mismatch flow mismatch")
    with client.stream("GET", f"{base}/api/video_feed") as video:
        prefix = b""
        for chunk in video.iter_bytes():
            prefix += chunk
            if b"Content-Type: image/jpeg" in prefix or len(prefix) >= 4096:
                break
        if video.status_code != 200 or b"Content-Type: image/jpeg" not in prefix:
            raise RuntimeError("MJPEG stream is blank")
    dashboard = client.get("http://127.0.0.1:3000/")
    if dashboard.status_code != 200 or "PetCare" not in dashboard.text:
        raise RuntimeError("dashboard process is unavailable")

    websocket = _RawWebSocket(origin)
    publisher = MqttPublisher(
        profile["client_host"], profile["port"], os.environ["PETCARE_MQTT_USERNAME"], os.environ["PETCARE_MQTT_PASSWORD"]
    )
    started = time.monotonic()
    latency_ms = 0
    try:
        publisher.publish_sensor("entrance-01", "temperature", 23.75, "C")
        deadline = started + 2.0
        received = False
        while time.monotonic() < deadline:
            opcode, payload = websocket.frame(max(0.05, deadline - time.monotonic()))
            if opcode != 1:
                continue
            message = json.loads(payload)
            if message.get("type") == "dashboard_update" and any(
                item.get("device_id") == "entrance-01"
                and item.get("sensor_type") == "temperature"
                and item.get("value") == 23.75
                for item in message["payload"]["latest_sensors"]
            ):
                received = True
                latency_ms = round((time.monotonic() - started) * 1000)
                break
        if not received or time.monotonic() - started > 2.0:
            raise RuntimeError("sensor-to-WebSocket propagation exceeded 2 seconds")
        invalid = json.dumps(
            {
                "device_id": "entrance-01",
                "sensor_type": "temperature",
                "value": "23.75",
                "unit": "C",
                "observed_at": _utc_text(),
            },
            separators=(",", ":"),
        ).encode("utf-8")
        publisher.publish_raw("home/pico/entrance-01/sensor/temperature", invalid)
        time.sleep(0.5)
        latest = client.get(f"{base}/api/sensors/latest").json()
        temperature = next(
            item
            for item in latest
            if item["device_id"] == "entrance-01" and item["sensor_type"] == "temperature"
        )
        if temperature["value"] != 23.75:
            raise RuntimeError("strict MQTT coercion rejection failed")
    finally:
        publisher.close()
        websocket.close()

    if not _hostile_websocket_rejected("https://evil.example"):
        raise RuntimeError("hostile WebSocket Origin was not rejected at the network boundary")
    if not _anonymous_mqtt_rejected(profile["client_host"], profile["port"]):
        raise RuntimeError("anonymous MQTT connection was accepted")

    result = {
        "status": "PASS",
        "behaviors": behavior_pairs,
        "mismatches": mismatch_pairs,
        "behavior_count": len(behaviors),
        "anomaly_count": len(anomalies),
        "sensor_latency_ms": latency_ms,
        "vision_boundary": {
            "real_model": "file-source process with pinned YOLO",
            "deterministic_sequence": "production VisionPipeline, CameraService, MQTT handlers, RuleWorker, and RuleEngine",
        },
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_suffix(output.suffix + ".tmp")
    temporary.write_text(json.dumps(result, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    temporary.replace(output)
    print(PASS_MARKER)


def verify_restart(baseline: Path) -> None:
    import httpx

    before = json.loads(baseline.read_text(encoding="utf-8"))
    client = httpx.Client(timeout=10)
    behaviors = client.get("http://127.0.0.1:8000/api/behaviors").json()
    anomalies = client.get("http://127.0.0.1:8000/api/anomalies").json()
    if len(behaviors) != before["behavior_count"] or len(anomalies) != before["anomaly_count"]:
        raise RuntimeError("hard restart replayed persisted events")
    if any(item["ended_at"] is None for item in behaviors):
        raise RuntimeError("hard restart left an orphan behavior open")
    print("PETCARE_RESTART=PASS")


class _FixtureHandler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:  # noqa: N802
        if self.path != "/health":
            self.send_error(404)
            return
        body = b"PETCARE-INTEGRATION-FIXTURE-V1\n"
        self.send_response(200)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, _format: str, *_args: object) -> None:
        return


def fixture_server(port: int) -> None:
    server = ThreadingHTTPServer(("127.0.0.1", port), _FixtureHandler)
    threading.Thread(target=lambda: (sys.stdin.readline(), server.shutdown()), daemon=True).start()
    server.serve_forever()


def fixture_client(port: int) -> None:
    import httpx

    response = httpx.get(f"http://127.0.0.1:{port}/health", timeout=5)
    if response.status_code != 200 or response.text != "PETCARE-INTEGRATION-FIXTURE-V1\n":
        raise RuntimeError("fixture health failed")
    print("PETCARE_INTEGRATION_FIXTURE=PASS")


def fixture_hang(port: int) -> None:
    code = (
        "import socket,sys,time;"
        "s=socket.socket();s.setsockopt(socket.SOL_SOCKET,socket.SO_REUSEADDR,1);"
        "s.bind(('127.0.0.1',int(sys.argv[1])));s.listen(1);time.sleep(3600)"
    )
    subprocess.Popen([sys.executable, "-c", code, str(port)])
    while True:
        time.sleep(60)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="command", required=True)
    prepare = subparsers.add_parser("prepare-camera")
    prepare.add_argument("--valid", type=Path, required=True)
    prepare.add_argument("--invalid", type=Path, required=True)
    subparsers.add_parser("prepare-database")
    subparsers.add_parser("serve-backend")
    verify = subparsers.add_parser("verify-external")
    verify.add_argument("--output", type=Path, required=True)
    restart = subparsers.add_parser("verify-restart")
    restart.add_argument("--baseline", type=Path, required=True)
    fixture = subparsers.add_parser("fixture-server")
    fixture.add_argument("--port", type=int, required=True)
    fixture_check = subparsers.add_parser("fixture-client")
    fixture_check.add_argument("--port", type=int, required=True)
    fixture_wait = subparsers.add_parser("fixture-hang")
    fixture_wait.add_argument("--port", type=int, required=True)
    args = parser.parse_args(argv)
    if args.command == "prepare-camera":
        prepare_camera_files(args.valid, args.invalid)
    elif args.command == "prepare-database":
        prepare_database()
    elif args.command == "serve-backend":
        return serve_backend()
    elif args.command == "verify-external":
        verify_external_stack(args.output)
    elif args.command == "verify-restart":
        verify_restart(args.baseline)
    elif args.command == "fixture-server":
        fixture_server(args.port)
    elif args.command == "fixture-client":
        fixture_client(args.port)
    else:
        fixture_hang(args.port)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
