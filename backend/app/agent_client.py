from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import secrets
import stat
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from typing import Any, BinaryIO

import httpx
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from app.agent_config import (
    LOCAL_CAMERA_ID,
    AgentRuntimeConfig,
    LocalSettings,
    require_https_origin,
    write_runtime_config,
)
from app.account_cleanup import ActivityCleanupCommand, ActivityCleanupError
from app.clip_contracts import ClipMetadata, UploadReceipt, utc_text


ENROLL_PATH = "/api/petcare/agent/enroll"
UPLOAD_PATH = "/api/petcare/agent/clips"
CLEANUP_PATH = "/api/petcare/agent/cleanup"
HTTP_TIMEOUT_SECONDS = 30.0
CLEANUP_HTTP_TIMEOUT_SECONDS = 5.0
_CLEANUP_COMMAND_ID = re.compile(rb"clc_[0-9a-f]{32}\Z")


class EnrollmentError(RuntimeError):
    pass


class UploadVerificationError(RuntimeError):
    pass


def b64url(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).decode("ascii").rstrip("=")


def validate_code(value: str) -> str:
    message = "value must be canonical 22-character base64url encoding of 16 bytes"
    if not isinstance(value, str) or len(value) != 22 or "=" in value:
        raise ValueError(message)
    try:
        decoded = base64.b64decode(value + "==", altchars=b"-_", validate=True)
    except (ValueError, base64.binascii.Error) as error:
        raise ValueError(message) from error
    if len(decoded) != 16 or b64url(decoded) != value:
        raise ValueError(message)
    return value


def content_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as source:
        while chunk := source.read(1024 * 1024):
            digest.update(chunk)
    return b64url(digest.digest())


def _open_regular_file(path: Path) -> BinaryIO:
    path = Path(path)
    flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0)
    reparse = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
    before = path.lstat()
    if not stat.S_ISREG(before.st_mode) or getattr(before, "st_file_attributes", 0) & reparse:
        raise ValueError("invalid upload source")
    descriptor = os.open(path, flags)
    try:
        opened = os.fstat(descriptor)
        after = path.lstat()
        if (
            not stat.S_ISREG(opened.st_mode)
            or getattr(opened, "st_file_attributes", 0) & reparse
            or not os.path.samestat(before, opened)
            or not os.path.samestat(opened, after)
        ):
            raise ValueError("invalid upload source")
        return os.fdopen(descriptor, "rb")
    except BaseException:
        os.close(descriptor)
        raise


def _validate_content_digest(value: str) -> str:
    message = "invalid content digest"
    if type(value) is not str or len(value) != 43 or "=" in value:
        raise ValueError(message)
    try:
        decoded = base64.b64decode(value + "=", altchars=b"-_", validate=True)
    except (ValueError, base64.binascii.Error) as error:
        raise ValueError(message) from error
    if len(decoded) != 32 or b64url(decoded) != value:
        raise ValueError(message)
    return value


def generate_private_key() -> Ed25519PrivateKey:
    return Ed25519PrivateKey.generate()


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON key")
        result[key] = value
    return result


def _reject_constant(value: str) -> None:
    raise ValueError("non-finite JSON value")


def _strict_json_object(content: bytes, *, error: type[RuntimeError]) -> dict[str, Any]:
    try:
        decoded = content.decode("utf-8", errors="strict")
        value = json.loads(decoded, object_pairs_hook=_unique_object, parse_constant=_reject_constant)
        if not isinstance(value, dict):
            raise ValueError("JSON object required")
        return value
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError, TypeError) as cause:
        raise error("invalid server response") from cause


def _required_nonempty_strings(
    value: dict[str, Any], expected: set[str], *, error: type[RuntimeError]
) -> dict[str, str]:
    if set(value) != expected:
        raise error("invalid server response")
    if any(type(value[key]) is not str or not value[key] or value[key] != value[key].strip() for key in expected):
        raise error("invalid server response")
    return value  # type: ignore[return-value]


def _parse_enrollment_response(content: bytes) -> dict[str, str]:
    return _required_nonempty_strings(
        _strict_json_object(content, error=EnrollmentError),
        {"agent_id", "camera_id", "connector_token"},
        error=EnrollmentError,
    )


def parse_upload_receipt(content: bytes) -> UploadReceipt:
    try:
        payload = _required_nonempty_strings(
            _strict_json_object(content, error=UploadVerificationError),
            {"id", "createdAt", "expiresAt"},
            error=UploadVerificationError,
        )
        return UploadReceipt(payload["id"], payload["createdAt"], payload["expiresAt"])
    except UploadVerificationError:
        raise
    except (TypeError, ValueError) as cause:
        raise UploadVerificationError("invalid upload receipt") from cause


