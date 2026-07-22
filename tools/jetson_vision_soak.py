from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import re
import subprocess
import tempfile
import threading
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Callable, Iterable, Mapping, Protocol
from urllib.parse import urlsplit


SCHEMA = "PETCARE-JETSON-SOAK-V1"
BRINGUP_SCHEMA = "PETCARE-JETSON-BRINGUP-V1"
HARNESS_SCHEMA = "PETCARE-JETSON-HARNESS-V1"
SHA = re.compile(r"[0-9a-f]{40}\Z")
SHA256 = re.compile(r"[0-9a-f]{64}\Z")
BOOT_ID = re.compile(r"[0-9a-f]{32}\Z")
COMMAND_ID = re.compile(r"[0-9a-f]{32}\Z")
KERNEL_FAULT = re.compile(r"soctherm|throttl|OC ALARM|under.?voltage|vdd.*fail", re.IGNORECASE)
IPV4_VALUE = re.compile(r"(?<![0-9])(?:[0-9]{1,3}\.){3}[0-9]{1,3}(?![0-9])")
MAC_VALUE = re.compile(r"(?i)(?<![0-9a-f])(?:[0-9a-f]{2}:){5}[0-9a-f]{2}(?![0-9a-f])")
FORBIDDEN_EVIDENCE_NAMES = (
    "header", "url", "ip", "address", "path", "cert", "psk", "secret", "credential",
    "username", "hostname", "mac", "serial", "token", "signature", "request", "body",
)
SOAK_CHECKS = (
    "candidate_sha", "duration", "capture_coverage", "authenticated_collection",
    "inference_fps", "observation_gap", "home_age", "healthy_gap", "clock_calibration",
    "wall_monotonic_guard", "preview", "clips", "event_scenarios", "resources",
    "temperature", "boot_id", "kernel", "locked_clocks", "sensor_independence",
    "temporary_files", "webcam_recovery",
)
SOAK_METRICS = (
    "duration_seconds", "inference_fps_min", "observation_gap_p99_seconds",
    "home_age_p99_seconds", "temperature_max_c",
)
BRINGUP_CHECKS = (
    "board_model", "software_versions", "executable_model_hashes", "camera_format",
    "private_binding", "home_only_firewall", "runtime_imports", "encoder_plugins",
    "service_active", "single_listener", "no_cloud_process",
)
BRINGUP_VERSIONS = ("l4t", "jetpack", "tensorrt", "python", "architecture")
BRINGUP_HASHES = ("source", "python", "model", "engine")
BRINGUP_CAMERA = ("width", "height", "format")
BRINGUP_COUNTS = ("service", "private_listener", "public_listener", "cloud_process")
BRINGUP_IMPORTS = ("tensorrt", "pycuda", "opencv", "numpy", "gstreamer")
BRINGUP_PLUGINS = ("appsrc", "videoconvert", "nvvidconv", "nvv4l2h264enc", "h264parse", "qtmux", "filesink")
ELIGIBLE_EVENT_TYPES = {"eating", "resting", "bed_sensor_mismatch"}
ACCEPTED_EVENT_ROLES = ("default", "overlap_first", "overlap_second", "stall_new_put")
ALL_EVENT_ROLES = (*ACCEPTED_EVENT_ROLES, "deadline_expired")


class JetsonCollectorClient(Protocol):
    def status(self) -> object: ...
    def next_frame(self, zones: Mapping[str, object]) -> object: ...
    def calibrate_clock(self) -> object: ...
    def put_clip(self, command_id: str, command: Mapping[str, object], *, first: bool = True) -> object: ...
    def download_clip(self, command_id: str, destination: Path) -> object: ...
    def delete_clip(self, command_id: str) -> int: ...
    def close(self) -> None: ...


class HomeCollectorClient(Protocol):
    def get(self, target: str) -> object: ...
    def close(self) -> None: ...


def _number(value: object, name: str) -> float:
    if type(value) not in (int, float) or isinstance(value, bool) or not math.isfinite(float(value)):
        raise ValueError(f"invalid {name}")
    return float(value)


def _integer(value: object, name: str) -> int:
    if type(value) is not int:
        raise ValueError(f"invalid {name}")
    return value


def _unique_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    value: dict[str, object] = {}
    for key, item in pairs:
        if key in value:
            raise ValueError("duplicate JSON key")
        value[key] = item
    return value


def _reject_constant(_value: str) -> None:
    raise ValueError("non-finite JSON number")


def _items(value: object, name: str) -> list[Mapping[str, object]]:
    if type(value) is not list or not value or any(type(item) is not dict for item in value):
        raise ValueError(f"invalid {name}")
    return value  # type: ignore[return-value]


def _p99(values: Iterable[float]) -> float:
    ordered = sorted(values)
    if not ordered:
        raise ValueError("p99 requires samples")
    return ordered[max(0, math.ceil(len(ordered) * 0.99) - 1)]


def _ordered_times(items: list[Mapping[str, object]], field: str, name: str) -> list[float]:
    values = [_number(item.get(field), name) for item in items]
    if any(current <= previous for previous, current in zip(values, values[1:])):
        raise ValueError(f"invalid {name} order")
    return values


def _full_coverage(times: list[float], start: float, finish: float, maximum_gap: float) -> bool:
    return (
        bool(times)
        and times[0] <= start + maximum_gap
        and times[-1] >= finish - maximum_gap
        and all(current - previous <= maximum_gap + 1e-9 for previous, current in zip(times, times[1:]))
    )


def _clip_ok(value: Mapping[str, object], kind: str, frames: int, duration: float) -> bool:
    return (
        set(value) == {
            "kind", "frame_count", "duration_seconds", "width", "height", "frame_rate",
            "video_codec", "pixel_format",
        }
        and value.get("kind") == kind
        and value.get("frame_count") == frames
        and abs(_number(value.get("duration_seconds"), "clip duration") - duration) <= 0.1
        and value.get("width") == 640
        and value.get("height") == 480
        and value.get("frame_rate") == "10/1"
        and value.get("video_codec") == "h264"
        and value.get("pixel_format") == "yuv420p"
    )


def _utc(value: object, name: str) -> datetime:
    if type(value) is not str or not value.endswith("Z"):
        raise ValueError(f"invalid {name}")
    try:
        parsed = datetime.fromisoformat(value[:-1] + "+00:00")
    except ValueError as error:
        raise ValueError(f"invalid {name}") from error
    if parsed.tzinfo is None or parsed.utcoffset() != timedelta(0):
        raise ValueError(f"invalid {name}")
    return parsed


def _calibration_map(calibrations: list[Mapping[str, object]]) -> dict[str, Mapping[str, object]]:
    expected = {
        "role", "command_id", "first_put", "measured_monotonic", "put_started_monotonic",
        "age_seconds", "offset_ms", "half_rtt_ms",
    }
    output: dict[str, Mapping[str, object]] = {}
    for item in calibrations:
        role = item.get("role")
        command_id = item.get("command_id")
        measured = _number(item.get("measured_monotonic"), "calibration monotonic")
        put_started = _number(item.get("put_started_monotonic"), "PUT monotonic")
        age = _number(item.get("age_seconds"), "calibration age")
        half_rtt = _number(item.get("half_rtt_ms"), "half RTT")
        if (
            set(item) != expected
            or role not in ALL_EVENT_ROLES
            or type(command_id) is not str
            or COMMAND_ID.fullmatch(command_id) is None
            or item.get("first_put") is not True
            or role in output
        ):
            raise ValueError("invalid first PUT calibration")
        _number(item.get("offset_ms"), "clock offset")
        if age < 0 or half_rtt < 0 or measured > put_started:
            raise ValueError("invalid first PUT calibration")
        output[str(role)] = item
    return output


