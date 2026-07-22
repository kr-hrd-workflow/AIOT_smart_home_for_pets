from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import math
import subprocess
import sys
import threading
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient


ROOT = Path(__file__).resolve().parents[3]
BACKEND_ROOT = ROOT / "backend"
for import_root in (ROOT, BACKEND_ROOT):
    if str(import_root) not in sys.path:
        sys.path.insert(0, str(import_root))

import app.main as main_module
import app.api as api_module
from app.clip_contracts import ClipDeliveryIdentity, ClipEventMetadata, ClipIntent
from app.clip_delivery import ClipDeliveryWorker, _queue_id
from app.config import JetsonConfig
from app.contracts import CameraStatus, SensorReadingOut
from app.jetson_client import JetsonClientError, JetsonVisionClient
from app.jetson_contracts import JetsonClipCommand, canonical_json
from jetson.protocol import ProtocolError, ReplayGuard, verify_request


FIXTURE = json.loads((ROOT / "contracts" / "petcare-jetson-wire-v1.json").read_text(encoding="utf-8"))
PSK = base64.urlsafe_b64decode(FIXTURE["auth"]["secret_base64url"] + "==")
NOW = datetime.fromisoformat(FIXTURE["observation"]["body"]["observed_at"].replace("Z", "+00:00"))
BOOT_ID = FIXTURE["status"]["boot_id"]
COMMAND_ID = FIXTURE["command"]["response"]["command_id"]
ZONES = {"pet_bed": (0, 0, 640, 480)}


