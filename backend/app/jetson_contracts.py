from __future__ import annotations

import base64
import hashlib
import hmac
import json
import math
import re
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Annotated, Any, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictFloat, StrictInt, field_validator, model_validator


VERSION = "PETCARE-JETSON-V1"
EMPTY_SHA256 = hashlib.sha256(b"").hexdigest()
BOOT_ID = re.compile(r"[0-9a-f]{32}\Z")
COMMAND_ID = BOOT_ID
SHA256 = re.compile(r"[0-9a-f]{64}\Z")
CANONICAL_UTC = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{6}Z\Z")
UNRESERVED = frozenset(b"ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-._~")
MAX_JSON_BYTES = 65_536
MAX_PREVIEW_BYTES = 1_048_576
MAX_CLIP_BYTES = 268_435_456
STATUS_BY_CODE = {
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


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True, hide_input_in_errors=True)


def canonical_utc(value: str) -> datetime:
    if type(value) is not str or CANONICAL_UTC.fullmatch(value) is None:
        raise ValueError("canonical UTC timestamp required")
    try:
        parsed = datetime.fromisoformat(value[:-1] + "+00:00")
    except ValueError as error:
        raise ValueError("canonical UTC timestamp required") from error
    if parsed.isoformat(timespec="microseconds").replace("+00:00", "Z") != value:
        raise ValueError("canonical UTC timestamp required")
    return parsed


CanonicalDatetime = Annotated[datetime, Field(strict=True)]
FiniteFloat = Annotated[StrictFloat, Field(allow_inf_nan=False)]


class JetsonStatus(StrictModel):
    boot_id: str
    server_time: CanonicalDatetime
    camera_state: Literal["online", "offline"]
    clip_state: Literal["idle", "recording", "finalizing", "ready"]
    jetpack: Literal["4.6.6"]
    l4t: Literal["32.7.6"]
    tensorrt: Literal["8.2.1"]
    temperature_c: FiniteFloat
    throttled: StrictBool

    @field_validator("boot_id")
    @classmethod
    def valid_boot_id(cls, value: str) -> str:
        if type(value) is not str or BOOT_ID.fullmatch(value) is None:
            raise ValueError("invalid boot id")
        return value

    @field_validator("server_time", mode="before")
    @classmethod
    def valid_server_time(cls, value: object) -> datetime:
        return canonical_utc(value)  # type: ignore[arg-type]


class JetsonDetection(StrictModel):
    detected_type: Literal["person", "dog", "cat"]
    confidence: Annotated[StrictFloat, Field(ge=0, le=1, allow_inf_nan=False)]
    bbox_x: Annotated[StrictInt, Field(ge=0, lt=640)]
    bbox_y: Annotated[StrictInt, Field(ge=0, lt=480)]
    bbox_width: Annotated[StrictInt, Field(gt=0, le=640)]
    bbox_height: Annotated[StrictInt, Field(gt=0, le=480)]

    @model_validator(mode="after")
    def valid_geometry(self) -> Self:
        if self.bbox_x + self.bbox_width > 640 or self.bbox_y + self.bbox_height > 480:
            raise ValueError("invalid half-open detection geometry")
        return self


class JetsonObservation(StrictModel):
    boot_id: str
    sequence: Annotated[StrictInt, Field(ge=0)]
    observed_at: CanonicalDatetime
    width: Annotated[StrictInt, Field(ge=640, le=640)]
    height: Annotated[StrictInt, Field(ge=480, le=480)]
    fps: Annotated[StrictFloat, Field(ge=0, allow_inf_nan=False)]
    inference_ms: Annotated[StrictFloat, Field(ge=0, allow_inf_nan=False)]
    detections: tuple[JetsonDetection, ...]

    @field_validator("detections", mode="before")
    @classmethod
    def tuple_detections(cls, value: object) -> object:
        return tuple(value) if type(value) is list else value  # type: ignore[arg-type]

    @field_validator("boot_id")
    @classmethod
    def valid_boot_id(cls, value: str) -> str:
        if type(value) is not str or BOOT_ID.fullmatch(value) is None:
            raise ValueError("invalid boot id")
        return value

    @field_validator("observed_at", mode="before")
    @classmethod
    def valid_observed_at(cls, value: object) -> datetime:
        return canonical_utc(value)  # type: ignore[arg-type]

    @model_validator(mode="after")
    def unique_classes(self) -> Self:
        classes = [item.detected_type for item in self.detections]
        if len(classes) != len(set(classes)):
            raise ValueError("duplicate detection class")
        return self


class JetsonClipCommand(StrictModel):
    committed_at: CanonicalDatetime
    event_id: Annotated[StrictInt, Field(gt=0)]
    event_type: Literal["eating", "resting", "bed_sensor_mismatch"]
    occurred_at: CanonicalDatetime

    @field_validator("committed_at", "occurred_at", mode="before")
    @classmethod
    def valid_time(cls, value: object) -> datetime:
        return canonical_utc(value)  # type: ignore[arg-type]


class JetsonClipReceipt(StrictModel):
    accepted_boot_id: str
    command_id: str
    state: Literal["recording"]
    accepted_at: CanonicalDatetime

    @field_validator("accepted_boot_id", "command_id")
    @classmethod
    def valid_id(cls, value: str) -> str:
        if type(value) is not str or BOOT_ID.fullmatch(value) is None:
            raise ValueError("invalid identifier")
        return value

    @field_validator("accepted_at", mode="before")
    @classmethod
    def valid_time(cls, value: object) -> datetime:
        return canonical_utc(value)  # type: ignore[arg-type]


@dataclass(frozen=True, slots=True)
class JetsonPutResult:
    status_code: Literal[200, 201]
    receipt: JetsonClipReceipt