def _calibration_ok(calibrations: list[Mapping[str, object]]) -> bool:
    by_role = _calibration_map(calibrations)
    return set(by_role) == set(ALL_EVENT_ROLES) and all(
        0 <= _number(item.get("age_seconds"), "calibration age") <= 1.0
        and abs(
            _number(item.get("age_seconds"), "calibration age")
            - (
                _number(item.get("put_started_monotonic"), "PUT monotonic")
                - _number(item.get("measured_monotonic"), "calibration monotonic")
            )
        ) <= 1e-6
        and abs(_number(item.get("offset_ms"), "clock offset"))
        + _number(item.get("half_rtt_ms"), "half RTT") + 50 <= 200
        for item in calibrations
    )


def _scenario_ok(value: object, calibrations: list[Mapping[str, object]]) -> bool:
    if type(value) is not dict or set(value) != {
        "puts", "expired", "replay", "media_stall", "delete_recovery",
    }:
        return False
    calibration_by_role = _calibration_map(calibrations)
    if set(calibration_by_role) != set(ALL_EVENT_ROLES):
        return False

    puts = _items(value["puts"], "scenario PUTs")
    put_fields = {
        "role", "command_id", "event_type", "event_id", "first", "status_code", "committed_at",
        "put_started_wall", "put_finished_wall", "put_started_monotonic", "put_finished_monotonic",
        "accepted_at", "accepted_boot_id",
    }
    if [item.get("role") for item in puts] != list(ACCEPTED_EVENT_ROLES):
        return False
    by_role: dict[str, Mapping[str, object]] = {}
    for item in puts:
        role = str(item.get("role"))
        command_id = item.get("command_id")
        calibration = calibration_by_role[role]
        if (
            set(item) != put_fields
            or type(command_id) is not str
            or COMMAND_ID.fullmatch(command_id) is None
            or item.get("event_type") not in ELIGIBLE_EVENT_TYPES
            or _integer(item.get("event_id"), "event ID") <= 0
            or item.get("first") is not True
            or item.get("status_code") != 201
            or item.get("accepted_boot_id") is None
            or BOOT_ID.fullmatch(str(item.get("accepted_boot_id"))) is None
            or calibration.get("command_id") != command_id
            or calibration.get("put_started_monotonic") != item.get("put_started_monotonic")
        ):
            return False
        started_wall = _utc(item.get("put_started_wall"), "PUT started wall")
        finished_wall = _utc(item.get("put_finished_wall"), "PUT finished wall")
        accepted_at = _utc(item.get("accepted_at"), "accepted at")
        committed_at = _utc(item.get("committed_at"), "committed at")
        started_monotonic = _number(item.get("put_started_monotonic"), "PUT started monotonic")
        finished_monotonic = _number(item.get("put_finished_monotonic"), "PUT finished monotonic")
        offset = timedelta(milliseconds=_number(calibration.get("offset_ms"), "clock offset"))
        uncertainty = timedelta(
            milliseconds=_number(calibration.get("half_rtt_ms"), "half RTT") + 50.0
        )
        if (
            finished_wall < started_wall
            or finished_monotonic < started_monotonic
            or finished_monotonic - started_monotonic > 3.0
            or not started_wall + offset - uncertainty <= accepted_at <= finished_wall + offset + uncertainty
            or not -0.2 <= (accepted_at - committed_at).total_seconds() <= 2.8
        ):
            return False
        by_role[role] = item

    default = by_role["default"]
    if (
        _utc(default["committed_at"], "default committed at")
        <= _utc(default["put_started_wall"], "default PUT wall")
        or _utc(default["accepted_at"], "default accepted at")
        >= _utc(default["committed_at"], "default committed at")
    ):
        return False
    overlap_gap = _number(
        by_role["overlap_second"]["put_started_monotonic"], "overlap second"
    ) - _number(by_role["overlap_first"]["put_started_monotonic"], "overlap first")
    if abs(overlap_gap - 15.0) > 0.1:
        return False

    expired = value["expired"]
    if type(expired) is not dict or set(expired) != {
        "role", "command_id", "event_type", "event_id", "first", "committed_at", "put_started_wall",
        "put_started_monotonic", "put_finished_monotonic", "error_code",
    }:
        return False
    if (
        expired.get("role") != "deadline_expired"
        or type(expired.get("command_id")) is not str
        or COMMAND_ID.fullmatch(str(expired.get("command_id"))) is None
        or expired.get("first") is not True
        or expired.get("error_code") != "command_expired"
        or expired.get("event_type") not in ELIGIBLE_EVENT_TYPES
        or _integer(expired.get("event_id"), "expired event ID") <= 0
        or calibration_by_role["deadline_expired"].get("command_id") != expired.get("command_id")
        or calibration_by_role["deadline_expired"].get("put_started_monotonic")
        != expired.get("put_started_monotonic")
        or (_utc(expired.get("put_started_wall"), "expired PUT wall") - _utc(
            expired.get("committed_at"), "expired committed at"
        )).total_seconds() <= 2.8
    ):
        return False

    replay = value["replay"]
    if type(replay) is not dict or set(replay) != {
        "role", "command_id", "first", "status_code", "accepted_at", "accepted_boot_id",
    }:
        return False
    if (
        replay.get("role") != "default"
        or replay.get("command_id") != default.get("command_id")
        or replay.get("first") is not False
        or replay.get("status_code") != 200
        or replay.get("accepted_at") != default.get("accepted_at")
        or replay.get("accepted_boot_id") != default.get("accepted_boot_id")
    ):
        return False

    media = value["media_stall"]
    if type(media) is not dict or set(media) != {
        "source_command_id", "header_command_id", "content_sha256", "started_monotonic",
        "finished_monotonic", "new_put_role", "new_put_started_monotonic", "new_put_finished_monotonic",
    }:
        return False
    media_start = _number(media.get("started_monotonic"), "media start")
    media_finish = _number(media.get("finished_monotonic"), "media finish")
    new_start = _number(media.get("new_put_started_monotonic"), "new PUT start")
    new_finish = _number(media.get("new_put_finished_monotonic"), "new PUT finish")
    if (
        media.get("source_command_id") != by_role["overlap_first"].get("command_id")
        or media.get("header_command_id") != media.get("source_command_id")
        or type(media.get("content_sha256")) is not str
        or SHA256.fullmatch(str(media.get("content_sha256"))) is None
        or media.get("new_put_role") != "stall_new_put"
        or not media_start <= new_start <= new_finish <= media_finish
        or media_finish - media_start < 45.0
        or new_finish - new_start > 3.0
    ):
        return False

    recovery = value["delete_recovery"]
    if type(recovery) is not dict or set(recovery) != {
        "primary_command_id", "coalesced_command_id", "primary_status", "retry_status",
        "coalesced_status", "cleanup_status", "journal_command_ids", "journal_content_sha256",
        "media_content_sha256",
    }:
        return False
    return (
        recovery.get("primary_command_id") == by_role["overlap_first"].get("command_id")
        and recovery.get("coalesced_command_id") == by_role["overlap_second"].get("command_id")
        and recovery.get("primary_status") == 204
        and recovery.get("retry_status") == 410
        and recovery.get("coalesced_status") == 410
        and recovery.get("cleanup_status") == 204
        and recovery.get("journal_command_ids") == [
            by_role["overlap_first"].get("command_id"), by_role["overlap_second"].get("command_id")
        ]
        and recovery.get("journal_content_sha256") == recovery.get("media_content_sha256")
        and recovery.get("media_content_sha256") == media.get("content_sha256")
    )


