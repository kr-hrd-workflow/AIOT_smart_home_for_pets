from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Literal


EligibleEventType = Literal["eating", "resting", "bed_sensor_mismatch"]
ELIGIBLE_EVENT_TYPES = frozenset(("eating", "resting", "bed_sensor_mismatch"))
REMOTE_ID = re.compile(r"[0-9a-f]{32}\Z")


def utc_text(value: datetime) -> str:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("timestamp must be timezone-aware")
    return value.astimezone(UTC).isoformat(timespec="microseconds").replace("+00:00", "Z")


def canonical_utc_text(value: str) -> str:
    if not isinstance(value, str) or not value.endswith("Z"):
        raise ValueError("canonical UTC timestamp required")
    try:
        parsed = datetime.fromisoformat(value[:-1] + "+00:00")
    except ValueError as error:
        raise ValueError("canonical UTC timestamp required") from error
    if utc_text(parsed) != value:
        raise ValueError("canonical UTC timestamp required")
    return value


def bff_utc_datetime(value: str) -> datetime:
    if not isinstance(value, str) or not re.fullmatch(
        r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3}Z", value
    ):
        raise ValueError("BFF UTC timestamp required")
    try:
        return datetime.fromisoformat(value[:-1] + "+00:00")
    except ValueError as error:
        raise ValueError("BFF UTC timestamp required") from error


@dataclass(frozen=True, slots=True)
class ClipTrigger:
    event_type: EligibleEventType
    event_id: int
    occurred_at: datetime

    def __post_init__(self) -> None:
        if not isinstance(self.event_type, str) or self.event_type not in ELIGIBLE_EVENT_TYPES:
            raise ValueError("eligible event type required")
        if type(self.event_id) is not int or self.event_id <= 0:
            raise ValueError("event_id must be positive")
        if not isinstance(self.occurred_at, datetime):
            raise ValueError("occurred_at must be a datetime")
        utc_text(self.occurred_at)


@dataclass(frozen=True, slots=True)
class ClipEventMetadata:
    event_type: EligibleEventType
    event_id: int
    occurred_at: str

    def __post_init__(self) -> None:
        if not isinstance(self.event_type, str) or self.event_type not in ELIGIBLE_EVENT_TYPES:
            raise ValueError("eligible event type required")
        if type(self.event_id) is not int or self.event_id <= 0:
            raise ValueError("event_id must be positive")
        canonical_utc_text(self.occurred_at)

    @classmethod
    def from_trigger(cls, trigger: ClipTrigger) -> ClipEventMetadata:
        return cls(trigger.event_type, trigger.event_id, utc_text(trigger.occurred_at))


@dataclass(frozen=True, slots=True)
class ClipIntent:
    outbox_id: int
    event_type: EligibleEventType
    event_id: int
    occurred_at: datetime
    created_at: datetime
    deadline_at: datetime
    attempts: int
    remote_boot_id: str | None
    remote_command_id: str | None
    accepted_at: datetime | None

    def __post_init__(self) -> None:
        ClipTrigger(self.event_type, self.event_id, self.occurred_at)
        if type(self.outbox_id) is not int or self.outbox_id <= 0:
            raise ValueError("outbox_id must be positive")
        if type(self.attempts) is not int or self.attempts < 0:
            raise ValueError("attempts must be nonnegative")
        utc_text(self.created_at)
        utc_text(self.deadline_at)
        if self.deadline_at - self.created_at != timedelta(seconds=3):
            raise ValueError("deadline must be three seconds after creation")
        for value in (self.remote_boot_id, self.remote_command_id):
            if value is not None and (type(value) is not str or REMOTE_ID.fullmatch(value) is None):
                raise ValueError("invalid remote identifier")
        if self.remote_boot_id is not None and self.remote_command_id is None:
            raise ValueError("remote boot requires command")
        if self.remote_boot_id is not None and self.accepted_at is None:
            raise ValueError("remote boot requires acceptance")
        if self.accepted_at is not None:
            utc_text(self.accepted_at)
            if self.remote_boot_id is None or self.remote_command_id is None:
                raise ValueError("accepted intent requires remote identity")
            if not (
                self.created_at - timedelta(milliseconds=200)
                <= self.accepted_at
                <= self.deadline_at
            ):
                raise ValueError("accepted time is outside the admission window")

    def command_body(self) -> dict[str, object]:
        return {
            "committed_at": self.created_at,
            "event_id": self.event_id,
            "event_type": self.event_type,
            "occurred_at": self.occurred_at,
        }


