from __future__ import annotations

import argparse
import base64
import getpass
import hashlib
import json
import math
import os
import socket
import stat
import subprocess
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from ipaddress import IPv4Address
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from cryptography import x509
from cryptography.hazmat.primitives import serialization
from cryptography.x509 import BasicConstraints, SubjectAlternativeName

from .agent_client import enroll
from .agent_config import AgentRuntimeConfig, LocalSettings, _current_windows_sid, protect_runtime_file
from .config import JetsonConfig, _private_transport_pair, _secure_read, load_jetson_config
from .jetson_client import JetsonVisionClient


SAFE_PARENT_ENVIRONMENT = ("COMSPEC", "PATH", "PATHEXT", "SYSTEMROOT", "TEMP", "TMP", "WINDIR")
TOOL_NAMES = frozenset({"cloudflared_path", "ffmpeg_path", "ffprobe_path", "python_path", "uv_path"})
STATUS_MAX_AGE = timedelta(seconds=5)


@dataclass(frozen=True, slots=True)
class AgentTools:
    cloudflared_path: Path
    ffmpeg_path: Path
    ffprobe_path: Path
    python_path: Path
    uv_path: Path


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, item in pairs:
        if key in value:
            raise ValueError("duplicate JSON key")
        value[key] = item
    return value


def _strict_object(content: bytes, expected: set[str]) -> dict[str, Any]:
    try:
        value = json.loads(
            content.decode("utf-8", errors="strict"),
            object_pairs_hook=_unique_object,
            parse_constant=lambda _value: (_ for _ in ()).throw(ValueError("nonfinite JSON")),
        )
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError, TypeError, RecursionError) as error:
        raise ValueError("invalid JSON file") from error
    if type(value) is not dict or set(value) != expected:
        raise ValueError("invalid JSON shape")
    return value


def _expected_tool_versions(platform_manifest: Mapping[str, Any]) -> dict[str, str]:
    managed = platform_manifest["managed_exact"]
    return {
        "cloudflared_path": managed["cloudflared"]["version"],
        "ffmpeg_path": managed["ffmpeg"]["version"],
        "ffprobe_path": managed["ffmpeg"]["version"],
        "python_path": f'{managed["python"]["version"]}+{managed["python"]["build"]}',
        "uv_path": managed["uv"]["version"],
    }


def _validate_windows_owner_acl(path: Path) -> None:
    import win32con
    import win32security

    current = win32security.ConvertStringSidToSid(_current_windows_sid())
    system = win32security.ConvertStringSidToSid("S-1-5-18")
    administrators = win32security.ConvertStringSidToSid("S-1-5-32-544")
    allowed = {
        win32security.ConvertSidToStringSid(value)
        for value in (current, system, administrators)
    }
    security = win32security.GetNamedSecurityInfo(
        str(path),
        win32security.SE_FILE_OBJECT,
        win32security.OWNER_SECURITY_INFORMATION | win32security.DACL_SECURITY_INFORMATION,
    )
    owner = win32security.ConvertSidToStringSid(security.GetSecurityDescriptorOwner())
    dacl = security.GetSecurityDescriptorDacl()
    control, _revision = security.GetSecurityDescriptorControl()
    if owner not in allowed or dacl is None or not control & win32security.SE_DACL_PROTECTED:
        raise PermissionError("agent tools ACL is invalid")
    for index in range(dacl.GetAceCount()):
        ace = dacl.GetAce(index)
        if (
            ace[0][0] != win32security.ACCESS_ALLOWED_ACE_TYPE
            or win32security.ConvertSidToStringSid(ace[-1]) not in allowed
        ):
            raise PermissionError("agent tools ACL is invalid")