def _outage_windows(value: object, start: float, finish: float) -> list[tuple[str, float, float]]:
    items = _items(value, "expected outages")
    windows: list[tuple[str, float, float]] = []
    for item in items:
        if set(item) != {"kind", "started_monotonic", "ended_monotonic"}:
            raise ValueError("invalid expected outage")
        kind = item.get("kind")
        began = _number(item.get("started_monotonic"), "outage start")
        ended = _number(item.get("ended_monotonic"), "outage end")
        if kind not in {"jetson_disconnect", "webcam_reconnect"} or not start <= began < ended <= finish:
            raise ValueError("invalid expected outage")
        windows.append((kind, began, ended))
    windows.sort(key=lambda item: item[1])
    if any(current[1] < previous[2] for previous, current in zip(windows, windows[1:])):
        raise ValueError("overlapping expected outages")
    if len(windows) != 2 or {kind for kind, _began, _ended in windows} != {"jetson_disconnect", "webcam_reconnect"}:
        raise ValueError("both hardware outage scenarios are required")
    return windows


def _containing_outage(moment: float, windows: list[tuple[str, float, float]]) -> str | None:
    return next((kind for kind, began, ended in windows if began <= moment <= ended), None)


def _crosses_outage(previous: float, current: float, windows: list[tuple[str, float, float]]) -> bool:
    return any(previous <= ended and current >= began for _kind, began, ended in windows)


def evaluate_soak(
    payload: Mapping[str, object], *, expected_candidate_sha: str, temperature_limit_c: float = 80.0
) -> dict[str, object]:
    if type(payload) is not dict or SHA.fullmatch(expected_candidate_sha) is None:
        raise ValueError("invalid soak payload")
    start = _number(payload.get("collector_started_monotonic"), "collector start")
    finish = _number(payload.get("collector_finished_monotonic"), "collector finish")
    duration = finish - start
    if duration <= 0:
        raise ValueError("invalid collector duration")

    observations = _items(payload.get("observations"), "observations")
    observation_times = _ordered_times(observations, "monotonic", "observation monotonic")
    windows = _outage_windows(payload.get("expected_outages"), start, finish)
    connected: list[Mapping[str, object]] = []
    disconnected_times: list[float] = []
    authenticated = True
    expected_disconnects = True
    for item, moment in zip(observations, observation_times):
        if set(item) != {
            "monotonic", "connected", "authenticated_status", "inference_fps",
            "home_age_seconds", "preview_valid",
        }:
            raise ValueError("invalid observation sample")
        if item.get("connected") is True:
            authenticated = (
                authenticated
                and item.get("authenticated_status") is True
                and item.get("preview_valid") is True
            )
            _number(item.get("inference_fps"), "inference FPS")
            _number(item.get("home_age_seconds"), "home age")
            connected.append(item)
        elif item.get("connected") is False:
            expected_disconnects = expected_disconnects and _containing_outage(moment, windows) is not None
            disconnected_times.append(moment)
            if (
                item.get("inference_fps") is not None
                or item.get("home_age_seconds") is not None
                or item.get("preview_valid") is not False
            ):
                raise ValueError("disconnected observations cannot carry camera metrics")
        else:
            raise ValueError("invalid observation connection state")

    connected_times = [_number(item["monotonic"], "connected monotonic") for item in connected]
    healthy_gaps = [
        current - previous
        for previous, current in zip(connected_times, connected_times[1:])
        if not _crosses_outage(previous, current, windows)
    ]
    inference = [_number(item["inference_fps"], "inference FPS") for item in connected]
    home_ages = [_number(item["home_age_seconds"], "home age") for item in connected]

    calibrations = _items(payload.get("calibrations"), "calibrations")
    calibration_ok = _calibration_ok(calibrations)
    guard = payload.get("wall_monotonic_guard")
    if type(guard) is not dict or set(guard) != {
        "duration_seconds", "sample_count", "max_interval_seconds", "max_discontinuity_seconds"
    }:
        raise ValueError("invalid wall/monotonic guard")
    guard_ok = (
        _number(guard["duration_seconds"], "guard duration") >= 3600.0
        and _integer(guard["sample_count"], "guard sample count") >= 36_001
        and 0 < _number(guard["max_interval_seconds"], "guard interval") <= 0.1
        and 0 <= _number(guard["max_discontinuity_seconds"], "guard discontinuity") <= 0.025
    )

    previews = _items(payload.get("previews"), "previews")
    preview_times = _ordered_times(previews, "monotonic", "preview monotonic")
    preview_ok = (
        preview_times == connected_times
        and all(current - previous >= 0.5 for previous, current in zip(preview_times, preview_times[1:]))
        and all(
            set(item) == {"monotonic", "width", "height", "format"}
            and item.get("width") == 640 and item.get("height") == 480 and item.get("format") == "jpeg"
            for item in previews
        )
    )

    clips = _items(payload.get("clips"), "clips")
    by_kind = {item.get("kind"): item for item in clips}
    clip_ok = set(by_kind) == {"default", "overlap"} and _clip_ok(
        by_kind["default"], "default", 300, 30.0
    ) and _clip_ok(by_kind["overlap"], "overlap", 450, 45.0)

    locked = payload.get("locked_clocks")
    if type(locked) is not dict or set(locked) != {"cpu_hz", "gpu_hz"}:
        raise ValueError("invalid locked clocks")
    cpu_locked = _number(locked.get("cpu_hz"), "locked CPU clock")
    gpu_locked = _number(locked.get("gpu_hz"), "locked GPU clock")
    resources = _items(payload.get("resources"), "resources")
    resource_times = _ordered_times(resources, "monotonic", "resource monotonic")
    expected_resource_keys = {
        "monotonic", "ring_frames", "ready_files", "temp_bytes", "temperature_c",
        "cpu_clock_hz", "gpu_clock_hz", "ram_used_mib",
    }
    resource_ok = _full_coverage(resource_times, start, finish, 1.0) and all(
        set(item) == expected_resource_keys
        and 0 <= _integer(item.get("ring_frames"), "ring frames") <= 100
        and 0 <= _integer(item.get("ready_files"), "ready files") <= 2
        and 0 <= _integer(item.get("temp_bytes"), "temporary bytes") <= 256 * 1024 * 1024
        and _integer(item.get("ram_used_mib"), "RAM used") >= 0
        for item in resources
    )
    temperatures = [_number(item.get("temperature_c"), "temperature") for item in resources]
    clock_ok = all(
        _number(item.get("cpu_clock_hz"), "CPU clock") >= cpu_locked
        and _number(item.get("gpu_clock_hz"), "GPU clock") >= gpu_locked
        for item in resources
    )

    before = payload.get("kernel_before")
    after = payload.get("kernel_after")
    if type(before) is not list or type(after) is not list or any(type(item) is not str for item in before + after):
        raise ValueError("invalid kernel evidence")
    new_kernel = after[len(before):] if after[:len(before)] == before else [item for item in after if item not in before]

    sensors = _items(payload.get("sensor_samples"), "sensor samples")
    sensor_times = _ordered_times(sensors, "monotonic", "sensor monotonic")
    disconnect = next((window for window in windows if window[0] == "jetson_disconnect"), None)
    webcam = next((window for window in windows if window[0] == "webcam_reconnect"), None)
    assert disconnect is not None
    assert webcam is not None
    _kind, disconnect_start, disconnect_end = disconnect
    disconnect_sensors = [
        (item, moment) for item, moment in zip(sensors, sensor_times)
        if disconnect_start <= moment <= disconnect_end
    ]
    sensor_independence = (
        disconnect_end - disconnect_start > 3.0
        and bool(disconnect_sensors)
        and disconnect_sensors[0][1] <= disconnect_start + 1.0
        and disconnect_sensors[-1][1] >= disconnect_end - 1.0
        and all(current[1] - previous[1] <= 1.0 + 1e-9 for previous, current in zip(disconnect_sensors, disconnect_sensors[1:]))
        and all(
            set(item) == {"monotonic", "success", "health_success", "camera_state"}
            and item.get("success") is True and item.get("health_success") is True
            and item.get("camera_state") in {"online", "offline"}
            for item, _moment in disconnect_sensors
        )
        and any(
            moment >= disconnect_start + 3.0 and item.get("camera_state") == "offline"
            for item, moment in disconnect_sensors
        )
    )
    _webcam_kind, webcam_start, webcam_end = webcam
    webcam_recovery = (
        any(
            webcam_start <= moment <= webcam_end and item.get("connected") is False
            for item, moment in zip(observations, observation_times)
        )
        and any(
            webcam_end < moment <= webcam_end + 1.0
            and item.get("connected") is True
            and item.get("authenticated_status") is True
            and item.get("preview_valid") is True
            for item, moment in zip(observations, observation_times)
        )
    )

    checks = {
        "candidate_sha": payload.get("candidate_sha") == expected_candidate_sha,
        "duration": 3600.0 <= duration <= 3605.0,
        "capture_coverage": _full_coverage(observation_times, start, finish, 1.0),
        "authenticated_collection": authenticated,
        "inference_fps": bool(inference) and min(inference) >= 3.0,
        "observation_gap": bool(healthy_gaps) and _p99(healthy_gaps) <= 1.0,
        "home_age": bool(home_ages) and min(home_ages) >= 0 and _p99(home_ages) <= 1.5 and max(home_ages) <= 3.0,
        "healthy_gap": (
            expected_disconnects
            and all(any(began <= moment <= ended for moment in disconnected_times) for _kind, began, ended in windows)
            and bool(healthy_gaps)
            and max(healthy_gaps) <= 3.0
        ),
        "clock_calibration": calibration_ok,
        "wall_monotonic_guard": guard_ok,
        "preview": preview_ok,
        "clips": clip_ok,
        "event_scenarios": _scenario_ok(payload.get("scenarios"), calibrations),
        "resources": resource_ok,
        "temperature": bool(temperatures) and max(temperatures) < _number(temperature_limit_c, "temperature limit"),
        "boot_id": (
            type(payload.get("boot_id_before")) is str
            and BOOT_ID.fullmatch(str(payload.get("boot_id_before"))) is not None
            and payload.get("boot_id_before") == payload.get("boot_id_after")
        ),
        "kernel": not any(KERNEL_FAULT.search(item) for item in new_kernel),
        "locked_clocks": clock_ok,
        "sensor_independence": sensor_independence,
        "temporary_files": payload.get("pretrigger_files") == 0 and payload.get("acknowledged_temp_files") == 0,
        "webcam_recovery": webcam_recovery,
    }
    metrics = {
        "duration_seconds": duration,
        "inference_fps_min": min(inference),
        "observation_gap_p99_seconds": _p99(healthy_gaps),
        "home_age_p99_seconds": _p99(home_ages),
        "temperature_max_c": max(temperatures),
    }
    return {
        "schema": SCHEMA,
        "candidate_sha": expected_candidate_sha,
        "status": "PASS" if all(checks.values()) else "FAIL",
        "checks": checks,
        "metrics": metrics,
    }