def enroll(
    origin: str,
    code: str,
    local_settings: LocalSettings,
    path: Path,
    *,
    transport: httpx.BaseTransport | None = None,
    windows_identity_sid: str | None = None,
) -> AgentRuntimeConfig:
    require_https_origin(origin)
    validate_code(code)
    if not isinstance(local_settings, LocalSettings):
        raise TypeError("local_settings must be LocalSettings")
    private_key = generate_private_key()
    private_raw = private_key.private_bytes(
        serialization.Encoding.Raw,
        serialization.PrivateFormat.Raw,
        serialization.NoEncryption(),
    )
    public_raw = private_key.public_key().public_bytes(
        serialization.Encoding.Raw,
        serialization.PublicFormat.Raw,
    )
    request = {
        "enrollment_code": code,
        "algorithm": "Ed25519",
        "public_key": b64url(public_raw),
        "local_camera_id": LOCAL_CAMERA_ID,
    }
    with httpx.Client(
        base_url=origin,
        timeout=httpx.Timeout(HTTP_TIMEOUT_SECONDS),
        transport=transport,
    ) as client:
        response = client.post(ENROLL_PATH, json=request)
    if response.status_code != 201:
        raise EnrollmentError(f"unexpected enrollment status: {response.status_code}")
    server = _parse_enrollment_response(response.content)
    config = AgentRuntimeConfig(
        origin=origin,
        agent_id=server["agent_id"],
        camera_id=server["camera_id"],
        connector_token=server["connector_token"],
        private_key=b64url(private_raw),
        public_key=b64url(public_raw),
        local_camera_id=LOCAL_CAMERA_ID,
        local_settings=local_settings,
    )
    write_runtime_config(path, config, windows_identity_sid=windows_identity_sid)
    return config


class SignedActivityCleanupClient:
    def __init__(
        self,
        *,
        origin: str,
        agent_id: str,
        private_key: Ed25519PrivateKey,
        transport: httpx.BaseTransport | None = None,
        now: Callable[[], datetime] | None = None,
        nonce: Callable[[], str] | None = None,
    ) -> None:
        require_https_origin(origin)
        if (
            type(agent_id) is not str
            or not agent_id
            or agent_id != agent_id.strip()
            or len(agent_id) > 128
        ):
            raise ValueError("agent_id is required")
        if not isinstance(private_key, Ed25519PrivateKey):
            raise TypeError("private_key must be Ed25519PrivateKey")
        self.agent_id = agent_id
        self.private_key = private_key
        self._client = httpx.Client(
            base_url=origin,
            timeout=httpx.Timeout(CLEANUP_HTTP_TIMEOUT_SECONDS),
            transport=transport,
        )
        self._now = now or (lambda: datetime.now().astimezone())
        self._nonce = nonce or (lambda: b64url(secrets.token_bytes(16)))

    def close(self) -> None:
        self._client.close()

    @staticmethod
    def _private_no_store(response: httpx.Response) -> bool:
        directives = {
            directive.strip().lower()
            for directive in response.headers.get("Cache-Control", "").split(",")
        }
        return "private" in directives and "no-store" in directives and "public" not in directives

    def _post(self, body: bytes) -> httpx.Response:
        now = self._now()
        if now.tzinfo is None or now.utcoffset() is None:
            raise ActivityCleanupError("cleanup clock must be timezone-aware")
        try:
            nonce = validate_code(self._nonce())
        except ValueError as cause:
            raise ActivityCleanupError("invalid cleanup nonce") from cause
        timestamp = str(int(now.timestamp()))
        digest = b64url(hashlib.sha256(body).digest())
        canonical = "\n".join(
            (
                "PETCARE-CLEANUP-V1",
                "POST",
                CLEANUP_PATH,
                self.agent_id,
                timestamp,
                nonce,
                digest,
                "",
            )
        ).encode("utf-8")
        headers = {
            "Content-Type": "application/json",
            "X-PetCare-Agent-Id": self.agent_id,
            "X-PetCare-Timestamp": timestamp,
            "X-PetCare-Nonce": nonce,
            "X-PetCare-Content-SHA256": digest,
            "X-PetCare-Signature": b64url(self.private_key.sign(canonical)),
        }
        try:
            return self._client.post(CLEANUP_PATH, headers=headers, content=body)
        except httpx.HTTPError as cause:
            raise ActivityCleanupError("activity cleanup request failed") from cause

    def poll(self) -> ActivityCleanupCommand | None:
        response = self._post(b'{"action":"poll"}')
        if (
            response.status_code == 204
            and not response.content
            and self._private_no_store(response)
        ):
            return None
        if (
            response.status_code != 200
            or response.headers.get("Content-Type", "").split(";", 1)[0].strip().lower()
            != "application/json"
            or not self._private_no_store(response)
        ):
            raise ActivityCleanupError("invalid cleanup response")
        prefix = b'{"commandId":"'
        suffix = b'","type":"delete_activity_observations"}'
        if not response.content.startswith(prefix) or not response.content.endswith(suffix):
            raise ActivityCleanupError("invalid cleanup response")
        command_id = response.content[len(prefix) : -len(suffix)]
        if _CLEANUP_COMMAND_ID.fullmatch(command_id) is None:
            raise ActivityCleanupError("invalid cleanup response")
        expected = prefix + command_id + suffix
        if response.content != expected:
            raise ActivityCleanupError("invalid cleanup response")
        return ActivityCleanupCommand(command_id.decode("ascii"), "delete_activity_observations")

    def ack(self, command_id: str) -> None:
        if (
            type(command_id) is not str
            or not command_id.isascii()
            or _CLEANUP_COMMAND_ID.fullmatch(command_id.encode("ascii")) is None
        ):
            raise ActivityCleanupError("invalid cleanup command")
        body = b'{"action":"ack","commandId":"' + command_id.encode("ascii") + b'"}'
        response = self._post(body)
        if (
            response.status_code != 204
            or response.content
            or not self._private_no_store(response)
        ):
            raise ActivityCleanupError("invalid cleanup response")