def _validate_protected_runtime_chain(path: Path, root: Path) -> None:
    path = Path(os.path.abspath(path))
    root = Path(os.path.abspath(root))
    try:
        path.relative_to(root)
    except ValueError:
        raise PermissionError("agent tools path is outside runtime root") from None
    current = path
    while True:
        details = current.lstat()
        reparse = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
        if stat.S_ISLNK(details.st_mode) or getattr(details, "st_file_attributes", 0) & reparse:
            raise PermissionError("agent tools path is indirect")
        if os.name == "nt":
            _validate_windows_owner_acl(current)
        elif details.st_uid not in {os.getuid(), 0} or stat.S_IMODE(details.st_mode) & 0o022:
            raise PermissionError("agent tools path is writable")
        if current == root:
            return
        current = current.parent


def load_agent_tools(path: Path, *, platform_manifest_path: Path | None = None) -> AgentTools:
    path = Path(path)
    if not path.is_absolute():
        raise ValueError("agent tools path must be absolute")
    try:
        payload = _strict_object(
            _secure_read(path, owner_only=True),
            {
                "schema_version", "manifest_sha256", "platform", "architecture", "fixture",
                "paths", "executable_sha256", "versions",
            },
        )
        manifest_path = platform_manifest_path or Path(__file__).resolve().parents[2] / "tools" / "platform-manifest.json"
        manifest_content = _secure_read(Path(manifest_path), owner_only=False)
        expected_manifest_hash = hashlib.sha256(manifest_content).hexdigest().upper()
        platform_manifest = json.loads(manifest_content.decode("utf-8", errors="strict"))
        expected_versions = _expected_tool_versions(platform_manifest)
        paths = payload["paths"]
        hashes = payload["executable_sha256"]
        versions = payload["versions"]
        if (
            payload["schema_version"] != 1
            or type(payload["fixture"]) is not bool
            or payload["fixture"]
            or type(payload["platform"]) is not str
            or not payload["platform"]
            or type(payload["architecture"]) is not str
            or not payload["architecture"]
            or (os.name == "nt" and (payload["platform"], payload["architecture"]) != ("windows-x64", "x64"))
            or (os.name == "posix" and (payload["platform"], payload["architecture"]) != ("linux", "arm64"))
            or payload["manifest_sha256"] != expected_manifest_hash
            or type(paths) is not dict
            or set(paths) != TOOL_NAMES
            or type(hashes) is not dict
            or set(hashes) != TOOL_NAMES
            or type(versions) is not dict
            or set(versions) != TOOL_NAMES
            or versions != expected_versions
        ):
            raise ValueError("invalid agent tools manifest")
        runtime_root = path.parent
        _validate_protected_runtime_chain(path, runtime_root)
        resolved: dict[str, Path] = {}
        for name in TOOL_NAMES:
            value = paths[name]
            digest = hashes[name]
            if type(value) is not str or type(digest) is not str or type(versions[name]) is not str:
                raise ValueError("invalid agent tools manifest")
            executable = Path(value)
            if not executable.is_absolute() or not executable.is_file():
                raise ValueError("invalid agent tools manifest")
            _validate_protected_runtime_chain(executable, runtime_root)
            actual = hashlib.sha256(_secure_read(executable, owner_only=True)).hexdigest().upper()
            if digest != actual:
                raise ValueError("invalid agent tools executable hash")
            resolved[name] = executable
        return AgentTools(**resolved)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError, KeyError, TypeError, ValueError):
        raise ValueError("invalid agent tools manifest") from None


def _load_agent_config(path: Path) -> AgentRuntimeConfig:
    path = Path(path)
    if not path.is_absolute():
        raise ValueError("agent config path must be absolute")
    return AgentRuntimeConfig.model_validate(
        _strict_object(
            _secure_read(path, owner_only=True),
            {
                "origin", "agent_id", "camera_id", "connector_token", "private_key", "public_key",
                "local_camera_id", "local_settings",
            },
        )
    )


def _home_ip_for(jetson_host: str) -> str:
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as route:
        route.connect((jetson_host, 9443))
        return str(route.getsockname()[0])