def _assert_secret_free(value: object) -> None:
    if type(value) is dict:
        for key, item in value.items():
            parts = re.findall(r"[a-z0-9]+", key.lower()) if type(key) is str else []
            if type(key) is not str or any(
                part.startswith(token)
                for part in parts
                for token in FORBIDDEN_EVIDENCE_NAMES
            ):
                raise ValueError("forbidden evidence field")
            _assert_secret_free(item)
    elif type(value) is list:
        for item in value:
            _assert_secret_free(item)
    elif type(value) is str and SHA.fullmatch(value) is None and SHA256.fullmatch(value) is None:
        if (
            "://" in value or "-----BEGIN" in value or "@" in value
            or IPV4_VALUE.search(value) or MAC_VALUE.search(value)
            or re.match(r"^[A-Za-z]:[\\/]", value) or value.startswith(("/", "\\\\"))
        ):
            raise ValueError("forbidden evidence value")


def validate_soak_evidence(
    evidence: Mapping[str, object], *, expected_candidate_sha: str, require_pass: bool
) -> None:
    if (
        type(evidence) is not dict
        or SHA.fullmatch(expected_candidate_sha) is None
        or set(evidence) != {"schema", "candidate_sha", "status", "checks", "metrics"}
    ):
        raise ValueError("invalid soak evidence schema")
    checks = evidence.get("checks")
    metrics = evidence.get("metrics")
    if (
        evidence.get("schema") != SCHEMA
        or evidence.get("candidate_sha") != expected_candidate_sha
        or type(checks) is not dict or tuple(checks) != SOAK_CHECKS
        or any(type(value) is not bool for value in checks.values())
        or type(metrics) is not dict or tuple(metrics) != SOAK_METRICS
    ):
        raise ValueError("invalid soak evidence content")
    all_passed = all(checks.values())
    if evidence.get("status") != ("PASS" if all_passed else "FAIL") or (require_pass and not all_passed):
        raise ValueError("soak evidence did not pass")
    for key in SOAK_METRICS:
        _number(metrics[key], key)
    _assert_secret_free(evidence)


def validate_bringup_evidence(evidence: Mapping[str, object], *, expected_candidate_sha: str) -> None:
    if type(evidence) is not dict or SHA.fullmatch(expected_candidate_sha) is None or set(evidence) != {
        "schema", "candidate_sha", "status", "board_model", "checks", "versions", "hashes",
        "camera", "counts", "imports", "plugins",
    }:
        raise ValueError("invalid bring-up evidence schema")
    checks = evidence.get("checks")
    versions = evidence.get("versions")
    hashes = evidence.get("hashes")
    camera = evidence.get("camera")
    counts = evidence.get("counts")
    imports = evidence.get("imports")
    plugins = evidence.get("plugins")
    if (
        evidence.get("schema") != BRINGUP_SCHEMA
        or evidence.get("candidate_sha") != expected_candidate_sha
        or evidence.get("status") != "PASS"
        or evidence.get("board_model") != "P3450 B01"
        or type(checks) is not dict or tuple(checks) != BRINGUP_CHECKS
        or any(value is not True for value in checks.values())
        or type(versions) is not dict or tuple(versions) != BRINGUP_VERSIONS
        or versions.get("l4t") != "32.7.6"
        or versions.get("jetpack") != "4.6.6"
        or versions.get("tensorrt") != "8.2.1"
        or type(versions.get("python")) is not str or not str(versions.get("python")).startswith("3.6")
        or versions.get("architecture") != "aarch64"
        or type(hashes) is not dict or tuple(hashes) != BRINGUP_HASHES
        or any(type(value) is not str or SHA256.fullmatch(value) is None for value in hashes.values())
        or type(camera) is not dict or tuple(camera) != BRINGUP_CAMERA
        or camera.get("width") != 640 or camera.get("height") != 480
        or camera.get("format") not in {"YUYV", "MJPG"}
        or type(counts) is not dict or tuple(counts) != BRINGUP_COUNTS
        or counts != {"service": 1, "private_listener": 1, "public_listener": 0, "cloud_process": 0}
        or type(imports) is not dict or tuple(imports) != BRINGUP_IMPORTS
        or any(value is not True for value in imports.values())
        or type(plugins) is not dict or tuple(plugins) != BRINGUP_PLUGINS
        or any(value is not True for value in plugins.values())
    ):
        raise ValueError("invalid bring-up evidence content")
    _assert_secret_free(evidence)


