from __future__ import annotations

import hashlib
import json
import subprocess
import threading
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest

from tools.jetson_vision_soak import (
    BRINGUP_CHECKS,
    BRINGUP_SCHEMA,
    HARNESS_SCHEMA,
    _close_live_stream,
    _collect_live_sample,
    _multipart_jpegs,
    _post_close_live_admissions,
    _unique_jpeg_count,
    collect_authenticated_soak,
    evaluate_soak,
    parse_tegrastats,
    validate_bringup_evidence,
    validate_soak_evidence,
    write_evidence,
)


CANDIDATE = "a" * 40
BOOT = "b" * 32
LIVE_OBSERVED_AT = "2026-07-20T04:00:00.000000Z"
TEGRAPROBE = (
    "RAM 1234/3964MB (lfb 12x4MB) CPU [10%@1479,off,off,off] "
    "GR3D_FREQ 99%@921 EMC_FREQ 10%@1600 PLL@31.5C CPU@47.0C GPU@46.5C"
)


def _utc_text(value: datetime) -> str:
    return value.astimezone(UTC).isoformat(timespec="microseconds").replace("+00:00", "Z")


def scenario_samples() -> tuple[list[dict[str, object]], dict[str, object]]:
    roles = (
        ("default", "1" * 32, "eating", 41, 0.0),
        ("overlap_first", "2" * 32, "resting", 42, 300.0),
        ("overlap_second", "3" * 32, "bed_sensor_mismatch", 43, 315.0),
        ("stall_new_put", "5" * 32, "eating", 45, 340.0),
    )
    puts = []
    calibrations = []
    for role, command_id, event_type, event_id, second in roles:
        started_wall = datetime(2026, 7, 20, 4, tzinfo=UTC) + timedelta(seconds=second)
        committed_at = started_wall + timedelta(milliseconds=200) if role == "default" else started_wall
        puts.append({
            "role": role,
            "command_id": command_id,
            "event_type": event_type,
            "event_id": event_id,
            "first": True,
            "status_code": 201,
            "committed_at": _utc_text(committed_at),
            "put_started_wall": _utc_text(started_wall),
            "put_finished_wall": _utc_text(started_wall + timedelta(milliseconds=10)),
            "put_started_monotonic": second,
            "put_finished_monotonic": second + 0.01,
            "accepted_at": _utc_text(started_wall),
            "accepted_boot_id": BOOT,
        })
        calibrations.append({
            "role": role,
            "command_id": command_id,
            "first_put": True,
            "measured_monotonic": second,
            "put_started_monotonic": second,
            "age_seconds": 0.0,
            "offset_ms": 0.0,
            "half_rtt_ms": 1.0,
        })
    expired_started = datetime(2026, 7, 20, 4, 0, 30, tzinfo=UTC)
    calibrations.append({
        "role": "deadline_expired",
        "command_id": "4" * 32,
        "first_put": True,
        "measured_monotonic": 30.0,
        "put_started_monotonic": 30.0,
        "age_seconds": 0.0,
        "offset_ms": 0.0,
        "half_rtt_ms": 1.0,
    })
    digest = hashlib.sha256(b"overlap").hexdigest()
    return calibrations, {
        "puts": puts,
        "expired": {
            "role": "deadline_expired",
            "command_id": "4" * 32,
            "event_type": "eating",
            "event_id": 44,
            "first": True,
            "committed_at": _utc_text(expired_started - timedelta(seconds=3.001)),
            "put_started_wall": _utc_text(expired_started),
            "put_started_monotonic": 30.0,
            "put_finished_monotonic": 30.01,
            "error_code": "command_expired",
        },
        "replay": {
            "role": "default",
            "command_id": "1" * 32,
            "first": False,
            "status_code": 200,
            "accepted_at": puts[0]["accepted_at"],
            "accepted_boot_id": BOOT,
        },
        "media_stall": {
            "source_command_id": "2" * 32,
            "header_command_id": "2" * 32,
            "content_sha256": digest,
            "started_monotonic": 0.0,
            "finished_monotonic": 45.0,
            "new_put_role": "stall_new_put",
            "new_put_started_monotonic": 0.0,
            "new_put_finished_monotonic": 0.01,
        },
        "delete_recovery": {
            "primary_command_id": "2" * 32,
            "coalesced_command_id": "3" * 32,
            "primary_status": 204,
            "retry_status": 410,
            "coalesced_status": 410,
            "cleanup_status": 204,
            "journal_command_ids": ["2" * 32, "3" * 32],
            "journal_content_sha256": digest,
            "media_content_sha256": digest,
        },
    }


