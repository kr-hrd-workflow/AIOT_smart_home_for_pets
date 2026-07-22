from dataclasses import FrozenInstanceError
from datetime import UTC, datetime, timedelta, timezone

import pytest

from app.clip_contracts import (
    ClipDeliveryIdentity,
    ClipEventMetadata,
    ClipIntent,
    ClipMetadata,
    ClipTrigger,
    UploadReceipt,
    bff_utc_datetime,
    canonical_utc_text,
    utc_text,
)


NOW = datetime(2026, 7, 20, 4, 0, tzinfo=UTC)


def test_utc_text_is_canonical_and_requires_an_aware_datetime() -> None:
    assert utc_text(datetime(2026, 7, 20, 13, 0, tzinfo=timezone(timedelta(hours=9)))) == (
        "2026-07-20T04:00:00.000000Z"
    )
    assert canonical_utc_text("2026-07-20T04:00:00.000000Z") == "2026-07-20T04:00:00.000000Z"
    with pytest.raises(ValueError, match="timezone-aware"):
        utc_text(NOW.replace(tzinfo=None))
    for invalid in (
        "2026-07-20T04:00:00Z",
        "2026-07-20T04:00:00.000Z",
        "2026-07-20T04:00:00.000000+00:00",
    ):
        with pytest.raises(ValueError, match="canonical UTC"):
            canonical_utc_text(invalid)


def test_clip_trigger_is_frozen_and_strict() -> None:
    trigger = ClipTrigger("eating", 41, NOW)
    with pytest.raises(FrozenInstanceError):
        trigger.event_id = 42  # type: ignore[misc]
    with pytest.raises(ValueError, match="eligible event type"):
        ClipTrigger("no_meal_12h", 42, NOW)  # type: ignore[arg-type]
    for invalid in (0, -1, True, 1.0):
        with pytest.raises(ValueError, match="event_id must be positive"):
            ClipTrigger("eating", invalid, NOW)  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="occurred_at must be a datetime"):
        ClipTrigger("eating", 1, "2026-07-20T04:00:00.000000Z")  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="timezone-aware"):
        ClipTrigger("eating", 1, NOW.replace(tzinfo=None))


def test_event_metadata_from_trigger_is_canonical_and_strict() -> None:
    event = ClipEventMetadata.from_trigger(ClipTrigger("resting", 5, NOW))
    assert event == ClipEventMetadata("resting", 5, "2026-07-20T04:00:00.000000Z")
    with pytest.raises(ValueError, match="eligible event type"):
        ClipEventMetadata("no_meal_12h", 1, "2026-07-20T04:00:00.000000Z")  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="event_id must be positive"):
        ClipEventMetadata("eating", True, "2026-07-20T04:00:00.000000Z")  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="canonical UTC"):
        ClipEventMetadata("eating", 1, "2026-07-20T04:00:00+00:00")


def test_clip_metadata_serializes_the_exact_local_sidecar_shape() -> None:
    events = (
        ClipEventMetadata("bed_sensor_mismatch", 7, "2026-07-20T03:59:20.000000Z"),
        ClipEventMetadata("eating", 41, "2026-07-20T03:59:30.000000Z"),
        ClipEventMetadata("resting", 105, "2026-07-20T03:59:40.000000Z"),
    )
    metadata = ClipMetadata(
        "pc-webcam-01",
        NOW - timedelta(seconds=10),
        NOW + timedelta(seconds=20),
        events,
        ("1" * 32, "2" * 32),
    )
    assert metadata.remote_command_ids == ("1" * 32, "2" * 32)
    assert metadata.canonical_json() == (
        b'{"camera_id":"pc-webcam-01","ended_at":"2026-07-20T04:00:20.000000Z",'
        b'"events":[{"event_id":7,"event_type":"bed_sensor_mismatch",'
        b'"occurred_at":"2026-07-20T03:59:20.000000Z"},'
        b'{"event_id":41,"event_type":"eating","occurred_at":"2026-07-20T03:59:30.000000Z"},'
        b'{"event_id":105,"event_type":"resting","occurred_at":"2026-07-20T03:59:40.000000Z"}],'
        b'"started_at":"2026-07-20T03:59:50.000000Z"}'
    )


