from __future__ import annotations

import json
import os
import stat
from pathlib import Path

import pytest
from pydantic import SecretStr, ValidationError

from app.agent_config import (
    AgentRuntimeConfig,
    LocalSettings,
    load_runtime_config,
    protect_runtime_file,
    write_runtime_config,
)


PRIVATE_KEY = "AAECAwQFBgcICQoLDA0ODxAREhMUFRYXGBkaGxwdHh8"
PUBLIC_KEY = "A6EHv_POEL4dcN0Y50vAmWfk1jCbpQ1fHdyGZBJVMbg"


def local_settings() -> LocalSettings:
    return LocalSettings(
        database_url="postgresql+psycopg://petcare:db-secret@127.0.0.1:55432/petcare",
        mqtt_profile="local_live",
        mqtt_username="petcare",
        mqtt_password="mqtt-secret",
    )


def runtime_config() -> AgentRuntimeConfig:
    return AgentRuntimeConfig(
        origin="https://petcare.example",
        agent_id="agent_01",
        camera_id="camera_01",
        connector_token="connector-secret",
        private_key=PRIVATE_KEY,
        public_key=PUBLIC_KEY,
        local_camera_id="pc-webcam-01",
        local_settings=local_settings(),
    )


def test_runtime_config_redacts_every_local_secret_and_round_trips(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = runtime_config()
    rendered = repr(config)
    assert "connector-secret" not in rendered
    assert "db-secret" not in rendered
    assert "mqtt-secret" not in rendered
    assert PRIVATE_KEY not in rendered

    output = tmp_path / "agent.json"
    monkeypatch.setattr("app.agent_config.protect_runtime_file", lambda *args: None)
    write_runtime_config(output, config, windows_identity_sid="S-1-5-21-1000")

    persisted = json.loads(output.read_text(encoding="utf-8"))
    assert persisted["connector_token"] == "connector-secret"
    assert persisted["private_key"] == PRIVATE_KEY
    assert persisted["local_settings"]["mqtt_password"] == "mqtt-secret"
    loaded = load_runtime_config(output)
    assert loaded == config
    assert isinstance(loaded.connector_token, SecretStr)
    assert isinstance(loaded.local_settings.database_url, SecretStr)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("origin", "http://petcare.example"),
        ("origin", "https://petcare.example/path"),
        ("local_camera_id", "other-camera"),
        ("private_key", "short"),
        ("public_key", "short"),
    ],
)
def test_runtime_config_rejects_invalid_trust_boundary_values(field: str, value: str) -> None:
    values = {
        "origin": "https://petcare.example",
        "agent_id": "agent_01",
        "camera_id": "camera_01",
        "connector_token": "connector-secret",
        "private_key": PRIVATE_KEY,
        "public_key": PUBLIC_KEY,
        "local_camera_id": "pc-webcam-01",
        "local_settings": local_settings(),
    }
    values[field] = value
    with pytest.raises(ValidationError):
        AgentRuntimeConfig(**values)


@pytest.mark.parametrize(
    "database_url",
    [
        "postgresql+psycopg://petcare:secret@localhost:55432/petcare",
        "postgresql+psycopg://petcare:secret@127.0.0.1:5432/petcare",
        "postgresql+psycopg://petcare:secret@192.168.1.2:55432/petcare",
    ],
)
def test_local_settings_requires_exact_loopback_database(database_url: str) -> None:
    with pytest.raises(ValidationError):
        LocalSettings(
            database_url=database_url,
            mqtt_profile="local_live",
            mqtt_username="petcare",
            mqtt_password="mqtt-secret",
        )


def test_runtime_config_forbids_extra_fields_and_hides_secret_input_on_error() -> None:
    with pytest.raises(ValidationError) as error:
        LocalSettings(
            database_url="postgresql+psycopg://petcare:db-secret@127.0.0.1:55432/petcare",
            mqtt_profile="local_live",
            mqtt_username="petcare",
            mqtt_password="mqtt-secret",
            extra_secret="must-not-leak",  # type: ignore[call-arg]
        )
    assert "must-not-leak" not in str(error.value)