def passing_samples() -> dict[str, object]:
    calibrations, scenarios = scenario_samples()
    outages = (
        ("jetson_disconnect", 100.0, 105.0),
        ("webcam_reconnect", 200.0, 202.0),
    )
    observations = []
    live_samples = []
    sensors = []
    previews = []
    resources = []
    for second in range(3601):
        disconnected = any(start <= second <= end for _kind, start, end in outages)
        observations.append({
            "monotonic": float(second),
            "connected": not disconnected,
            "authenticated_status": not disconnected,
            "inference_fps": None if disconnected else 3.0,
            "home_age_seconds": None if disconnected else 1.5,
            "preview_valid": not disconnected,
        })
        sensors.append({
            "monotonic": float(second),
            "success": True,
            "health_success": True,
            "camera_state": "offline" if 103 <= second <= 105 else "online",
        })
        if not disconnected:
            previews.append({"monotonic": float(second), "width": 640, "height": 480, "format": "jpeg"})
            live_samples.append({
                "monotonic": float(second),
                "duration_seconds": 1.0,
                "total_frames": 30,
                "unique_frames": 30,
            })
        resources.append({
            "monotonic": float(second),
            "ring_frames": 100,
            "ready_files": 2,
            "temp_bytes": 256 * 1024 * 1024,
            "temperature_c": 79.9,
            "cpu_clock_hz": 1_479_000_000,
            "gpu_clock_hz": 921_000_000,
            "ram_used_mib": 1234,
        })
    return {
        "candidate_sha": CANDIDATE,
        "collector_started_monotonic": 0.0,
        "collector_finished_monotonic": 3600.0,
        "observations": observations,
        "live_samples": live_samples,
        "stream_lifecycle": {
            "opened": 3, "closed": 3, "reconnects": 2, "active_streams": 0,
            "post_close_admissions": 2,
        },
        "calibrations": calibrations,
        "wall_monotonic_guard": {
            "duration_seconds": 3600.0,
            "sample_count": 36_001,
            "max_interval_seconds": 0.1,
            "max_discontinuity_seconds": 0.025,
        },
        "previews": previews,
        "clips": [
            {
                "kind": "default", "frame_count": 300, "duration_seconds": 30.0,
                "width": 640, "height": 480, "frame_rate": "10/1",
                "video_codec": "h264", "pixel_format": "yuv420p",
            },
            {
                "kind": "overlap", "frame_count": 450, "duration_seconds": 45.0,
                "width": 640, "height": 480, "frame_rate": "10/1",
                "video_codec": "h264", "pixel_format": "yuv420p",
            },
        ],
        "resources": resources,
        "locked_clocks": {"cpu_hz": 1_479_000_000, "gpu_hz": 921_000_000},
        "boot_id_before": BOOT,
        "boot_id_after": BOOT,
        "kernel_before": ["baseline"],
        "kernel_after": ["baseline"],
        "sensor_samples": sensors,
        "expected_outages": [
            {"kind": kind, "started_monotonic": start, "ended_monotonic": end}
            for kind, start, end in outages
        ],
        "scenarios": scenarios,
        "pretrigger_files": 0,
        "acknowledged_temp_files": 0,
    }


def _set(payload: dict[str, object], path: tuple[object, ...], value: object) -> None:
    target: object = payload
    for part in path[:-1]:
        target = target[part]  # type: ignore[index]
    target[path[-1]] = value  # type: ignore[index]


def test_exact_thresholds_pass_and_expected_disconnect_is_excluded_from_healthy_gap(tmp_path: Path) -> None:
    evidence = evaluate_soak(passing_samples(), expected_candidate_sha=CANDIDATE)

    assert evidence["status"] == "PASS", [
        name for name, passed in evidence["checks"].items() if not passed  # type: ignore[union-attr]
    ]
    assert all(evidence["checks"].values())  # type: ignore[union-attr]
    assert evidence["metrics"] == {
        "duration_seconds": 3600.0,
        "inference_fps_min": 3.0,
        "live_fps_min": 30.0,
        "observation_gap_p99_seconds": 1.0,
        "home_age_p99_seconds": 1.5,
        "temperature_max_c": 79.9,
    }

    output = tmp_path / "jetson-vision-node.json"
    write_evidence(output, evidence)
    stored = json.loads(output.read_text(encoding="utf-8"))
    validate_soak_evidence(stored, expected_candidate_sha=CANDIDATE, require_pass=True)
    assert not list(tmp_path.glob("*.tmp"))


def _live_headers(frame: bytes, *, sequence: int = 1, observed_at: str = LIVE_OBSERVED_AT) -> list[bytes]:
    return [
        b"Content-Type: image/jpeg",
        b"Content-Length: " + str(len(frame)).encode(),
        b"Cache-Control: private, no-store, no-transform",
        b"X-PetCare-Jetson-Boot-Id: " + BOOT.encode(),
        b"X-PetCare-Jetson-Sequence: " + str(sequence).encode(),
        b"X-PetCare-Jetson-Observed-At: " + observed_at.encode(),
        b"X-PetCare-Jetson-Content-SHA256: " + hashlib.sha256(frame).hexdigest().encode(),
    ]


def _live_part(frame: bytes, *, sequence: int = 1, observed_at: str = LIVE_OBSERVED_AT) -> bytes:
    return b"--frame\r\n" + b"\r\n".join(_live_headers(frame, sequence=sequence, observed_at=observed_at)) + b"\r\n\r\n" + frame + b"\r\n"