def test_clip_metadata_rejects_invalid_shape_order_and_window() -> None:
    eating = ClipEventMetadata("eating", 41, "2026-07-20T04:00:00.000000Z")
    resting = ClipEventMetadata("resting", 105, "2026-07-20T04:00:01.000000Z")
    invalid_values = (
        ("other-camera", NOW, NOW + timedelta(seconds=1), (eating,)),
        ("pc-webcam-01", NOW, NOW, (eating,)),
        ("pc-webcam-01", NOW, NOW + timedelta(seconds=1), ()),
        ("pc-webcam-01", NOW, NOW + timedelta(seconds=1), [eating]),
        ("pc-webcam-01", NOW, NOW + timedelta(seconds=1), (resting, eating)),
        ("pc-webcam-01", NOW, NOW + timedelta(seconds=1), (eating, eating)),
    )
    for camera_id, started_at, ended_at, events in invalid_values:
        with pytest.raises(ValueError):
            ClipMetadata(camera_id, started_at, ended_at, events)  # type: ignore[arg-type]


def test_upload_receipt_requires_exact_bff_times_and_forward_expiry() -> None:
    receipt = UploadReceipt(
        "clip_01",
        "2026-07-20T04:00:21.000Z",
        "2026-07-27T04:00:21.000Z",
    )
    assert bff_utc_datetime(receipt.createdAt) == datetime(2026, 7, 20, 4, 0, 21, tzinfo=UTC)
    for invalid_id in ("", "clip 01", "x" * 129):
        with pytest.raises(ValueError, match="opaque clip id"):
            UploadReceipt(invalid_id, receipt.createdAt, receipt.expiresAt)
    with pytest.raises(ValueError, match="BFF UTC timestamp"):
        UploadReceipt("clip_01", "2026-07-20T04:00:21+00:00", receipt.expiresAt)
    with pytest.raises(ValueError, match="expiresAt"):
        UploadReceipt("clip_01", receipt.expiresAt, receipt.createdAt)


def test_clip_intent_and_delivery_identity_are_strict_immutable_db_values() -> None:
    command_id = "1" * 32
    intent = ClipIntent(
        outbox_id=9,
        event_type="eating",
        event_id=41,
        occurred_at=NOW - timedelta(seconds=2),
        created_at=NOW,
        deadline_at=NOW + timedelta(seconds=3),
        attempts=0,
        remote_boot_id=None,
        remote_command_id=None,
        accepted_at=None,
    )
    assert intent.command_body() == {
        "committed_at": NOW,
        "event_id": 41,
        "event_type": "eating",
        "occurred_at": NOW - timedelta(seconds=2),
    }
    with pytest.raises(FrozenInstanceError):
        intent.attempts = 1  # type: ignore[misc]

    identity = ClipDeliveryIdentity(
        events=(ClipEventMetadata("eating", 41, "2026-07-20T03:59:58.000000Z"),),
        remote_command_ids=(command_id,),
        accepted_at=(NOW + timedelta(seconds=1),),
    )
    assert identity.canonical_events == "eating:41"
    assert ClipIntent(
        outbox_id=10,
        event_type="eating",
        event_id=42,
        occurred_at=NOW,
        created_at=NOW,
        deadline_at=NOW + timedelta(seconds=3),
        attempts=0,
        remote_boot_id="2" * 32,
        remote_command_id="3" * 32,
        accepted_at=NOW + timedelta(seconds=2, milliseconds=900),
    ).accepted_at == NOW + timedelta(seconds=2, milliseconds=900)

    invalid = (
        {"outbox_id": 0},
        {"attempts": -1},
        {"deadline_at": NOW},
        {"deadline_at": NOW + timedelta(seconds=4)},
        {"remote_command_id": "bad"},
        {"remote_boot_id": "2" * 32, "remote_command_id": "1" * 32},
        {
            "remote_boot_id": "2" * 32,
            "remote_command_id": "1" * 32,
            "accepted_at": NOW + timedelta(seconds=3, milliseconds=1),
        },
    )
    base = intent.__dict__ if hasattr(intent, "__dict__") else {
        "outbox_id": intent.outbox_id,
        "event_type": intent.event_type,
        "event_id": intent.event_id,
        "occurred_at": intent.occurred_at,
        "created_at": intent.created_at,
        "deadline_at": intent.deadline_at,
        "attempts": intent.attempts,
        "remote_boot_id": intent.remote_boot_id,
        "remote_command_id": intent.remote_command_id,
        "accepted_at": intent.accepted_at,
    }
    for change in invalid:
        with pytest.raises(ValueError):
            ClipIntent(**(base | change))

    with pytest.raises(ValueError):
        ClipDeliveryIdentity(identity.events, ("ABC" + "1" * 29,), identity.accepted_at)
    with pytest.raises(ValueError):
        ClipDeliveryIdentity(identity.events, (command_id, command_id), identity.accepted_at)
