from __future__ import annotations

import json
import os
import stat
from ipaddress import IPv4Address
from pathlib import Path
from typing import Literal, Self
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo

from pydantic import BaseModel, ConfigDict, Field, PrivateAttr, SecretStr, StrictInt, ValidationInfo, field_validator, model_validator


def _rfc1918(address: IPv4Address) -> bool:
    octets = address.packed
    return (
        octets[0] == 10
        or (octets[0] == 172 and 16 <= octets[1] <= 31)
        or (octets[0] == 192 and octets[1] == 168)
    )


def _tailscale_cgnat(address: IPv4Address) -> bool:
    octets = address.packed
    return octets[0] == 100 and 64 <= octets[1] <= 127


def _private_transport_pair(jetson_ip: IPv4Address, home_ip: IPv4Address) -> bool:
    return jetson_ip != home_ip and (
        (_rfc1918(jetson_ip) and _rfc1918(home_ip))
        or (_tailscale_cgnat(jetson_ip) and _tailscale_cgnat(home_ip))
    )


def _private_transport_address(address: IPv4Address) -> bool:
    return _rfc1918(address) or _tailscale_cgnat(address)


class JetsonConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True, hide_input_in_errors=True)

    url: str
    home_ip: str
    ca_cert_path: Path
    psk_path: Path
    _ca_pem: bytes = PrivateAttr(default=b"")
    _psk: bytes = PrivateAttr(default=b"")

    @field_validator("url")
    @classmethod
    def validate_url(cls, value: str) -> str:
        parsed = urlsplit(value)
        try:
            address = IPv4Address(parsed.hostname or "")
        except ValueError as error:
            raise ValueError("Jetson URL must use a private literal IPv4 address") from error
        if (
            parsed.scheme != "https"
            or parsed.port != 9443
            or parsed.username is not None
            or parsed.password is not None
            or parsed.path not in ("", "/")
            or parsed.query
            or parsed.fragment
            or not _private_transport_address(address)
            or address.is_loopback
            or address.is_link_local
            or address.is_multicast
            or address.is_unspecified
            or value.endswith("/")
        ):
            raise ValueError("Jetson URL must use private HTTPS port 9443")
        return value

    @field_validator("home_ip")
    @classmethod
    def validate_home_ip(cls, value: str) -> str:
        try:
            address = IPv4Address(value)
        except ValueError as error:
            raise ValueError("Home IP must be a private literal IPv4 address") from error
        if str(address) != value or not _private_transport_address(address):
            raise ValueError("Home IP must be a private literal IPv4 address")
        return value

    @model_validator(mode="after")
    def validate_transport_pair(self) -> Self:
        jetson_ip = IPv4Address(urlsplit(self.url).hostname or "")
        home_ip = IPv4Address(self.home_ip)
        if not _private_transport_pair(jetson_ip, home_ip):
            raise ValueError("Home and Jetson must use the same private transport")
        return self

    @field_validator("ca_cert_path", "psk_path")
    @classmethod
    def validate_file_path(cls, value: Path) -> Path:
        if not value.is_absolute() or not value.is_file():
            raise ValueError("Jetson trust files must be absolute regular files")
        return value

    @model_validator(mode="after")
    def validate_secret(self, info: ValidationInfo) -> Self:
        context = info.context or {}
        ca_pem = context.get("ca_pem")
        secret = context.get("psk")
        if ca_pem is None or secret is None:
            ca_pem = _secure_read(self.ca_cert_path, owner_only=True)
            secret = _secure_read(self.psk_path, owner_only=True)
        if type(ca_pem) is not bytes or not ca_pem or type(secret) is not bytes or len(secret) != 32:
            raise ValueError("Jetson secret file must contain exactly 32 bytes")
        self._ca_pem = ca_pem
        self._psk = secret
        return self

    @property
    def ca_pem(self) -> bytes:
        return self._ca_pem

    @property
    def psk(self) -> bytes:
        return self._psk


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
    camera_source: Literal["usb", "file", "jetson", "disabled"] = "usb"
    camera_file_path: str | None = None
    camera_model_path: str = ".runtime/models/yolo11n.pt"
    camera_index: StrictInt = Field(default=0, ge=0)
    jetson_config: JetsonConfig | None = None
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
        if self.camera_source == "jetson" and self.jetson_config is None:
            raise ValueError("Jetson camera source requires runtime configuration")
        if self.camera_source != "jetson" and self.jetson_config is not None:
            raise ValueError("Jetson runtime configuration requires Jetson camera source")
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