def test_runtime_config_rejects_mismatched_ed25519_keypair() -> None:
    with pytest.raises(ValidationError, match="Ed25519 keypair"):
        AgentRuntimeConfig(
            origin="https://petcare.example",
            agent_id="agent_01",
            camera_id="camera_01",
            connector_token="connector-secret",
            private_key=PRIVATE_KEY,
            public_key="AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA",
            local_camera_id="pc-webcam-01",
            local_settings=local_settings(),
        )


def test_atomic_replace_failure_preserves_previous_config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    output = tmp_path / "agent.json"
    output.write_text("previous", encoding="utf-8")

    def fail_replace(source: object, destination: object) -> None:
        raise OSError("replace failed")

    monkeypatch.setattr("app.agent_config.os.replace", fail_replace)
    monkeypatch.setattr("app.agent_config.protect_runtime_file", lambda *args: None)
    with pytest.raises(OSError, match="replace failed"):
        write_runtime_config(output, runtime_config(), windows_identity_sid="S-1-5-21-1000")

    assert output.read_text(encoding="utf-8") == "previous"
    assert list(tmp_path.iterdir()) == [output]


def test_runtime_file_is_protected_before_first_secret_byte(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    output = tmp_path / "agent.json"
    observed_sizes: list[int] = []

    def observe_empty(path: Path, windows_identity_sid: str | None = None) -> None:
        observed_sizes.append(path.stat().st_size)

    monkeypatch.setattr("app.agent_config.protect_runtime_file", observe_empty)
    write_runtime_config(output, runtime_config(), windows_identity_sid="S-1-5-21-1000")
    assert observed_sizes == [0]
    assert output.stat().st_size > 0


def test_atomic_writer_uses_the_acl_protected_exclusive_file_descriptor(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    output = tmp_path / "agent.json"
    protected_identity: list[tuple[int, int]] = []
    written_identity: list[tuple[int, int]] = []
    real_fdopen = os.fdopen

    def observe_protected(path: Path, windows_identity_sid: str | None = None) -> None:
        status = path.stat()
        protected_identity.append((status.st_dev, status.st_ino))

    def observe_written(descriptor: int, *args: object, **kwargs: object):
        status = os.fstat(descriptor)
        written_identity.append((status.st_dev, status.st_ino))
        return real_fdopen(descriptor, *args, **kwargs)

    monkeypatch.setattr("app.agent_config.protect_runtime_file", observe_protected)
    monkeypatch.setattr("app.agent_config.os.fdopen", observe_written)
    write_runtime_config(output, runtime_config(), windows_identity_sid="S-1-5-21-1000")

    assert written_identity == protected_identity


@pytest.mark.skipif(os.name != "posix", reason="POSIX mode assertion")
def test_posix_runtime_file_is_mode_0600(tmp_path: Path) -> None:
    output = tmp_path / "agent.json"
    write_runtime_config(output, runtime_config())
    assert stat.S_IMODE(output.stat().st_mode) == 0o600


@pytest.mark.skipif(os.name != "nt", reason="Windows ACL assertion")
def test_windows_acl_uses_only_user_and_system_sids(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    output = tmp_path / "empty"
    output.touch()
    calls: list[list[str]] = []

    def fake_run(arguments: list[str], **kwargs: object) -> object:
        calls.append(arguments)
        return object()

    monkeypatch.setattr("app.agent_config.subprocess.run", fake_run)
    protect_runtime_file(output, "S-1-5-21-1000")
    assert calls == [[
        "icacls.exe",
        str(output),
        "/inheritance:r",
        "/grant:r",
        "*S-1-5-21-1000:(F)",
        "*S-1-5-18:(F)",
        "/remove:g",
        "*S-1-5-32-545",
    ]]