def _decode_psk(value: object) -> bytes:
    if type(value) is not str or len(value) != 43 or "=" in value:
        raise ValueError("invalid pairing bundle")
    try:
        decoded = base64.b64decode(value + "=", altchars=b"-_", validate=True)
    except (ValueError, base64.binascii.Error) as error:
        raise ValueError("invalid pairing bundle") from error
    if len(decoded) != 32 or base64.urlsafe_b64encode(decoded).decode("ascii").rstrip("=") != value:
        raise ValueError("invalid pairing bundle")
    return decoded


def _validate_pairing_bundle(payload: dict[str, Any], home_ip_for: Callable[[str], str]) -> tuple[str, str, bytes]:
    url = payload["url"]
    certificate_pem = payload["certificate_pem"]
    if type(url) is not str or type(certificate_pem) is not str:
        raise ValueError("invalid pairing bundle")
    parsed = urlsplit(url)
    try:
        jetson_ip = IPv4Address(parsed.hostname or "")
        home_ip = IPv4Address(home_ip_for(str(jetson_ip)))
    except (OSError, ValueError) as error:
        raise ValueError("invalid pairing network") from error
    if (
        parsed.scheme != "https"
        or parsed.port != 9443
        or parsed.path not in ("", "/")
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
        or url.endswith("/")
        or not _private_transport_pair(jetson_ip, home_ip)
    ):
        raise ValueError("invalid pairing network")
    try:
        certificate = x509.load_pem_x509_certificate(certificate_pem.encode("ascii"))
        sans = certificate.extensions.get_extension_for_class(SubjectAlternativeName).value
        addresses = sans.get_values_for_type(x509.IPAddress)
        constraints = certificate.extensions.get_extension_for_class(BasicConstraints).value
        now = datetime.now(UTC)
    except (UnicodeEncodeError, ValueError, x509.ExtensionNotFound) as error:
        raise ValueError("invalid pairing certificate") from error
    if (
        addresses != [jetson_ip]
        or not constraints.ca
        or now < certificate.not_valid_before_utc
        or now > certificate.not_valid_after_utc
        or certificate.public_bytes(serialization.Encoding.PEM) != certificate_pem.encode("ascii")
    ):
        raise ValueError("invalid pairing certificate")
    return str(home_ip), certificate_pem, _decode_psk(payload["psk_base64url"])


def _write_private_file(path: Path, content: bytes) -> None:
    descriptor: int | None = None
    created = False
    try:
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0), 0o600)
        created = True
        protect_runtime_file(path, descriptor=descriptor)
        with os.fdopen(descriptor, "wb") as output:
            descriptor = None
            output.write(content)
            output.flush()
            os.fsync(output.fileno())
    except BaseException:
        if descriptor is not None:
            os.close(descriptor)
        if created:
            Path(path).unlink(missing_ok=True)
        raise


def _write_config_no_overwrite(path: Path, payload: dict[str, str]) -> None:
    temporary = path.with_name(f".{path.name}.{os.urandom(8).hex()}.new")
    try:
        _write_private_file(
            temporary,
            json.dumps(payload, ensure_ascii=False, separators=(",", ":"), sort_keys=True).encode("utf-8"),
        )
        os.link(temporary, path)
        temporary.unlink()
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise


def _verify_signed_status(config: JetsonConfig) -> None:
    client = JetsonVisionClient(config)
    try:
        client.status()
    finally:
        client.close()


