from __future__ import annotations

from typing import Literal, Self, TypeAlias

from pydantic import BaseModel, ConfigDict, StrictInt, model_validator

from .contracts import DeviceId, SensorType, SubjectId, UtcDatetime


EVENT_QUEUE_MAXSIZE = 1024


class FrozenEvent(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)


class SensorReadingCommitted(FrozenEvent):
    reading_id: StrictInt
    device_id: DeviceId
    sensor_type: SensorType
    observed_at: UtcDatetime


class DeviceStatusCommitted(FrozenEvent):
    device_id: DeviceId
    status: Literal["online", "offline"]
    observed_at: UtcDatetime


class CameraFrameCommitted(FrozenEvent):
    camera_id: Literal["pc-webcam-01"]
    observed_at: UtcDatetime
    detection_ids: tuple[StrictInt, ...]
    bed_subject_ids: tuple[SubjectId, ...]
    selected_bed_subject_id: SubjectId | None

    @model_validator(mode="after")
    def validate_subjects(self) -> Self:
        expected = tuple(subject for subject in ("dog_001", "cat_001") if subject in self.bed_subject_ids)
        if self.bed_subject_ids != expected or len(set(self.bed_subject_ids)) != len(self.bed_subject_ids):
            raise ValueError("bed subjects must use fixed dog, cat order")
        if bool(self.bed_subject_ids) != (self.selected_bed_subject_id is not None):
            raise ValueError("bed subject selection must match presence")
        if self.selected_bed_subject_id is not None and self.selected_bed_subject_id not in self.bed_subject_ids:
            raise ValueError("selected bed subject must be present")
        return self


DomainEvent: TypeAlias = SensorReadingCommitted | DeviceStatusCommitted | CameraFrameCommitted


class CalibrateBedCommand(FrozenEvent):
    device_id: Literal["petzone-01"]
