from __future__ import annotations

import base64
import hashlib
import json
import os
import subprocess
from datetime import UTC, datetime, timedelta
from ipaddress import ip_address
from pathlib import Path

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID

import app.agent_runtime as runtime
from app.agent_config import AgentRuntimeConfig, LocalSettings, protect_runtime_file, write_runtime_config
from app.agent_runtime import (
    AgentSupervisor,
    build_backend_environment,
    load_agent_tools,
    main,
    pair_jetson,
    safe_status,
)
from app.config import JetsonConfig, _secure_read, load_jetson_config


PRIVATE_KEY = "AAECAwQFBgcICQoLDA0ODxAREhMUFRYXGBkaGxwdHh8"
PUBLIC_KEY = "A6EHv_POEL4dcN0Y50vAmWfk1jCbpQ1fHdyGZBJVMbg"
PSK = bytes(range(32))
ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture(autouse=True)
def isolate_windows_acl_probe(monkeypatch: pytest.MonkeyPatch) -> None:
    if os.name == "nt":
        monkeypatch.setattr(
            runtime,
            "_validate_protected_runtime_chain",
            lambda _path, _root: None,
            raising=False,
        )


def agent_config(path: Path) -> None:
    write_runtime_config(
        path,
        AgentRuntimeConfig(
            origin="https://petcare.example",
            agent_id="agent_01",
            camera_id="camera_01",
            connector_token="connector-secret",
            private_key=PRIVATE_KEY,
            public_key=PUBLIC_KEY,
            local_settings=LocalSettings(
                database_url="postgresql+psycopg://petcare:db-secret@127.0.0.1:55432/petcare",
                mqtt_profile="local_live",
                mqtt_username="petcare",
                mqtt_password="mqtt-secret",
            ),
        ),
    )


def certificate_pem(address: str = "192.168.50.20") -> str:
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, address)])
    now = datetime.now(UTC)
    certificate = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(days=1))
        .not_valid_after(now + timedelta(days=30))
        .add_extension(x509.BasicConstraints(ca=True, path_length=0), critical=True)
        .add_extension(x509.SubjectAlternativeName([x509.IPAddress(ip_address(address))]), critical=False)
        .sign(key, hashes.SHA256())
    )
    return certificate.public_bytes(serialization.Encoding.PEM).decode("ascii")


def pairing_bundle(path: Path, *, url: str = "https://192.168.50.20:9443", certificate: str | None = None) -> None:
    path.write_text(
        json.dumps(
            {
                "url": url,
                "certificate_pem": certificate or certificate_pem(),
                "psk_base64url": base64.urlsafe_b64encode(PSK).decode("ascii").rstrip("="),
            },
            separators=(",", ":"),
            sort_keys=True,
        ),
        encoding="utf-8",
    )
    protect_runtime_file(path)


def agent_tools(path: Path, *, fixture: bool = False, tamper: str | None = None) -> dict[str, Path]:
    executable_dir = path.parent / "bin"
    executable_dir.mkdir()
    if os.name != "nt":
        path.parent.chmod(0o700)
        executable_dir.chmod(0o700)
    paths = {
        name: (executable_dir / filename).resolve()
        for name, filename in {
            "cloudflared_path": "cloudflared.exe",
            "ffmpeg_path": "ffmpeg.exe",
            "ffprobe_path": "ffprobe.exe",
            "python_path": "python.exe",
            "uv_path": "uv.exe",
        }.items()
    }
    for name, executable in paths.items():
        executable.write_bytes(f"fixture-{name}".encode("ascii"))
        protect_runtime_file(executable)
        if os.name != "nt":
            executable.chmod(0o700)
    payload = {
        "schema_version": 1,
        "manifest_sha256": hashlib.sha256((ROOT / "tools" / "platform-manifest.json").read_bytes()).hexdigest().upper(),
        "platform": "windows-x64",
        "architecture": "x64",
        "fixture": fixture,
        "paths": {name: str(value) for name, value in paths.items()},
        "executable_sha256": {
            name: hashlib.sha256(value.read_bytes()).hexdigest().upper() for name, value in paths.items()
        },
        "versions": {
            "cloudflared_path": "2026.7.2",
            "ffmpeg_path": "8.1.2-22-g94138f6973",
            "ffprobe_path": "8.1.2-22-g94138f6973",
            "python_path": "3.12.13+20260623",
            "uv_path": "0.11.28",
        },
    }
    path.write_text(json.dumps(payload, separators=(",", ":"), sort_keys=True), encoding="utf-8")
    protect_runtime_file(path)
    if tamper is not None:
        paths[tamper].write_bytes(b"tampered")
    return paths


