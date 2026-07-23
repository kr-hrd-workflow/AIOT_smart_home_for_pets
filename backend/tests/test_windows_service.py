from __future__ import annotations

import json
from pathlib import Path
from threading import Event

import pytest
import win32service

from app.windows_service import (
    PetCareHomeAgentService,
    ServicePaths,
    read_service_paths,
    safe_service_status,
)


class Registry:
    HKEY_LOCAL_MACHINE = object()
    REG_SZ = 1

    def __init__(self, values: dict[str, str]) -> None:
        self.values = values
        self.opened: list[tuple[object, str]] = []

    def OpenKey(self, hive: object, path: str):
        self.opened.append((hive, path))
        return self

    def QueryValueEx(self, _key: object, name: str) -> tuple[str, int]:
        return self.values[name], 1

    def EnumValue(self, _key: object, index: int) -> tuple[str, str, int]:
        try:
            name = list(self.values)[index]
        except IndexError as error:
            raise OSError("no more values") from error
        return name, self.values[name], self.REG_SZ

    def __enter__(self):
        return self

    def __exit__(self, *_args: object) -> None:
        pass


def test_registry_reads_only_absolute_nonsecret_runtime_paths(tmp_path: Path) -> None:
    agent = (tmp_path / "agent.json").resolve()
    tools = (tmp_path / "agent-tools.json").resolve()
    jetson = (tmp_path / "jetson.json").resolve()
    registry = Registry({"ConfigPath": str(agent), "ToolsPath": str(tools), "JetsonConfigPath": str(jetson)})

    paths = read_service_paths(registry=registry)

    assert paths == ServicePaths(agent, tools, jetson)
    assert registry.opened == [(registry.HKEY_LOCAL_MACHINE, r"SOFTWARE\PetCare\HomeAgent")]
    assert set(registry.values) == {"ConfigPath", "ToolsPath", "JetsonConfigPath"}


@pytest.mark.parametrize("name", ["ConfigPath", "ToolsPath", "JetsonConfigPath"])
def test_registry_rejects_missing_relative_or_extra_values(tmp_path: Path, name: str) -> None:
    values = {
        "ConfigPath": str((tmp_path / "agent.json").resolve()),
        "ToolsPath": str((tmp_path / "agent-tools.json").resolve()),
        "JetsonConfigPath": str((tmp_path / "jetson.json").resolve()),
    }
    values[name] = "relative.json"
    with pytest.raises(ValueError, match="absolute"):
        read_service_paths(registry=Registry(values))


def test_registry_rejects_stale_secret_or_unknown_value(tmp_path: Path) -> None:
    values = {
        "ConfigPath": str((tmp_path / "agent.json").resolve()),
        "ToolsPath": str((tmp_path / "agent-tools.json").resolve()),
        "JetsonConfigPath": str((tmp_path / "jetson.json").resolve()),
        "ConnectorToken": "stale-secret",
    }

    with pytest.raises(ValueError, match="registry"):
        read_service_paths(registry=Registry(values))


def test_service_delegates_once_with_exact_three_runtime_paths(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    agent = (tmp_path / "agent.json").resolve()
    tools = (tmp_path / "agent-tools.json").resolve()
    jetson = (tmp_path / "jetson.json").resolve()
    paths = ServicePaths(agent, tools, jetson)
    constructed: list[tuple[Path, Path, Path]] = []
    run_events: list[Event] = []

    class Supervisor:
        def __init__(self, config: Path, runtime_tools: Path, jetson_config: Path) -> None:
            constructed.append((config, runtime_tools, jetson_config))

        def run(self, stop_event: Event) -> int:
            run_events.append(stop_event)
            return 0

    service = object.__new__(PetCareHomeAgentService)
    service._stop_event = Event()
    monkeypatch.setattr("app.windows_service.read_service_paths", lambda: paths)
    monkeypatch.setattr("app.windows_service._optional_jetson_config_path", lambda path: path)
    monkeypatch.setattr("app.windows_service.AgentSupervisor", Supervisor)

    service.SvcDoRun()

    assert constructed == [(agent, tools, jetson)]
    assert run_events == [service._stop_event]


def test_service_treats_a_missing_reserved_jetson_path_as_optional(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    paths = ServicePaths(
        (tmp_path / "agent.json").resolve(),
        (tmp_path / "agent-tools.json").resolve(),
        (tmp_path / "jetson.json").resolve(),
    )
    constructed: list[tuple[Path, Path, Path | None]] = []

    class Supervisor:
        def __init__(
            self,
            config: Path,
            runtime_tools: Path,
            jetson_config: Path | None,
        ) -> None:
            constructed.append((config, runtime_tools, jetson_config))

        def run(self, _stop_event: Event) -> int:
            return 0

    service = object.__new__(PetCareHomeAgentService)
    service._stop_event = Event()
    monkeypatch.setattr("app.windows_service.read_service_paths", lambda: paths)
    monkeypatch.setattr("app.windows_service.AgentSupervisor", Supervisor)

    service.SvcDoRun()

    assert constructed == [(paths.config_path, paths.tools_path, None)]


def test_service_status_never_exposes_paths_or_secrets(tmp_path: Path) -> None:
    paths = ServicePaths(
        (tmp_path / "agent-secret.json").resolve(),
        (tmp_path / "tools-secret.json").resolve(),
        (tmp_path / "jetson-secret.json").resolve(),
    )
    rendered = json.dumps(safe_service_status(paths, running=True), sort_keys=True)
    assert rendered == '{"configured": true, "jetson": true, "running": true}'
    assert str(tmp_path) not in rendered


def test_service_uses_exact_windows_identity() -> None:
    assert PetCareHomeAgentService._svc_name_ == "PetCareHomeAgent"
    assert PetCareHomeAgentService._svc_display_name_ == "PetCare Home Agent"
    assert PetCareHomeAgentService._svc_description_ == "Runs the loopback PetCare backend and outbound Cloudflare Tunnel."


def test_service_stop_reports_pending_and_sets_event(monkeypatch: pytest.MonkeyPatch) -> None:
    statuses: list[int] = []
    service = object.__new__(PetCareHomeAgentService)
    service._stop_event = Event()
    monkeypatch.setattr(service, "ReportServiceStatus", lambda status: statuses.append(status))

    service.SvcStop()

    assert service._stop_event.is_set()
    assert statuses == [win32service.SERVICE_STOP_PENDING]
