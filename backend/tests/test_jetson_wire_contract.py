from __future__ import annotations

import base64
import hashlib
import hmac
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
FIXTURE = ROOT / "contracts" / "petcare-jetson-wire-v1.json"
AGENT_FIXTURE = ROOT / "contracts" / "petcare-agent-wire-v1.json"
UNRESERVED = b"ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-._~"


def load_fixture() -> dict[str, object]:
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def b64url_decode(value: str) -> bytes:
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))


def b64url_encode(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def quote(value: str) -> str:
    return "".join(chr(byte) if byte in UNRESERVED else "%{:02X}".format(byte) for byte in value.encode("utf-8"))


def canonical_query(pairs: list[list[str]]) -> str:
    encoded = sorted((quote(key), quote(value)) for key, value in pairs)
    return "&".join("{}={}".format(key, value) for key, value in encoded)


def first_put_admitted(admission: dict[str, object], calibration_age: float, offset_ms: float, half_rtt_ms: float, wall_age: float, discontinuity: bool = False) -> bool:
    uncertainty = abs(offset_ms) + half_rtt_ms + admission["drift_budget_ms"]
    return (
        not discontinuity
        and calibration_age <= admission["calibration_max_age_seconds"]
        and uncertainty <= admission["uncertainty_max_ms"]
        and admission["wall_age_min_seconds"] <= wall_age <= admission["wall_age_max_seconds"]
    )


def replay_status(replay: dict[str, object], state: str, same_digest: bool) -> int:
    if not same_digest:
        return replay["changed_digest_status"]
    if state in replay["identical_active_states"]:
        return replay["identical_status"]
    if state in replay["gone_states"]:
        return replay["gone_status"]
    raise AssertionError("unknown replay state")


def jpeg_shape(data: bytes) -> tuple[int, int, int]:
    assert data[:2] == b"\xff\xd8"
    offset = 2
    while offset < len(data):
        assert data[offset] == 0xFF
        marker = data[offset + 1]
        offset += 2
        if marker in (0xD8, 0xD9):
            continue
        length = int.from_bytes(data[offset : offset + 2], "big")
        if marker in (0xC0, 0xC1, 0xC2, 0xC3):
            return (
                int.from_bytes(data[offset + 3 : offset + 5], "big"),
                int.from_bytes(data[offset + 5 : offset + 7], "big"),
                data[offset + 7],
            )
        offset += length
    raise AssertionError("JPEG has no SOF marker")


def test_auth_vector_and_exact_six_operations() -> None:
    fixture = load_fixture()
    assert list(fixture) == ["auth", "status", "observation", "command", "clip", "errors"]
    auth = fixture["auth"]
    assert list(auth) == ["version", "secret_base64url", "request", "canonical_query", "operations"]
    request = auth["request"]
    assert list(request) == [
        "method", "target", "boot_id", "timestamp", "nonce", "body", "body_json",
        "body_sha256", "canonical", "signature", "headers",
    ]
    assert list(request["body"]) == ["committed_at", "event_id", "event_type", "occurred_at"]
    body = json.dumps(request["body"], sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
    digest = hashlib.sha256(body).hexdigest()
    assert body.decode() == request["body_json"]
    assert digest == request["body_sha256"]
    assert request["target"] == "/v1/clips/fedcba9876543210fedcba9876543210"
    canonical = "{}\n{}\n{}\n{}\n{}\n{}\n{}\n".format(
        auth["version"],
        request["method"],
        request["target"],
        request["boot_id"],
        request["timestamp"],
        request["nonce"],
        digest,
    ).encode()
    assert canonical.decode() == request["canonical"]
    signature = b64url_encode(hmac.new(b64url_decode(auth["secret_base64url"]), canonical, hashlib.sha256).digest())
    assert signature == request["signature"] == "jRDgkQ3q6mrGL7rQxGtI1QANRKamx9ieVseiURXrnzE"

    headers = request["headers"]
    assert list(headers) == [
        "X-PetCare-Jetson-Version",
        "X-PetCare-Jetson-Boot-Id",
        "X-PetCare-Jetson-Timestamp",
        "X-PetCare-Jetson-Nonce",
        "X-PetCare-Jetson-Content-SHA256",
        "X-PetCare-Jetson-Signature",
    ]
    assert headers["X-PetCare-Jetson-Version"] == auth["version"]
    assert headers["X-PetCare-Jetson-Boot-Id"] == request["boot_id"]
    assert len(request["boot_id"]) == 32 and set(request["boot_id"]) <= set("0123456789abcdef")
    assert headers["X-PetCare-Jetson-Timestamp"] == request["timestamp"]
    assert headers["X-PetCare-Jetson-Timestamp"].isdigit()
    assert headers["X-PetCare-Jetson-Nonce"] == request["nonce"]
    assert len(b64url_decode(headers["X-PetCare-Jetson-Nonce"])) == 16
    assert headers["X-PetCare-Jetson-Content-SHA256"] == digest == request["body_sha256"]
    assert len(digest) == 64 and set(digest) <= set("0123456789abcdef")
    assert headers["X-PetCare-Jetson-Signature"] == request["signature"]
    assert len(b64url_decode(headers["X-PetCare-Jetson-Signature"])) == 32

    query = auth["canonical_query"]
    assert list(query) == ["pairs", "value", "target", "empty_body_sha256", "canonical", "signature"]
    assert canonical_query(query["pairs"]) == query["value"]
    assert query["target"] == "/v1/observations?" + query["value"]
    assert query["empty_body_sha256"] == hashlib.sha256(b"").hexdigest()
    query_canonical = "{}\nGET\n{}\n{}\n{}\n{}\n{}\n".format(
        auth["version"], query["target"], request["boot_id"], request["timestamp"], request["nonce"], query["empty_body_sha256"]
    ).encode()
    assert query_canonical.decode() == query["canonical"]
    assert b64url_encode(hmac.new(b64url_decode(auth["secret_base64url"]), query_canonical, hashlib.sha256).digest()) == query["signature"]
    assert auth["operations"] == [
        {"method": "GET", "target": "/v1/status", "success": [200]},
        {"method": "GET", "target": "/v1/observations?after=42&wait_ms=1000", "success": [200, 204]},
        {"method": "GET", "target": "/v1/preview.jpg", "success": [200]},
        {"method": "PUT", "target": "/v1/clips/fedcba9876543210fedcba9876543210", "success": [201, 200]},
        {"method": "GET", "target": "/v1/clips/fedcba9876543210fedcba9876543210", "success": [200]},
        {"method": "DELETE", "target": "/v1/clips/fedcba9876543210fedcba9876543210", "success": [204]},
    ]


def test_strict_json_and_preview_contracts() -> None:
    fixture = load_fixture()
    status = fixture["status"]
    assert list(status) == ["boot_id", "server_time", "camera_state", "clip_state", "jetpack", "l4t", "tensorrt", "temperature_c", "throttled"]
    assert (status["jetpack"], status["l4t"], status["tensorrt"]) == ("4.6.6", "32.7.6", "8.2.1")

    observation = fixture["observation"]
    assert list(observation) == ["body", "preview"]
    body = observation["body"]
    assert list(body) == ["boot_id", "sequence", "observed_at", "width", "height", "fps", "inference_ms", "detections"]
    assert list(body["detections"][0]) == ["detected_type", "confidence", "bbox_x", "bbox_y", "bbox_width", "bbox_height"]
    assert (body["sequence"], body["width"], body["height"]) == (42, 640, 480)

    preview = observation["preview"]
    assert list(preview) == ["body_base64", "max_content_length", "max_fps", "headers", "ignored_transport_headers"]
    jpeg = base64.b64decode(preview["body_base64"])
    assert jpeg_shape(jpeg) == (480, 640, 3)
    assert len(jpeg) == 2097
    assert hashlib.sha256(jpeg).hexdigest() == "e8f2ea5a7c0511f2e32a41933ef8af70a1b96fc3acb9523a89dfb9d8aeb63cfd"
    assert 0 < len(jpeg) <= preview["max_content_length"] == 1_048_576
    headers = preview["headers"]
    assert list(headers) == [
        "Content-Type",
        "Content-Length",
        "Cache-Control",
        "X-PetCare-Jetson-Boot-Id",
        "X-PetCare-Jetson-Sequence",
        "X-PetCare-Jetson-Observed-At",
        "X-PetCare-Jetson-Content-SHA256",
    ]
    assert headers["Content-Type"] == "image/jpeg"
    assert int(headers["Content-Length"]) == len(jpeg)
    assert headers["Cache-Control"] == "private, no-store, no-transform"
    assert headers["X-PetCare-Jetson-Boot-Id"] == body["boot_id"]
    assert headers["X-PetCare-Jetson-Sequence"] == "42"
    assert headers["X-PetCare-Jetson-Sequence"] == str(body["sequence"])
    assert headers["X-PetCare-Jetson-Observed-At"] == body["observed_at"]
    assert headers["X-PetCare-Jetson-Content-SHA256"] == hashlib.sha256(jpeg).hexdigest()
    assert preview["ignored_transport_headers"] == ["Date", "Connection"]
    assert preview["max_fps"] == 2


def test_command_admission_and_idempotency_contract() -> None:
    command = load_fixture()["command"]
    assert list(command) == ["request", "outbox_created_at", "allowed_event_types", "response", "first_status", "admission", "receipt_capture", "replay"]
    assert list(command["request"]) == ["committed_at", "event_id", "event_type", "occurred_at"]
    assert command["request"]["committed_at"] == command["outbox_created_at"]
    assert command["allowed_event_types"] == ["eating", "resting", "bed_sensor_mismatch"]
    assert "no_meal_12h" not in command["allowed_event_types"]
    assert list(command["response"]) == ["accepted_boot_id", "command_id", "state", "accepted_at"]
    assert command["first_status"] == 201

    admission = command["admission"]
    assert admission == {
        "first_put_requires_fresh_calibration": True,
        "calibration_max_age_seconds": 1.0,
        "midpoint_offset_ms": -100,
        "half_rtt_ms": 49,
        "drift_budget_ms": 50,
        "uncertainty_max_ms": 200,
        "guard_sample_ms": 100,
        "discontinuity_threshold_ms": 25,
        "discontinuity_disable_seconds": 60,
        "wall_age_min_seconds": -0.2,
        "wall_age_max_seconds": 2.8,
    }
    assert abs(admission["midpoint_offset_ms"]) + admission["half_rtt_ms"] + admission["drift_budget_ms"] <= admission["uncertainty_max_ms"]
    assert first_put_admitted(admission, 1.0, -100, 50, -0.2)
    assert first_put_admitted(admission, 1.0, 100, 50, 2.8)
    assert not first_put_admitted(admission, 1.000001, 0, 0, 0)
    assert not first_put_admitted(admission, 1.0, 100.001, 50, 0)
    assert not first_put_admitted(admission, 1.0, 0, 0, -0.200001)
    assert not first_put_admitted(admission, 1.0, 0, 0, 2.800001)
    assert not first_put_admitted(admission, 1.0, 0, 0, 0, discontinuity=True)

    capture = command["receipt_capture"]
    assert list(capture) == ["socket_received_at", "accepted_monotonic_ns", "sampler_period_ns", "trigger_bucket"]
    assert capture["socket_received_at"] == command["response"]["accepted_at"]
    period = capture["sampler_period_ns"]
    assert capture["trigger_bucket"] == (capture["accepted_monotonic_ns"] + period - 1) // period

    replay = command["replay"]
    assert replay == {
        "identical_active_states": ["recording", "finalizing", "ready"],
        "identical_status": 200,
        "reruns_age_test": False,
        "receipt_fields": ["accepted_boot_id", "command_id", "state", "accepted_at"],
        "gone_states": ["delivered", "expired", "restart_gone"],
        "gone_status": 410,
        "changed_digest_status": 409,
    }
    for state in replay["identical_active_states"]:
        assert replay_status(replay, state, True) == 200
    for state in replay["gone_states"]:
        assert replay_status(replay, state, True) == 410
    assert replay_status(replay, "recording", False) == 409


def test_clip_error_and_cloud_contract_separation() -> None:
    fixture = load_fixture()
    clip = fixture["clip"]
    assert list(clip) == ["body_base64", "body_sha256", "max_content_length", "headers", "ignored_transport_headers", "media", "wire_body_is_valid_mp4"]
    body = base64.b64decode(clip["body_base64"])
    assert body == b"mp4-bytes"
    assert hashlib.sha256(body).hexdigest() == clip["body_sha256"]
    headers = clip["headers"]
    assert list(headers) == [
        "Content-Type",
        "Content-Length",
        "X-PetCare-Jetson-Boot-Id",
        "X-PetCare-Jetson-Command-Id",
        "X-PetCare-Jetson-Content-SHA256",
        "X-PetCare-Jetson-Started-At",
        "X-PetCare-Jetson-Ended-At",
        "X-PetCare-Jetson-Events",
        "X-PetCare-Jetson-Frame-Count",
        "X-PetCare-Jetson-Video-Codec",
        "X-PetCare-Jetson-Pixel-Format",
    ]
    assert headers["Content-Type"] == "video/mp4"
    assert int(headers["Content-Length"]) == len(body)
    assert headers["X-PetCare-Jetson-Content-SHA256"] == clip["body_sha256"]
    assert headers["X-PetCare-Jetson-Boot-Id"] == fixture["command"]["response"]["accepted_boot_id"]
    assert headers["X-PetCare-Jetson-Command-Id"] == fixture["command"]["response"]["command_id"]
    assert clip["max_content_length"] == 268_435_456
    assert clip["ignored_transport_headers"] == ["Date", "Connection"]
    assert clip["media"] == {
        "width": 640,
        "height": 480,
        "frame_count": 300,
        "frame_rate": "10/1",
        "duration_seconds": 30.0,
        "duration_tolerance_ms": 100,
        "video_codec": "h264",
        "pixel_format": "yuv420p",
        "started_at": "2026-07-20T03:59:50.000000Z",
        "ended_at": "2026-07-20T04:00:20.000000Z",
        "events": "eating:41",
    }
    assert headers["X-PetCare-Jetson-Started-At"] == clip["media"]["started_at"]
    assert headers["X-PetCare-Jetson-Ended-At"] == clip["media"]["ended_at"]
    assert headers["X-PetCare-Jetson-Events"] == clip["media"]["events"]
    assert headers["X-PetCare-Jetson-Frame-Count"] == str(clip["media"]["frame_count"])
    assert headers["X-PetCare-Jetson-Video-Codec"] == clip["media"]["video_codec"]
    assert headers["X-PetCare-Jetson-Pixel-Format"] == clip["media"]["pixel_format"]
    assert clip["wire_body_is_valid_mp4"] is False

    errors = fixture["errors"]
    assert list(errors) == ["external_codes", "auth_internal_reasons", "external_by_internal_reason", "unauthorized", "status_by_code"]
    assert errors["external_codes"] == [
        "invalid_request",
        "unauthorized",
        "command_conflict",
        "command_expired",
        "camera_unavailable",
        "clip_busy",
        "clip_not_ready",
        "clip_gone",
        "internal_error",
    ]
    assert errors["auth_internal_reasons"] == ["stale_request", "replayed_request", "wrong_boot"]
    assert errors["unauthorized"] == {"status": 401, "body": {"code": "unauthorized", "message": "Unauthorized"}}
    assert list(errors["external_by_internal_reason"]) == errors["auth_internal_reasons"]
    assert all(value == errors["unauthorized"] for value in errors["external_by_internal_reason"].values())
    assert errors["status_by_code"] == {
        "invalid_request": 400,
        "unauthorized": 401,
        "command_conflict": 409,
        "command_expired": 409,
        "camera_unavailable": 503,
        "clip_busy": 503,
        "clip_not_ready": 425,
        "clip_gone": 410,
        "internal_error": 500,
    }

    assert hashlib.sha256(AGENT_FIXTURE.read_bytes()).hexdigest() == "d9849424f38a2f99b844c4705eb0652bf245b74ece6173c62e0271d1db7e2e4b"
    agent = json.loads(AGENT_FIXTURE.read_text(encoding="utf-8"))
    assert agent["clip"]["version"] == "PETCARE-CLIP-V1"
    assert "PETCARE-JETSON-V1" not in AGENT_FIXTURE.read_text(encoding="utf-8")