def test_live_fps_rejects_repeated_frames() -> None:
    payload = passing_samples()
    payload["live_samples"][0].update({  # type: ignore[index]
        "duration_seconds": 1.0,
        "total_frames": 30,
        "unique_frames": 29,
    })

    evidence = evaluate_soak(payload, expected_candidate_sha=CANDIDATE)

    assert evidence["status"] == "FAIL"
    assert evidence["checks"]["live_fps"] is False  # type: ignore[index]


def test_live_fps_rejects_sustained_below_nominal_cadence() -> None:
    payload = passing_samples()
    for sample in payload["live_samples"]:  # type: ignore[union-attr]
        sample["duration_seconds"] = 1.018

    evidence = evaluate_soak(payload, expected_candidate_sha=CANDIDATE)

    assert evidence["status"] == "FAIL"
    assert evidence["checks"]["live_fps"] is False  # type: ignore[index]


def test_live_fps_accepts_nominal_30fps_with_bounded_capture_jitter() -> None:
    payload = passing_samples()
    for sample in payload["live_samples"]:  # type: ignore[union-attr]
        sample["duration_seconds"] = 1.016

    evidence = evaluate_soak(payload, expected_candidate_sha=CANDIDATE)

    assert evidence["status"] == "PASS"
    assert evidence["checks"]["live_fps"] is True  # type: ignore[index]
    assert evidence["metrics"]["live_fps_min"] == pytest.approx(29.527559)  # type: ignore[index]


def test_live_fps_rejects_persistent_tail_stalls() -> None:
    payload = passing_samples()
    live_samples = payload["live_samples"]  # type: ignore[assignment]
    stalled = len(live_samples) // 100 + 1
    for sample in live_samples:
        sample["duration_seconds"] = 0.99
    for sample in live_samples[:stalled]:
        sample["duration_seconds"] = 1.051

    evidence = evaluate_soak(payload, expected_candidate_sha=CANDIDATE)

    assert evidence["status"] == "FAIL"
    assert evidence["checks"]["live_fps"] is False  # type: ignore[index]


@pytest.mark.parametrize(
    ("key", "value"),
    [("total_frames", 30.0), ("unique_frames", 30.0), ("unique_frames", 31)],
)
def test_live_fps_rejects_invalid_counts(key: str, value: object) -> None:
    payload = passing_samples()
    payload["live_samples"][0][key] = value  # type: ignore[index]

    with pytest.raises(ValueError):
        evaluate_soak(payload, expected_candidate_sha=CANDIDATE)


@pytest.mark.parametrize("key", ["total_frames", "extra"])
def test_live_fps_rejects_invalid_schema(key: str) -> None:
    payload = passing_samples()
    sample = payload["live_samples"][0]  # type: ignore[index]
    if key == "total_frames":
        del sample[key]
    else:
        sample[key] = 1

    with pytest.raises(ValueError):
        evaluate_soak(payload, expected_candidate_sha=CANDIDATE)


def test_claimed_duration_cannot_replace_collector_elapsed_time() -> None:
    payload = passing_samples()
    payload["duration_seconds"] = 3600.0
    payload["collector_finished_monotonic"] = 3599.0

    evidence = evaluate_soak(payload, expected_candidate_sha=CANDIDATE)

    assert evidence["status"] == "FAIL"
    assert evidence["checks"]["duration"] is False  # type: ignore[index]
    assert evidence["metrics"]["duration_seconds"] == 3599.0  # type: ignore[index]


def test_home_age_p99_threshold_fails_closed() -> None:
    payload = passing_samples()
    for observation in payload["observations"][:40]:  # type: ignore[index]
        observation["home_age_seconds"] = 1.501

    evidence = evaluate_soak(payload, expected_candidate_sha=CANDIDATE)

    assert evidence["status"] == "FAIL"
    assert evidence["checks"]["home_age"] is False  # type: ignore[index]
    assert evidence["metrics"]["home_age_p99_seconds"] == 1.501  # type: ignore[index]


@pytest.mark.parametrize(
    ("path", "value", "check"),
    [
        (("candidate_sha",), "c" * 40, "candidate_sha"),
        (("observations", 0, "inference_fps"), 2.99, "inference_fps"),
        (("stream_lifecycle", "reconnects"), 1, "stream_reconnect"),
        (("stream_lifecycle", "closed"), 2, "stream_shutdown"),
        (("stream_lifecycle", "post_close_admissions"), 1, "stream_shutdown"),
        (("calibrations", 0, "age_seconds"), 1.001, "clock_calibration"),
        (("calibrations", 0, "offset_ms"), -150.001, "clock_calibration"),
        (("wall_monotonic_guard", "max_discontinuity_seconds"), 0.0251, "wall_monotonic_guard"),
        (("previews", 0, "width"), 639, "preview"),
        (("clips", 0, "frame_count"), 299, "clips"),
        (("clips", 1, "duration_seconds"), 45.101, "clips"),
        (("resources", 0, "ring_frames"), 101, "resources"),
        (("resources", 0, "temperature_c"), 80.0, "temperature"),
        (("resources", 0, "cpu_clock_hz"), 1_478_999_999, "locked_clocks"),
        (("boot_id_after",), "d" * 32, "boot_id"),
        (("sensor_samples", 103, "success"), False, "sensor_independence"),
        (("scenarios", "delete_recovery", "retry_status"), 204, "event_scenarios"),
        (("scenarios", "media_stall", "finished_monotonic"), 44.999, "event_scenarios"),
        (("pretrigger_files",), 1, "temporary_files"),
        (("acknowledged_temp_files",), 1, "temporary_files"),
        (("observations", 203, "preview_valid"), False, "webcam_recovery"),
    ],
)
def test_each_hardware_gate_fails_closed(path: tuple[object, ...], value: object, check: str) -> None:
    payload = passing_samples()
    _set(payload, path, value)

    evidence = evaluate_soak(payload, expected_candidate_sha=CANDIDATE)

    assert evidence["status"] == "FAIL"
    assert evidence["checks"][check] is False  # type: ignore[index]