@dataclass(frozen=True, slots=True)
class ClipDeliveryIdentity:
    events: tuple[ClipEventMetadata, ...]
    remote_command_ids: tuple[str, ...]
    accepted_at: tuple[datetime, ...]

    def __post_init__(self) -> None:
        if (
            type(self.events) is not tuple
            or not self.events
            or any(not isinstance(event, ClipEventMetadata) for event in self.events)
            or self.events != tuple(sorted(set(self.events), key=lambda event: (event.event_type, event.event_id)))
        ):
            raise ValueError("events must use canonical order")
        if (
            type(self.remote_command_ids) is not tuple
            or len(self.remote_command_ids) != len(self.events)
            or self.remote_command_ids != tuple(sorted(set(self.remote_command_ids)))
            or any(type(value) is not str or REMOTE_ID.fullmatch(value) is None for value in self.remote_command_ids)
        ):
            raise ValueError("command IDs must use canonical order")
        if (
            type(self.accepted_at) is not tuple
            or len(self.accepted_at) != len(self.remote_command_ids)
            or any(not isinstance(value, datetime) for value in self.accepted_at)
        ):
            raise ValueError("accepted times must match commands")
        for value in self.accepted_at:
            utc_text(value)

    @property
    def canonical_events(self) -> str:
        return ",".join(f"{event.event_type}:{event.event_id}" for event in self.events)


@dataclass(frozen=True, slots=True)
class ClipMetadata:
    camera_id: Literal["pc-webcam-01"]
    started_at: datetime
    ended_at: datetime
    events: tuple[ClipEventMetadata, ...]
    remote_command_ids: tuple[str, ...] = field(default=(), compare=False, repr=False)

    def __post_init__(self) -> None:
        if (
            self.camera_id != "pc-webcam-01"
            or not isinstance(self.started_at, datetime)
            or not isinstance(self.ended_at, datetime)
            or type(self.events) is not tuple
            or not self.events
            or any(not isinstance(event, ClipEventMetadata) for event in self.events)
        ):
            raise ValueError("invalid clip metadata")
        utc_text(self.started_at)
        utc_text(self.ended_at)
        if self.ended_at <= self.started_at:
            raise ValueError("invalid clip metadata")
        if len({(event.event_type, event.event_id) for event in self.events}) != len(self.events):
            raise ValueError("clip events must be unique")
        if self.events != tuple(sorted(self.events, key=lambda event: (event.event_type, event.event_id))):
            raise ValueError("clip events must use canonical order")
        if (
            type(self.remote_command_ids) is not tuple
            or any(
                type(value) is not str or REMOTE_ID.fullmatch(value) is None
                for value in self.remote_command_ids
            )
            or self.remote_command_ids != tuple(sorted(set(self.remote_command_ids)))
        ):
            raise ValueError("remote command ids must use canonical order")

    def canonical_json(self) -> bytes:
        value = {
            "camera_id": self.camera_id,
            "ended_at": utc_text(self.ended_at),
            "events": [asdict(event) for event in self.events],
            "started_at": utc_text(self.started_at),
        }
        return json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")


@dataclass(frozen=True, slots=True)
class UploadReceipt:
    id: str
    createdAt: str
    expiresAt: str

    def __post_init__(self) -> None:
        if not isinstance(self.id, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", self.id):
            raise ValueError("opaque clip id required")
        if not isinstance(self.createdAt, str) or not isinstance(self.expiresAt, str):
            raise ValueError("BFF UTC timestamp required")
        created = bff_utc_datetime(self.createdAt)
        expires = bff_utc_datetime(self.expiresAt)
        if expires <= created:
            raise ValueError("expiresAt must be after createdAt")
