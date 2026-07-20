from __future__ import annotations

import json
from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from app.config import AppConfig, load_config
from app.contracts import (
    AnomalyEventOut,
    ApiError,
    BedCalibrationError,
    BedCalibrationSuccess,
    BedChannelStatus,
    BedStatus,
    BehaviorEventOut,
    CameraDetectionIn,
    CameraEventOut,
    CameraStatus,
    DashboardSummary,
    DeviceOut,
    DeviceStatusIn,
    HealthOut,
    SensorReadingIn,
    SensorReadingOut,
    SevenDayComparison,
    ZoneIn,
    ZoneOut,
)
from app.events import (
    EVENT_QUEUE_MAXSIZE,
    CameraFrameCommitted,
    DeviceStatusCommitted,
    SensorReadingCommitted,
)
from app.db import configure_database, dispose_database, session_factory


NOW = datetime(2026, 7, 15, 1, 2, 3, tzinfo=UTC)


def sensor_payload(**overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "device_id": "petzone-01",
        "sensor_type": "temperature",
        "value": 23.5,
        "unit": "C",
        "observed_at": NOW.isoformat().replace("+00:00", "Z"),
    }
    payload.update(overrides)
    return payload


@pytest.mark.parametrize(
    ("overrides", "expected_value"),
    [
        ({"device_id": "entrance-01", "sensor_type": "temperature", "value": 21, "unit": "C"}, 21),
        ({"sensor_type": "humidity", "value": 52.25, "unit": "%"}, 52.25),
        ({"device_id": "entrance-01", "sensor_type": "presence_moving", "value": True, "unit": "bool"}, True),
        ({"sensor_type": "presence_stationary", "value": False, "unit": "bool"}, False),
        ({"sensor_type": "food_weight", "value": 807, "unit": "g"}, 807),
        ({"sensor_type": "water_weight", "value": 603.5, "unit": "g"}, 603.5),
        ({"sensor_type": "bed_pressure_left", "value": 0, "unit": "adc"}, 0),
        ({"sensor_type": "bed_pressure_center", "value": 4095, "unit": "adc"}, 4095),
        ({"sensor_type": "bed_pressure_right", "value": 1000, "unit": "adc"}, 1000),
    ],
)
def test_sensor_contract_accepts_exact_union(overrides: dict[str, object], expected_value: object) -> None:
    model = SensorReadingIn.model_validate_json(json.dumps(sensor_payload(**overrides)))
    assert model.value == expected_value
    assert list(model.model_dump()) == ["device_id", "sensor_type", "value", "unit", "observed_at"]
    assert model.observed_at.tzinfo is UTC