def test_unplanned_disconnect_fails_without_weakening_expected_disconnect_gate() -> None:
    payload = passing_samples()
    sample = payload["observations"][500]  # type: ignore[index]
    sample.update({"connected": False, "authenticated_status": False, "inference_fps": None, "home_age_seconds": None, "preview_valid": False})

    evidence = evaluate_soak(payload, expected_candidate_sha=CANDIDATE)

    assert evidence["checks"]["healthy_gap"] is False  # type: ignore[index]
    assert evidence["checks"]["sensor_independence"] is True  # type: ignore[index]


def test_new_kernel_throttle_or_undervoltage_message_fails() -> None:
    payload = passing_samples()
    payload["kernel_after"] = ["baseline", "soctherm: throttling due to OC ALARM"]
    evidence = evaluate_soak(payload, expected_candidate_sha=CANDIDATE)
    assert evidence["status"] == "FAIL"
    assert evidence["checks"]["kernel"] is False  # type: ignore[index]


def test_strict_bringup_schema_rejects_minimal_fake_and_secret_values() -> None:
    with pytest.raises(ValueError, match="bring-up evidence schema"):
        validate_bringup_evidence({"candidate_sha": CANDIDATE, "status": "PASS"}, expected_candidate_sha=CANDIDATE)

    evidence = {
        "schema": BRINGUP_SCHEMA,
        "candidate_sha": CANDIDATE,
        "status": "PASS",
        "board_model": "P3450 B01",
        "checks": {name: True for name in BRINGUP_CHECKS},
        "versions": {
            "l4t": "32.7.6", "jetpack": "4.6.6", "tensorrt": "8.2.1",
            "python": "3.6 https://credential.invalid", "architecture": "aarch64",
        },
        "hashes": {name: "c" * 64 for name in ("source", "python", "model", "engine")},
        "camera": {"width": 640, "height": 480, "format": "YUYV"},
        "counts": {"service": 1, "private_listener": 1, "public_listener": 0, "cloud_process": 0},
        "imports": {name: True for name in ("tensorrt", "pycuda", "opencv", "numpy", "gstreamer")},
        "plugins": {name: True for name in ("appsrc", "videoconvert", "nvvidconv", "nvv4l2h264enc", "h264parse", "qtmux", "filesink")},
    }
    with pytest.raises(ValueError, match="forbidden evidence value"):
        validate_bringup_evidence(evidence, expected_candidate_sha=CANDIDATE)


class FakeClock:
    def __init__(self) -> None:
        self.value = 0.0
        self.wall = datetime(2026, 7, 20, 4, tzinfo=UTC)
        self.changed = threading.Condition()
        self.scenario_deadlines = [0.0, 25.0, 30.0, 300.0, 315.0, 340.0]

    def monotonic(self) -> float:
        return self.value

    def sleep(self, seconds: float) -> None:
        with self.changed:
            target = self.value + seconds
            while self.scenario_deadlines and self.value < self.scenario_deadlines[0] <= target:
                self.value = self.scenario_deadlines[0]
                self.changed.notify_all()
                while self.scenario_deadlines and self.scenario_deadlines[0] <= self.value:
                    self.changed.wait(timeout=0.1)
            self.value = max(self.value, target)
            self.changed.notify_all()

    def wait_until(self, deadline: float) -> None:
        with self.changed:
            while self.value < deadline:
                self.changed.wait(timeout=0.1)
            if self.scenario_deadlines and abs(self.scenario_deadlines[0] - deadline) <= 1e-9:
                self.scenario_deadlines.pop(0)
                self.changed.notify_all()

    def utc_now(self) -> datetime:
        return self.wall + timedelta(seconds=self.value)


class OperationClock:
    def __init__(self) -> None:
        self.value = 0.0
        self.lock = threading.Lock()

    def monotonic(self) -> float:
        with self.lock:
            return self.value

    def sleep(self, seconds: float) -> None:
        with self.lock:
            self.value += seconds