def write_evidence(path: Path, evidence: Mapping[str, object]) -> None:
    path = Path(path)
    validate_soak_evidence(evidence, expected_candidate_sha=str(evidence.get("candidate_sha")), require_pass=False)
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp", dir=path.parent)
    try:
        os.chmod(temporary, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as output:
            descriptor = -1
            json.dump(evidence, output, sort_keys=False, separators=(",", ":"), allow_nan=False)
            output.write("\n")
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, path)
    except BaseException:
        if descriptor >= 0:
            os.close(descriptor)
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass
        raise


def parse_tegrastats(line: str) -> dict[str, object]:
    if type(line) is not str:
        raise ValueError("invalid tegrastats sample")
    ram = re.search(r"\bRAM\s+(\d+)/\d+MB", line)
    cpu = re.search(r"\bCPU\s+\[[^]]*?@(\d+)", line)
    gpu = re.search(r"\bGR3D_FREQ\s+\d+%@(\d+)", line)
    temperatures = [float(value) for value in re.findall(r"\b(?:CPU|GPU)@([0-9]+(?:\.[0-9]+)?)C", line)]
    if not ram or not cpu or not gpu or not temperatures:
        raise ValueError("invalid tegrastats sample")
    return {
        "ram_used_mib": int(ram.group(1)),
        "cpu_clock_hz": int(cpu.group(1)) * 1_000_000,
        "gpu_clock_hz": int(gpu.group(1)) * 1_000_000,
        "temperature_c": max(temperatures),
    }


def _home_sample(client: HomeCollectorClient) -> tuple[bool, bool, str]:
    try:
        sensors = client.get("/api/sensors/latest")
        health = client.get("/api/health")
        camera = client.get("/api/camera/status")
        sensor_ok = getattr(sensors, "status_code", None) == 200 and type(sensors.json()) is list
        health_ok = getattr(health, "status_code", None) == 200 and type(health.json()) is dict
        camera_body = camera.json()
        camera_state = camera_body.get("state") if getattr(camera, "status_code", None) == 200 and type(camera_body) is dict else "unknown"
        return sensor_ok, health_ok, camera_state if camera_state in {"online", "offline"} else "unknown"
    except Exception:
        return False, False, "unknown"


def _normalize_outages(value: object, started: float) -> list[dict[str, object]]:
    items = _items(value, "harness outages")
    if any(set(item) != {"kind", "start_offset_seconds", "end_offset_seconds"} for item in items):
        raise ValueError("invalid harness outage")
    return [
        {
            "kind": item.get("kind"),
            "started_monotonic": started + _number(item.get("start_offset_seconds"), "outage offset"),
            "ended_monotonic": started + _number(item.get("end_offset_seconds"), "outage offset"),
        }
        for item in items
    ]


def _normalize_resources(value: object, started: float) -> list[dict[str, object]]:
    output: list[dict[str, object]] = []
    for item in _items(value, "resource samples"):
        if set(item) != {"offset_seconds", "tegrastats", "ring_frames", "ready_files", "temp_bytes"}:
            raise ValueError("invalid resource sample")
        parsed = parse_tegrastats(item["tegrastats"])  # type: ignore[arg-type]
        output.append({
            "monotonic": started + _number(item["offset_seconds"], "resource offset"),
            "ring_frames": item["ring_frames"],
            "ready_files": item["ready_files"],
            "temp_bytes": item["temp_bytes"],
            **parsed,
        })
    return output


def _inspect_clips(
    value: object, ffprobe_path: Path, run: Callable[..., subprocess.CompletedProcess[str]]
) -> list[dict[str, object]]:
    output: list[dict[str, object]] = []
    for item in _items(value, "clip files"):
        if set(item) != {"kind", "file"} or item.get("kind") not in {"default", "overlap"}:
            raise ValueError("invalid clip file")
        media = Path(str(item["file"]))
        if not media.is_absolute() or not media.is_file():
            raise ValueError("invalid clip file")
        result = run(
            [
                str(ffprobe_path), "-v", "error", "-show_entries",
                "stream=codec_name,pix_fmt,width,height,r_frame_rate,nb_frames:format=duration",
                "-of", "json", str(media),
            ],
            capture_output=True, text=True, encoding="utf-8", errors="strict", timeout=30.0, check=False,
        )
        if result.returncode != 0 or len(result.stdout) > 65_536:
            raise ValueError("ffprobe failed")
        try:
            parsed = json.loads(
                result.stdout,
                object_pairs_hook=_unique_object,
                parse_constant=_reject_constant,
            )
            if type(parsed) is not dict or set(parsed) != {"streams", "format"}:
                raise ValueError
            if type(parsed["streams"]) is not list or len(parsed["streams"]) != 1:
                raise ValueError
            stream = parsed["streams"][0]
            media_format = parsed["format"]
            if (
                type(stream) is not dict
                or set(stream) != {"codec_name", "pix_fmt", "width", "height", "r_frame_rate", "nb_frames"}
                or type(media_format) is not dict
                or set(media_format) != {"duration"}
            ):
                raise ValueError
            frame_text = stream["nb_frames"]
            duration_text = media_format["duration"]
            if (
                type(frame_text) is not str or not frame_text.isdigit() or str(int(frame_text)) != frame_text
                or type(duration_text) is not str
            ):
                raise ValueError
            duration = float(duration_text)
            if not math.isfinite(duration):
                raise ValueError
            output.append({
                "kind": item["kind"],
                "frame_count": int(frame_text),
                "duration_seconds": duration,
                "width": stream["width"],
                "height": stream["height"],
                "frame_rate": stream["r_frame_rate"],
                "video_codec": stream["codec_name"],
                "pixel_format": stream["pix_fmt"],
            })
        except (KeyError, IndexError, TypeError, ValueError, json.JSONDecodeError) as error:
            raise ValueError("invalid ffprobe output") from error
    return output


def _utc_text(value: datetime) -> str:
    if not isinstance(value, datetime) or value.tzinfo is None:
        raise ValueError("aware UTC timestamp required")
    return value.astimezone(UTC).isoformat(timespec="microseconds").replace("+00:00", "Z")


def _authorized_events(value: object) -> dict[str, Mapping[str, object]]:
    items = _items(value, "authorized events")
    expected = {"role", "offset_seconds", "command_id", "event_type", "event_id"}
    output: dict[str, Mapping[str, object]] = {}
    command_ids: set[str] = set()
    for item in items:
        role = item.get("role")
        command_id = item.get("command_id")
        if (
            set(item) != expected
            or role not in ALL_EVENT_ROLES
            or role in output
            or type(command_id) is not str
            or COMMAND_ID.fullmatch(command_id) is None
            or command_id in command_ids
            or item.get("event_type") not in ELIGIBLE_EVENT_TYPES
            or _integer(item.get("event_id"), "authorized event ID") <= 0
        ):
            raise ValueError("invalid authorized event")
        offset = _number(item.get("offset_seconds"), "authorized event offset")
        if not 0 <= offset < 3500:
            raise ValueError("invalid authorized event offset")
        output[str(role)] = item
        command_ids.add(command_id)
    if set(output) != set(ALL_EVENT_ROLES):
        raise ValueError("exact authorized event roles are required")
    offsets = {role: _number(item["offset_seconds"], "authorized event offset") for role, item in output.items()}
    if not (
        offsets["default"] >= 0
        and offsets["default"] + 20 < offsets["deadline_expired"] < offsets["overlap_first"]
        and offsets["default"] + 25 <= offsets["overlap_first"]
        and abs(offsets["overlap_second"] - offsets["overlap_first"] - 15.0) <= 1e-9
        and offsets["stall_new_put"] >= offsets["overlap_second"] + 25.0
    ):
        raise ValueError("invalid authorized event schedule")
    return output