def pair_jetson(
    config_path: Path,
    bundle_path: Path,
    jetson_config_path: Path,
    *,
    home_ip_for: Callable[[str], str] = _home_ip_for,
    status_check: Callable[[JetsonConfig], None] = _verify_signed_status,
) -> JetsonConfig:
    _load_agent_config(Path(config_path))
    bundle_path = Path(bundle_path)
    jetson_config_path = Path(jetson_config_path)
    if not bundle_path.is_absolute() or not jetson_config_path.is_absolute():
        raise ValueError("pairing paths must be absolute")
    payload = _strict_object(
        _secure_read(bundle_path, owner_only=True),
        {"url", "certificate_pem", "psk_base64url"},
    )
    home_ip, certificate_pem, psk = _validate_pairing_bundle(payload, home_ip_for)
    ca_path = jetson_config_path.with_name("jetson.crt")
    psk_path = jetson_config_path.with_name("jetson.psk")
    if any(path.exists() for path in (ca_path, psk_path, jetson_config_path)):
        raise FileExistsError("Jetson runtime already exists")
    created: list[Path] = []
    try:
        _write_private_file(ca_path, certificate_pem.encode("ascii"))
        created.append(ca_path)
        _write_private_file(psk_path, psk)
        created.append(psk_path)
        _write_config_no_overwrite(jetson_config_path, {
            "url": payload["url"],
            "home_ip": home_ip,
            "ca_cert_path": str(ca_path),
            "psk_path": str(psk_path),
        })
        created.append(jetson_config_path)
        imported = load_jetson_config(jetson_config_path)
        status_check(imported)
        return imported
    except BaseException:
        for path in reversed(created):
            path.unlink(missing_ok=True)
        raise


def build_backend_environment(
    config_path: Path,
    jetson_config_path: Path | None,
    *,
    tools_path: Path,
    parent_environment: Mapping[str, str] | None = None,
) -> dict[str, str]:
    config_path = Path(config_path).resolve()
    jetson_config_path = Path(jetson_config_path).resolve() if jetson_config_path is not None else None
    tools_path = Path(tools_path).resolve()
    config = _load_agent_config(config_path)
    if jetson_config_path is not None:
        load_jetson_config(jetson_config_path)
    parent = os.environ if parent_environment is None else parent_environment
    environment = {name: parent[name] for name in SAFE_PARENT_ENVIRONMENT if name in parent}
    environment.update({
        "DATABASE_URL": config.local_settings.database_url.get_secret_value(),
        "PETCARE_AGENT_CONFIG": str(config_path),
        "PETCARE_AGENT_TOOLS": str(tools_path),
        "PETCARE_CAMERA_SOURCE": "jetson" if jetson_config_path is not None else "disabled",
        "PETCARE_MQTT_PASSWORD": config.local_settings.mqtt_password.get_secret_value(),
        "PETCARE_MQTT_PROFILE": config.local_settings.mqtt_profile,
        "PETCARE_MQTT_USERNAME": config.local_settings.mqtt_username,
    })
    if jetson_config_path is not None:
        environment["PETCARE_JETSON_CONFIG"] = str(jetson_config_path)
    return environment


def _safe_parent_environment(parent: Mapping[str, str]) -> dict[str, str]:
    return {name: parent[name] for name in SAFE_PARENT_ENVIRONMENT if name in parent}


def _write_connector_token(path: Path, token: str) -> None:
    temporary = path.with_name(f".{path.name}.{os.urandom(8).hex()}.new")
    try:
        _write_private_file(temporary, token.encode("utf-8"))
        os.replace(temporary, path)
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise


class RuntimeStatusError(RuntimeError):
    pass


def _timestamp(value: datetime) -> str:
    if value.tzinfo is None:
        raise ValueError("status timestamp must be aware")
    return value.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def _default_status_snapshot(started_at: datetime) -> dict[str, Any]:
    return {
        "camera": {"last_frame_at": None, "reason": "not_started", "state": "offline"},
        "clip_recorder": {
            "active": False,
            "last_error": None,
            "queue_depth": 0,
            "trigger_dispatcher_running": True,
            "writer_queue_depth": 0,
        },
        "last_successful_upload_at": None,
        "rule_worker": {"last_error": None, "running": True},
        "started_at": _timestamp(started_at),
        "status": "degraded",
    }