class FakeProcess:
    def __init__(self, returncode: int | None) -> None:
        self.returncode = returncode
        self.terminated = False
        self.killed = False

    def poll(self) -> int | None:
        return self.returncode

    def terminate(self) -> None:
        self.terminated = True
        if self.returncode is None:
            self.returncode = -15

    def wait(self, timeout: float) -> int:
        assert timeout > 0
        if self.returncode is None:
            raise subprocess.TimeoutExpired("fixture", timeout)
        return self.returncode

    def kill(self) -> None:
        self.killed = True
        self.returncode = -9


def test_pair_jetson_imports_private_files_checks_status_and_preserves_source_bundle(tmp_path: Path) -> None:
    config_path = tmp_path / "agent.json"
    bundle_path = tmp_path / "pairing.json"
    jetson_path = tmp_path / "jetson.json"
    agent_config(config_path)
    pairing_bundle(bundle_path)
    checked: list[JetsonConfig] = []

    imported = pair_jetson(
        config_path,
        bundle_path,
        jetson_path,
        home_ip_for=lambda _host: "192.168.50.10",
        status_check=lambda config: checked.append(config),
    )

    assert checked == [imported]
    assert bundle_path.exists()
    assert imported == load_jetson_config(jetson_path)
    assert json.loads(_secure_read(jetson_path, owner_only=True)) == {
        "ca_cert_path": str(tmp_path / "jetson.crt"),
        "home_ip": "192.168.50.10",
        "psk_path": str(tmp_path / "jetson.psk"),
        "url": "https://192.168.50.20:9443",
    }
    assert _secure_read(tmp_path / "jetson.crt", owner_only=True) == certificate_pem_from_file(imported)
    assert _secure_read(tmp_path / "jetson.psk", owner_only=True) == PSK


def test_pair_jetson_accepts_same_tailscale_network_pair(tmp_path: Path) -> None:
    config_path = tmp_path / "agent.json"
    bundle_path = tmp_path / "pairing.json"
    jetson_path = tmp_path / "jetson.json"
    agent_config(config_path)
    pairing_bundle(
        bundle_path,
        url="https://100.64.0.10:9443",
        certificate=certificate_pem("100.64.0.10"),
    )

    imported = pair_jetson(
        config_path,
        bundle_path,
        jetson_path,
        home_ip_for=lambda _host: "100.64.0.11",
        status_check=lambda _config: None,
    )

    assert imported.url == "https://100.64.0.10:9443"
    assert imported.home_ip == "100.64.0.11"
    assert imported == load_jetson_config(jetson_path)


def certificate_pem_from_file(config: JetsonConfig) -> bytes:
    return config.ca_pem


def test_pair_jetson_never_overwrites_and_keeps_bundle_on_failure(tmp_path: Path) -> None:
    config_path = tmp_path / "agent.json"
    bundle_path = tmp_path / "pairing.json"
    jetson_path = tmp_path / "jetson.json"
    agent_config(config_path)
    pairing_bundle(bundle_path)
    (tmp_path / "jetson.crt").write_bytes(b"existing")

    with pytest.raises(FileExistsError):
        pair_jetson(
            config_path,
            bundle_path,
            jetson_path,
            home_ip_for=lambda _host: "192.168.50.10",
            status_check=lambda _config: None,
        )

    assert (tmp_path / "jetson.crt").read_bytes() == b"existing"
    assert bundle_path.exists() and not jetson_path.exists() and not (tmp_path / "jetson.psk").exists()


def test_pair_jetson_rolls_back_created_files_when_signed_status_fails(tmp_path: Path) -> None:
    config_path = tmp_path / "agent.json"
    bundle_path = tmp_path / "pairing.json"
    jetson_path = tmp_path / "jetson.json"
    agent_config(config_path)
    pairing_bundle(bundle_path)

    def fail_status(_config: JetsonConfig) -> None:
        raise RuntimeError("offline")

    with pytest.raises(RuntimeError, match="offline"):
        pair_jetson(
            config_path,
            bundle_path,
            jetson_path,
            home_ip_for=lambda _host: "192.168.50.10",
            status_check=fail_status,
        )

    assert bundle_path.exists()
    assert not any((tmp_path / name).exists() for name in ("jetson.crt", "jetson.psk", "jetson.json"))