def _utc(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


class FixtureJetson:
    def __init__(self) -> None:
        self.wall = NOW
        self.monotonic = 10.0
        self.boot_id = BOOT_ID
        self.connected = True
        self.guard = ReplayGuard(lambda: self.monotonic)
        self.operations: list[tuple[str, str]] = []
        self.commands: dict[str, tuple[bytes, dict[str, object]]] = {}
        self.accepted_monotonic: dict[str, float] = {}
        self.trigger_buckets: dict[str, int] = {}
        self.deleted: set[str] = set()
        self.media_started = threading.Event()
        self.release_media = threading.Event()
        self.stall_media = False

    def __call__(self, request: httpx.Request) -> httpx.Response:
        if not self.connected:
            raise httpx.ConnectError("fixture disconnected", request=request)
        target = request.url.raw_path.decode("ascii")
        body = request.read()
        try:
            verify_request(
                request.method,
                target,
                request.headers.multi_items(),
                body,
                PSK,
                self.boot_id,
                self.wall.timestamp(),
                self.guard,
            )
        except ProtocolError:
            return httpx.Response(
                401,
                content=canonical_json({"code": "unauthorized", "message": "Unauthorized"}),
            )
        self.operations.append((request.method, target))

        if request.method == "GET" and target == "/v1/status":
            status = dict(FIXTURE["status"])
            status["boot_id"] = self.boot_id
            status["server_time"] = _utc_text(self.wall)
            return httpx.Response(200, content=canonical_json(status))
        if request.method == "GET" and target == "/v1/observations?after=0&wait_ms=1000":
            observation = dict(FIXTURE["observation"]["body"])
            observation["boot_id"] = self.boot_id
            observation["observed_at"] = _utc_text(self.wall)
            return httpx.Response(200, content=canonical_json(observation))
        if request.method == "GET" and target == "/v1/preview.jpg":
            preview_headers = dict(FIXTURE["observation"]["preview"]["headers"])
            preview_headers["X-PetCare-Jetson-Boot-Id"] = self.boot_id
            preview_headers["X-PetCare-Jetson-Observed-At"] = _utc_text(self.wall)
            return httpx.Response(
                200,
                headers=preview_headers,
                content=base64.b64decode(FIXTURE["observation"]["preview"]["body_base64"]),
            )
        if target.startswith("/v1/clips/"):
            command_id = target.rsplit("/", 1)[1]
            if request.method == "PUT":
                return self._put(command_id, body)
            if request.method == "GET":
                return self._get(command_id)
            if request.method == "DELETE":
                return self._delete(command_id)
        raise AssertionError(f"unexpected fixture operation: {request.method} {target}")

    def _put(self, command_id: str, body: bytes) -> httpx.Response:
        existing = self.commands.get(command_id)
        if existing is not None and existing[0] != body:
            return httpx.Response(409, content=canonical_json({"code": "command_conflict", "message": "conflict"}))
        if command_id in self.deleted:
            return httpx.Response(410, content=canonical_json({"code": "clip_gone", "message": "gone"}))
        if existing is None:
            command = json.loads(body)
            committed_at = _utc(command["committed_at"])
            age = (self.wall - committed_at).total_seconds()
            if not -0.2 <= age <= 2.8:
                return httpx.Response(
                    409,
                    content=canonical_json({"code": "command_expired", "message": "expired"}),
                )
            receipt = {
                "accepted_at": _utc_text(self.wall),
                "accepted_boot_id": self.boot_id,
                "command_id": command_id,
                "state": "recording",
            }
            self.commands[command_id] = (body, receipt)
            self.accepted_monotonic[command_id] = self.monotonic
            self.trigger_buckets[command_id] = math.ceil(self.monotonic * 10)
            return httpx.Response(201, content=canonical_json(receipt))
        return httpx.Response(200, content=canonical_json(existing[1]))

    def _get(self, command_id: str) -> httpx.Response:
        if command_id in self.deleted:
            return httpx.Response(410, content=canonical_json({"code": "clip_gone", "message": "gone"}))
        if self.stall_media:
            self.media_started.set()
            assert self.release_media.wait(50), "test did not release the controlled media stall"
        headers = dict(FIXTURE["clip"]["headers"])
        headers["X-PetCare-Jetson-Boot-Id"] = self.boot_id
        headers["X-PetCare-Jetson-Command-Id"] = command_id
        return httpx.Response(200, headers=headers, content=base64.b64decode(FIXTURE["clip"]["body_base64"]))

    def _delete(self, command_id: str) -> httpx.Response:
        if command_id in self.deleted:
            return httpx.Response(410, content=canonical_json({"code": "clip_gone", "message": "gone"}))
        self.deleted.add(command_id)
        return httpx.Response(204)

    def restart(self) -> None:
        self.boot_id = "f" * 32
        self.deleted.update(self.commands)
        self.guard = ReplayGuard(lambda: self.monotonic)


def _utc_text(value: datetime) -> str:
    return value.astimezone(UTC).isoformat(timespec="microseconds").replace("+00:00", "Z")


def _config(tmp_path: Path) -> JetsonConfig:
    certificate = tmp_path / "jetson.crt"
    psk = tmp_path / "jetson.psk"
    certificate.write_bytes(b"fixture")
    psk.write_bytes(PSK)
    return JetsonConfig.model_validate(
        {
            "url": "https://192.168.50.20:9443",
            "home_ip": "192.168.50.10",
            "ca_cert_path": certificate,
            "psk_path": psk,
        },
        context={"ca_pem": b"fixture", "psk": PSK},
    )


def _client(tmp_path: Path, fixture: FixtureJetson) -> JetsonVisionClient:
    transport = httpx.MockTransport(fixture)
    clients = tuple(
        httpx.Client(base_url="https://192.168.50.20:9443", transport=transport) for _ in range(3)
    )
    return JetsonVisionClient(
        _config(tmp_path),
        clients=clients,  # type: ignore[arg-type]
        now=lambda: fixture.wall,
        now_seconds=lambda: fixture.wall.timestamp(),
        monotonic=lambda: fixture.monotonic,
    )


def _command(
    event_id: int = 41,
    *,
    committed_at: datetime | None = None,
    event_type: str = "eating",
) -> JetsonClipCommand:
    return JetsonClipCommand.model_validate(
        {
            "committed_at": _utc_text(committed_at) if committed_at is not None else FIXTURE["command"]["request"]["committed_at"],
            "event_id": event_id,
            "event_type": event_type,
            "occurred_at": FIXTURE["command"]["request"]["occurred_at"],
        }
    )


def test_exact_six_operation_fixture_receipt_replay_headers_and_jpeg(tmp_path: Path) -> None:
    fixture = FixtureJetson()
    client = _client(tmp_path, fixture)
    try:
        frame = client.next_frame(ZONES)
        first = client.put_clip(COMMAND_ID, _command())
        replay = client.put_clip(COMMAND_ID, _command(), first=False)
        destination = tmp_path / "clip.mp4"
        headers = client.download_clip(COMMAND_ID, destination)
        assert client.delete_clip(COMMAND_ID) == 204
        assert client.delete_clip(COMMAND_ID) == 410
    finally:
        client.close()

    assert frame.jpeg[:2] == b"\xff\xd8" and frame.detections[0].subject_id == "dog_001"
    assert first.status_code == 201 and replay.status_code == 200
    assert replay.receipt == first.receipt
    assert first.receipt.accepted_at == fixture.wall
    assert first.receipt.accepted_at != _command().committed_at
    assert headers.content_sha256 == hashlib.sha256(destination.read_bytes()).hexdigest()
    assert headers.video_codec == "h264" and headers.pixel_format == "yuv420p"
    expected_operations = {
        ("GET", "/v1/status"),
        ("GET", "/v1/observations?after=0&wait_ms=1000"),
        ("GET", "/v1/preview.jpg"),
        ("PUT", f"/v1/clips/{COMMAND_ID}"),
        ("GET", f"/v1/clips/{COMMAND_ID}"),
        ("DELETE", f"/v1/clips/{COMMAND_ID}"),
    }
    assert set(fixture.operations) == expected_operations


def test_real_admission_age_negative_skew_expiry_conflict_and_immutable_replay(tmp_path: Path) -> None:
    committed_at = NOW
    fixture = FixtureJetson()
    fixture.wall = committed_at - timedelta(milliseconds=200)
    fixture.monotonic = 10.01
    client = _client(tmp_path, fixture)
    command = _command(committed_at=committed_at)
    try:
        first = client.put_clip(COMMAND_ID, command)
        fixture.wall = committed_at + timedelta(seconds=20)
        fixture.monotonic += 20
        replay = client.put_clip(COMMAND_ID, command, first=False)
        with pytest.raises(JetsonClientError, match="command_conflict"):
            client.put_clip(COMMAND_ID, _command(42, committed_at=committed_at), first=False)
    finally:
        client.close()

    assert first.status_code == 201
    assert first.receipt.accepted_at == committed_at - timedelta(milliseconds=200)
    assert fixture.accepted_monotonic[COMMAND_ID] == 10.01
    assert fixture.trigger_buckets[COMMAND_ID] == 101
    assert replay.status_code == 200 and replay.receipt == first.receipt

    expired_fixture = FixtureJetson()
    expired_fixture.wall = committed_at + timedelta(seconds=2, microseconds=800_001)
    expired_root = tmp_path / "expired"
    expired_root.mkdir()
    expired_client = _client(expired_root, expired_fixture)
    try:
        with pytest.raises(JetsonClientError, match="command_expired"):
            expired_client.put_clip("2" * 32, command)
    finally:
        expired_client.close()
    assert "2" * 32 not in expired_fixture.commands


def test_restart_returns_gone_then_readmits_with_new_boot_inside_original_deadline(tmp_path: Path) -> None:
    fixture = FixtureJetson()
    committed_at = fixture.wall
    client = _client(tmp_path, fixture)
    try:
        first = client.put_clip(COMMAND_ID, _command(committed_at=committed_at))
        fixture.restart()
        with pytest.raises(JetsonClientError, match="clip_gone"):
            client.download_clip(COMMAND_ID, tmp_path / "gone.mp4")
        fixture.wall = committed_at + timedelta(seconds=2)
        fixture.monotonic += 2
        replacement = client.put_clip("3" * 32, _command(42, committed_at=committed_at))
    finally:
        client.close()

    assert first.receipt.accepted_boot_id == BOOT_ID
    assert replacement.status_code == 201
    assert replacement.receipt.accepted_boot_id == "f" * 32


def test_45_second_media_budget_times_out_without_blocking_new_put(tmp_path: Path) -> None:
    fixture = FixtureJetson()
    fixture.stall_media = True
    client = _client(tmp_path, fixture)
    first = client.put_clip(COMMAND_ID, _command())
    assert first.status_code == 201
    errors: list[BaseException] = []

    def download() -> None:
        try:
            client.download_clip(COMMAND_ID, tmp_path / "stalled.mp4")
        except BaseException as error:
            errors.append(error)

    worker = threading.Thread(target=download)
    stall_started = time.monotonic()
    worker.start()
    assert fixture.media_started.wait(1)
    second_id = "1" * 32
    put_started = time.monotonic()
    second = client.put_clip(second_id, _command(42), first=True)
    put_elapsed = time.monotonic() - put_started
    remaining = 45.0 - (time.monotonic() - stall_started)
    if remaining > 0:
        time.sleep(remaining)
    fixture.release_media.set()
    worker.join(2)
    stall_elapsed = time.monotonic() - stall_started
    client.close()

    assert second.status_code == 201
    assert put_elapsed <= 3.0
    second_put = fixture.operations.index(("PUT", f"/v1/clips/{second_id}"))
    assert fixture.operations[second_put - 1] == ("GET", "/v1/status")
    assert stall_elapsed >= 45.0
    assert not worker.is_alive()
    assert len(errors) == 1 and isinstance(errors[0], JetsonClientError)
    assert str(errors[0]) == "clip_timeout"
    assert not (tmp_path / "stalled.mp4").exists()


def test_ffprobe_is_validation_only_and_event_identity_comes_from_database(tmp_path: Path) -> None:
    ffprobe = tmp_path / "ffprobe"
    ffprobe.write_bytes(b"fixture")
    media = tmp_path / "media.mp4"
    media.write_bytes(base64.b64decode(FIXTURE["clip"]["body_base64"]))
    run_calls: list[list[str]] = []

    def run(args: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        run_calls.append(args)
        return subprocess.CompletedProcess(
            args,
            0,
            stdout=json.dumps({
                "streams": [{
                    "codec_name": "h264", "pix_fmt": "yuv420p", "width": 640, "height": 480,
                    "r_frame_rate": "10/1", "nb_frames": "300",
                }],
                "format": {"duration": "30.000"},
            }),
            stderr="",
        )

    worker = ClipDeliveryWorker(object(), object(), object(), work_dir=tmp_path, ffprobe_path=ffprobe, run=run)  # type: ignore[arg-type]
    from app.jetson_contracts import JetsonClipHeaders

    parsed = JetsonClipHeaders(
        boot_id=BOOT_ID,
        command_id=COMMAND_ID,
        content_sha256=hashlib.sha256(media.read_bytes()).hexdigest(),
        started_at=FIXTURE["clip"]["media"]["started_at"],
        ended_at=FIXTURE["clip"]["media"]["ended_at"],
        events="eating:41",
        frame_count=300,
        video_codec="h264",
        pixel_format="yuv420p",
    )
    worker._validate_media(media, parsed)

    accepted_at = _utc(FIXTURE["command"]["response"]["accepted_at"])
    row = ClipIntent(
        1,
        "eating",
        41,
        _utc(FIXTURE["command"]["request"]["occurred_at"]),
        accepted_at,
        accepted_at + timedelta(seconds=3),
        0,
        BOOT_ID,
        COMMAND_ID,
        accepted_at,
    )
    identity = ClipDeliveryIdentity(
        (ClipEventMetadata("eating", 41, FIXTURE["command"]["request"]["occurred_at"]),),
        (COMMAND_ID,),
        (accepted_at,),
    )
    metadata = worker._validate_identity(row, parsed, identity)

    assert metadata.events == identity.events
    assert run_calls and run_calls[0][-1] == str(media)


def test_unreleased_queue_reconciles_delete_crash_without_duplicate_item(tmp_path: Path) -> None:
    fixture = FixtureJetson()
    client = _client(tmp_path, fixture)
    client.status()
    command_ids = (COMMAND_ID, "4" * 32)
    content_digest = FIXTURE["clip"]["headers"]["X-PetCare-Jetson-Content-SHA256"]
    queue_id = _queue_id(BOOT_ID, "eating:41,resting:42", content_digest)

    class Repository:
        def __init__(self) -> None:
            self.failed_once = False
            self.processed: set[str] = set()
            self.commits: list[tuple[str, ...]] = []

        def command_processed(self, command_id: str) -> bool:
            return command_id in self.processed

        def mark_commands_processed(self, received: tuple[str, ...], _processed_at: datetime) -> None:
            self.commits.append(received)
            if not self.failed_once:
                self.failed_once = True
                raise RuntimeError("simulated database crash after DELETE")
            self.processed.update(received)

    class Queue:
        def __init__(self) -> None:
            self.releases: list[str] = []

        def _unreleased_command_ids(self, received_queue_id: str) -> tuple[str, ...]:
            assert received_queue_id == queue_id
            return command_ids

        def release(self, received_queue_id: str) -> None:
            assert received_queue_id == queue_id
            self.releases.append(received_queue_id)

    repository = Repository()
    queue = Queue()
    ffprobe = (tmp_path / "ffprobe.exe").resolve()
    ffprobe.write_bytes(b"fixture")
    worker = ClipDeliveryWorker(
        repository, client, queue, work_dir=tmp_path, ffprobe_path=ffprobe  # type: ignore[arg-type]
    )
    try:
        with pytest.raises(RuntimeError, match="simulated database crash"):
            worker._reconcile_queued(COMMAND_ID, queue_id, NOW)
        assert queue.releases == [] and repository.processed == set()

        worker._reconcile_queued(COMMAND_ID, queue_id, NOW + timedelta(seconds=1))
    finally:
        client.close()

    assert repository.commits == [command_ids, command_ids]
    assert repository.processed == set(command_ids)
    assert queue.releases == [queue_id]
    assert queue_id == _queue_id(BOOT_ID, "eating:41,resting:42", content_digest)
    assert fixture.operations.count(("DELETE", f"/v1/clips/{COMMAND_ID}")) == 2


def test_disconnect_over_three_real_seconds_leaves_main_lifecycle_sensor_and_health_apis_live(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fixture = FixtureJetson()
    fixture.connected = False
    client = _client(tmp_path, fixture)

    class Config:
        mqtt_enabled = False
        camera_source = "jetson"
        database_url = "postgresql+psycopg://fixture@127.0.0.1:55432/petcare"

    class Scalar:
        @staticmethod
        def scalar_one() -> int:
            return 1

    class Session:
        @staticmethod
        def execute(_statement: object) -> Scalar:
            return Scalar()

        @staticmethod
        def close() -> None:
            return None

    class Ingress:
        queue_full = False

        def __init__(self, _clock: object) -> None:
            pass

        @staticmethod
        def stop_accepting() -> None:
            return None

    class AliveThread:
        @staticmethod
        def is_alive() -> bool:
            return True

    class Worker:
        thread = AliveThread()

        def __init__(self, **_kwargs: object) -> None:
            pass

        @staticmethod
        def start() -> None:
            return None

        @staticmethod
        def shutdown() -> None:
            return None

    class Camera:
        pipeline = None
        jetson_client = client
        status = CameraStatus(state="offline", fps=0.0, inference_ms=0.0, last_frame_at=None, reason="jetson_unavailable")

        @staticmethod
        def start() -> None:
            return None

        @staticmethod
        def shutdown() -> None:
            return None

    sensor = SensorReadingOut(
        id=1,
        device_id="petzone-01",
        sensor_type="food_weight",
        value=10.0,
        unit="g",
        observed_at=NOW,
    )
    monkeypatch.delenv("PETCARE_AGENT_CONFIG", raising=False)
    monkeypatch.delenv("PETCARE_AGENT_TOOLS", raising=False)
    monkeypatch.setattr(main_module, "load_config", lambda: Config())
    monkeypatch.setattr(main_module, "configure_database", lambda _url: None)
    monkeypatch.setattr(main_module, "dispose_database", lambda: None)
    monkeypatch.setattr(main_module, "RuleIngress", Ingress)
    monkeypatch.setattr(main_module, "RuleEngine", lambda **_kwargs: object())
    monkeypatch.setattr(main_module, "RuleWorker", Worker)
    monkeypatch.setattr(main_module, "build_camera_service", lambda *_args: Camera())
    monkeypatch.setattr(api_module, "_session", lambda _application: Session())
    monkeypatch.setattr(api_module, "_latest_sensors", lambda _session: [sensor])

    results: list[tuple[int, int, str]] = []
    started = time.monotonic()
    try:
        with TestClient(main_module.app) as home:
            for index in range(4):
                with pytest.raises(JetsonClientError, match="jetson_unavailable"):
                    client.status()
                results.append((
                    home.get("/api/sensors/latest").status_code,
                    home.get("/api/health").status_code,
                    home.get("/api/camera/status").json()["state"],
                ))
                if index < 3:
                    time.sleep(1.05)
    finally:
        client.close()

    assert time.monotonic() - started > 3.0
    assert results == [(200, 200, "offline")] * 4


@pytest.mark.asyncio
async def test_deployed_lifespan_starts_jetson_camera_worker(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[str] = []

    class Camera:
        pipeline = None
        jetson_client = object()

        def start(self) -> None:
            calls.append("camera:start")

        def shutdown(self) -> None:
            calls.append("camera:shutdown")

    class Worker:
        def __init__(self, **_kwargs: object) -> None:
            pass

        def start(self) -> None:
            calls.append("rules:start")

        def shutdown(self) -> None:
            calls.append("rules:stop")

    class Config:
        mqtt_enabled = False
        camera_source = "jetson"

    monkeypatch.setattr(main_module, "load_config", lambda: Config())
    monkeypatch.setattr(main_module, "configure_database", lambda _url: None)
    monkeypatch.setattr(main_module, "dispose_database", lambda: None)
    monkeypatch.setattr(main_module, "build_camera_service", lambda *_args: Camera())
    monkeypatch.setattr(main_module, "RuleEngine", lambda **_kwargs: object())
    monkeypatch.setattr(main_module, "RuleWorker", Worker)
    components = object()
    monkeypatch.setenv("PETCARE_AGENT_CONFIG", str(ROOT / ".runtime" / "agent.json"))
    monkeypatch.setenv("PETCARE_AGENT_TOOLS", str(ROOT / ".runtime" / "agent-tools.json"))
    monkeypatch.setattr(
        main_module,
        "build_agent_components",
        lambda *_args: calls.append("agent:build") or components,
        raising=False,
    )
    monkeypatch.setattr(
        main_module,
        "start_agent_components",
        lambda received: calls.append("agent:start") if received is components else None,
        raising=False,
    )
    monkeypatch.setattr(
        main_module,
        "stop_agent_components",
        lambda received: calls.append("agent:stop") if received is components else None,
        raising=False,
    )
    Config.database_url = "postgresql+psycopg://fixture@127.0.0.1:55432/petcare"  # type: ignore[attr-defined]

    async with main_module.lifespan(FastAPI()):
        pass

    assert calls == [
        "agent:build",
        "rules:start",
        "camera:start",
        "agent:start",
        "rules:stop",
        "camera:shutdown",
        "agent:stop",
    ]


@pytest.mark.asyncio
async def test_lifespan_runs_required_shutdown_order_and_preserves_first_exception(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []
    first = RuntimeError("ingress shutdown failed")

    class Ingress:
        def __init__(self, _clock: object) -> None:
            pass

        def stop_accepting(self) -> None:
            calls.append("ingress:stop")
            raise first

    class Mqtt:
        @staticmethod
        def disabled() -> "Mqtt":
            return Mqtt()

        def start(self) -> None:
            calls.append("mqtt:start")

        def stop(self) -> None:
            calls.append("mqtt:stop")
            raise RuntimeError("mqtt shutdown failed")

    class Camera:
        pipeline = None
        jetson_client = object()

        def start(self) -> None:
            calls.append("camera:start")

        def shutdown(self) -> None:
            calls.append("camera:shutdown")
            raise RuntimeError("camera shutdown failed")

    class Worker:
        def __init__(self, **_kwargs: object) -> None:
            pass

        def start(self) -> None:
            calls.append("rules:start")

        def shutdown(self) -> None:
            calls.append("rules:stop")
            raise RuntimeError("rules shutdown failed")

    class Hub:
        async def _done(self) -> None:
            return None

        def start_broadcaster(self) -> asyncio.Task[None]:
            return asyncio.create_task(self._done())

        def publish_from_worker(self, _message: object) -> None:
            pass

        def shutdown(self) -> None:
            calls.append("hub:shutdown")

    class Config:
        mqtt_enabled = False
        camera_source = "jetson"
        database_url = "postgresql+psycopg://fixture@127.0.0.1:55432/petcare"

    components = object()
    monkeypatch.setattr(main_module, "load_config", lambda: Config())
    monkeypatch.setattr(main_module, "configure_database", lambda _url: calls.append("database:configure"))
    monkeypatch.setattr(main_module, "dispose_database", lambda: calls.append("database:dispose"))
    monkeypatch.setattr(main_module, "RuleIngress", Ingress)
    monkeypatch.setattr(main_module, "MqttIngestor", Mqtt)
    monkeypatch.setattr(main_module, "DashboardHub", Hub)
    monkeypatch.setattr(main_module, "build_camera_service", lambda *_args: Camera())
    monkeypatch.setattr(main_module, "RuleEngine", lambda **_kwargs: object())
    monkeypatch.setattr(main_module, "RuleWorker", Worker)
    monkeypatch.setenv("PETCARE_AGENT_CONFIG", str(ROOT / ".runtime" / "agent.json"))
    monkeypatch.setenv("PETCARE_AGENT_TOOLS", str(ROOT / ".runtime" / "agent-tools.json"))
    monkeypatch.setattr(main_module, "build_agent_components", lambda *_args: components)
    monkeypatch.setattr(main_module, "start_agent_components", lambda _components: calls.append("agent:start"))

    def stop_agent(_components: object) -> None:
        calls.append("agent:stop")
        raise RuntimeError("agent shutdown failed")

    monkeypatch.setattr(main_module, "stop_agent_components", stop_agent)

    with pytest.raises(RuntimeError) as raised:
        async with main_module.lifespan(FastAPI()):
            pass

    assert raised.value is first
    assert calls[-7:] == [
        "ingress:stop",
        "mqtt:stop",
        "rules:stop",
        "camera:shutdown",
        "agent:stop",
        "hub:shutdown",
        "database:dispose",
    ]