class FakeJetson:
    def __init__(
        self, clock: FakeClock, *, live_attempts: list[int | None] | None = None, stick_remote_after_close: int | None = None,
    ) -> None:
        self.clock = clock
        self.status_calls = 0
        self.frame_calls = 0
        self.calibration_calls = 0
        self.operations: list[tuple[str, str, bool | None]] = []
        self.receipts: dict[str, SimpleNamespace] = {}
        self.deleted: set[str] = set()
        self.live_streams: list[FakeLiveStream] = []
        self.live_stream_calls = 0
        self.active_live_streams = 0
        self.live_attempts = list(live_attempts or [0])
        self.remote_live_admitted = False
        self.live_closes = 0
        self.stick_remote_after_close = stick_remote_after_close
        self.closed = False

    def _offline(self) -> bool:
        return 100 <= self.clock.value <= 105 or 200 <= self.clock.value <= 202

    def status(self) -> object:
        self.status_calls += 1
        if self._offline():
            raise OSError("disconnected")
        return SimpleNamespace(boot_id=BOOT)

    def next_frame(self, _zones: Mapping[str, object]) -> object:
        self.frame_calls += 1
        if self._offline():
            raise OSError("disconnected")
        return SimpleNamespace(
            observed_at=self.clock.utc_now() - timedelta(seconds=1),
            fps=3.0,
            jpeg=b"\xff\xd8fixture\xff\xd9",
        )

    def calibrate_clock(self) -> object:
        self.calibration_calls += 1
        return SimpleNamespace(measured_monotonic=self.clock.value, offset_ms=0.0, half_rtt_ms=1.0)

    def live_stream(self) -> object:
        failure_after = self.live_attempts[self.live_stream_calls] if self.live_stream_calls < len(self.live_attempts) else None
        stream = FakeLiveStream(self, failure_after=failure_after)
        self.live_stream_calls += 1
        self.live_streams.append(stream)
        return stream

    def put_clip(self, command_id: str, command: Mapping[str, object], *, first: bool = True) -> object:
        self.operations.append(("PUT", command_id, first))
        committed_at = datetime.fromisoformat(str(command["committed_at"]).replace("Z", "+00:00"))
        if (self.clock.utc_now() - committed_at).total_seconds() > 2.8:
            raise RuntimeError("command_expired")
        receipt = self.receipts.get(command_id)
        status_code = 200
        if receipt is None:
            receipt = SimpleNamespace(
                accepted_at=self.clock.utc_now(),
                accepted_boot_id=BOOT,
                command_id=command_id,
                state="recording",
            )
            self.receipts[command_id] = receipt
            status_code = 201
        return SimpleNamespace(status_code=status_code, receipt=receipt)

    def download_clip(self, command_id: str, destination: Path) -> object:
        self.operations.append(("GET", command_id, None))
        content = b"default" if command_id == "1" * 32 else b"overlap"
        destination.write_bytes(content)
        return SimpleNamespace(command_id=command_id, content_sha256=hashlib.sha256(content).hexdigest())

    def delete_clip(self, command_id: str) -> int:
        self.operations.append(("DELETE", command_id, None))
        if command_id in self.deleted:
            return 410
        self.deleted.add(command_id)
        if command_id == "2" * 32:
            self.deleted.add("3" * 32)
        return 204

    def close(self) -> None:
        self.closed = True


class FakeLiveStream:
    def __init__(self, jetson: FakeJetson, *, failure_after: int | None) -> None:
        self.jetson = jetson
        self.failure_after = failure_after
        self.closed = False
        self._iterator: object | None = None

    def __iter__(self):
        iterator = self._chunks()
        self._iterator = iterator
        return iterator

    def _chunks(self):
        self.jetson.active_live_streams += 1
        admitted = False
        try:
            if self.jetson.remote_live_admitted:
                raise OSError("live stream already admitted")
            self.jetson.remote_live_admitted = True
            admitted = True
            frame_number = 0
            while True:
                if self.failure_after is not None and frame_number >= self.failure_after:
                    raise OSError("live stream disconnected")
                if self.jetson._offline():
                    raise OSError("live stream disconnected")
                self.jetson.clock.sleep(1.0 / 30.0)
                if self.jetson._offline():
                    raise OSError("live stream disconnected")
                frame = b"\xff\xd8" + f"{frame_number:08d}".encode() + b"\xff\xd9"
                observed_at = self.jetson.clock.utc_now().isoformat(timespec="microseconds").replace("+00:00", "Z")
                yield _live_part(frame, sequence=frame_number + 1, observed_at=observed_at)
                frame_number += 1
        finally:
            self.closed = True
            self.jetson.active_live_streams -= 1
            if admitted:
                self.jetson.live_closes += 1
                if self.jetson.stick_remote_after_close != self.jetson.live_closes:
                    self.jetson.remote_live_admitted = False

    def close(self) -> None:
        iterator = self._iterator
        if iterator is not None:
            close = getattr(iterator, "close", None)
            if callable(close):
                close()
        self.closed = True


