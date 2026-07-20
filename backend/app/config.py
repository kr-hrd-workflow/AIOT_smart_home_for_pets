from __future__ import annotations

import os
from typing import Literal, Self
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo

from pydantic import BaseModel, ConfigDict, Field, SecretStr, StrictInt, model_validator


class AppConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True, hide_input_in_errors=True)

    database_url: str
    fsr_polarity_left: Literal[-1, 1] = 1
    fsr_polarity_center: Literal[-1, 1] = 1
    fsr_polarity_right: Literal[-1, 1] = 1
    fsr_stability_counts_left: StrictInt = Field(default=40, ge=0, le=4095)
    fsr_stability_counts_center: StrictInt = Field(default=40, ge=0, le=4095)
    fsr_stability_counts_right: StrictInt = Field(default=40, ge=0, le=4095)
    fsr_entry_threshold: StrictInt = Field(default=450, le=12285)
    fsr_exit_threshold: StrictInt = Field(default=250, ge=0)
    sensor_ttl_seconds: Literal[3] = 3
    camera_ttl_seconds: Literal[3] = 3
    timezone_name: Literal["Asia/Seoul"] = "Asia/Seoul"
    night_start_hour: Literal[22] = 22
    night_end_hour: Literal[6] = 6
    camera_source: Literal["usb", "file", "disabled"] = "usb"
    camera_file_path: str | None = None
    camera_model_path: str = ".runtime/models/yolo11n.pt"
    camera_index: StrictInt = Field(default=0, ge=0)
    mqtt_profile: Literal["local_live", "hardware"] | None = None
    mqtt_services_manifest: str = ".runtime/services.json"
    mqtt_username: SecretStr | None = None
    mqtt_password: SecretStr | None = None

    @model_validator(mode="after")
    def validate_all(self) -> Self:
        parsed = urlsplit(self.database_url.replace("postgresql+psycopg", "postgresql", 1))
        if parsed.scheme != "postgresql" or parsed.hostname not in {"127.0.0.1", "localhost"} or parsed.port != 55432:
            raise ValueError("DATABASE_URL must target loopback PostgreSQL on port 55432")
        if not 0 <= self.fsr_exit_threshold < self.fsr_entry_threshold <= 12285:
            raise ValueError("invalid FSR thresholds")
        if not self.camera_model_path:
            raise ValueError("camera model path must not be empty")
        if self.camera_source == "file" and not self.camera_file_path:
            raise ValueError("file camera source requires a path")
        mqtt_values = (self.mqtt_profile, self.mqtt_username, self.mqtt_password)
        if any(value is not None for value in mqtt_values) and not all(value is not None for value in mqtt_values):
            raise ValueError("MQTT profile, username, and password must be configured together")
        if self.mqtt_username is not None and not self.mqtt_username.get_secret_value():
            raise ValueError("MQTT username must not be empty")
        if self.mqtt_password is not None and not self.mqtt_password.get_secret_value():
            raise ValueError("MQTT password must not be empty")
        return self

    @property
    def fsr_polarities(self) -> tuple[int, int, int]:
        return self.fsr_polarity_left, self.fsr_polarity_center, self.fsr_polarity_right

    @property
    def fsr_stability_counts(self) -> tuple[int, int, int]:
        return self.fsr_stability_counts_left, self.fsr_stability_counts_center, self.fsr_stability_counts_right

    @property
    def timezone(self) -> ZoneInfo:
        return ZoneInfo(self.timezone_name)

    @property
    def mqtt_enabled(self) -> bool:
        return self.mqtt_profile is not None


def _integer(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if raw is None:
        return default
    try:
        return int(raw)
    except ValueError as error:
        raise ValueError(f"{name} must be an integer") from error


def load_config() -> AppConfig:
    return AppConfig(
        database_url=os.environ.get("DATABASE_URL", ""),
        fsr_polarity_left=_integer("FSR_POLARITY_LEFT", 1),
        fsr_polarity_center=_integer("FSR_POLARITY_CENTER", 1),
        fsr_polarity_right=_integer("FSR_POLARITY_RIGHT", 1),
        fsr_stability_counts_left=_integer("FSR_STABILITY_COUNTS_LEFT", 40),
        fsr_stability_counts_center=_integer("FSR_STABILITY_COUNTS_CENTER", 40),
        fsr_stability_counts_right=_integer("FSR_STABILITY_COUNTS_RIGHT", 40),
        fsr_entry_threshold=_integer("FSR_ENTRY_THRESHOLD", 450),
        fsr_exit_threshold=_integer("FSR_EXIT_THRESHOLD", 250),
        camera_source=os.environ.get("PETCARE_CAMERA_SOURCE", "usb"),
        camera_file_path=os.environ.get("PETCARE_CAMERA_FILE"),
        camera_model_path=os.environ.get("PETCARE_CAMERA_MODEL", ".runtime/models/yolo11n.pt"),
        camera_index=_integer("PETCARE_CAMERA_INDEX", 0),
        mqtt_profile=os.environ.get("PETCARE_MQTT_PROFILE"),
        mqtt_services_manifest=os.environ.get("PETCARE_SERVICES_MANIFEST", ".runtime/services.json"),
        mqtt_username=os.environ.get("PETCARE_MQTT_USERNAME"),
        mqtt_password=os.environ.get("PETCARE_MQTT_PASSWORD"),
    )
