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

    def observe_empty(
        path: Path, windows_identity_sid: str | None = None, descriptor: int | None = None
    ) -> None:
        assert descriptor is not None
        observed_sizes.append(os.fstat(descriptor).st_size)

    monkeypatch.setattr("app.agent_config.protect_runtime_file", observe_empty)
    write_runtime_config(output, runtime_config(), windows_identity_sid="S-1-5-21-1000")
    assert observed_sizes == [0]
    assert output.stat().st_size > 0


def test_optional_windows_service_sid_is_forwarded_to_acl_builder(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    output = tmp_path / "empty"
    output.touch()
    calls: list[tuple[Path, str]] = []
    monkeypatch.setattr("app.agent_config.os.name", "nt")
    monkeypatch.setattr(
        "app.agent_config._protect_windows_runtime_file",
        lambda path, sid, descriptor=None: calls.append((path, sid)),
    )

    protect_runtime_file(output, "S-1-5-80-12345")

    assert calls == [(output, "S-1-5-80-12345")]


def test_atomic_writer_uses_the_acl_protected_exclusive_file_descriptor(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    output = tmp_path / "agent.json"
    protected_identity: list[tuple[int, int]] = []
    written_identity: list[tuple[int, int]] = []
    real_fdopen = os.fdopen

    def observe_protected(
        path: Path, windows_identity_sid: str | None = None, descriptor: int | None = None
    ) -> None:
        assert descriptor is not None
        status = os.fstat(descriptor)
        assert os.path.samestat(status, path.stat())
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
def test_windows_atomic_writer_protects_the_existing_secret_descriptor(tmp_path: Path) -> None:
    from app.config import _owner_only_descriptor

    output = tmp_path / "agent.json"
    write_runtime_config(output, runtime_config())

    descriptor = os.open(output, os.O_RDONLY | getattr(os, "O_BINARY", 0))
    try:
        assert _owner_only_descriptor(descriptor, os.fstat(descriptor))
    finally:
        os.close(descriptor)
    assert load_runtime_config(output) == runtime_config()


@pytest.mark.skipif(os.name != "nt", reason="Windows ACL assertion")
def test_windows_acl_is_protected_and_exactly_current_user_plus_system(tmp_path: Path) -> None:
    import win32api
    import win32con
    import win32security
    import ntsecuritycon

    from app.config import _owner_only_descriptor

    token = win32security.OpenProcessToken(win32api.GetCurrentProcess(), win32con.TOKEN_QUERY)
    current = win32security.GetTokenInformation(token, win32security.TokenUser)[0]
    system = win32security.ConvertStringSidToSid("S-1-5-18")
    parent = tmp_path / "protected-parent"
    parent.mkdir()
    parent_dacl = win32security.ACL()
    parent_dacl.AddAccessAllowedAce(win32security.ACL_REVISION, win32con.GENERIC_ALL, current)
    parent_dacl.AddAccessAllowedAce(win32security.ACL_REVISION, win32con.GENERIC_ALL, system)
    parent_security = win32security.SECURITY_DESCRIPTOR()
    parent_security.SetSecurityDescriptorDacl(True, parent_dacl, False)
    parent_security.SetSecurityDescriptorControl(
        win32security.SE_DACL_PROTECTED, win32security.SE_DACL_PROTECTED
    )
    win32security.SetFileSecurity(str(parent), win32security.DACL_SECURITY_INFORMATION, parent_security)

    output = parent / "empty"
    output.touch()
    protect_runtime_file(output)

    expected = {
        win32security.ConvertSidToStringSid(current),
        win32security.ConvertSidToStringSid(system),
    }
    security = win32security.GetNamedSecurityInfo(
        str(output),
        win32security.SE_FILE_OBJECT,
        win32security.OWNER_SECURITY_INFORMATION | win32security.DACL_SECURITY_INFORMATION,
    )
    owner = win32security.ConvertSidToStringSid(security.GetSecurityDescriptorOwner())
    dacl = security.GetSecurityDescriptorDacl()
    assert owner in expected
    assert dacl is not None and dacl.GetAceCount() == 2
    assert {
        win32security.ConvertSidToStringSid(dacl.GetAce(index)[-1])
        for index in range(dacl.GetAceCount())
    } == expected
    assert all(
        dacl.GetAce(index)[0][0] == win32security.ACCESS_ALLOWED_ACE_TYPE
        and dacl.GetAce(index)[1] == ntsecuritycon.FILE_ALL_ACCESS
        for index in range(dacl.GetAceCount())
    )
    control, _revision = security.GetSecurityDescriptorControl()
    assert control & win32security.SE_DACL_PROTECTED

    descriptor = os.open(output, os.O_RDONLY | getattr(os, "O_BINARY", 0))
    try:
        assert _owner_only_descriptor(descriptor, os.fstat(descriptor))
    finally:
        os.close(descriptor)