class FakeResponse:
    def __init__(self, status_code: int, body: object) -> None:
        self.status_code = status_code
        self._body = body

    def json(self) -> object:
        return self._body


class FakeHome:
    def __init__(self, clock: FakeClock) -> None:
        self.clock = clock
        self.targets: list[str] = []
        self.closed = False

    def get(self, target: str) -> FakeResponse:
        self.targets.append(target)
        if target == "/api/sensors/latest":
            return FakeResponse(200, [])
        if target == "/api/health":
            return FakeResponse(200, {"status": "healthy"})
        offline = 103 <= self.clock.value <= 105
        return FakeResponse(200, {"state": "offline" if offline else "online"})

    def close(self) -> None:
        self.closed = True


def test_collector_uses_authenticated_jetson_and_live_home_apis_for_real_monotonic_hour(tmp_path: Path) -> None:
    clock = FakeClock()
    operation_clock = OperationClock()
    jetson = FakeJetson(clock)
    home = FakeHome(clock)
    ffprobe = (tmp_path / "ffprobe.exe").resolve()
    ffprobe.write_bytes(b"fixture")
    resource_samples = [
        {
            "offset_seconds": float(second), "tegrastats": TEGRAPROBE,
            "ring_frames": 100, "ready_files": 2, "temp_bytes": 256 * 1024 * 1024,
        }
        for second in range(3601)
    ]
    harness = {
        "schema": HARNESS_SCHEMA,
        "candidate_sha": CANDIDATE,
        "authorized_events": [
            {"role": "default", "offset_seconds": 0.0, "command_id": "1" * 32, "event_type": "eating", "event_id": 41},
            {"role": "deadline_expired", "offset_seconds": 30.0, "command_id": "4" * 32, "event_type": "eating", "event_id": 44},
            {"role": "overlap_first", "offset_seconds": 300.0, "command_id": "2" * 32, "event_type": "resting", "event_id": 42},
            {"role": "overlap_second", "offset_seconds": 315.0, "command_id": "3" * 32, "event_type": "bed_sensor_mismatch", "event_id": 43},
            {"role": "stall_new_put", "offset_seconds": 340.0, "command_id": "5" * 32, "event_type": "eating", "event_id": 45},
        ],
        "media_stall_seconds": 45.0,
        "wall_monotonic_guard": {
            "duration_seconds": 3600.0, "sample_count": 36_001,
            "max_interval_seconds": 0.1, "max_discontinuity_seconds": 0.0,
        },
        "resource_samples": resource_samples,
        "locked_clocks": {"cpu_hz": 1_479_000_000, "gpu_hz": 921_000_000},
        "kernel_before": ["baseline"],
        "kernel_after": ["baseline"],
        "expected_outages": [
            {"kind": "jetson_disconnect", "start_offset_seconds": 100.0, "end_offset_seconds": 105.0},
            {"kind": "webcam_reconnect", "start_offset_seconds": 200.0, "end_offset_seconds": 202.0},
        ],
        "pretrigger_files": 0,
        "acknowledged_temp_files": 0,
    }

    def ffprobe_run(args: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        frames = 450 if Path(args[-1]).name == "overlap.mp4" else 300
        return subprocess.CompletedProcess(
            args, 0,
            stdout=json.dumps({
                "streams": [{
                    "codec_name": "h264", "pix_fmt": "yuv420p", "width": 640, "height": 480,
                    "r_frame_rate": "10/1", "nb_frames": str(frames),
                }],
                "format": {"duration": f"{frames / 10:.3f}"},
            }),
            stderr="",
        )

    payload = collect_authenticated_soak(
        jetson=jetson,
        home=home,
        harness_loader=lambda: harness,
        ffprobe_path=ffprobe,
        candidate_sha=CANDIDATE,
        monotonic=clock.monotonic,
        utc_now=clock.utc_now,
        sleep=clock.sleep,
        wait_until=clock.wait_until,
        operation_monotonic=operation_clock.monotonic,
        stall_sleep=operation_clock.sleep,
        run=ffprobe_run,
    )
    evidence = evaluate_soak(payload, expected_candidate_sha=CANDIDATE)

    assert payload["collector_finished_monotonic"] - payload["collector_started_monotonic"] == 3600.0  # type: ignore[operator]
    live_samples = payload["live_samples"]
    connected_times = [item["monotonic"] for item in payload["observations"] if item["connected"]]
    assert [item["monotonic"] for item in live_samples] == connected_times  # type: ignore[index]
    assert all(item["duration_seconds"] == pytest.approx(1.0) for item in live_samples)  # type: ignore[index]
    assert {(item["total_frames"], item["unique_frames"]) for item in live_samples} == {(30, 30)}  # type: ignore[index]
    assert jetson.live_stream_calls == 6
    assert all(stream.closed for stream in jetson.live_streams)
    assert jetson.active_live_streams == 0
    assert jetson.remote_live_admitted is False
    assert payload["stream_lifecycle"] == {
        "opened": 4,
        "closed": 4,
        "reconnects": 3,
        "active_streams": 0,
        "post_close_admissions": 2,
    }
    assert evidence["checks"]["stream_reconnect"] is True  # type: ignore[index]
    assert evidence["checks"]["stream_shutdown"] is True  # type: ignore[index]
    assert jetson.status_calls == 3600 and jetson.frame_calls == 3591 and jetson.calibration_calls == 5
    assert [(method, first) for method, _command_id, first in jetson.operations if method == "PUT"] == [
        ("PUT", True),
        ("PUT", False),
        ("PUT", True),
        ("PUT", True),
        ("PUT", True),
        ("PUT", True),
    ]
    assert len([item for item in jetson.operations if item[0] == "GET"]) == 2
    assert payload["scenarios"] != harness.get("scenarios")
    assert set(home.targets) == {"/api/sensors/latest", "/api/health", "/api/camera/status"}
    failed_checks = [name for name, passed in evidence["checks"].items() if not passed]  # type: ignore[union-attr]
    assert evidence["status"] == "PASS", (failed_checks, payload["scenarios"], payload["calibrations"])


def test_persistent_live_sample_discards_partial_attempt_before_reconnect() -> None:
    clock = FakeClock()
    jetson = FakeJetson(clock, live_attempts=[10])
    state = {"stream": None, "parser": None, "opened": 0, "closed": 0, "reconnects": 0}

    sample = _collect_live_sample(jetson, state)
    _close_live_stream(state)

    assert sample["duration_seconds"] == pytest.approx(1.0, abs=3e-6)
    assert sample["total_frames"] == 30 and sample["unique_frames"] == 30
    assert state == {"stream": None, "parser": None, "opened": 2, "closed": 2, "reconnects": 1}
    assert all(stream.closed for stream in jetson.live_streams)
    assert jetson.active_live_streams == 0
    assert jetson.remote_live_admitted is False


def test_live_sample_uses_part_metadata_instead_of_buffer_drain_speed() -> None:
    class BufferedStream:
        closed = False

        def __iter__(self):
            started = datetime(2026, 7, 20, 4, tzinfo=UTC)
            for sequence in range(1, 31):
                observed_at = _utc_text(started + timedelta(seconds=(sequence - 1) / 24))
                yield _live_part(b"\xff\xd8" + bytes([sequence]) + b"\xff\xd9", sequence=sequence, observed_at=observed_at)

        def close(self) -> None:
            self.closed = True

    class Jetson:
        def __init__(self) -> None:
            self.stream = BufferedStream()

        def live_stream(self) -> BufferedStream:
            return self.stream

    jetson = Jetson()
    stream = jetson.stream
    state = {"stream": None, "parser": None, "opened": 0, "closed": 0, "reconnects": 0}

    with pytest.raises(ValueError, match="live sample duration"):
        _collect_live_sample(jetson, state)

    assert stream.closed is True


def test_live_sample_rejects_nonmonotonic_part_sequence() -> None:
    class Stream:
        closed = False

        def __iter__(self):
            started = datetime(2026, 7, 20, 4, tzinfo=UTC)
            for position in range(30):
                sequence = 1 if position == 1 else position + 1
                observed_at = _utc_text(started + timedelta(seconds=position / 30))
                yield _live_part(b"\xff\xd8" + bytes([position]) + b"\xff\xd9", sequence=sequence, observed_at=observed_at)

        def close(self) -> None:
            self.closed = True

    stream = Stream()
    jetson = type("Jetson", (), {"live_stream": lambda self: stream})()
    state = {"stream": None, "parser": None, "opened": 0, "closed": 0, "reconnects": 0}

    with pytest.raises(ValueError, match="live sample metadata"):
        _collect_live_sample(jetson, state)

    assert stream.closed is True


def test_persistent_live_sample_closes_both_failed_attempts() -> None:
    clock = FakeClock()
    jetson = FakeJetson(clock, live_attempts=[0, 0])
    state = {"stream": None, "parser": None, "opened": 0, "closed": 0, "reconnects": 0}

    with pytest.raises(RuntimeError, match="live stream reconnect failed"):
        _collect_live_sample(jetson, state)

    assert state == {"stream": None, "parser": None, "opened": 2, "closed": 2, "reconnects": 1}
    assert all(stream.closed for stream in jetson.live_streams)
    assert jetson.active_live_streams == 0
    assert jetson.remote_live_admitted is False
    assert _post_close_live_admissions(jetson) == 2
    assert jetson.active_live_streams == 0


def test_post_close_live_admissions_prove_remote_single_admission_released() -> None:
    clock = FakeClock()
    jetson = FakeJetson(clock, live_attempts=[None])
    state = {"stream": None, "parser": None, "opened": 0, "closed": 0, "reconnects": 0}

    _collect_live_sample(jetson, state)
    _close_live_stream(state)

    assert _post_close_live_admissions(jetson) == 2
    assert jetson.live_stream_calls == 3
    assert jetson.active_live_streams == 0
    assert jetson.remote_live_admitted is False


def test_post_close_live_admissions_rejects_occupied_remote_admission() -> None:
    clock = FakeClock()
    jetson = FakeJetson(clock, live_attempts=[None], stick_remote_after_close=1)
    state = {"stream": None, "parser": None, "opened": 0, "closed": 0, "reconnects": 0}

    _collect_live_sample(jetson, state)
    _close_live_stream(state)

    assert _post_close_live_admissions(jetson) == 0
    assert jetson.active_live_streams == 0
    assert jetson.remote_live_admitted is True


def test_tegrastats_parser_keeps_only_gate_metrics() -> None:
    assert parse_tegrastats(TEGRAPROBE) == {
        "ram_used_mib": 1234,
        "cpu_clock_hz": 1_479_000_000,
        "gpu_clock_hz": 921_000_000,
        "temperature_c": 47.0,
    }


def test_multipart_jpegs_handles_every_two_chunk_split() -> None:
    first = b"\xff\xd8first\xff\xd9"
    second = b"\xff\xd8second\xff\xd9"
    payload = _live_part(first, sequence=1) + _live_part(second, sequence=2) + b"--frame--\r\n"

    for split in range(len(payload) + 1):
        assert list(_multipart_jpegs((payload[:split], payload[split:]))) == [first, second]


def test_unique_jpeg_count_deduplicates_identical_frames() -> None:
    first = b"\xff\xd8first\xff\xd9"
    second = b"\xff\xd8second\xff\xd9"

    assert _unique_jpeg_count((first, first, second, first)) == 2


@pytest.mark.parametrize(
    "replacement",
    [
        (b"Cache-Control: private, no-store, no-transform", b"Cache-Control: public"),
        (b"X-PetCare-Jetson-Boot-Id: " + BOOT.encode(), b"X-PetCare-Jetson-Boot-Id: " + b"g" * 32),
        (b"X-PetCare-Jetson-Sequence: 1", b"X-PetCare-Jetson-Sequence: 0"),
        (LIVE_OBSERVED_AT.encode(), b"2026-07-20T04:00:00Z"),
        (
            b"X-PetCare-Jetson-Content-SHA256: " + hashlib.sha256(b"\xff\xd8first\xff\xd9").hexdigest().encode(),
            b"X-PetCare-Jetson-Content-SHA256: " + b"0" * 64,
        ),
    ],
)
def test_multipart_jpegs_rejects_live_contract_mutations(replacement: tuple[bytes, bytes]) -> None:
    frame = b"\xff\xd8first\xff\xd9"
    payload = _live_part(frame) + b"--frame--\r\n"
    source, destination = replacement
    payload = payload.replace(source, destination, 1)

    with pytest.raises(ValueError):
        list(_multipart_jpegs((payload,)))


def test_multipart_jpegs_rejects_tampered_live_digest() -> None:
    frame = b"\xff\xd8first\xff\xd9"
    payload = _live_part(frame) + b"--frame--\r\n"
    payload = payload.replace(frame, b"\xff\xd8other\xff\xd9", 1)

    with pytest.raises(ValueError, match="digest"):
        list(_multipart_jpegs((payload,)))


@pytest.mark.parametrize(
    ("chunks", "maximum_frame_bytes", "message"),
    [
        (
            (_live_part(b"\xff\xd8a\xff\xd9").replace(
                b"Content-Type: image/jpeg\r\n", b"Content-Type: image/jpeg\r\nContent-Type: image/jpeg\r\n", 1,
            ) + b"--frame--\r\n",),
            10,
            "invalid multipart headers",
        ),
        (
            (_live_part(b"\xff\xd8a\xff\xd9").replace(
                b"\r\n\r\n", b"\r\nX-Test: 1\r\n\r\n", 1,
            ) + b"--frame--\r\n",),
            10,
            "invalid multipart headers",
        ),
        ((_live_part(b"\xff\xd8a\xff\xd9").replace(b"Content-Length: 5", b"Content-Length: 05", 1) + b"--frame--\r\n",), 10, "invalid multipart length"),
        ((_live_part(b"\xff\xd8a\xff\xd9") + b"--frame--\r\n",), 4, "invalid multipart length"),
        ((_live_part(b"\xff\xd8a\xff\xd9"),), 10, "truncated multipart stream"),
        ((_live_part(b"\xff\xd8a\xff\xd9") + b"--frame--\r\ntrailing",), 10, "trailing multipart data"),
        ((_live_part(b"\x00\xd8a\xff\xd9") + b"--frame--\r\n",), 10, "invalid JPEG frame"),
        ((_live_part(b"\xff\xd8a\x00\x00") + b"--frame--\r\n",), 10, "invalid JPEG frame"),
        ((_live_part(b"\xff\xd8a\xff\xd9") + b"--frame--\r\n", "not bytes"), 10, "invalid multipart chunk"),
    ],
)
def test_multipart_jpegs_rejects_invalid_streams(
    chunks: tuple[object, ...], maximum_frame_bytes: int, message: str,
) -> None:
    with pytest.raises(ValueError, match=message):
        list(_multipart_jpegs(chunks, maximum_frame_bytes=maximum_frame_bytes))  # type: ignore[arg-type]
