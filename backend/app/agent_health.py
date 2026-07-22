from __future__ import annotations

import math
import re
from dataclasses import dataclass
from datetime import datetime
from typing import Literal

from .clip_contracts import utc_text


_SAFE_ERRORS = frozenset((
    "agent_degraded",
    "clock_uncertain",
    "delivery_failed",
    "jetson_unavailable",
    "queue_full",
))


def _safe_error(value: str | BaseException | None) -> str | None:
    if value is None:
        return None
    text = str(value)
    return text if text in _SAFE_ERRORS else "agent_degraded"


@dataclass(frozen=True, slots=True)
class AgentHealthSnapshot:
    started_at: datetime
    jetson_camera: Literal["online", "offline"]
    jetson_boot: str | None
    jetson_temperature: float | None
    jetson_throttle: bool | None
    clip_delivery_state: Literal["running", "stopped"]
    clip_delivery_queue_depth: int
    upload_queue_depth: int
    last_error: str | None = None

    def __post_init__(self) -> None:
        utc_text(self.started_at)
        if (
            self.jetson_camera not in ("online", "offline")
            or (self.jetson_boot is not None and re.fullmatch(r"[0-9a-f]{32}", self.jetson_boot) is None)
            or (
                self.jetson_temperature is not None
                and (type(self.jetson_temperature) is not float or not math.isfinite(self.jetson_temperature))
            )
            or (self.jetson_throttle is not None and type(self.jetson_throttle) is not bool)
            or self.clip_delivery_state not in ("running", "stopped")
            or type(self.clip_delivery_queue_depth) is not int
            or self.clip_delivery_queue_depth < 0
            or type(self.upload_queue_depth) is not int
            or self.upload_queue_depth < 0
        ):
            raise ValueError("invalid health snapshot")
        object.__setattr__(self, "last_error", _safe_error(self.last_error))

    @property
    def status(self) -> Literal["healthy", "degraded"]:
        healthy = (
            self.jetson_camera == "online"
            and self.jetson_boot is not None
            and self.jetson_temperature is not None
            and self.jetson_throttle is False
            and self.clip_delivery_state == "running"
            and self.clip_delivery_queue_depth < 8
            and self.upload_queue_depth < 8
            and self.last_error is None
        )
        return "healthy" if healthy else "degraded"

    def to_dict(self) -> dict[str, object]:
        return {
            "status": self.status,
            "started_at": utc_text(self.started_at),
            "jetson": {
                "camera": self.jetson_camera,
                "boot": self.jetson_boot,
                "temperature": self.jetson_temperature,
                "throttle": self.jetson_throttle,
            },
            "clip_delivery": {
                "state": self.clip_delivery_state,
                "queue_depth": self.clip_delivery_queue_depth,
            },
            "upload_queue": {"queue_depth": self.upload_queue_depth},
            "last_error": self.last_error,
        }


def agent_health_snapshot(
    components: object,
    *,
    clip_delivery_queue_depth: int = 0,
    last_error: str | BaseException | None = None,
) -> AgentHealthSnapshot:
    from .agent_lifecycle import _component_health_state

    delivery_state, lifecycle_error = _component_health_state(components)  # type: ignore[arg-type]
    error = _safe_error(last_error) or lifecycle_error
    try:
        jetson = components.jetson_client.status()  # type: ignore[attr-defined]
        camera = jetson.camera_state
        boot = jetson.boot_id
        temperature = jetson.temperature_c
        throttle = jetson.throttled
    except Exception:
        camera, boot, temperature, throttle = "offline", None, None, None
        error = error or "jetson_unavailable"
    try:
        upload_depth = components.upload_queue.depth  # type: ignore[attr-defined]
    except Exception:
        upload_depth = 0
        error = error or "queue_full"
    return AgentHealthSnapshot(
        started_at=components.started_at,  # type: ignore[attr-defined]
        jetson_camera=camera,
        jetson_boot=boot,
        jetson_temperature=temperature,
        jetson_throttle=throttle,
        clip_delivery_state=delivery_state,
        clip_delivery_queue_depth=clip_delivery_queue_depth,
        upload_queue_depth=upload_depth,
        last_error=error,
    )