def _validate_status_snapshot(snapshot: object) -> dict[str, Any]:
    if type(snapshot) is not dict or set(snapshot) != {
        "status", "started_at", "camera", "rule_worker", "clip_recorder", "last_successful_upload_at"
    }:
        raise RuntimeStatusError("unavailable")
    camera = snapshot["camera"]
    rule_worker = snapshot["rule_worker"]
    recorder = snapshot["clip_recorder"]
    if (
        snapshot["status"] not in {"healthy", "degraded"}
        or type(snapshot["started_at"]) is not str
        or snapshot["last_successful_upload_at"] is not None
        and type(snapshot["last_successful_upload_at"]) is not str
        or type(camera) is not dict
        or set(camera) != {"last_frame_at", "reason", "state"}
        or camera["state"] not in {"online", "offline"}
        or (camera["last_frame_at"] is not None and type(camera["last_frame_at"]) is not str)
        or (camera["reason"] is not None and type(camera["reason"]) is not str)
        or type(rule_worker) is not dict
        or set(rule_worker) != {"last_error", "running"}
        or type(rule_worker["running"]) is not bool
        or (rule_worker["last_error"] is not None and type(rule_worker["last_error"]) is not str)
        or type(recorder) is not dict
        or set(recorder) != {
            "active", "last_error", "queue_depth", "trigger_dispatcher_running", "writer_queue_depth"
        }
        or type(recorder["active"]) is not bool
        or type(recorder["trigger_dispatcher_running"]) is not bool
        or type(recorder["queue_depth"]) is not int
        or type(recorder["writer_queue_depth"]) is not int
        or recorder["queue_depth"] < 0
        or recorder["writer_queue_depth"] < 0
        or (recorder["last_error"] is not None and type(recorder["last_error"]) is not str)
    ):
        raise RuntimeStatusError("unavailable")
    return snapshot


def _status_path(config_path: Path) -> Path:
    return Path(config_path).with_name("agent-status.json")


def _write_status_file(
    config_path: Path,
    *,
    pid: int,
    updated_at: datetime,
    snapshot: dict[str, Any],
) -> None:
    _validate_status_snapshot(snapshot)
    if type(pid) is not int or pid <= 0:
        raise ValueError("invalid status pid")
    path = _status_path(config_path)
    temporary = path.with_name(f".{path.name}.{os.urandom(8).hex()}.new")
    payload = {
        "pid": pid,
        "snapshot": snapshot,
        "updated_at": _timestamp(updated_at),
    }
    try:
        _write_private_file(
            temporary,
            json.dumps(payload, ensure_ascii=False, separators=(",", ":"), sort_keys=True).encode("utf-8"),
        )
        os.replace(temporary, path)
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise


