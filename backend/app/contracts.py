from __future__ import annotations

import math
from datetime import UTC, datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal
from typing import Annotated, Literal, Self, TypeAlias

from pydantic import AfterValidator, BaseModel, BeforeValidator, ConfigDict, Field, StrictBool, StrictFloat, StrictInt, model_validator


DeviceId = Literal["entrance-01", "petzone-01"]
SensorType = Literal[
    "temperature",
    "humidity",
    "presence_moving",
    "presence_stationary",
    "food_weight",
    "water_weight",
    "bed_pressure_left",
    "bed_pressure_center",
    "bed_pressure_right",
]
Unit = Literal["C", "%", "bool", "g", "adc"]
SubjectId = Literal["dog_001", "cat_001"]
BehaviorType = Literal["eating", "resting"]
AnomalyType = Literal["no_meal_12h", "bed_sensor_mismatch"]
MismatchKind = Literal["unconfirmed_pressure", "sensor_check"]
RestCloseReason = Literal["pressure_exit", "camera_exit", "sensor_loss", "camera_loss", "shutdown", "restart"]
ZoneName = Literal["food_bowl", "pet_bed"]
ChannelName = Literal["left", "center", "right"]


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("datetime must be timezone-aware")
    return value.astimezone(UTC)


def _parse_datetime(value: object) -> datetime:
    if isinstance(value, datetime):
        return value
    if type(value) is str:
        try:
            return datetime.fromisoformat(value)
        except ValueError as error:
            raise ValueError("datetime must be ISO-8601") from error
    raise ValueError("datetime must be an ISO-8601 string or datetime")


def _finite(value: int | float) -> int | float:
    if not math.isfinite(value):
        raise ValueError("number must be finite")
    return value


def _exact_float(value: object) -> float:
    if type(value) is not float:
        raise ValueError("value must be a float")
    return value


UtcDatetime = Annotated[datetime, BeforeValidator(_parse_datetime), AfterValidator(_utc)]
FiniteNumber = Annotated[StrictInt | StrictFloat, AfterValidator(_finite)]
FiniteFloat = Annotated[float, BeforeValidator(_exact_float), AfterValidator(_finite)]
NonNegativeFiniteFloat = Annotated[FiniteFloat, Field(ge=0)]
NonNegativeInt = Annotated[StrictInt, Field(ge=0)]


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class SensorReadingIn(StrictModel):
    device_id: DeviceId
    sensor_type: SensorType
    value: StrictBool | FiniteNumber
    unit: Unit
    observed_at: UtcDatetime

    @model_validator(mode="after")
    def validate_mapping(self) -> Self:
        numeric = isinstance(self.value, (int, float)) and not isinstance(self.value, bool)
        if self.sensor_type == "temperature":
            valid = numeric and self.unit == "C"
        elif self.sensor_type == "humidity":
            valid = numeric and self.unit == "%"
        elif self.sensor_type in {"presence_moving", "presence_stationary"}:
            valid = isinstance(self.value, bool) and self.unit == "bool"
        elif self.sensor_type in {"food_weight", "water_weight"}:
            valid = self.device_id == "petzone-01" and numeric and self.unit == "g"
        else:
            valid = (
                self.device_id == "petzone-01"
                and type(self.value) is int
                and 0 <= self.value <= 4095
                and self.unit == "adc"
            )
        if self.device_id == "entrance-01" and self.sensor_type not in {
            "temperature",
            "humidity",
            "presence_moving",
            "presence_stationary",
        }:
            valid = False
        if not valid:
            raise ValueError("sensor contract mismatch")
        return self


class SensorReadingOut(StrictModel):
    id: StrictInt
    device_id: DeviceId
    sensor_type: SensorType
    value: StrictBool | FiniteNumber
    unit: Unit
    observed_at: UtcDatetime

    _mapping = model_validator(mode="after")(SensorReadingIn.validate_mapping)


class DeviceStatusIn(StrictModel):
    device_id: DeviceId
    status: Literal["online", "offline"]
    observed_at: UtcDatetime


class DeviceOut(StrictModel):
    device_id: DeviceId
    status: Literal["online", "offline", "unknown"]
    last_seen_at: UtcDatetime | None


