from __future__ import annotations

import base64
import inspect
import json
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

import pytest

import app.agent_lifecycle as lifecycle
from app.agent_lifecycle import (
    AgentLifecycleComponents,
    build_agent_components,
    start_agent_components,
    stop_agent_components,
)


NOW = datetime(2026, 7, 20, 4, tzinfo=UTC)


def test_build_composes_concrete_dependencies_without_starting_background_work(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    calls: list[tuple[object, ...]] = []
    config_path = tmp_path / "agent.json"
    tools_path = tmp_path / "agent-tools.json"
    ffprobe_path = tmp_path / "ffprobe.exe"
    ffprobe_path.touch()
    tools_path.write_text(json.dumps({"ffprobe_path": str(ffprobe_path.resolve())}), encoding="utf-8")
    private_key = base64.urlsafe_b64encode(bytes(range(32))).decode("ascii").rstrip("=")
    runtime = SimpleNamespace(
        origin="https://petcare.example",
        agent_id="agent-1",
        camera_id="camera-1",
        private_key=SimpleNamespace(get_secret_value=lambda: private_key),
    )
    jetson_config = object()
    app_config = SimpleNamespace(camera_source="jetson", jetson_config=jetson_config)
    session_factory = object()
    upload_client = object()
    jetson = object()
    repository = object()
    queue = object()
    admission = object()
    delivery = object()

    monkeypatch.setattr(lifecycle, "load_runtime_config", lambda path: calls.append(("config", path)) or runtime)
    monkeypatch.setattr(lifecycle, "load_config", lambda: calls.append(("app-config",)) or app_config)
    monkeypatch.setattr(lifecycle, "SignedClipUploadClient", lambda **kwargs: calls.append(("upload-client", kwargs)) or upload_client)
    monkeypatch.setattr(lifecycle, "JetsonVisionClient", lambda config: calls.append(("jetson", config)) or jetson)
    monkeypatch.setattr(lifecycle, "SqlAlchemyClipOutboxRepository", lambda factory: calls.append(("repository", factory)) or repository)

    class UploadQueue:
        @classmethod
        def open(cls, root: Path, client: object, *, now: object) -> object:
            calls.append(("queue", root, client, now))
            return queue

    monkeypatch.setattr(lifecycle, "ClipUploadQueue", UploadQueue)
    monkeypatch.setattr(
        lifecycle,
        "ClipAdmissionWorker",
        lambda repo, client, *, now: calls.append(("admission", repo, client, now)) or admission,
    )
    monkeypatch.setattr(
        lifecycle,
        "ClipDeliveryWorker",
        lambda repo, client, received_queue, **kwargs: calls.append(
            ("delivery", repo, client, received_queue, kwargs)
        ) or delivery,
    )

    result = build_agent_components(config_path, tools_path, session_factory, now=lambda: NOW)

    assert result == AgentLifecycleComponents(jetson, admission, delivery, queue, NOW)
    assert calls[0] == ("config", config_path)
    assert ("app-config",) in calls
    assert ("jetson", jetson_config) in calls
    assert ("repository", session_factory) in calls
    assert any(call[:3] == ("queue", config_path.parent / "clip-upload-queue", upload_client) for call in calls)
    delivery_call = next(call for call in calls if call[0] == "delivery")
    assert delivery_call[4]["work_dir"] == config_path.parent / "clip-delivery"
    assert delivery_call[4]["ffprobe_path"] == ffprobe_path.resolve()
    assert not any(call[0] in {"start", "status", "calibrate", "process"} for call in calls)


def test_public_shape_and_start_order_contains_jetson_handshake_failure() -> None:
    calls: list[str] = []

    class Component:
        def __init__(self, name: str) -> None:
            self.name = name

        def start(self) -> None:
            calls.append(f"{self.name}:start")

        def stop(self, *, timeout_seconds: float) -> None:
            calls.append(f"{self.name}:stop:{timeout_seconds}")

    class Jetson:
        def calibrate_clock(self) -> None:
            calls.append("jetson:start")
            raise RuntimeError("jetson unavailable")

        def close(self) -> None:
            calls.append("jetson:close")

    value = AgentLifecycleComponents(Jetson(), Component("admission"), Component("delivery"), Component("upload"), NOW)
    start_agent_components(value)

    assert calls == ["upload:start", "jetson:start", "admission:start", "delivery:start"]
    assert tuple(AgentLifecycleComponents.__dataclass_fields__) == (
        "jetson_client", "clip_admission", "clip_delivery", "upload_queue", "started_at"
    )
    assert lifecycle.__all__ == (
        "AgentLifecycleComponents", "build_agent_components", "start_agent_components", "stop_agent_components"
    )
    assert "fastapi" not in inspect.getsource(lifecycle).lower()
    assert not any(name in inspect.getsource(lifecycle) for name in ("FrameRing", "ClipRecorder", "latest_frame_sink"))


def test_stop_uses_one_deadline_exact_caps_and_preserves_first_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[str, float | None]] = []
    first = RuntimeError("admission failed")

    class Component:
        def __init__(self, name: str, error: BaseException | None = None) -> None:
            self.name = name
            self.error = error

        def start(self) -> None:
            pass

        def stop(self, *, timeout_seconds: float) -> None:
            calls.append((self.name, timeout_seconds))
            if self.error is not None:
                raise self.error

    class Jetson:
        def calibrate_clock(self) -> None:
            pass

        def close(self) -> None:
            calls.append(("jetson", None))

    class ImmediateThread:
        def __init__(self, *, target: object, name: str, daemon: bool) -> None:
            assert name == "petcare-jetson-close" and daemon is True
            self.target = target
            self.join_timeout: float | None = None

        def start(self) -> None:
            self.target()  # type: ignore[operator]

        def join(self, timeout: float) -> None:
            self.join_timeout = timeout
            calls.append(("jetson-cap", timeout))

        def is_alive(self) -> bool:
            return False

    ticks = iter((0.0, 0.0, 5.0, 50.0, 52.0))
    monkeypatch.setattr(lifecycle.time, "monotonic", lambda: next(ticks))
    monkeypatch.setattr(lifecycle.threading, "Thread", ImmediateThread)
    value = AgentLifecycleComponents(
        Jetson(), Component("admission", first), Component("delivery"), Component("upload"), NOW
    )

    with pytest.raises(RuntimeError) as raised:
        stop_agent_components(value, timeout_seconds=105.0)

    assert raised.value is first
    assert calls == [
        ("admission", 5.0),
        ("delivery", 45.0),
        ("jetson", None),
        ("jetson-cap", 2.0),
        ("upload", 45.0),
    ]
    stop_agent_components(value, timeout_seconds=105.0)
    assert len(calls) == 5
