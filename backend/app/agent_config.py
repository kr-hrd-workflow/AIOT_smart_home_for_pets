from __future__ import annotations

import base64
import json
import os
import secrets
import subprocess
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from pydantic import BaseModel, ConfigDict, SecretStr, field_validator, model_validator


LOCAL_CAMERA_ID = "pc-webcam-01"


def _validate_nonempty(value: str, field: str) -> str:
    if not isinstance(value, str) or not value or value != value.strip():
        raise ValueError(f"{field} must be a nonempty string")
    return value


def _raw_key_bytes(value: str, field: str) -> bytes:
    if not isinstance(value, str) or len(value) != 43 or "=" in value:
        raise ValueError(f"{field} must be an unpadded base64url raw Ed25519 key")
    try:
        decoded = base64.b64decode(value + "=", altchars=b"-_", validate=True)
    except (ValueError, base64.binascii.Error) as error:
        raise ValueError(f"{field} must be an unpadded base64url raw Ed25519 key") from error
    if len(decoded) != 32 or base64.urlsafe_b64encode(decoded).decode("ascii").rstrip("=") != value:
        raise ValueError(f"{field} must be an unpadded base64url raw Ed25519 key")
    return decoded


def _decode_raw_key(value: str, field: str) -> str:
    _raw_key_bytes(value, field)
    return value


class LocalSettings(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True, hide_input_in_errors=True)

    database_url: SecretStr
    mqtt_profile: str
    mqtt_username: str
    mqtt_password: SecretStr

    @field_validator("mqtt_profile", "mqtt_username")
    @classmethod
    def validate_nonempty_text(cls, value: str) -> str:
        return _validate_nonempty(value, "MQTT value")

    @field_validator("mqtt_password")
    @classmethod
    def validate_mqtt_password(cls, value: SecretStr) -> SecretStr:
        _validate_nonempty(value.get_secret_value(), "MQTT password")
        return value

    @field_validator("database_url")
    @classmethod
    def validate_database_url(cls, value: SecretStr) -> SecretStr:
        parsed = urlsplit(value.get_secret_value())
        if (
            parsed.scheme != "postgresql+psycopg"
            or parsed.hostname != "127.0.0.1"
            or parsed.port != 55432
            or not parsed.username
            or not parsed.path
            or parsed.path == "/"
            or parsed.query
            or parsed.fragment
        ):
            raise ValueError("database_url must use loopback PostgreSQL port 55432")
        return value


class AgentRuntimeConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True, hide_input_in_errors=True)

    origin: str
    agent_id: str
    camera_id: str
    connector_token: SecretStr
    private_key: SecretStr
    public_key: str
    local_camera_id: str = LOCAL_CAMERA_ID
    local_settings: LocalSettings

    @field_validator("origin")
    @classmethod
    def validate_origin(cls, value: str) -> str:
        return require_https_origin(value)

    @field_validator("agent_id", "camera_id")
    @classmethod
    def validate_server_id(cls, value: str) -> str:
        return _validate_nonempty(value, "server id")

    @field_validator("connector_token")
    @classmethod
    def validate_connector_token(cls, value: SecretStr) -> SecretStr:
        _validate_nonempty(value.get_secret_value(), "connector token")
        return value

    @field_validator("private_key")
    @classmethod
    def validate_private_key(cls, value: SecretStr) -> SecretStr:
        _decode_raw_key(value.get_secret_value(), "private_key")
        return value

    @field_validator("public_key")
    @classmethod
    def validate_public_key(cls, value: str) -> str:
        return _decode_raw_key(value, "public_key")

    @field_validator("local_camera_id")
    @classmethod
    def validate_local_camera_id(cls, value: str) -> str:
        if value != LOCAL_CAMERA_ID:
            raise ValueError(f"local_camera_id must be {LOCAL_CAMERA_ID}")
        return value

    @model_validator(mode="after")
    def validate_keypair(self) -> "AgentRuntimeConfig":
        private_key = Ed25519PrivateKey.from_private_bytes(
            _raw_key_bytes(self.private_key.get_secret_value(), "private_key")
        )
        derived_public = private_key.public_key().public_bytes(
            serialization.Encoding.Raw,
            serialization.PublicFormat.Raw,
        )
        if derived_public != _raw_key_bytes(self.public_key, "public_key"):
            raise ValueError("Ed25519 keypair does not match")
        return self


def require_https_origin(value: str) -> str:
    parsed = urlsplit(value)
    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.path not in ("", "/")
        or parsed.query
        or parsed.fragment
        or value.endswith("/")
    ):
        raise ValueError("origin must be an HTTPS origin without path")
    return value


def _runtime_payload(config: AgentRuntimeConfig) -> dict[str, Any]:
    return {
        "origin": config.origin,
        "agent_id": config.agent_id,
        "camera_id": config.camera_id,
        "connector_token": config.connector_token.get_secret_value(),
        "private_key": config.private_key.get_secret_value(),
        "public_key": config.public_key,
        "local_camera_id": config.local_camera_id,
        "local_settings": {
            "database_url": config.local_settings.database_url.get_secret_value(),
            "mqtt_profile": config.local_settings.mqtt_profile,
            "mqtt_username": config.local_settings.mqtt_username,
            "mqtt_password": config.local_settings.mqtt_password.get_secret_value(),
        },
    }


def _current_windows_sid() -> str:
    import win32api
    import win32con
    import win32security

    token = win32security.OpenProcessToken(win32api.GetCurrentProcess(), win32con.TOKEN_QUERY)
    sid = win32security.GetTokenInformation(token, win32security.TokenUser)[0]
    return win32security.ConvertSidToStringSid(sid)


def protect_runtime_file(path: Path, windows_identity_sid: str | None = None) -> None:
    if os.name == "nt":
        sid = windows_identity_sid or _current_windows_sid()
        subprocess.run(
            [
                "icacls.exe",
                str(path),
                "/inheritance:r",
                "/grant:r",
                f"*{sid}:(F)",
                "*S-1-5-18:(F)",
                "/remove:g",
                "*S-1-5-32-545",
            ],
            check=True,
            capture_output=True,
            text=True,
        )
    else:
        os.chmod(path, 0o600)


def write_runtime_config(
    path: Path,
    config: AgentRuntimeConfig,
    *,
    windows_identity_sid: str | None = None,
) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{secrets.token_hex(8)}.new")
    descriptor: int | None = None
    try:
        descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        protect_runtime_file(temporary, windows_identity_sid)
        serialized = json.dumps(
            _runtime_payload(config),
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
        output = os.fdopen(descriptor, "wb")
        descriptor = None
        with output:
            output.write(serialized)
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, path)
        if os.name == "posix":
            directory = os.open(path.parent, os.O_RDONLY)
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
    except BaseException:
        if descriptor is not None:
            os.close(descriptor)
        temporary.unlink(missing_ok=True)
        raise


def _reject_duplicate_pairs(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate configuration key")
        result[key] = value
    return result


def load_runtime_config(path: Path) -> AgentRuntimeConfig:
    try:
        payload = json.loads(
            Path(path).read_text(encoding="utf-8"),
            object_pairs_hook=_reject_duplicate_pairs,
        )
        return AgentRuntimeConfig.model_validate(payload)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError, ValueError) as error:
        raise ValueError("invalid agent runtime configuration") from error