@pytest.mark.parametrize(
    "overrides",
    [
        {"sensor_type": "temperature", "value": True},
        {"sensor_type": "temperature", "value": "23.5"},
        {"sensor_type": "temperature", "value": float("inf")},
        {"sensor_type": "humidity", "unit": "C"},
        {"device_id": "entrance-01", "sensor_type": "food_weight", "unit": "g"},
        {"sensor_type": "presence_moving", "value": 1, "unit": "bool"},
        {"sensor_type": "bed_pressure_left", "value": 1.0, "unit": "adc"},
        {"sensor_type": "bed_pressure_left", "value": 4096, "unit": "adc"},
        {"extra": "forbidden"},
        {"observed_at": "2026-07-15T01:02:03"},
    ],
)
def test_sensor_contract_rejects_coercion_and_cross_field_mismatch(overrides: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        SensorReadingIn.model_validate_json(json.dumps(sensor_payload(**overrides), allow_nan=True))


def test_python_json_boundary_parses_only_iso_datetimes_and_normalizes_utc() -> None:
    utc = SensorReadingIn.model_validate(sensor_payload()).observed_at
    offset = SensorReadingIn.model_validate(sensor_payload(observed_at="2026-07-15T10:02:03+09:00")).observed_at
    assert utc == offset == NOW
    for observed_at in ("2026-07-15T01:02:03", 1, 1.0, True):
        with pytest.raises(ValidationError):
            SensorReadingIn.model_validate(sensor_payload(observed_at=observed_at))


def test_camera_and_zone_geometry_are_half_open_and_strict() -> None:
    payload = {
        "camera_id": "pc-webcam-01",
        "subject_id": "dog_001",
        "detected_type": "dog",
        "confidence": 0.9,
        "bbox_x": 0,
        "bbox_y": 0,
        "bbox_width": 640,
        "bbox_height": 480,
        "center_x": 639,
        "center_y": 479,
        "zone_name": "pet_bed",
        "observed_at": NOW,
    }
    assert CameraDetectionIn.model_validate(payload).center_x == 639
    for bad in (
        {"subject_id": None},
        {"confidence": True},
        {"confidence": float("nan")},
        {"bbox_width": 641},
        {"center_x": 640},
        {"bbox_x": 0.0},
        {"detected_type": "person", "subject_id": "dog_001"},
    ):
        with pytest.raises(ValidationError):
            CameraDetectionIn.model_validate(payload | bad)

    assert ZoneIn(x1=0, y1=0, x2=640, y2=480, enabled=True).x2 == 640
    for bad in ({"x2": 0}, {"x2": 641}, {"y2": 481}, {"x1": 0.0}, {"enabled": 1}):
        with pytest.raises(ValidationError):
            ZoneIn.model_validate({"x1": 0, "y1": 0, "x2": 1, "y2": 1, "enabled": True} | bad)


def test_named_wire_models_keep_exact_field_order() -> None:
    expected = {
        SensorReadingOut: ("id", "device_id", "sensor_type", "value", "unit", "observed_at"),
        DeviceStatusIn: ("device_id", "status", "observed_at"),
        DeviceOut: ("device_id", "status", "last_seen_at"),
        CameraEventOut: ("id", "camera_id", "subject_id", "detected_type", "confidence", "bbox_x", "bbox_y", "bbox_width", "bbox_height", "center_x", "center_y", "zone_name", "observed_at"),
        BehaviorEventOut: ("id", "subject_id", "behavior_type", "started_at", "ended_at", "duration_seconds"),
        AnomalyEventOut: ("id", "subject_id", "anomaly_type", "severity", "mismatch_kind", "message", "occurred_at"),
        CameraStatus: ("state", "fps", "inference_ms", "last_frame_at", "reason"),
        BedChannelStatus: ("channel", "raw", "baseline", "delta", "polarity", "available", "observed_at"),
        SevenDayComparison: ("status", "today_seconds", "baseline_seconds", "difference_seconds", "percent_change", "complete_days"),
        BedStatus: ("device_id", "sensor_state", "pressure_state", "fusion_state", "camera_confirmed", "channels", "current_rest_seconds", "today_rest_seconds", "nighttime_exit_count", "seven_day", "calibrated_at"),
        BedCalibrationSuccess: ("device_id", "calibrated_at", "window_start", "window_end", "channels"),
        BedCalibrationError: ("code", "message", "channels"),
        ZoneOut: ("zone_name", "x1", "y1", "x2", "y2", "enabled", "updated_at"),
        HealthOut: ("status", "database", "mqtt", "camera", "queue", "worker"),
        DashboardSummary: ("generated_at", "health", "devices", "latest_sensors", "camera", "bed", "behaviors", "anomalies"),
        ApiError: ("code", "message"),
    }
    for model, fields in expected.items():
        assert tuple(model.model_fields) == fields
        assert model.model_config["extra"] == "forbid"
        assert model.model_config["strict"] is True


def test_float_fields_do_not_coerce_integers() -> None:
    with pytest.raises(ValidationError):
        CameraStatus(state="online", fps=1, inference_ms=1.0, last_frame_at=NOW, reason=None)


@pytest.mark.parametrize(
    "payload",
    [
        {"id": 1, "subject_id": None, "anomaly_type": "no_meal_12h", "severity": "warning", "mismatch_kind": None, "message": "x", "occurred_at": NOW},
        {"id": 1, "subject_id": "dog_001", "anomaly_type": "bed_sensor_mismatch", "severity": "warning", "mismatch_kind": "unconfirmed_pressure", "message": "x", "occurred_at": NOW},
        {"id": 1, "subject_id": None, "anomaly_type": "bed_sensor_mismatch", "severity": "warning", "mismatch_kind": "sensor_check", "message": "x", "occurred_at": NOW},
    ],
)
def test_anomaly_output_rejects_invalid_relation(payload: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        AnomalyEventOut.model_validate(payload)


def test_events_are_closed_frozen_values() -> None:
    assert EVENT_QUEUE_MAXSIZE == 1024
    reading = SensorReadingCommitted(reading_id=1, device_id="petzone-01", sensor_type="temperature", observed_at=NOW)
    status = DeviceStatusCommitted(device_id="entrance-01", status="offline", observed_at=NOW)
    frame = CameraFrameCommitted(
        camera_id="pc-webcam-01",
        observed_at=NOW,
        detection_ids=(3, 4),
        bed_subject_ids=("dog_001", "cat_001"),
        selected_bed_subject_id="dog_001",
    )
    assert reading.reading_id == 1 and status.status == "offline" and frame.detection_ids == (3, 4)
    with pytest.raises(ValidationError):
        reading.reading_id = 2
    with pytest.raises(ValidationError):
        CameraFrameCommitted(
            camera_id="pc-webcam-01",
            observed_at=NOW,
            detection_ids=(),
            bed_subject_ids=("cat_001", "dog_001"),
            selected_bed_subject_id="cat_001",
        )
    with pytest.raises(ValidationError):
        CameraFrameCommitted(
            camera_id="pc-webcam-01",
            observed_at=NOW,
            detection_ids=(),
            bed_subject_ids=("dog_001",),
            selected_bed_subject_id="cat_001",
        )
    with pytest.raises(ValidationError):
        CameraFrameCommitted(
            camera_id="pc-webcam-01",
            observed_at=NOW,
            detection_ids=(),
            bed_subject_ids=("dog_001",),
            selected_bed_subject_id=None,
        )
    assert CameraFrameCommitted(
        camera_id="pc-webcam-01",
        observed_at=NOW,
        detection_ids=(),
        bed_subject_ids=(),
        selected_bed_subject_id=None,
    ).bed_subject_ids == ()


def test_calibration_error_requires_channels_only_for_channel_failures() -> None:
    with pytest.raises(ValidationError):
        BedCalibrationError(code="sensor_unavailable", message="missing", channels=[])
    assert BedCalibrationError(code="unstable", message="unstable", channels=["left"]).channels == ["left"]
    assert BedCalibrationError(code="camera_unavailable", message="offline", channels=[]).channels == []


def test_ready_seven_day_comparison_enforces_half_up_relation() -> None:
    positive = SevenDayComparison(
        status="ready",
        today_seconds=2001,
        baseline_seconds=2000,
        difference_seconds=1,
        percent_change=0.1,
        complete_days=7,
    )
    negative = SevenDayComparison(
        status="ready",
        today_seconds=1999,
        baseline_seconds=2000,
        difference_seconds=-1,
        percent_change=-0.1,
        complete_days=7,
    )
    assert (positive.percent_change, negative.percent_change) == (0.1, -0.1)
    for bad in (
        {"difference_seconds": 2, "percent_change": 0.1},
        {"difference_seconds": 1, "percent_change": 0.0},
    ):
        with pytest.raises(ValidationError):
            SevenDayComparison.model_validate(
                {
                    "status": "ready",
                    "today_seconds": 2001,
                    "baseline_seconds": 2000,
                    "difference_seconds": 1,
                    "percent_change": 0.1,
                    "complete_days": 7,
                }
                | bad
            )


def test_config_defaults_and_exact_validation(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DATABASE_URL", "postgresql+psycopg://petcare:secret@127.0.0.1:55432/petcare")
    config = load_config()
    assert config.fsr_polarities == (1, 1, 1)
    assert config.fsr_stability_counts == (40, 40, 40)
    assert (config.fsr_exit_threshold, config.fsr_entry_threshold) == (250, 450)
    assert config.sensor_ttl_seconds == config.camera_ttl_seconds == 3
    assert config.timezone.key == "Asia/Seoul"
    assert (config.night_start_hour, config.night_end_hour) == (22, 6)
    assert (config.camera_source, config.camera_model_path, config.camera_index) == (
        "usb",
        ".runtime/models/yolo11n.pt",
        0,
    )

    base = {"database_url": "postgresql+psycopg://petcare:secret@127.0.0.1:55432/petcare"}
    for bad in (
        {"fsr_polarity_left": 0},
        {"fsr_stability_counts_right": 4096},
        {"fsr_exit_threshold": 450},
        {"fsr_entry_threshold": 12286},
        {"sensor_ttl_seconds": 4},
        {"camera_ttl_seconds": 2},
        {"camera_source": "file"},
        {"camera_source": "other"},
        {"camera_model_path": ""},
        {"camera_index": -1},
        {"database_url": "sqlite:///petcare.db"},
        {"database_url": "postgresql+psycopg://petcare:secret@127.0.0.1:5432/petcare"},
    ):
        with pytest.raises(ValidationError):
            AppConfig.model_validate(base | bad)


def test_config_errors_hide_password_bearing_input() -> None:
    sentinel = "todo3-db-password-sentinel"
    invalid_url = f"postgresql+psycopg://petcare:{sentinel}@127.0.0.1:5432/petcare"
    with pytest.raises(ValidationError) as caught:
        AppConfig(database_url=invalid_url)
    assert sentinel not in str(caught.value)
    assert "input_value" not in str(caught.value)

    valid_url = f"postgresql+psycopg://petcare:{sentinel}@127.0.0.1:55432/petcare"
    with pytest.raises(ValidationError) as caught:
        AppConfig(database_url=valid_url, fsr_exit_threshold=450)
    assert sentinel not in str(caught.value)
    assert "input_value" not in str(caught.value)


def test_session_factory_returns_fresh_sessions() -> None:
    configure_database("postgresql+psycopg://petcare:secret@127.0.0.1:55432/petcare")
    first = session_factory()
    second = session_factory()
    try:
        assert first is not second
    finally:
        first.close()
        second.close()
        dispose_database()
