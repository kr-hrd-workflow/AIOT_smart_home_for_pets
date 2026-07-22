from __future__ import annotations

import asyncio
import importlib
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import FastAPI


main_module = importlib.import_module("app.main")


def _agent_environment(monkeypatch: pytest.MonkeyPatch) -> tuple[Path, Path]:
    config_path = Path("C:/petcare/runtime/agent.json")
    tools_path = Path("C:/petcare/runtime/agent-tools.json")
    monkeypatch.setenv("PETCARE_AGENT_CONFIG", str(config_path))
    monkeypatch.setenv("PETCARE_AGENT_TOOLS", str(tools_path))
    monkeypatch.setenv("PETCARE_CAMERA_SOURCE", "jetson")
    monkeypatch.setenv("PETCARE_JETSON_CONFIG", "C:/petcare/runtime/jetson.json")
    return config_path, tools_path


def _install_lifespan_fakes(
    monkeypatch: pytest.MonkeyPatch,
    calls: list[str],
    *,
    failures: set[str] = frozenset(),
) -> object:
    components = object()

    def call(name: str) -> None:
        calls.append(name)
        if name in failures:
            raise RuntimeError(f"{name} failed")

    class Ingress:
        def __init__(self, _clock: object) -> None:
            pass

        def stop_accepting(self) -> None:
            call("rule_ingress.stop_accepting")

    class Mqtt:
        @classmethod
        def disabled(cls) -> "Mqtt":
            return cls()

        def start(self) -> None:
            call("mqtt.start")

        def stop(self) -> None:
            call("mqtt.stop")

    class Camera:
        pipeline = object()

        def start(self) -> None:
            call("camera.start")

        def shutdown(self) -> None:
            call("camera.shutdown")

    class Worker:
        def __init__(self, **_kwargs: object) -> None:
            pass

        def start(self) -> None:
            call("rule_worker.start")

        def shutdown(self) -> None:
            call("rule_worker.shutdown")

    class Hub:
        def __init__(self) -> None:
            self._closed = asyncio.Event()

        def start_broadcaster(self) -> asyncio.Task[None]:
            return asyncio.create_task(self._closed.wait())

        def shutdown(self) -> None:
            self._closed.set()

        def publish_from_worker(self, _message: object) -> None:
            pass

    monkeypatch.setattr(
        main_module,
        "load_config",
        lambda: SimpleNamespace(
            database_url="postgresql+psycopg://petcare:x@127.0.0.1:55432/petcare",
            mqtt_enabled=False,
            camera_source="jetson",
            jetson_config=object(),
        ),
    )
    monkeypatch.setattr(main_module, "configure_database", lambda _url: call("database.configure"))
    monkeypatch.setattr(main_module, "dispose_database", lambda: call("dispose_database"))
    monkeypatch.setattr(main_module, "SystemRuleClock", object)
    monkeypatch.setattr(main_module, "RuleIngress", Ingress)
    monkeypatch.setattr(main_module, "MqttIngestor", Mqtt)
    monkeypatch.setattr(main_module, "build_camera_service", lambda *_args: Camera())
    monkeypatch.setattr(main_module, "RuleEngine", lambda **_kwargs: SimpleNamespace())
    monkeypatch.setattr(main_module, "RuleWorker", Worker)
    monkeypatch.setattr(main_module, "DashboardHub", Hub)

    def build(config_path: Path, tools_path: Path, _session_factory: object) -> object:
        assert config_path == Path("C:/petcare/runtime/agent.json")
        assert tools_path == Path("C:/petcare/runtime/agent-tools.json")
        call("build_agent_components")
        return components

    monkeypatch.setattr(main_module, "build_agent_components", build, raising=False)
    monkeypatch.setattr(
        main_module,
        "start_agent_components",
        lambda actual: (actual is components) and call("start_agent_components"),
        raising=False,
    )

    def stop(actual: object, *, timeout_seconds: float) -> None:
        assert actual is components and timeout_seconds == 105.0
        call("stop_agent_components")

    monkeypatch.setattr(main_module, "stop_agent_components", stop, raising=False)
    return components


def test_task7_exact_lifecycle_exports_are_importable() -> None:
    lifecycle = importlib.import_module("app.agent_lifecycle")

    assert lifecycle.AgentLifecycleComponents.__dataclass_fields__.keys() == {
        "jetson_client",
        "clip_admission",
        "clip_delivery",
        "upload_queue",
        "started_at",
    }
    assert callable(lifecycle.build_agent_components)
    assert callable(lifecycle.start_agent_components)
    assert callable(lifecycle.stop_agent_components)


@pytest.mark.asyncio
async def test_agent_mode_attaches_components_starts_before_intake_and_preserves_api(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []
    _agent_environment(monkeypatch)
    components = _install_lifespan_fakes(monkeypatch, calls)
    application = FastAPI()

    async with main_module.lifespan(application):
        assert application.state.agent_components is components
        calls.append("yield")

    assert calls.index("start_agent_components") < calls.index("rule_worker.start")
    assert calls.index("start_agent_components") < calls.index("mqtt.start")
    assert calls.index("start_agent_components") < calls.index("camera.start")
    assert calls[calls.index("yield") + 1 :] == [
        "rule_ingress.stop_accepting",
        "mqtt.stop",
        "rule_worker.shutdown",
        "camera.shutdown",
        "stop_agent_components",
        "dispose_database",
    ]

    paths = {route.path for route in main_module.app.routes}
    assert paths == {
        "/api/health",
        "/api/dashboard/summary",
        "/api/devices",
        "/api/sensors/latest",
        "/api/behaviors",
        "/api/anomalies",
        "/api/camera/status",
        "/api/video_feed",
        "/api/bed/status",
        "/api/bed/calibration",
        "/api/zones",
        "/api/zones/{zone_name}",
        "/ws/dashboard",
    }
    assert not hasattr(main_module, "AgentSupervisor")


@pytest.mark.asyncio
async def test_partial_agent_configuration_fails_before_background_start(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []
    _agent_environment(monkeypatch)
    monkeypatch.delenv("PETCARE_AGENT_TOOLS")
    _install_lifespan_fakes(monkeypatch, calls)

    with pytest.raises(ValueError):
        async with main_module.lifespan(FastAPI()):
            pass

    assert not {
        "start_agent_components",
        "rule_worker.start",
        "mqtt.start",
        "camera.start",
    }.intersection(calls)


@pytest.mark.asyncio
async def test_shutdown_attempts_later_cleanup_and_preserves_first_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []
    _agent_environment(monkeypatch)
    _install_lifespan_fakes(
        monkeypatch,
        calls,
        failures={"rule_ingress.stop_accepting", "rule_worker.shutdown", "stop_agent_components"},
    )

    with pytest.raises(RuntimeError, match=r"rule_ingress\.stop_accepting failed"):
        async with main_module.lifespan(FastAPI()):
            calls.append("yield")

    assert calls[calls.index("yield") + 1 :] == [
        "rule_ingress.stop_accepting",
        "mqtt.stop",
        "rule_worker.shutdown",
        "camera.shutdown",
        "stop_agent_components",
        "dispose_database",
    ]