class CameraDetectionIn(StrictModel):
    camera_id: Literal["pc-webcam-01"]
    subject_id: SubjectId | None
    detected_type: Literal["person", "dog", "cat"]
    confidence: Annotated[FiniteNumber, Field(ge=0, le=1)]
    bbox_x: StrictInt
    bbox_y: StrictInt
    bbox_width: StrictInt
    bbox_height: StrictInt
    center_x: StrictInt
    center_y: StrictInt
    zone_name: ZoneName | None
    observed_at: UtcDatetime

    @model_validator(mode="after")
    def validate_detection(self) -> Self:
        subjects = {"person": None, "dog": "dog_001", "cat": "cat_001"}
        if self.subject_id != subjects[self.detected_type]:
            raise ValueError("detected type and subject do not match")
        right = self.bbox_x + self.bbox_width
        bottom = self.bbox_y + self.bbox_height
        if not (
            0 <= self.bbox_x < right <= 640
            and 0 <= self.bbox_y < bottom <= 480
            and self.bbox_x <= self.center_x < right
            and self.bbox_y <= self.center_y < bottom
        ):
            raise ValueError("invalid half-open detection geometry")
        return self


class CameraEventOut(StrictModel):
    id: StrictInt
    camera_id: Literal["pc-webcam-01"]
    subject_id: SubjectId | None
    detected_type: Literal["person", "dog", "cat"]
    confidence: Annotated[FiniteNumber, Field(ge=0, le=1)]
    bbox_x: StrictInt
    bbox_y: StrictInt
    bbox_width: StrictInt
    bbox_height: StrictInt
    center_x: StrictInt
    center_y: StrictInt
    zone_name: ZoneName | None
    observed_at: UtcDatetime

    _detection = model_validator(mode="after")(CameraDetectionIn.validate_detection)


class BehaviorEventOut(StrictModel):
    id: StrictInt
    subject_id: SubjectId
    behavior_type: BehaviorType
    started_at: UtcDatetime
    ended_at: UtcDatetime | None
    duration_seconds: NonNegativeInt | None

    @model_validator(mode="after")
    def validate_state(self) -> Self:
        if (self.ended_at is None) != (self.duration_seconds is None):
            raise ValueError("end and duration must both be null or present")
        if self.ended_at is not None and self.ended_at < self.started_at:
            raise ValueError("end precedes start")
        return self


class AnomalyEventOut(StrictModel):
    id: StrictInt
    subject_id: SubjectId | None
    anomaly_type: AnomalyType
    severity: Literal["warning"]
    mismatch_kind: MismatchKind | None
    message: str
    occurred_at: UtcDatetime

    @model_validator(mode="after")
    def validate_relation(self) -> Self:
        if not self.message:
            raise ValueError("message must not be empty")
        if self.anomaly_type == "no_meal_12h":
            valid = self.subject_id is not None and self.mismatch_kind is None
        elif self.mismatch_kind == "sensor_check":
            valid = self.subject_id is not None
        else:
            valid = self.mismatch_kind == "unconfirmed_pressure" and self.subject_id is None
        if not valid:
            raise ValueError("invalid anomaly relation")
        return self


class CameraStatus(StrictModel):
    state: Literal["online", "offline"]
    fps: NonNegativeFiniteFloat
    inference_ms: NonNegativeFiniteFloat
    last_frame_at: UtcDatetime | None
    reason: str | None


class BedChannelStatus(StrictModel):
    channel: ChannelName
    raw: Annotated[StrictInt, Field(ge=0, le=4095)] | None
    baseline: Annotated[FiniteFloat, Field(ge=0, le=4095)] | None
    delta: NonNegativeFiniteFloat | None
    polarity: Literal[-1, 1] | None
    available: StrictBool
    observed_at: UtcDatetime | None


class SevenDayComparison(StrictModel):
    status: Literal["insufficient_data", "zero_baseline", "ready"]
    today_seconds: NonNegativeInt
    baseline_seconds: NonNegativeInt | None
    difference_seconds: StrictInt | None
    percent_change: FiniteFloat | None
    complete_days: Annotated[StrictInt, Field(ge=0, le=7)]

    @model_validator(mode="after")
    def validate_status(self) -> Self:
        if self.status == "insufficient_data":
            valid = self.complete_days <= 6 and self.baseline_seconds is None and self.difference_seconds is None and self.percent_change is None
        elif self.status == "zero_baseline":
            valid = self.complete_days == 7 and self.baseline_seconds == 0 and self.difference_seconds == self.today_seconds and self.percent_change is None
        else:
            difference = None if self.baseline_seconds is None else self.today_seconds - self.baseline_seconds
            expected_percent = None
            if self.baseline_seconds is not None and self.baseline_seconds > 0 and difference is not None:
                expected_percent = float(
                    (Decimal(100) * Decimal(difference) / Decimal(self.baseline_seconds)).quantize(
                        Decimal("0.1"), rounding=ROUND_HALF_UP
                    )
                )
            valid = (
                self.complete_days == 7
                and self.baseline_seconds is not None
                and self.baseline_seconds > 0
                and self.difference_seconds == difference
                and self.percent_change == expected_percent
            )
        if not valid:
            raise ValueError("invalid seven-day comparison")
        return self