def _event_command(event: Mapping[str, object], committed_at: datetime) -> dict[str, object]:
    return {
        "committed_at": _utc_text(committed_at),
        "event_id": event["event_id"],
        "event_type": event["event_type"],
        "occurred_at": _utc_text(committed_at),
    }


def _receipt_record(result: object) -> tuple[int, object]:
    status_code = getattr(result, "status_code", None)
    receipt = getattr(result, "receipt", None)
    if status_code not in {200, 201} or receipt is None:
        raise ValueError("invalid collector PUT result")
    return status_code, receipt


def _first_put(
    *,
    jetson: JetsonCollectorClient,
    event: Mapping[str, object],
    committed_at: datetime,
    calibrations: list[dict[str, object]],
    monotonic: Callable[[], float],
    utc_now: Callable[[], datetime],
    expected_error: str | None = None,
) -> dict[str, object]:
    role = str(event["role"])
    command_id = str(event["command_id"])
    calibration = jetson.calibrate_clock()
    measured = _number(getattr(calibration, "measured_monotonic"), "calibration monotonic")
    put_started_monotonic = monotonic()
    calibrations.append({
        "role": role,
        "command_id": command_id,
        "first_put": True,
        "measured_monotonic": measured,
        "put_started_monotonic": put_started_monotonic,
        "age_seconds": max(0.0, put_started_monotonic - measured),
        "offset_ms": _number(getattr(calibration, "offset_ms"), "calibration offset"),
        "half_rtt_ms": _number(getattr(calibration, "half_rtt_ms"), "calibration half RTT"),
    })
    put_started_wall = utc_now().astimezone(UTC)
    command = _event_command(event, committed_at)
    try:
        result = jetson.put_clip(command_id, command, first=True)
    except Exception as error:
        put_finished_monotonic = monotonic()
        if expected_error is None or str(error) != expected_error:
            raise
        return {
            "role": role,
            "command_id": command_id,
            "event_type": event["event_type"],
            "event_id": event["event_id"],
            "first": True,
            "committed_at": _utc_text(committed_at),
            "put_started_wall": _utc_text(put_started_wall),
            "put_started_monotonic": put_started_monotonic,
            "put_finished_monotonic": put_finished_monotonic,
            "error_code": str(error),
        }
    if expected_error is not None:
        raise ValueError("expired first PUT was accepted")
    put_finished_wall = utc_now().astimezone(UTC)
    put_finished_monotonic = monotonic()
    status_code, receipt = _receipt_record(result)
    accepted_at = getattr(receipt, "accepted_at", None)
    accepted_boot_id = getattr(receipt, "accepted_boot_id", None)
    receipt_command_id = getattr(receipt, "command_id", None)
    if (
        not isinstance(accepted_at, datetime)
        or accepted_at.tzinfo is None
        or type(accepted_boot_id) is not str
        or BOOT_ID.fullmatch(accepted_boot_id) is None
        or receipt_command_id != command_id
    ):
        raise ValueError("invalid collector admission receipt")
    return {
        "role": role,
        "command_id": command_id,
        "event_type": event["event_type"],
        "event_id": event["event_id"],
        "first": True,
        "status_code": status_code,
        "committed_at": _utc_text(committed_at),
        "put_started_wall": _utc_text(put_started_wall),
        "put_finished_wall": _utc_text(put_finished_wall),
        "put_started_monotonic": put_started_monotonic,
        "put_finished_monotonic": put_finished_monotonic,
        "accepted_at": _utc_text(accepted_at),
        "accepted_boot_id": accepted_boot_id,
    }


def _write_crash_journal(path: Path, command_ids: list[str], content_sha256: str) -> None:
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as output:
            descriptor = -1
            json.dump(
                {"command_ids": command_ids, "content_sha256": content_sha256},
                output,
                sort_keys=True,
                separators=(",", ":"),
                allow_nan=False,
            )
            output.write("\n")
            output.flush()
            os.fsync(output.fileno())
    finally:
        if descriptor >= 0:
            os.close(descriptor)


