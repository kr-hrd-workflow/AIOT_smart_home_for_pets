from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from pydantic import ValidationError

from app.jetson_contracts import (
    JetsonClipCommand,
    JetsonClipReceipt,
    JetsonObservation,
    JetsonStatus,
    canonical_query,
    canonical_json,
    parse_observation,
    sign_request,
    strict_json,
)


ROOT = Path(__file__).resolve().parents[2]
FIXTURE = json.loads((ROOT / "contracts" / "petcare-jetson-wire-v1.json").read_text(encoding="utf-8"))


def test_signing_and_query_match_the_frozen_fixture() -> None:
    auth = FIXTURE["auth"]
    request = auth["request"]
    body = canonical_json(request["body"])

    headers = sign_request(
        method=request["method"],
        target=request["target"],
        boot_id=request["boot_id"],
        timestamp=request["timestamp"],
        nonce=request["nonce"],
        body=body,
        secret=__import__("base64").urlsafe_b64decode(auth["secret_base64url"] + "="),
    )

    assert body.decode() == request["body_json"]
    assert headers == request["headers"]
    assert canonical_query(auth["canonical_query"]["pairs"]) == auth["canonical_query"]["value"]


def test_strict_models_accept_only_the_frozen_shapes() -> None:
    status = JetsonStatus.model_validate(FIXTURE["status"])
    observation = JetsonObservation.model_validate(FIXTURE["observation"]["body"])
    command = JetsonClipCommand.model_validate(FIXTURE["command"]["request"])

    assert status.boot_id == observation.boot_id
    assert command.event_type == "eating"
    for model, payload in (
        (JetsonStatus, FIXTURE["status"] | {"extra": True}),
        (JetsonObservation, FIXTURE["observation"]["body"] | {"width": 641}),
        (JetsonClipCommand, FIXTURE["command"]["request"] | {"event_type": "no_meal_12h"}),
    ):
        with pytest.raises(ValidationError):
            model.model_validate(payload)

    with pytest.raises(ValidationError):
        JetsonClipReceipt.model_validate(FIXTURE["command"]["response"] | {"state": "ready"})


@pytest.mark.parametrize(
    "change",
    [
        {"sequence": True},
        {"width": 640.0},
        {"fps": float("nan")},
        {"inference_ms": float("inf")},
        {"detections": [FIXTURE["observation"]["body"]["detections"][0]] * 2},
        {"detections": [FIXTURE["observation"]["body"]["detections"][0] | {"bbox_width": 541}]},
    ],
)
def test_observation_rejects_boolean_nonfinite_duplicate_and_bad_geometry(change: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        JetsonObservation.model_validate(FIXTURE["observation"]["body"] | change)


def test_observation_requires_current_boot_sequence_and_source_timestamp() -> None:
    payload = FIXTURE["observation"]["body"]
    now = datetime.fromisoformat(payload["observed_at"].replace("Z", "+00:00"))
    parsed = parse_observation(canonical_json(payload), payload["boot_id"], 41, now)
    assert parsed.sequence == 42

    for boot, sequence, received_at in (
        ("f" * 32, 41, now),
        (payload["boot_id"], 42, now),
        (payload["boot_id"], 41, now + timedelta(seconds=3, microseconds=1)),
        (payload["boot_id"], 41, now - timedelta(microseconds=1)),
    ):
        with pytest.raises(ValueError):
            parse_observation(canonical_json(payload), boot, sequence, received_at)


def test_timestamps_must_be_canonical_aware_utc() -> None:
    payload = FIXTURE["observation"]["body"]
    for value in ("2026-07-20T04:00:00Z", "2026-07-20T13:00:00.100000+09:00", "2026-07-20T04:00:00.100000"):
        with pytest.raises(ValidationError):
            JetsonObservation.model_validate(payload | {"observed_at": value})

    with pytest.raises(ValueError):
        parse_observation(canonical_json(payload), payload["boot_id"], 41, datetime(2026, 7, 20, 4, 0))


def test_deep_json_is_a_bounded_validation_error() -> None:
    payload = b'{"value":' + b"[" * 1000 + b"0" + b"]" * 1000 + b"}"
    with pytest.raises(ValueError, match="invalid JSON"):
        strict_json(payload)