def _pid_alive(pid: int) -> bool:
    if type(pid) is not int or pid <= 0:
        return False
    if os.name == "nt":
        import pywintypes
        import win32api
        import win32con
        import win32process

        try:
            process = win32api.OpenProcess(win32con.PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
        except (OSError, pywintypes.error):
            return False
        try:
            return win32process.GetExitCodeProcess(process) == win32con.STILL_ACTIVE
        except (OSError, pywintypes.error):
            return False
        finally:
            process.Close()
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


class AgentSupervisor:
    def __init__(
        self,
        config_path: Path,
        tools_path: Path,
        jetson_config_path: Path | None,
        *,
        popen: Callable[..., Any] = subprocess.Popen,
        parent_environment: Mapping[str, str] | None = None,
        poll_interval: float = 0.25,
        stop_timeout: float = 5.0,
    ) -> None:
        self.config_path = Path(config_path).resolve()
        self.tools_path = Path(tools_path).resolve()
        self.jetson_config_path = (
            Path(jetson_config_path).resolve() if jetson_config_path is not None else None
        )
        self._popen = popen
        self._parent_environment = os.environ if parent_environment is None else parent_environment
        if (
            type(poll_interval) not in {int, float}
            or type(stop_timeout) not in {int, float}
            or not math.isfinite(poll_interval)
            or not math.isfinite(stop_timeout)
            or poll_interval <= 0
            or stop_timeout <= 0
        ):
            raise ValueError("supervisor timing must be positive and finite")
        self._poll_interval = poll_interval
        self._stop_timeout = stop_timeout

    def _stop(self, process: Any) -> None:
        first_error: BaseException | None = None
        running = True
        try:
            running = process.poll() is None
        except BaseException as error:
            first_error = error
        if running:
            try:
                process.terminate()
            except BaseException as error:
                first_error = first_error or error
        try:
            process.wait(timeout=self._stop_timeout)
        except BaseException as error:
            first_error = first_error or error
            try:
                process.kill()
            except BaseException as kill_error:
                first_error = first_error or kill_error
            try:
                process.wait(timeout=self._stop_timeout)
            except BaseException as wait_error:
                first_error = first_error or wait_error
        if first_error is not None:
            raise first_error

    def run(self, stop_event: Any | None = None) -> int:
        from threading import Event

        processes: list[Any] = []
        status_path = _status_path(self.config_path)
        primary_error = False
        outcome: int | None = None
        preserve_outcome = False
        try:
            config = _load_agent_config(self.config_path)
            tools = load_agent_tools(self.tools_path)
            if self.jetson_config_path is not None:
                load_jetson_config(self.jetson_config_path)
            token_path = self.config_path.with_name("connector-token")
            _write_connector_token(token_path, config.connector_token.get_secret_value())
            backend_environment = build_backend_environment(
                self.config_path,
                self.jetson_config_path,
                tools_path=self.tools_path,
                parent_environment=self._parent_environment,
            )
            tunnel_environment = _safe_parent_environment(self._parent_environment)
            commands = [
                [
                    str(tools.python_path), "-m", "uvicorn", "app.main:app",
                    "--host", "127.0.0.1", "--port", "8000", "--no-access-log",
                ],
                [
                    str(tools.cloudflared_path), "tunnel", "--metrics", "127.0.0.1:20241",
                    "run", "--token-file", str(token_path),
                ],
            ]
            environments = [backend_environment, tunnel_environment]
            launch_options: dict[str, object]
            if os.name == "nt":
                launch_options = {"creationflags": getattr(subprocess, "CREATE_NO_WINDOW", 0)}
            else:
                launch_options = {"start_new_session": False}
            executable_names = ("python_path", "cloudflared_path")
            for command, environment, executable_name in zip(
                commands, environments, executable_names, strict=True
            ):
                current_tools = load_agent_tools(self.tools_path)
                if str(getattr(current_tools, executable_name)) != command[0]:
                    raise ValueError("invalid agent tools manifest")
                processes.append(self._popen(command, env=environment, **launch_options))
            started_at = datetime.now(UTC)
            snapshot = _default_status_snapshot(started_at)
            _write_status_file(
                self.config_path,
                pid=os.getpid(),
                updated_at=started_at,
                snapshot=snapshot,
            )
            event = stop_event or Event()
            while True:
                _write_status_file(
                    self.config_path,
                    pid=os.getpid(),
                    updated_at=datetime.now(UTC),
                    snapshot=snapshot,
                )
                for process in processes:
                    code = process.poll()
                    if code is not None:
                        outcome = int(code) if code else 1
                        preserve_outcome = True
                        return outcome
                if event.wait(self._poll_interval):
                    outcome = 0
                    return outcome
        except BaseException:
            primary_error = True
            raise
        finally:
            cleanup_error: BaseException | None = None
            for process in reversed(processes):
                try:
                    self._stop(process)
                except BaseException as error:
                    cleanup_error = cleanup_error or error
            try:
                status_path.unlink(missing_ok=True)
            except BaseException as error:
                cleanup_error = cleanup_error or error
            if cleanup_error is not None and not primary_error and not preserve_outcome:
                raise cleanup_error


def run_agent(config_path: Path, tools_path: Path, jetson_config_path: Path | None) -> int:
    return AgentSupervisor(config_path, tools_path, jetson_config_path).run()


def _parse_timestamp(value: object) -> datetime:
    if type(value) is not str or len(value) != 27 or not value.endswith("Z"):
        raise RuntimeStatusError("unavailable")
    try:
        parsed = datetime.strptime(value, "%Y-%m-%dT%H:%M:%S.%fZ").replace(tzinfo=UTC)
    except ValueError:
        raise RuntimeStatusError("unavailable") from None
    if _timestamp(parsed) != value:
        raise RuntimeStatusError("unavailable")
    return parsed


def safe_status(
    config_path: Path,
    tools_path: Path,
    jetson_config_path: Path | None,
    *,
    now: Callable[[], datetime] = lambda: datetime.now(UTC),
    pid_alive: Callable[[int], bool] = _pid_alive,
) -> dict[str, Any]:
    try:
        config_path = Path(config_path).resolve()
        _load_agent_config(config_path)
        load_agent_tools(Path(tools_path).resolve())
        if jetson_config_path is not None:
            load_jetson_config(Path(jetson_config_path).resolve())
        envelope = _strict_object(
            _secure_read(_status_path(config_path), owner_only=True),
            {"pid", "snapshot", "updated_at"},
        )
        pid = envelope["pid"]
        updated_at = _parse_timestamp(envelope["updated_at"])
        current = now()
        if (
            type(pid) is not int
            or pid <= 0
            or current.tzinfo is None
            or current < updated_at
            or current - updated_at > STATUS_MAX_AGE
            or not pid_alive(pid)
        ):
            raise RuntimeStatusError("unavailable")
        return _validate_status_snapshot(envelope["snapshot"])
    except RuntimeStatusError:
        raise
    except BaseException:
        raise RuntimeStatusError("unavailable") from None


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m app.agent_runtime")
    commands = parser.add_subparsers(dest="command", required=True)
    pair = commands.add_parser("pair-jetson")
    pair.add_argument("--config", type=Path, required=True)
    pair.add_argument("--bundle", type=Path, required=True)
    pair.add_argument("--jetson-config", type=Path, required=True)
    run = commands.add_parser("run")
    run.add_argument("--config", type=Path, required=True)
    run.add_argument("--tools", type=Path, required=True)
    run.add_argument("--jetson-config", type=Path)
    status = commands.add_parser("status")
    status.add_argument("--config", type=Path, required=True)
    status.add_argument("--tools", type=Path, required=True)
    status.add_argument("--jetson-config", type=Path)
    enroll_parser = commands.add_parser("enroll")
    enroll_parser.add_argument("--origin", required=True)
    enroll_parser.add_argument("--config", type=Path, required=True)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    arguments = _parser().parse_args(argv)
    if arguments.command == "pair-jetson":
        pair_jetson(arguments.config, arguments.bundle, arguments.jetson_config)
        print('{"paired":true}')
        return 0
    if arguments.command == "run":
        return run_agent(arguments.config, arguments.tools, arguments.jetson_config)
    if arguments.command == "status":
        try:
            snapshot = safe_status(arguments.config, arguments.tools, arguments.jetson_config)
            exit_code = 0
        except RuntimeStatusError:
            snapshot = _default_status_snapshot(datetime.now(UTC))
            snapshot["camera"]["reason"] = "agent_unavailable"
            snapshot["rule_worker"]["running"] = False
            snapshot["clip_recorder"]["trigger_dispatcher_running"] = False
            exit_code = 1
        print(json.dumps(
            snapshot,
            separators=(",", ":"),
            sort_keys=True,
        ))
        return exit_code
    config = enroll(
        arguments.origin,
        getpass.getpass("Enrollment code: "),
        LocalSettings(
            database_url=os.environ["DATABASE_URL"],
            mqtt_profile=os.environ["PETCARE_MQTT_PROFILE"],
            mqtt_username=os.environ["PETCARE_MQTT_USERNAME"],
            mqtt_password=os.environ["PETCARE_MQTT_PASSWORD"],
        ),
        arguments.config,
    )
    print(json.dumps({"agent_id": config.agent_id, "camera_id": config.camera_id}, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