class SignedClipUploadClient:
    def __init__(
        self,
        *,
        origin: str,
        agent_id: str,
        camera_id: str,
        private_key: Ed25519PrivateKey,
        transport: httpx.BaseTransport | None = None,
        now: Callable[[], datetime] | None = None,
        nonce: Callable[[], str] | None = None,
    ) -> None:
        require_https_origin(origin)
        if (
            not isinstance(agent_id, str)
            or not isinstance(camera_id, str)
            or not agent_id.strip()
            or not camera_id.strip()
            or agent_id != agent_id.strip()
            or camera_id != camera_id.strip()
        ):
            raise ValueError("agent_id and camera_id are required")
        if not isinstance(private_key, Ed25519PrivateKey):
            raise TypeError("private_key must be Ed25519PrivateKey")
        self.agent_id = agent_id
        self.camera_id = camera_id
        self.private_key = private_key
        self._client = httpx.Client(
            base_url=origin,
            timeout=httpx.Timeout(HTTP_TIMEOUT_SECONDS),
            transport=transport,
        )
        self._now = now or (lambda: datetime.now().astimezone())
        self._nonce = nonce or (lambda: b64url(secrets.token_bytes(16)))

    def close(self) -> None:
        self._client.close()

    def upload(self, path: Path, metadata: ClipMetadata) -> UploadReceipt:
        source: BinaryIO | None
        try:
            source = _open_regular_file(Path(path))
        except (OSError, ValueError):
            source = None
        if source is None:
            raise UploadVerificationError("invalid upload source")
        with source:
            digest = hashlib.sha256()
            size = 0
            while chunk := source.read(1024 * 1024):
                digest.update(chunk)
                size += len(chunk)
            source.seek(0)
            return self.upload_open_file(
                source,
                size=size,
                content_digest=b64url(digest.digest()),
                metadata=metadata,
            )

    def upload_open_file(
        self,
        source: BinaryIO,
        *,
        size: int,
        content_digest: str,
        metadata: ClipMetadata,
    ) -> UploadReceipt:
        if type(size) is not int or size < 0:
            raise ValueError("invalid upload size")
        content_digest = _validate_content_digest(content_digest)
        try:
            status = os.fstat(source.fileno())
            if (
                not source.readable()
                or not source.seekable()
                or type(source.read(0)) is not bytes
                or not stat.S_ISREG(status.st_mode)
            ):
                raise ValueError("invalid upload source")
        except (AttributeError, OSError, TypeError, ValueError) as cause:
            raise ValueError("invalid upload source") from cause
        if status.st_size != size:
            raise ValueError("invalid upload size")
        if source.tell() != 0:
            raise ValueError("invalid upload position")
        now = self._now()
        if now.tzinfo is None or now.utcoffset() is None:
            raise ValueError("upload clock must be timezone-aware")
        timestamp = str(int(now.timestamp()))
        nonce = validate_code(self._nonce())
        started_at = utc_text(metadata.started_at)
        ended_at = utc_text(metadata.ended_at)
        identities = sorted(
            ((event.event_type, event.event_id) for event in metadata.events),
            key=lambda identity: (identity[0], identity[1]),
        )
        if len(identities) != len(set(identities)):
            raise ValueError("clip events must be unique")
        events = ",".join(f"{event_type}:{event_id}" for event_type, event_id in identities)
        canonical = "\n".join((
            "PETCARE-CLIP-V1",
            "POST",
            UPLOAD_PATH,
            self.agent_id,
            self.camera_id,
            timestamp,
            nonce,
            content_digest,
            started_at,
            ended_at,
            events,
            "",
        )).encode("utf-8")
        signature = b64url(self.private_key.sign(canonical))
        headers = {
            "Content-Type": "video/mp4",
            "Content-Length": str(size),
            "X-PetCare-Agent-Id": self.agent_id,
            "X-PetCare-Camera-Id": self.camera_id,
            "X-PetCare-Timestamp": timestamp,
            "X-PetCare-Nonce": nonce,
            "X-PetCare-Content-SHA256": content_digest,
            "X-PetCare-Started-At": started_at,
            "X-PetCare-Ended-At": ended_at,
            "X-PetCare-Events": events,
            "X-PetCare-Signature": signature,
        }
        response = self._client.post(UPLOAD_PATH, headers=headers, content=source)
        if response.status_code != 201:
            raise UploadVerificationError(f"unexpected upload status: {response.status_code}")
        return parse_upload_receipt(response.content)