def _owner_only_descriptor(descriptor: int, status: os.stat_result) -> bool:
    if os.name == "posix":
        return status.st_uid == os.getuid() and stat.S_IMODE(status.st_mode) & 0o077 == 0
    if os.name == "nt":
        import msvcrt
        import win32api
        import win32con
        import win32security

        current = win32security.GetTokenInformation(
            win32security.OpenProcessToken(win32api.GetCurrentProcess(), win32con.TOKEN_QUERY),
            win32security.TokenUser,
        )[0]
        system = win32security.ConvertStringSidToSid("S-1-5-18")
        allowed = {win32security.ConvertSidToStringSid(current), win32security.ConvertSidToStringSid(system)}
        security = win32security.GetSecurityInfo(
            msvcrt.get_osfhandle(descriptor),
            win32security.SE_FILE_OBJECT,
            win32security.OWNER_SECURITY_INFORMATION | win32security.DACL_SECURITY_INFORMATION,
        )
        owner = win32security.ConvertSidToStringSid(security.GetSecurityDescriptorOwner())
        if owner not in allowed:
            return False
        dacl = security.GetSecurityDescriptorDacl()
        if dacl is None:
            return False
        for index in range(dacl.GetAceCount()):
            ace = dacl.GetAce(index)
            ace_type = ace[0][0]
            if ace_type not in (win32security.ACCESS_ALLOWED_ACE_TYPE, win32security.ACCESS_DENIED_ACE_TYPE):
                return False
            if (
                ace_type == win32security.ACCESS_ALLOWED_ACE_TYPE
                and win32security.ConvertSidToStringSid(ace[-1]) not in allowed
            ):
                return False
        return True
    return False


def _secure_read(path: Path, *, owner_only: bool) -> bytes:
    path = Path(path)
    flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0)
    before = path.lstat()
    reparse = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
    if not stat.S_ISREG(before.st_mode) or getattr(before, "st_file_attributes", 0) & reparse:
        raise ValueError("invalid protected file")
    descriptor = os.open(path, flags)
    try:
        opened = os.fstat(descriptor)
        after = path.lstat()
        if (
            not stat.S_ISREG(opened.st_mode)
            or getattr(opened, "st_file_attributes", 0) & reparse
            or not os.path.samestat(before, opened)
            or not os.path.samestat(opened, after)
            or (owner_only and not _owner_only_descriptor(descriptor, opened))
        ):
            raise ValueError("invalid protected file")
        with os.fdopen(descriptor, "rb", closefd=False) as source:
            return source.read()
    finally:
        os.close(descriptor)


def _unique_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate configuration key")
        result[key] = value
    return result


def load_jetson_config(path: Path) -> JetsonConfig:
    path = Path(path)
    try:
        if not path.is_absolute():
            raise ValueError("invalid Jetson runtime configuration")
        payload = json.loads(_secure_read(path, owner_only=True).decode("utf-8"), object_pairs_hook=_unique_object)
        if type(payload) is dict:
            for key in ("ca_cert_path", "psk_path"):
                if type(payload.get(key)) is str:
                    payload[key] = Path(payload[key])
        if type(payload) is not dict or set(payload) != {"url", "home_ip", "ca_cert_path", "psk_path"}:
            raise ValueError("invalid Jetson runtime configuration")
        ca_path, psk_path = payload["ca_cert_path"], payload["psk_path"]
        if not isinstance(ca_path, Path) or not isinstance(psk_path, Path):
            raise ValueError("invalid Jetson runtime configuration")
        ca_pem = _secure_read(ca_path, owner_only=True)
        psk = _secure_read(psk_path, owner_only=True)
        return JetsonConfig.model_validate(payload, context={"ca_pem": ca_pem, "psk": psk})
    except (OSError, UnicodeDecodeError, json.JSONDecodeError, ValueError, RecursionError) as error:
        raise ValueError("invalid Jetson runtime configuration") from error


def load_config() -> AppConfig:
    camera_source = os.environ.get("PETCARE_CAMERA_SOURCE", "usb")
    jetson_path = os.environ.get("PETCARE_JETSON_CONFIG")
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
        camera_source=camera_source,
        camera_file_path=os.environ.get("PETCARE_CAMERA_FILE"),
        camera_model_path=os.environ.get("PETCARE_CAMERA_MODEL", ".runtime/models/yolo11n.pt"),
        camera_index=_integer("PETCARE_CAMERA_INDEX", 0),
        jetson_config=load_jetson_config(Path(jetson_path)) if camera_source == "jetson" and jetson_path else None,
        mqtt_profile=os.environ.get("PETCARE_MQTT_PROFILE"),
        mqtt_services_manifest=os.environ.get("PETCARE_SERVICES_MANIFEST", ".runtime/services.json"),
        mqtt_username=os.environ.get("PETCARE_MQTT_USERNAME"),
        mqtt_password=os.environ.get("PETCARE_MQTT_PASSWORD"),
    )
