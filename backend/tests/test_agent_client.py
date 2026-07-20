from __future__ import annotations

import base64
import hashlib
import json
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from app.agent_client import (
    EnrollmentError,
    SignedClipUploadClient,
    UploadVerificationError,
    b64url,
    enroll,
    parse_upload_receipt,
    validate_code,
)
from app.agent_config import LocalSettings
from app.clip_contracts import ClipEventMetadata, ClipMetadata, ClipTrigger


NOW = datetime(2026, 7, 20, 4, 0, tzinfo=UTC)
ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture(autouse=True)
def avoid_real_windows_acl(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("app.agent_config.protect_runtime_file", lambda *args: None)


def local_settings() -> LocalSettings:
    return LocalSettings(
        database_url="postgresql+psycopg://petcare:db-secret@127.0.0.1:55432/petcare",
        mqtt_profile="local_live",
        mqtt_username="petcare",
        mqtt_password="mqtt-secret",
    )


def metadata(*events: ClipEventMetadata) -> ClipMetadata:
    return ClipMetadata(
        "pc-webcam-01",
        NOW - timedelta(seconds=10),
        NOW + timedelta(seconds=20),
        events or (ClipEventMetadata.from_trigger(ClipTrigger("eating", 41, NOW)),),
    )


def upload_client(
    private_key: Ed25519PrivateKey,
    handler: object,
    *,
    nonce: str = "AAAAAAAAAAAAAAAAAAAAAA",
) -> SignedClipUploadClient:
    return SignedClipUploadClient(
        origin="https://petcare.example",
        agent_id="agent_01",
        camera_id="camera_01",
        private_key=private_key,
        transport=httpx.MockTransport(handler),  # type: ignore[arg-type]
        now=lambda: NOW,
        nonce=lambda: nonce,
    )


def test_enrollment_generates_identity_and_never_sends_private_or_local_secrets(tmp_path: Path) -> None:
    captured: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["path"] = request.url.path
        captured["json"] = json.loads(request.content)
        return httpx.Response(201, json={
            "agent_id": "agent_01",
            "camera_id": "camera_01",
            "connector_token": "connector-secret",
        })

    original_argv = tuple(sys.argv)
    output = tmp_path / "agent.json"
    config = enroll(
        origin="https://petcare.example",
        code="AQEBAQEBAQEBAQEBAQEBAQ",
        local_settings=local_settings(),
        path=output,
        transport=httpx.MockTransport(handler),
        windows_identity_sid="S-1-5-21-1000",
    )

    assert captured["path"] == "/api/petcare/agent/enroll"
    request_json = captured["json"]
    assert isinstance(request_json, dict)
    assert set(request_json) == {"enrollment_code", "algorithm", "public_key", "local_camera_id"}
    assert request_json["algorithm"] == "Ed25519"
    assert request_json["enrollment_code"] == "AQEBAQEBAQEBAQEBAQEBAQ"
    assert request_json["local_camera_id"] == "pc-webcam-01"
    assert "private_key" not in request_json
    assert config.camera_id == "camera_01"
    assert config.connector_token.get_secret_value() == "connector-secret"
    assert "connector-secret" not in repr(config)
    assert "mqtt-secret" not in repr(config)
    assert tuple(sys.argv) == original_argv


def test_enrollment_matches_shared_golden_request(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fixture = json.loads((ROOT / "contracts" / "petcare-agent-wire-v1.json").read_text(encoding="utf-8"))
    enrollment = fixture["enrollment"]
    private_key = Ed25519PrivateKey.from_private_bytes(
        base64.urlsafe_b64decode(fixture["clip"]["seed"] + "=")
    )
    captured: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["request"] = json.loads(request.content)
        assert request.extensions["timeout"] == {
            "connect": 30.0,
            "read": 30.0,
            "write": 30.0,
            "pool": 30.0,
        }
        return httpx.Response(
            enrollment["response"]["status"],
            json=enrollment["response"]["body"],
        )

    monkeypatch.setattr("app.agent_client.generate_private_key", lambda: private_key)
    config = enroll(
        origin="https://petcare.example",
        code=enrollment["request"]["enrollment_code"],
        local_settings=local_settings(),
        path=tmp_path / "agent.json",
        transport=httpx.MockTransport(handler),
        windows_identity_sid="S-1-5-21-1000",
    )

    assert captured["request"] == enrollment["request"]
    assert config.public_key == enrollment["request"]["public_key"]


def test_enrollment_and_upload_reject_non_https_before_network(tmp_path: Path) -> None:
    called = False

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal called
        called = True
        return httpx.Response(500)

    with pytest.raises(ValueError, match="HTTPS origin"):
        enroll(
            origin="http://petcare.example",
            code="AQEBAQEBAQEBAQEBAQEBAQ",
            local_settings=local_settings(),
            path=tmp_path / "agent.json",
            transport=httpx.MockTransport(handler),
        )
    with pytest.raises(ValueError, match="HTTPS origin"):
        SignedClipUploadClient(
            origin="http://petcare.example",
            agent_id="agent_01",
            camera_id="camera_01",
            private_key=Ed25519PrivateKey.generate(),
            transport=httpx.MockTransport(handler),
        )
    assert called is False


def test_enrollment_rejects_invalid_local_settings_before_consuming_code(tmp_path: Path) -> None:
    called = False

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal called
        called = True
        return httpx.Response(500)

    with pytest.raises(TypeError, match="LocalSettings"):
        enroll(
            origin="https://petcare.example",
            code="AQEBAQEBAQEBAQEBAQEBAQ",
            local_settings={},  # type: ignore[arg-type]
            path=tmp_path / "agent.json",
            transport=httpx.MockTransport(handler),
        )
    assert called is False


@pytest.mark.parametrize(("agent_id", "camera_id"), [(" ", "camera_01"), ("agent_01", " ")])
def test_upload_client_rejects_blank_server_ids(agent_id: str, camera_id: str) -> None:
    with pytest.raises(ValueError, match="agent_id and camera_id"):
        SignedClipUploadClient(
            origin="https://petcare.example",
            agent_id=agent_id,
            camera_id=camera_id,
            private_key=Ed25519PrivateKey.generate(),
            transport=httpx.MockTransport(lambda request: httpx.Response(500)),
        )


@pytest.mark.parametrize(
    "code",
    [
        "",
        "AQEBAQEBAQEBAQEBAQEBAQ==",
        "AQEBAQEBAQEBAQEBAQEBAB",
        "AQEBAQEBAQEBAQEBAQEBA!",
        "AQEBAQEBAQEBAQEBAQEBA",
    ],
)
def test_enrollment_code_must_be_canonical_unpadded_16_byte_base64url(code: str) -> None:
    with pytest.raises(ValueError, match="canonical 22-character base64url"):
        validate_code(code)


@pytest.mark.parametrize(
    ("status", "content"),
    [
        (200, b'{"agent_id":"a","camera_id":"c","connector_token":"t"}'),
        (201, b"not-json"),
        (201, b'{"agent_id":"a","agent_id":"b","camera_id":"c","connector_token":"t"}'),
        (201, b'{"agent_id":"a","camera_id":"c"}'),
        (201, b'{"agent_id":"a","camera_id":"c","connector_token":"t","extra":1}'),
        (201, b'{"agent_id":1,"camera_id":"c","connector_token":"t"}'),
        (201, b'{"agent_id":"","camera_id":"c","connector_token":"t"}'),
    ],
)
def test_enrollment_requires_strict_201_response_without_echoing_body(
    tmp_path: Path, status: int, content: bytes
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(status, content=content)

    with pytest.raises(EnrollmentError) as error:
        enroll(
            origin="https://petcare.example",
            code="AQEBAQEBAQEBAQEBAQEBAQ",
            local_settings=local_settings(),
            path=tmp_path / "agent.json",
            transport=httpx.MockTransport(handler),
            windows_identity_sid="S-1-5-21-1000",
        )
    assert content.decode("utf-8", errors="ignore") not in str(error.value)


def test_signed_upload_matches_petcare_clip_v1_golden_vector(tmp_path: Path) -> None:
    fixture = json.loads((ROOT / "contracts" / "petcare-agent-wire-v1.json").read_text(encoding="utf-8"))
    clip = fixture["clip"]
    enrollment = fixture["enrollment"]
    private_key = Ed25519PrivateKey.from_private_bytes(base64.urlsafe_b64decode(clip["seed"] + "="))
    video = tmp_path / "clip.mp4"
    video.write_bytes(base64.urlsafe_b64decode(clip["body_base64"] + "=="))
    events = tuple(
        ClipEventMetadata.from_trigger(ClipTrigger(event_type, event_id, NOW))
        for event_type, event_id in (("bed_sensor_mismatch", 7), ("eating", 41), ("resting", 105))
    )
    captured: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["body"] = request.read()
        captured["headers"] = dict(request.headers)
        return httpx.Response(clip["receipt"]["status"], json=clip["receipt"]["body"])

    client = SignedClipUploadClient(
        origin="https://petcare.example",
        agent_id=enrollment["response"]["body"]["agent_id"],
        camera_id=enrollment["response"]["body"]["camera_id"],
        private_key=private_key,
        transport=httpx.MockTransport(handler),
        now=lambda: NOW,
        nonce=lambda: clip["nonce"],
    )
    receipt = client.upload(video, metadata(*events))

    assert captured["body"] == b"mp4-bytes"
    actual_headers = captured["headers"]
    assert isinstance(actual_headers, dict)
    for name, value in clip["headers"].items():
        assert actual_headers[name.lower()] == value
    assert actual_headers["x-petcare-content-sha256"] == clip["content_sha256"]
    assert actual_headers["x-petcare-signature"] == clip["signature"]
    assert receipt.id == clip["receipt"]["body"]["id"]

    canonical_headers = clip["headers"]
    canonical = "\n".join((
        clip["version"],
        "POST",
        "/api/petcare/agent/clips",
        canonical_headers["X-PetCare-Agent-Id"],
        canonical_headers["X-PetCare-Camera-Id"],
        canonical_headers["X-PetCare-Timestamp"],
        canonical_headers["X-PetCare-Nonce"],
        canonical_headers["X-PetCare-Content-SHA256"],
        canonical_headers["X-PetCare-Started-At"],
        canonical_headers["X-PetCare-Ended-At"],
        canonical_headers["X-PetCare-Events"],
        "",
    ))
    assert canonical == clip["canonical"]


def test_signed_upload_streams_body_with_canonical_events_and_exact_headers(tmp_path: Path) -> None:
    private_key = Ed25519PrivateKey.generate()
    public_key = private_key.public_key()
    video = tmp_path / "clip.mp4"
    video.write_bytes(b"mp4-bytes")
    events = tuple(
        ClipEventMetadata.from_trigger(ClipTrigger(event_type, event_id, NOW))
        for event_type, event_id in (("bed_sensor_mismatch", 7), ("eating", 41), ("resting", 105))
    )

    def handler(request: httpx.Request) -> httpx.Response:
        body = request.read()
        assert request.url.path == "/api/petcare/agent/clips"
        assert body == b"mp4-bytes"
        assert request.headers["X-PetCare-Content-SHA256"] == b64url(hashlib.sha256(body).digest())
        assert request.headers["X-PetCare-Events"] == "bed_sensor_mismatch:7,eating:41,resting:105"
        assert request.headers["X-PetCare-Started-At"] == "2026-07-20T03:59:50.000000Z"
        assert request.headers["X-PetCare-Ended-At"] == "2026-07-20T04:00:20.000000Z"
        assert "X-PetCare-Body-SHA256" not in request.headers
        assert "X-PetCare-Metadata-SHA256" not in request.headers
        assert "X-PetCare-Clip-Metadata" not in request.headers
        canonical = "\n".join((
            "PETCARE-CLIP-V1", "POST", request.url.path,
            request.headers["X-PetCare-Agent-Id"],
            request.headers["X-PetCare-Camera-Id"],
            request.headers["X-PetCare-Timestamp"],
            request.headers["X-PetCare-Nonce"],
            request.headers["X-PetCare-Content-SHA256"],
            request.headers["X-PetCare-Started-At"],
            request.headers["X-PetCare-Ended-At"],
            request.headers["X-PetCare-Events"], "",
        )).encode("utf-8")
        public_key.verify(
            base64.urlsafe_b64decode(request.headers["X-PetCare-Signature"] + "=="),
            canonical,
        )
        return httpx.Response(201, json={
            "id": "clip_01",
            "createdAt": "2026-07-20T04:00:00.000Z",
            "expiresAt": "2026-07-20T05:00:00.000Z",
        })

    receipt = upload_client(private_key, handler).upload(video, metadata(*events))
    assert receipt.id == "clip_01"


@pytest.mark.parametrize(
    "nonce",
    ["", "AAAAAAAAAAAAAAAAAAAAAA==", "AAAAAAAAAAAAAAAAAAAAA!", "AAAAAAAAAAAAAAAAAAAAA"],
)
def test_upload_rejects_malformed_nonce_before_network(tmp_path: Path, nonce: str) -> None:
    called = False

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal called
        called = True
        return httpx.Response(500)

    video = tmp_path / "clip.mp4"
    video.write_bytes(b"mp4-bytes")
    with pytest.raises(ValueError, match="canonical 22-character base64url"):
        upload_client(Ed25519PrivateKey.generate(), handler, nonce=nonce).upload(video, metadata())
    assert called is False


@pytest.mark.parametrize(
    ("status", "content"),
    [
        (200, b'{"id":"clip_01","createdAt":"2026-07-20T04:00:00.000Z","expiresAt":"2026-07-20T05:00:00.000Z"}'),
        (201, b"not-json"),
        (201, b"\xff"),
        (201, b'{"id":"a","id":"b","createdAt":"2026-07-20T04:00:00.000Z","expiresAt":"2026-07-20T05:00:00.000Z"}'),
        (201, b'{"id":"clip_01","createdAt":"2026-07-20T04:00:00.000Z"}'),
        (201, b'{"id":"clip_01","createdAt":"2026-07-20T04:00:00.000Z","expiresAt":"2026-07-20T05:00:00.000Z","extra":1}'),
        (201, b'{"id":1,"createdAt":"2026-07-20T04:00:00.000Z","expiresAt":"2026-07-20T05:00:00.000Z"}'),
        (201, b'{"id":"clip_01","createdAt":"2026-07-20T05:00:00.000Z","expiresAt":"2026-07-20T04:00:00.000Z"}'),
    ],
)
def test_upload_accepts_only_strict_201_receipt_without_echoing_body(
    tmp_path: Path, status: int, content: bytes
) -> None:
    video = tmp_path / "clip.mp4"
    video.write_bytes(b"mp4-bytes")

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(status, content=content)

    with pytest.raises(UploadVerificationError) as error:
        upload_client(Ed25519PrivateKey.generate(), handler).upload(video, metadata())
    decoded = content.decode("utf-8", errors="ignore")
    if decoded:
        assert decoded not in str(error.value)


def test_receipt_duplicate_keys_are_rejected() -> None:
    with pytest.raises(UploadVerificationError):
        parse_upload_receipt(
            b'{"id":"a","id":"b","createdAt":"2026-07-20T04:00:00.000Z",'
            b'"expiresAt":"2026-07-20T05:00:00.000Z"}'
        )


def test_http_clients_use_exact_30_second_timeout(tmp_path: Path) -> None:
    client = upload_client(Ed25519PrivateKey.generate(), lambda request: httpx.Response(500))
    assert client._client.timeout.connect == 30.0
    assert client._client.timeout.read == 30.0
    assert client._client.timeout.write == 30.0
    assert client._client.timeout.pool == 30.0