@pytest.mark.parametrize(
    ("url", "certificate"),
    [
        ("http://192.168.50.20:9443", None),
        ("https://8.8.8.8:9443", None),
        ("https://192.168.50.20:9443", certificate_pem("192.168.50.21")),
    ],
)
def test_pair_jetson_rejects_unsafe_url_or_wrong_ip_san(
    tmp_path: Path, url: str, certificate: str | None
) -> None:
    config_path = tmp_path / "agent.json"
    bundle_path = tmp_path / "pairing.json"
    agent_config(config_path)
    pairing_bundle(bundle_path, url=url, certificate=certificate)
    with pytest.raises(ValueError):
        pair_jetson(
            config_path,
            bundle_path,
            tmp_path / "jetson.json",
            home_ip_for=lambda _host: "192.168.50.10",
            status_check=lambda _config: pytest.fail("status must not run"),
        )


def test_agent_tools_binds_versions_to_platform_authority_and_rejects_permissive_chain(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    tools_path = tmp_path / "agent-tools.json"
    paths = agent_tools(tools_path)
    payload = json.loads(tools_path.read_text(encoding="utf-8"))
    payload["versions"]["cloudflared_path"] = "wrong-version"
    tools_path.write_text(json.dumps(payload), encoding="utf-8")
    protect_runtime_file(tools_path)

    with pytest.raises(ValueError, match="agent tools"):
        load_agent_tools(tools_path)

    payload["versions"]["cloudflared_path"] = "2026.7.2"
    tools_path.write_text(json.dumps(payload), encoding="utf-8")
    protect_runtime_file(tools_path)
    checked: list[tuple[Path, Path]] = []

    def reject_ffprobe(path: Path, root: Path) -> None:
        checked.append((path, root))
        if path == paths["ffprobe_path"]:
            raise PermissionError("agent tools chain is writable")

    monkeypatch.setattr("app.agent_runtime._validate_protected_runtime_chain", reject_ffprobe)

    with pytest.raises((PermissionError, ValueError), match="agent tools"):
        load_agent_tools(tools_path)
    assert (paths["ffprobe_path"], tools_path.parent) in checked


def test_supervisor_rechecks_tool_identity_immediately_before_each_launch(tmp_path: Path) -> None:
    config_path = tmp_path / "agent.json"
    bundle_path = tmp_path / "pairing.json"
    jetson_path = tmp_path / "jetson.json"
    tools_path = tmp_path / "agent-tools.json"
    agent_config(config_path)
    pairing_bundle(bundle_path)
    pair_jetson(
        config_path,
        bundle_path,
        jetson_path,
        home_ip_for=lambda _host: "192.168.50.10",
        status_check=lambda _config: None,
    )
    paths = agent_tools(tools_path)
    backend = FakeProcess(None)
    launches = 0

    def popen(_command: list[str], **_kwargs: object) -> FakeProcess:
        nonlocal launches
        launches += 1
        paths["cloudflared_path"].write_bytes(b"replaced-after-validation")
        return backend

    with pytest.raises(ValueError, match="agent tools"):
        AgentSupervisor(config_path, tools_path, jetson_path, popen=popen).run()

    assert launches == 1
    assert backend.terminated


def test_supervisor_uses_exact_commands_scrubbed_environments_and_jetson_authority(tmp_path: Path) -> None:
    config_path = tmp_path / "agent.json"
    bundle_path = tmp_path / "pairing.json"
    jetson_path = tmp_path / "jetson.json"
    tools_path = tmp_path / "agent-tools.json"
    agent_config(config_path)
    tools = agent_tools(tools_path)
    pairing_bundle(bundle_path)
    pair_jetson(
        config_path,
        bundle_path,
        jetson_path,
        home_ip_for=lambda _host: "192.168.50.10",
        status_check=lambda _config: None,
    )
    calls: list[tuple[list[str], dict[str, object]]] = []
    processes = [FakeProcess(0), FakeProcess(None)]

    def popen(command: list[str], **kwargs: object) -> FakeProcess:
        calls.append((command, kwargs))
        return processes[len(calls) - 1]

    result = AgentSupervisor(
        config_path,
        tools_path,
        jetson_path,
        popen=popen,
        parent_environment={
            "SYSTEMROOT": r"C:\Windows",
            "PATH": r"C:\safe",
            "UNRELATED_SECRET": "must-not-pass",
            "PETCARE_JETSON_CONFIG": r"C:\wrong.json",
        },
        poll_interval=0.001,
    ).run()

    assert result == 1
    assert processes[1].terminated
    assert calls[0][0] == [
        str(tools["python_path"]), "-m", "uvicorn", "app.main:app",
        "--host", "127.0.0.1", "--port", "8000", "--no-access-log",
    ]
    token_path = config_path.with_name("connector-token")
    assert calls[1][0] == [
        str(tools["cloudflared_path"]), "tunnel", "--metrics", "127.0.0.1:20241",
        "run", "--token-file", str(token_path),
    ]
    backend_environment = calls[0][1]["env"]
    assert backend_environment == build_backend_environment(
        config_path,
        jetson_path,
        parent_environment={"SYSTEMROOT": r"C:\Windows", "PATH": r"C:\safe"},
        tools_path=tools_path,
    )
    assert backend_environment["PETCARE_CAMERA_SOURCE"] == "jetson"
    assert backend_environment["PETCARE_JETSON_CONFIG"] == str(jetson_path.resolve())
    assert backend_environment["PETCARE_AGENT_TOOLS"] == str(tools_path.resolve())
    assert calls[1][1]["env"] == {"PATH": r"C:\safe", "SYSTEMROOT": r"C:\Windows"}
    assert _secure_read(token_path, owner_only=True) == b"connector-secret"
    rendered = repr(calls)
    assert "connector-secret" not in rendered and "UNRELATED_SECRET" not in rendered


def test_supervisor_runs_without_jetson_and_disables_only_the_camera(tmp_path: Path) -> None:
    config_path = tmp_path / "agent.json"
    tools_path = tmp_path / "agent-tools.json"
    agent_config(config_path)
    tools = agent_tools(tools_path)
    calls: list[tuple[list[str], dict[str, object]]] = []
    processes = [FakeProcess(0), FakeProcess(None)]

    def popen(command: list[str], **kwargs: object) -> FakeProcess:
        calls.append((command, kwargs))
        return processes[len(calls) - 1]

    result = AgentSupervisor(
        config_path,
        tools_path,
        None,
        popen=popen,
        parent_environment={"SYSTEMROOT": r"C:\Windows", "PATH": r"C:\safe"},
        poll_interval=0.001,
    ).run()

    assert result == 1
    assert calls[0][0][0] == str(tools["python_path"])
    backend_environment = calls[0][1]["env"]
    assert backend_environment["PETCARE_CAMERA_SOURCE"] == "disabled"
    assert "PETCARE_JETSON_CONFIG" not in backend_environment
    assert backend_environment["PETCARE_MQTT_PROFILE"] == "local_live"
    assert processes[1].terminated


@pytest.mark.parametrize(("fixture", "tamper"), [(True, None), (False, "ffprobe_path")])
def test_supervisor_refuses_fixture_or_tampered_tools_before_launch(
    tmp_path: Path, fixture: bool, tamper: str | None
) -> None:
    config_path = tmp_path / "agent.json"
    bundle_path = tmp_path / "pairing.json"
    jetson_path = tmp_path / "jetson.json"
    tools_path = tmp_path / "agent-tools.json"
    agent_config(config_path)
    pairing_bundle(bundle_path)
    pair_jetson(
        config_path,
        bundle_path,
        jetson_path,
        home_ip_for=lambda _host: "192.168.50.10",
        status_check=lambda _config: None,
    )
    agent_tools(tools_path, fixture=fixture, tamper=tamper)

    with pytest.raises(ValueError, match="agent tools"):
        AgentSupervisor(
            config_path,
            tools_path,
            jetson_path,
            popen=lambda *_args, **_kwargs: pytest.fail("child must not launch"),
        ).run()


def test_supervisor_stops_first_child_if_second_launch_fails(tmp_path: Path) -> None:
    config_path = tmp_path / "agent.json"
    bundle_path = tmp_path / "pairing.json"
    jetson_path = tmp_path / "jetson.json"
    tools_path = tmp_path / "agent-tools.json"
    agent_config(config_path)
    pairing_bundle(bundle_path)
    pair_jetson(
        config_path,
        bundle_path,
        jetson_path,
        home_ip_for=lambda _host: "192.168.50.10",
        status_check=lambda _config: None,
    )
    agent_tools(tools_path)
    backend = FakeProcess(None)
    launches = 0

    def popen(_command: list[str], **_kwargs: object) -> FakeProcess:
        nonlocal launches
        launches += 1
        if launches == 2:
            raise OSError("cloudflared failed")
        return backend

    with pytest.raises(OSError, match="cloudflared failed"):
        AgentSupervisor(config_path, tools_path, jetson_path, popen=popen).run()

    assert backend.terminated


def test_supervisor_preserves_monitor_error_and_best_effort_cleans_every_child(tmp_path: Path) -> None:
    config_path = tmp_path / "agent.json"
    bundle_path = tmp_path / "pairing.json"
    jetson_path = tmp_path / "jetson.json"
    tools_path = tmp_path / "agent-tools.json"
    agent_config(config_path)
    pairing_bundle(bundle_path)
    pair_jetson(
        config_path,
        bundle_path,
        jetson_path,
        home_ip_for=lambda _host: "192.168.50.10",
        status_check=lambda _config: None,
    )
    agent_tools(tools_path)

    class BrokenProcess(FakeProcess):
        def __init__(self, *, poll_error: bool = False, terminate_error: bool = False) -> None:
            super().__init__(None)
            self.poll_error = poll_error
            self.terminate_error = terminate_error
            self.terminate_attempted = False

        def poll(self) -> int | None:
            if self.poll_error:
                raise RuntimeError("first-monitor-error")
            return super().poll()

        def terminate(self) -> None:
            self.terminate_attempted = True
            if self.terminate_error:
                raise RuntimeError("cleanup-error")
            super().terminate()

    first = BrokenProcess(poll_error=True, terminate_error=True)
    second = BrokenProcess()
    processes = iter((first, second))

    with pytest.raises(RuntimeError, match="first-monitor-error"):
        AgentSupervisor(
            config_path,
            tools_path,
            jetson_path,
            popen=lambda *_args, **_kwargs: next(processes),
        ).run()

    assert first.terminate_attempted
    assert second.terminate_attempted


@pytest.mark.parametrize(("poll_interval", "stop_timeout"), [(0, 1), (-1, 1), (1, 0), (1, -1)])
def test_supervisor_rejects_nonpositive_timing(poll_interval: float, stop_timeout: float) -> None:
    with pytest.raises(ValueError, match="positive"):
        AgentSupervisor(
            Path("C:/agent.json"),
            Path("C:/tools.json"),
            Path("C:/jetson.json"),
            poll_interval=poll_interval,
            stop_timeout=stop_timeout,
        )


def test_status_reads_only_restricted_fresh_live_safe_snapshot(tmp_path: Path) -> None:
    config_path = tmp_path / "agent.json"
    bundle_path = tmp_path / "pairing.json"
    jetson_path = tmp_path / "jetson.json"
    tools_path = tmp_path / "agent-tools.json"
    agent_config(config_path)
    pairing_bundle(bundle_path)
    pair_jetson(
        config_path,
        bundle_path,
        jetson_path,
        home_ip_for=lambda _host: "192.168.50.10",
        status_check=lambda _config: None,
    )
    agent_tools(tools_path)
    now = datetime(2026, 7, 22, 4, 0, tzinfo=UTC)
    snapshot = runtime._default_status_snapshot(now)
    runtime._write_status_file(config_path, pid=321, updated_at=now, snapshot=snapshot)

    assert safe_status(
        config_path,
        tools_path,
        jetson_path,
        now=lambda: now + timedelta(seconds=1),
        pid_alive=lambda pid: pid == 321,
    ) == snapshot
    assert _secure_read(runtime._status_path(config_path), owner_only=True)
    assert set(snapshot) == {
        "status", "started_at", "camera", "rule_worker", "clip_recorder", "last_successful_upload_at"
    }
    assert "secret" not in json.dumps(snapshot).lower()


@pytest.mark.parametrize(("age", "alive"), [(timedelta(seconds=30), True), (timedelta(), False)])
def test_status_rejects_stale_or_stopped_process(
    tmp_path: Path, age: timedelta, alive: bool
) -> None:
    config_path = tmp_path / "agent.json"
    bundle_path = tmp_path / "pairing.json"
    jetson_path = tmp_path / "jetson.json"
    tools_path = tmp_path / "agent-tools.json"
    agent_config(config_path)
    pairing_bundle(bundle_path)
    pair_jetson(
        config_path,
        bundle_path,
        jetson_path,
        home_ip_for=lambda _host: "192.168.50.10",
        status_check=lambda _config: None,
    )
    agent_tools(tools_path)
    now = datetime(2026, 7, 22, 4, 0, tzinfo=UTC)
    runtime._write_status_file(
        config_path,
        pid=321,
        updated_at=now,
        snapshot=runtime._default_status_snapshot(now),
    )

    with pytest.raises(runtime.RuntimeStatusError):
        safe_status(
            config_path,
            tools_path,
            jetson_path,
            now=lambda: now + age,
            pid_alive=lambda _pid: alive,
        )


def test_status_cli_prints_safe_schema_and_returns_nonzero_when_unavailable(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "app.agent_runtime.safe_status",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(runtime.RuntimeStatusError("unavailable")),
    )

    result = main([
        "status", "--config", str(tmp_path / "agent.json"),
        "--tools", str(tmp_path / "agent-tools.json"),
        "--jetson-config", str(tmp_path / "jetson.json"),
    ])

    assert result == 1
    output = json.loads(capsys.readouterr().out)
    assert set(output) == {
        "status", "started_at", "camera", "rule_worker", "clip_recorder", "last_successful_upload_at"
    }
    assert str(tmp_path) not in json.dumps(output)


def test_status_and_cli_output_are_secret_free(tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch) -> None:
    config_path = tmp_path / "agent.json"
    bundle_path = tmp_path / "pairing.json"
    jetson_path = tmp_path / "jetson.json"
    tools_path = tmp_path / "agent-tools.json"
    agent_config(config_path)
    agent_tools(tools_path)
    pairing_bundle(bundle_path)
    pair_jetson(
        config_path,
        bundle_path,
        jetson_path,
        home_ip_for=lambda _host: "192.168.50.10",
        status_check=lambda _config: None,
    )
    runtime._write_status_file(
        config_path,
        pid=os.getpid(),
        updated_at=datetime.now(UTC),
        snapshot=runtime._default_status_snapshot(datetime.now(UTC)),
    )
    rendered = json.dumps(safe_status(config_path, tools_path, jetson_path), sort_keys=True)
    assert all(secret not in rendered for secret in ("connector-secret", "db-secret", "mqtt-secret", PRIVATE_KEY))
    assert str(jetson_path) not in rendered

    called: list[tuple[Path, Path, Path]] = []
    monkeypatch.setattr(
        "app.agent_runtime.pair_jetson",
        lambda config, bundle, jetson_config: called.append((config, bundle, jetson_config)),
    )
    assert main([
        "pair-jetson", "--config", str(config_path), "--bundle", str(tmp_path / "new-bundle.json"),
        "--jetson-config", str(tmp_path / "new-jetson.json"),
    ]) == 0
    assert called == [(config_path, tmp_path / "new-bundle.json", tmp_path / "new-jetson.json")]
    output = capsys.readouterr().out
    assert output.strip() == '{"paired":true}'
    assert "secret" not in output.lower()

    run_calls: list[tuple[Path, Path, Path]] = []
    monkeypatch.setattr(
        "app.agent_runtime.run_agent",
        lambda config, tools, jetson_config: run_calls.append((config, tools, jetson_config)) or 0,
    )
    assert main([
        "run", "--config", str(config_path), "--tools", str(tools_path),
        "--jetson-config", str(jetson_path),
    ]) == 0
    assert run_calls == [(config_path, tools_path, jetson_path)]


@pytest.mark.skipif(os.name != "nt", reason="Windows-specific process probing")
def test_pid_alive_does_not_signal_the_process_on_windows(monkeypatch: pytest.MonkeyPatch) -> None:
    def fail_if_signaled(_pid: int, _signal: int) -> None:
        raise AssertionError("Windows process probing must not call os.kill")

    monkeypatch.setattr(runtime.os, "kill", fail_if_signaled)

    assert runtime._pid_alive(os.getpid()) is True


@pytest.mark.skipif(os.name != "nt", reason="Windows-specific process probing")
def test_pid_alive_returns_false_when_the_windows_probe_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    import pywintypes
    import win32api

    def fail_probe(_access: int, _inherit: bool, _pid: int) -> None:
        raise pywintypes.error(87, "OpenProcess", "The parameter is incorrect.")

    monkeypatch.setattr(win32api, "OpenProcess", fail_probe)

    assert runtime._pid_alive(2_147_483_647) is False
