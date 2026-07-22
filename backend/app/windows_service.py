from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from threading import Event
from typing import Any

import servicemanager
import win32service
import win32serviceutil

from .agent_runtime import AgentSupervisor


REGISTRY_PATH = r"SOFTWARE\PetCare\HomeAgent"


@dataclass(frozen=True, slots=True)
class ServicePaths:
    config_path: Path
    tools_path: Path
    jetson_config_path: Path

    def __post_init__(self) -> None:
        if not all(path.is_absolute() for path in (self.config_path, self.tools_path, self.jetson_config_path)):
            raise ValueError("service paths must be absolute")


def read_service_paths(*, registry: Any = None) -> ServicePaths:
    if registry is None:
        import winreg as registry

    expected = {"ConfigPath", "ToolsPath", "JetsonConfigPath"}
    with registry.OpenKey(registry.HKEY_LOCAL_MACHINE, REGISTRY_PATH) as key:
        names: set[str] = set()
        index = 0
        while True:
            try:
                name, _value, value_type = registry.EnumValue(key, index)
            except OSError:
                break
            if type(name) is not str or value_type != registry.REG_SZ:
                raise ValueError("invalid service registry surface")
            names.add(name)
            index += 1
        if names != expected:
            raise ValueError("invalid service registry surface")
        values: dict[str, str] = {}
        for name in expected:
            value, value_type = registry.QueryValueEx(key, name)
            if value_type != registry.REG_SZ:
                raise ValueError("invalid service registry surface")
            values[name] = value
    if any(type(value) is not str or not value for value in values.values()):
        raise ValueError("invalid service registry path")
    return ServicePaths(
        Path(values["ConfigPath"]), Path(values["ToolsPath"]), Path(values["JetsonConfigPath"])
    )


def safe_service_status(paths: ServicePaths, *, running: bool) -> dict[str, bool]:
    return {"configured": True, "jetson": True, "running": bool(running)}


class PetCareHomeAgentService(win32serviceutil.ServiceFramework):
    _svc_name_ = "PetCareHomeAgent"
    _svc_display_name_ = "PetCare Home Agent"
    _svc_description_ = "Runs the loopback PetCare backend and outbound Cloudflare Tunnel."

    def __init__(self, args: list[str]) -> None:
        super().__init__(args)
        self._stop_event = Event()

    def SvcStop(self) -> None:
        self.ReportServiceStatus(win32service.SERVICE_STOP_PENDING)
        self._stop_event.set()

    def SvcDoRun(self) -> None:
        try:
            paths = read_service_paths()
            AgentSupervisor(paths.config_path, paths.tools_path, paths.jetson_config_path).run(self._stop_event)
        except BaseException:
            servicemanager.LogErrorMsg("PetCare Home Agent supervisor failed")


def main() -> None:
    if os.name != "nt":
        raise SystemExit("PetCare Home Agent Windows service requires Windows")
    win32serviceutil.HandleCommandLine(PetCareHomeAgentService)


if __name__ == "__main__":
    main()