class BedStatus(StrictModel):
    device_id: Literal["petzone-01"]
    sensor_state: Literal["unavailable", "uncalibrated", "ready"]
    pressure_state: Literal["unavailable", "uncalibrated", "empty", "occupied"]
    fusion_state: Literal["unavailable", "empty", "confirmed_rest", "unconfirmed_pressure", "sensor_check"]
    camera_confirmed: StrictBool
    channels: list[BedChannelStatus]
    current_rest_seconds: NonNegativeInt
    today_rest_seconds: NonNegativeInt
    nighttime_exit_count: NonNegativeInt
    seven_day: SevenDayComparison
    calibrated_at: UtcDatetime | None

    @model_validator(mode="after")
    def validate_channels_and_fusion(self) -> Self:
        if [item.channel for item in self.channels] != ["left", "center", "right"]:
            raise ValueError("channels must be left, center, right")
        if self.camera_confirmed != (self.fusion_state == "confirmed_rest"):
            raise ValueError("camera confirmation does not match fusion state")
        return self


class BedCalibrationChannel(StrictModel):
    channel: ChannelName
    sample_count: Annotated[StrictInt, Field(ge=45)]
    baseline: Annotated[FiniteFloat, Field(ge=0, le=4095)]
    polarity: Literal[-1, 1]


class BedCalibrationSuccess(StrictModel):
    device_id: Literal["petzone-01"]
    calibrated_at: UtcDatetime
    window_start: UtcDatetime
    window_end: UtcDatetime
    channels: list[BedCalibrationChannel]

    @model_validator(mode="after")
    def validate_calibration(self) -> Self:
        if self.window_end != self.window_start + timedelta(seconds=60) or self.calibrated_at < self.window_end:
            raise ValueError("invalid calibration window")
        if [item.channel for item in self.channels] != ["left", "center", "right"]:
            raise ValueError("channels must be left, center, right")
        return self


class BedCalibrationError(StrictModel):
    code: Literal["insufficient_samples", "occupied", "unstable", "camera_unavailable", "sensor_unavailable"]
    message: str
    channels: list[ChannelName]

    @model_validator(mode="after")
    def validate_channels(self) -> Self:
        order = ["left", "center", "right"]
        if self.channels != [channel for channel in order if channel in self.channels] or len(set(self.channels)) != len(self.channels):
            raise ValueError("channels must use deterministic order")
        if self.code in {"camera_unavailable", "occupied"} and self.channels:
            raise ValueError("non-channel error must have no channels")
        if self.code in {"sensor_unavailable", "insufficient_samples", "unstable"} and not self.channels:
            raise ValueError("channel error must name at least one channel")
        return self


class ZoneIn(StrictModel):
    x1: StrictInt
    y1: StrictInt
    x2: StrictInt
    y2: StrictInt
    enabled: StrictBool

    @model_validator(mode="after")
    def validate_geometry(self) -> Self:
        if not (0 <= self.x1 < self.x2 <= 640 and 0 <= self.y1 < self.y2 <= 480):
            raise ValueError("invalid zone geometry")
        return self


class ZoneOut(StrictModel):
    zone_name: ZoneName
    x1: StrictInt
    y1: StrictInt
    x2: StrictInt
    y2: StrictInt
    enabled: StrictBool
    updated_at: UtcDatetime

    _geometry = model_validator(mode="after")(ZoneIn.validate_geometry)


class HealthOut(StrictModel):
    status: Literal["healthy", "degraded"]
    database: Literal["up", "down"]
    mqtt: Literal["up", "down", "disabled"]
    camera: Literal["online", "offline"]
    queue: Literal["ok", "full"]
    worker: Literal["running", "stopped"]


class DashboardSummary(StrictModel):
    generated_at: UtcDatetime
    health: HealthOut
    devices: list[DeviceOut]
    latest_sensors: list[SensorReadingOut]
    camera: CameraStatus
    bed: BedStatus
    behaviors: list[BehaviorEventOut]
    anomalies: list[AnomalyEventOut]


class DashboardUpdate(StrictModel):
    type: Literal["dashboard_update"]
    payload: DashboardSummary


class BedStatusMessage(StrictModel):
    type: Literal["bed_status"]
    payload: BedStatus


class AnomalyAlert(StrictModel):
    type: Literal["anomaly_alert"]
    payload: AnomalyEventOut


DashboardMessage: TypeAlias = DashboardUpdate | BedStatusMessage | AnomalyAlert


class ApiError(StrictModel):
    code: Literal[
        "queue_unavailable",
        "worker_unavailable",
        "database_unavailable",
        "validation_error",
        "zone_not_found",
        "zone_conflict",
        "camera_unavailable",
        "origin_forbidden",
    ]
    message: str