def _run_direct_scenarios(
    *,
    jetson: JetsonCollectorClient,
    events: Mapping[str, Mapping[str, object]],
    started: float,
    media_stall_seconds: float,
    ffprobe_path: Path,
    calibrations: list[dict[str, object]],
    monotonic: Callable[[], float],
    operation_monotonic: Callable[[], float],
    utc_now: Callable[[], datetime],
    wait_until: Callable[[float], None],
    stall_sleep: Callable[[float], None],
    run: Callable[..., subprocess.CompletedProcess[str]],
    first_put_completed: threading.Event,
) -> tuple[list[dict[str, object]], dict[str, object]]:
    puts: list[dict[str, object]] = []
    clips: list[dict[str, object]] = []
    with tempfile.TemporaryDirectory(prefix="petcare-jetson-soak-") as temporary:
        work = Path(temporary)
        default = events["default"]
        wait_until(started + _number(default["offset_seconds"], "default offset"))
        default_committed = utc_now().astimezone(UTC) + timedelta(milliseconds=200)
        default_put = _first_put(
            jetson=jetson,
            event=default,
            committed_at=default_committed,
            calibrations=calibrations,
            monotonic=monotonic,
            utc_now=utc_now,
        )
        puts.append(default_put)
        first_put_completed.set()
        replay_result = jetson.put_clip(
            str(default["command_id"]), _event_command(default, default_committed), first=False
        )
        replay_status, replay_receipt = _receipt_record(replay_result)
        replay = {
            "role": "default",
            "command_id": str(default["command_id"]),
            "first": False,
            "status_code": replay_status,
            "accepted_at": _utc_text(getattr(replay_receipt, "accepted_at")),
            "accepted_boot_id": getattr(replay_receipt, "accepted_boot_id"),
        }
        wait_until(started + _number(default["offset_seconds"], "default offset") + 25.0)
        default_media = work / "default.mp4"
        default_headers = jetson.download_clip(str(default["command_id"]), default_media)
        if getattr(default_headers, "command_id", None) != default["command_id"]:
            raise ValueError("default media identity mismatch")
        clips.extend(_inspect_clips([{"kind": "default", "file": str(default_media)}], ffprobe_path, run))
        if jetson.delete_clip(str(default["command_id"])) != 204:
            raise ValueError("default media was not acknowledged")

        expired = events["deadline_expired"]
        wait_until(started + _number(expired["offset_seconds"], "expired offset"))
        expired_result = _first_put(
            jetson=jetson,
            event=expired,
            committed_at=utc_now().astimezone(UTC) - timedelta(seconds=3.001),
            calibrations=calibrations,
            monotonic=monotonic,
            utc_now=utc_now,
            expected_error="command_expired",
        )

        overlap_first = events["overlap_first"]
        wait_until(started + _number(overlap_first["offset_seconds"], "overlap offset"))
        puts.append(_first_put(
            jetson=jetson,
            event=overlap_first,
            committed_at=utc_now().astimezone(UTC),
            calibrations=calibrations,
            monotonic=monotonic,
            utc_now=utc_now,
        ))
        overlap_second = events["overlap_second"]
        wait_until(started + _number(overlap_second["offset_seconds"], "overlap offset"))
        puts.append(_first_put(
            jetson=jetson,
            event=overlap_second,
            committed_at=utc_now().astimezone(UTC),
            calibrations=calibrations,
            monotonic=monotonic,
            utc_now=utc_now,
        ))

        stall_event = events["stall_new_put"]
        wait_until(started + _number(stall_event["offset_seconds"], "stall event offset"))
        media_ready = threading.Event()
        admission_done = threading.Event()
        media_errors: list[BaseException] = []
        media_result: dict[str, object] = {}

        def hold_media_lane() -> None:
            try:
                destination = work / "overlap.mp4"
                media_result["started_monotonic"] = operation_monotonic()
                media_ready.set()
                headers = jetson.download_clip(str(overlap_first["command_id"]), destination)
                media_result["header_command_id"] = getattr(headers, "command_id", None)
                header_digest = getattr(headers, "content_sha256", None)
                observed_digest = hashlib.sha256(destination.read_bytes()).hexdigest()
                if header_digest != observed_digest:
                    raise ValueError("observed media digest mismatch")
                media_result["content_sha256"] = observed_digest
                if not admission_done.wait(3.1):
                    raise TimeoutError("new PUT did not finish during held media lane")
                stall_sleep(media_stall_seconds)
                clips.extend(_inspect_clips(
                    [{"kind": "overlap", "file": str(destination)}], ffprobe_path, run
                ))
                media_result["finished_monotonic"] = operation_monotonic()
            except BaseException as error:
                media_errors.append(error)
                media_ready.set()

        media_thread = threading.Thread(target=hold_media_lane, name="petcare-soak-media", daemon=False)
        media_thread.start()
        if not media_ready.wait(3.0):
            raise TimeoutError("media lane did not start")
        new_put_started = operation_monotonic()
        try:
            puts.append(_first_put(
                jetson=jetson,
                event=stall_event,
                committed_at=utc_now().astimezone(UTC),
                calibrations=calibrations,
                monotonic=monotonic,
                utc_now=utc_now,
            ))
        finally:
            new_put_finished = operation_monotonic()
            admission_done.set()
        media_thread.join(55.0)
        if media_thread.is_alive():
            raise TimeoutError("held media lane did not finish")
        if media_errors:
            raise media_errors[0]

        content_sha256 = media_result.get("content_sha256")
        if type(content_sha256) is not str or SHA256.fullmatch(content_sha256) is None:
            raise ValueError("invalid observed media digest")
        journal = work / "unreleased.json"
        command_ids = [str(overlap_first["command_id"]), str(overlap_second["command_id"])]
        _write_crash_journal(journal, command_ids, content_sha256)
        primary_status = jetson.delete_clip(command_ids[0])
        recovered = _load_json(journal)
        retry_status = jetson.delete_clip(command_ids[0])
        coalesced_status = jetson.delete_clip(command_ids[1])
        cleanup_status = jetson.delete_clip(str(stall_event["command_id"]))
        if set(recovered) != {"command_ids", "content_sha256"}:
            raise ValueError("invalid recovered crash journal")

        scenarios = {
            "puts": puts,
            "expired": expired_result,
            "replay": replay,
            "media_stall": {
                "source_command_id": command_ids[0],
                "header_command_id": media_result.get("header_command_id"),
                "content_sha256": content_sha256,
                "started_monotonic": media_result.get("started_monotonic"),
                "finished_monotonic": media_result.get("finished_monotonic"),
                "new_put_role": "stall_new_put",
                "new_put_started_monotonic": new_put_started,
                "new_put_finished_monotonic": new_put_finished,
            },
            "delete_recovery": {
                "primary_command_id": command_ids[0],
                "coalesced_command_id": command_ids[1],
                "primary_status": primary_status,
                "retry_status": retry_status,
                "coalesced_status": coalesced_status,
                "cleanup_status": cleanup_status,
                "journal_command_ids": recovered.get("command_ids"),
                "journal_content_sha256": recovered.get("content_sha256"),
                "media_content_sha256": content_sha256,
            },
        }
        return clips, scenarios


def collect_authenticated_soak(
    *,
    jetson: JetsonCollectorClient,
    home: HomeCollectorClient,
    harness_loader: Callable[[], Mapping[str, object]],
    ffprobe_path: Path,
    candidate_sha: str,
    monotonic: Callable[[], float] = time.monotonic,
    utc_now: Callable[[], datetime] = lambda: datetime.now(UTC),
    sleep: Callable[[float], None] = time.sleep,
    wait_until: Callable[[float], None] | None = None,
    operation_monotonic: Callable[[], float] | None = None,
    stall_sleep: Callable[[float], None] = time.sleep,
    run: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
) -> dict[str, object]:
    if SHA.fullmatch(candidate_sha) is None or not Path(ffprobe_path).is_absolute() or not Path(ffprobe_path).is_file():
        raise ValueError("invalid collector configuration")
    initial = harness_loader()
    if type(initial) is not dict or initial.get("schema") != HARNESS_SCHEMA or initial.get("candidate_sha") != candidate_sha:
        raise ValueError("invalid harness input")
    if {"first_put_offsets_seconds", "clip_files", "scenarios"} & set(initial):
        raise ValueError("harness outcomes and pre-created media are forbidden")
    events = _authorized_events(initial.get("authorized_events"))
    media_stall_seconds = _number(initial.get("media_stall_seconds"), "media stall authorization")
    if abs(media_stall_seconds - 45.0) > 1e-9:
        raise ValueError("media stall authorization must be exactly 45 seconds")

    started = monotonic()
    operation_clock = operation_monotonic or monotonic
    if wait_until is None:
        def wait_for_deadline(deadline: float) -> None:
            while True:
                remaining = deadline - monotonic()
                if remaining <= 0:
                    return
                sleep(min(remaining, 0.1))
        scenario_wait = wait_for_deadline
    else:
        scenario_wait = wait_until
    observations: list[dict[str, object]] = []
    previews: list[dict[str, object]] = []
    sensors: list[dict[str, object]] = []
    calibrations: list[dict[str, object]] = []
    scenario_result: dict[str, object] = {}
    scenario_errors: list[BaseException] = []
    first_put_completed = threading.Event()

    def run_scenarios() -> None:
        try:
            clips, scenarios = _run_direct_scenarios(
                jetson=jetson,
                events=events,
                started=started,
                media_stall_seconds=media_stall_seconds,
                ffprobe_path=Path(ffprobe_path),
                calibrations=calibrations,
                monotonic=monotonic,
                operation_monotonic=operation_clock,
                utc_now=utc_now,
                wait_until=scenario_wait,
                stall_sleep=stall_sleep,
                run=run,
                first_put_completed=first_put_completed,
            )
            scenario_result["clips"] = clips
            scenario_result["scenarios"] = scenarios
        except BaseException as error:
            scenario_errors.append(error)
        finally:
            first_put_completed.set()

    scenario_thread = threading.Thread(target=run_scenarios, name="petcare-soak-scenarios", daemon=False)
    scenario_thread.start()
    if not first_put_completed.wait(3.0):
        raise TimeoutError("first fixture-authorized PUT exceeded three seconds")
    if scenario_errors:
        raise scenario_errors[0]
    first_boot: str | None = None
    last_boot: str | None = None
    sample_index = 0
    while True:
        now_monotonic = monotonic()

        connected = False
        authenticated = False
        inference_fps: float | None = None
        home_age: float | None = None
        preview_valid = False
        try:
            status = jetson.status()
            authenticated = True
            boot_id = getattr(status, "boot_id")
            if type(boot_id) is not str:
                raise ValueError("invalid authenticated status")
            first_boot = first_boot or boot_id
            last_boot = boot_id
            frame = jetson.next_frame({"pet_bed": (0, 0, 640, 480)})
            observed_at = getattr(frame, "observed_at")
            if not isinstance(observed_at, datetime) or observed_at.tzinfo is None:
                raise ValueError("invalid frame timestamp")
            inference_fps = _number(getattr(frame, "fps"), "frame FPS")
            home_age = (utc_now().astimezone(UTC) - observed_at.astimezone(UTC)).total_seconds()
            jpeg = getattr(frame, "jpeg")
            preview_valid = type(jpeg) is bytes and jpeg.startswith(b"\xff\xd8") and jpeg.endswith(b"\xff\xd9")
            connected = preview_valid
        except Exception:
            connected = False

        sensor_ok, health_ok, camera_state = _home_sample(home)
        sampled_at = monotonic()
        observations.append({
            "monotonic": sampled_at,
            "connected": connected,
            "authenticated_status": authenticated,
            "inference_fps": inference_fps if connected else None,
            "home_age_seconds": home_age if connected else None,
            "preview_valid": preview_valid,
        })
        sensors.append({
            "monotonic": sampled_at,
            "success": sensor_ok,
            "health_success": health_ok,
            "camera_state": camera_state,
        })
        if connected:
            previews.append({"monotonic": sampled_at, "width": 640, "height": 480, "format": "jpeg"})

        if sampled_at - started >= 3600.0:
            break
        sample_index += 1
        target = started + min(3600.0, float(sample_index))
        sleep(max(0.0, target - monotonic()))

    finished = monotonic()
    scenario_thread.join(5.0)
    if scenario_thread.is_alive():
        raise TimeoutError("fixture-authorized scenario execution did not finish")
    if scenario_errors:
        raise scenario_errors[0]
    final = harness_loader()
    if type(final) is not dict or final.get("schema") != HARNESS_SCHEMA or final.get("candidate_sha") != candidate_sha:
        raise ValueError("invalid completed harness input")
    if (
        final.get("authorized_events") != initial.get("authorized_events")
        or final.get("media_stall_seconds") != initial.get("media_stall_seconds")
        or set(_calibration_map(calibrations)) != set(ALL_EVENT_ROLES)
        or set(scenario_result) != {"clips", "scenarios"}
    ):
        raise ValueError("incomplete fixture-authorized scenario execution")
    return {
        "candidate_sha": candidate_sha,
        "collector_started_monotonic": started,
        "collector_finished_monotonic": finished,
        "observations": observations,
        "calibrations": calibrations,
        "wall_monotonic_guard": final.get("wall_monotonic_guard"),
        "previews": previews,
        "clips": scenario_result["clips"],
        "resources": _normalize_resources(final.get("resource_samples"), started),
        "locked_clocks": final.get("locked_clocks"),
        "boot_id_before": first_boot,
        "boot_id_after": last_boot,
        "kernel_before": final.get("kernel_before"),
        "kernel_after": final.get("kernel_after"),
        "sensor_samples": sensors,
        "expected_outages": _normalize_outages(final.get("expected_outages"), started),
        "scenarios": scenario_result["scenarios"],
        "pretrigger_files": final.get("pretrigger_files"),
        "acknowledged_temp_files": final.get("acknowledged_temp_files"),
    }