class JetsonError(StrictModel):
    code: Literal[
        "invalid_request", "unauthorized", "command_conflict", "command_expired",
        "camera_unavailable", "clip_busy", "clip_not_ready", "clip_gone", "internal_error",
    ]
    message: str


class ClockCalibration(StrictModel):
    measured_monotonic: FiniteFloat
    offset_ms: FiniteFloat
    half_rtt_ms: Annotated[StrictFloat, Field(ge=0, allow_inf_nan=False)]

    def valid_at(self, monotonic_now: float) -> bool:
        return (
            math.isfinite(monotonic_now)
            and 0 <= monotonic_now - self.measured_monotonic <= 1.0
            and abs(self.offset_ms) + self.half_rtt_ms + 50 <= 200
        )


class JetsonClipHeaders(StrictModel):
    boot_id: str
    command_id: str
    content_sha256: str
    started_at: CanonicalDatetime
    ended_at: CanonicalDatetime
    events: str
    frame_count: Annotated[StrictInt, Field(ge=1, le=1200)]
    video_codec: Literal["h264"]
    pixel_format: Literal["yuv420p"]

    @field_validator("boot_id", "command_id")
    @classmethod
    def valid_id(cls, value: str) -> str:
        if type(value) is not str or BOOT_ID.fullmatch(value) is None:
            raise ValueError("invalid identifier")
        return value

    @field_validator("content_sha256")
    @classmethod
    def valid_digest(cls, value: str) -> str:
        if type(value) is not str or SHA256.fullmatch(value) is None:
            raise ValueError("invalid digest")
        return value

    @field_validator("started_at", "ended_at", mode="before")
    @classmethod
    def valid_time(cls, value: object) -> datetime:
        return canonical_utc(value)  # type: ignore[arg-type]

    @field_validator("events")
    @classmethod
    def canonical_events(cls, value: str) -> str:
        if type(value) is not str or not value:
            raise ValueError("invalid events")
        pairs: list[tuple[str, int]] = []
        for item in value.split(","):
            match = re.fullmatch(r"(eating|resting|bed_sensor_mismatch):([1-9][0-9]*)", item)
            if match is None:
                raise ValueError("invalid events")
            pairs.append((match.group(1), int(match.group(2))))
        if pairs != sorted(set(pairs)):
            raise ValueError("noncanonical events")
        return value

    @model_validator(mode="after")
    def valid_duration(self) -> Self:
        actual = (self.ended_at - self.started_at).total_seconds()
        if self.ended_at <= self.started_at or abs(actual - self.frame_count / 10) > 0.1:
            raise ValueError("invalid clip duration")
        return self


def canonical_json(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False).encode("utf-8")


def canonical_query(pairs: list[list[str]] | list[tuple[str, str]]) -> str:
    def quote(value: str) -> str:
        if type(value) is not str:
            raise TypeError("query values must be strings")
        return "".join(chr(byte) if byte in UNRESERVED else f"%{byte:02X}" for byte in value.encode("utf-8"))

    encoded = sorted((quote(key), quote(value)) for key, value in pairs)
    return "&".join(f"{key}={value}" for key, value in encoded)


def b64url(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def sign_request(
    *, method: str, target: str, boot_id: str, timestamp: str, nonce: str, body: bytes, secret: bytes
) -> dict[str, str]:
    if type(secret) is not bytes or len(secret) != 32:
        raise ValueError("invalid Jetson secret")
    digest = hashlib.sha256(body).hexdigest()
    canonical = "\n".join((VERSION, method, target, boot_id, timestamp, nonce, digest, "")).encode("utf-8")
    return {
        "X-PetCare-Jetson-Version": VERSION,
        "X-PetCare-Jetson-Boot-Id": boot_id,
        "X-PetCare-Jetson-Timestamp": timestamp,
        "X-PetCare-Jetson-Nonce": nonce,
        "X-PetCare-Jetson-Content-SHA256": digest,
        "X-PetCare-Jetson-Signature": b64url(hmac.new(secret, canonical, hashlib.sha256).digest()),
    }


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON key")
        result[key] = value
    return result


def strict_json(content: bytes, *, maximum: int = MAX_JSON_BYTES) -> dict[str, Any]:
    if type(content) is not bytes or len(content) > maximum:
        raise ValueError("invalid JSON response")
    nesting = 0
    quoted = False
    escaped = False
    for byte in content:
        if quoted:
            if escaped:
                escaped = False
            elif byte == ord("\\"):
                escaped = True
            elif byte == ord('"'):
                quoted = False
            continue
        if byte == ord('"'):
            quoted = True
        elif byte in (ord("["), ord("{")):
            nesting += 1
            if nesting > 64:
                raise ValueError("invalid JSON response")
        elif byte in (ord("]"), ord("}")):
            nesting -= 1
    try:
        value = json.loads(
            content.decode("utf-8", errors="strict"),
            object_pairs_hook=_unique_object,
            parse_constant=lambda _value: (_ for _ in ()).throw(ValueError("nonfinite JSON")),
        )
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError, TypeError, RecursionError) as error:
        raise ValueError("invalid JSON response") from error
    if type(value) is not dict:
        raise ValueError("JSON object required")
    return value


def parse_observation(content: bytes, boot_id: str, after: int, now: datetime) -> JetsonObservation:
    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("aware receipt time required")
    observation = JetsonObservation.model_validate(strict_json(content))
    age = now.astimezone(UTC) - observation.observed_at
    if observation.boot_id != boot_id or observation.sequence <= after or not timedelta(0) <= age <= timedelta(seconds=3):
        raise ValueError("invalid observation freshness")
    return observation
