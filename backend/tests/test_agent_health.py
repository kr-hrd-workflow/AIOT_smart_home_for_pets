from __future__ import annotations

import json
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

from app.agent_health import AgentHealthSnapshot, agent_health_snapshot
from app.agent_lifecycle import AgentLifecycleComponents, start_agent_components, stop_agent_components


NOW = datetime(2026, 7, 20, 4, tzinfo=UTC)


class Jetson:
    def __init__(self, status: object | BaseException) -> None:
        self._status = status

    def calibrate_clock(self) -> None:
        pass

    def status(self) -> object:
        if isinstance(self._status, BaseException):
            raise self._status
        return self._status

    def close(self) -> None:
        pass


class Worker:
    def start(self) -> None:
        pass

    def stop(self, *, timeout_seconds: float) -> None:
        pass


class Queue(Worker):
    depth = 3


def components(jetson: Jetson) -> AgentLifecycleComponents:
    return AgentLifecycleComponents(jetson, Worker(), Worker(), Queue(), NOW)


def test_snapshot_has_only_bounded_operational_fields() -> None:
    value = components(Jetson(SimpleNamespace(
        camera_state="online",
        boot_id="a" * 32,
        temperature_c=47.5,
        throttled=False,
    )))
    start_agent_components(value)

    payload = agent_health_snapshot(value, clip_delivery_queue_depth=2).to_dict()

    assert payload == {
        "status": "healthy",
        "started_at": "2026-07-20T04:00:00.000000Z",
        "jetson": {
            "camera": "online",
            "boot": "a" * 32,
            "temperature": 47.5,
            "throttle": False,
        },
        "clip_delivery": {"state": "running", "queue_depth": 2},
        "upload_queue": {"queue_depth": 3},
        "last_error": None,
    }
    assert set(AgentHealthSnapshot.__dataclass_fields__) == {
        "started_at",
        "jetson_camera",
        "jetson_boot",
        "jetson_temperature",
        "jetson_throttle",
        "clip_delivery_state",
        "clip_delivery_queue_depth",
        "upload_queue_depth",
        "last_error",
    }


def test_faults_degrade_without_serializing_secret_material() -> None:
    value = components(Jetson(RuntimeError(
        "https://192.168.1.20:9443 cert=C:/private/jetson.crt psk=token database_url=secret mqtt_password=secret clip_path=C:/clip.mp4"
    )))
    start_agent_components(value)

    payload = agent_health_snapshot(
        value,
        clip_delivery_queue_depth=8,
        last_error="https://192.168.1.20/private/clip.mp4?token=secret",
    ).to_dict()
    serialized = json.dumps(payload).lower()

    assert payload["status"] == "degraded"
    assert payload["jetson"] == {
        "camera": "offline",
        "boot": None,
        "temperature": None,
        "throttle": None,
    }
    assert payload["last_error"] == "agent_degraded"
    assert not any(secret in serialized for secret in (
        "https://", "192.168.", "psk", "certificate", ".crt", "token",
        "private_key", "database_url", "mqtt_password", "clip_path", ".mp4",
    ))
    stop_agent_components(value)
    assert agent_health_snapshot(value).to_dict()["clip_delivery"]["state"] == "stopped"


def test_snapshot_rejects_unvalidated_operational_values() -> None:
    with pytest.raises(ValueError, match="invalid health snapshot"):
        AgentHealthSnapshot(
            started_at=NOW,
            jetson_camera="https://192.168.1.20",  # type: ignore[arg-type]
            jetson_boot="C:/private/jetson.crt",
            jetson_temperature=47.5,
            jetson_throttle=False,
            clip_delivery_state="running",
            clip_delivery_queue_depth=0,
            upload_queue_depth=0,
        )