def _load_json(path: Path) -> dict[str, object]:
    try:
        value = json.loads(
            Path(path).read_text(encoding="utf-8"),
            object_pairs_hook=_unique_object,
            parse_constant=_reject_constant,
        )
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ValueError("invalid JSON input") from error
    if type(value) is not dict:
        raise ValueError("JSON object required")
    return value


def _verify_evidence(arguments: argparse.Namespace) -> int:
    candidate = Path(arguments.candidate).read_text(encoding="utf-8").strip()
    if SHA.fullmatch(candidate) is None or candidate != arguments.expected_candidate_sha:
        raise ValueError("candidate marker mismatch")
    validate_bringup_evidence(_load_json(arguments.bringup), expected_candidate_sha=candidate)
    validate_soak_evidence(_load_json(arguments.soak), expected_candidate_sha=candidate, require_pass=True)
    return 0


def _serve_fixture(port: int) -> int:
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802 - stdlib handler API
            body = b"PETCARE-JETSON-FIXTURE-V1\n"
            if self.path != "/health":
                self.send_error(404)
                return
            self.send_response(200)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, _format: str, *_args: object) -> None:
            return

    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    server.daemon_threads = True
    try:
        server.serve_forever(poll_interval=0.1)
    finally:
        server.server_close()
    return 0


def _collect(arguments: argparse.Namespace) -> int:
    for value in (arguments.jetson_config, arguments.harness_input, arguments.ffprobe, arguments.output):
        if not Path(value).is_absolute():
            raise ValueError("collector paths must be absolute")
    parsed = urlsplit(arguments.home_origin)
    if (
        parsed.scheme != "http" or parsed.hostname != "127.0.0.1" or parsed.port is None
        or parsed.username is not None or parsed.password is not None
        or parsed.path not in ("", "/") or parsed.query or parsed.fragment
    ):
        raise ValueError("Home origin must be explicit loopback HTTP")

    root = Path(__file__).resolve().parents[1]
    backend = root / "backend"
    import sys
    if str(backend) not in sys.path:
        sys.path.insert(0, str(backend))
    import httpx
    from app.config import load_jetson_config
    from app.jetson_client import JetsonVisionClient

    jetson = JetsonVisionClient(load_jetson_config(arguments.jetson_config))
    home = httpx.Client(base_url=arguments.home_origin, timeout=2.0)
    try:
        payload = collect_authenticated_soak(
            jetson=jetson,
            home=home,
            harness_loader=lambda: _load_json(arguments.harness_input),
            ffprobe_path=arguments.ffprobe,
            candidate_sha=arguments.candidate_sha,
        )
        evidence = evaluate_soak(
            payload,
            expected_candidate_sha=arguments.candidate_sha,
            temperature_limit_c=arguments.temperature_limit_c,
        )
        write_evidence(arguments.output, evidence)
        return 0 if evidence["status"] == "PASS" else 1
    finally:
        first_error: BaseException | None = None
        try:
            jetson.close()
        except BaseException as error:
            first_error = error
        try:
            home.close()
        except BaseException as error:
            first_error = first_error or error
        if first_error is not None:
            raise first_error


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Collect and verify PetCare Jetson hardware evidence")
    commands = parser.add_subparsers(dest="command", required=True)

    collect = commands.add_parser("collect")
    collect.add_argument("--jetson-config", type=Path, required=True)
    collect.add_argument("--home-origin", required=True)
    collect.add_argument("--harness-input", type=Path, required=True)
    collect.add_argument("--ffprobe", type=Path, required=True)
    collect.add_argument("--output", type=Path, required=True)
    collect.add_argument("--candidate-sha", required=True)
    collect.add_argument("--temperature-limit-c", type=float, default=80.0)

    verify = commands.add_parser("verify-evidence")
    verify.add_argument("--candidate", type=Path, required=True)
    verify.add_argument("--bringup", type=Path, required=True)
    verify.add_argument("--soak", type=Path, required=True)
    verify.add_argument("--expected-candidate-sha", required=True)

    fixture = commands.add_parser("fixture-service")
    fixture.add_argument("--port", type=int, choices=(58080,), required=True)

    arguments = parser.parse_args(argv)
    if arguments.command == "collect":
        return _collect(arguments)
    if arguments.command == "verify-evidence":
        return _verify_evidence(arguments)
    return _serve_fixture(arguments.port)


if __name__ == "__main__":
    raise SystemExit(main())
