from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
BUILD_SCRIPT = ROOT / "tools" / "build_pico_host.sh"


def require(condition: bool, label: str) -> None:
    if not condition:
        raise SystemExit(f"FAIL {label}")


def find_git_bash() -> str:
    candidates = [
        Path("C:/Program Files/Git/bin/bash.exe"),
        Path("C:/Program Files/Git/usr/bin/bash.exe"),
    ]
    git = shutil.which("git")
    if git:
        git_path = Path(git)
        candidates.append(git_path.parents[1] / "bin" / "bash.exe")
        candidates.append(git_path.parents[1] / "usr" / "bin" / "bash.exe")
    for candidate in candidates:
        if candidate.is_file():
            return str(candidate)
    bash = shutil.which("bash")
    require(bash is not None and "Windows\\System32" not in bash, "Git Bash is required")
    return bash


def run_build_demo() -> str:
    bash = find_git_bash()
    require(BUILD_SCRIPT.is_file(), f"missing {BUILD_SCRIPT}")
    result = subprocess.run(
        [bash, "tools/build_pico_host.sh"],
        cwd=ROOT,
        text=True,
        encoding="utf-8",
        errors="replace",
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        check=False,
    )
    if result.returncode != 0:
        raise SystemExit(result.stdout + f"\nFAIL build script exited {result.returncode}")
    return result.stdout


def parse_topic_payloads(output: str) -> dict[str, list[dict[str, Any]]]:
    lines = [line.strip() for line in output.splitlines() if line.strip()]
    messages: dict[str, list[dict[str, Any]]] = {}
    for index, line in enumerate(lines[:-1]):
        if not line.startswith("home/"):
            continue
        payload_line = lines[index + 1]
        try:
            payload = json.loads(payload_line)
        except json.JSONDecodeError as exc:
            raise SystemExit(f"FAIL invalid JSON after {line}: {exc}") from exc
        messages.setdefault(line, []).append(payload)
    return messages


def one(messages: dict[str, list[dict[str, Any]]], topic: str) -> dict[str, Any]:
    payloads = messages.get(topic, [])
    require(bool(payloads), f"missing topic {topic}")
    return payloads[0]


def anomaly(messages: dict[str, list[dict[str, Any]]], anomaly_type: str) -> dict[str, Any]:
    topic = "home/camera/pc_webcam_01/anomaly"
    for payload in messages.get(topic, []):
        inner = payload.get("payload", {})
        if inner.get("anomaly_type") == anomaly_type:
            return inner
    raise SystemExit(f"FAIL missing anomaly {anomaly_type}")


output = run_build_demo()
require("100% tests passed" in output, "CTest did not pass")
messages = parse_topic_payloads(output)

telemetry = one(messages, "home/pico/pico_petzone_01/telemetry")
for field in [
    "device_id",
    "zone",
    "timestamp_ms",
    "temperature_c",
    "humidity_pct",
    "light_lux",
    "motion",
    "door_open",
    "food_weight_g",
    "water_weight_g",
    "bed_weight_g",
    "trigger_camera",
    "reason",
]:
    require(field in telemetry, f"telemetry missing {field}")
require(telemetry["trigger_camera"] is True, "telemetry trigger_camera must be true")
require(telemetry["reason"] == "motion", "telemetry reason must be motion")
print("PASS runtime telemetry contains Pico sensor fields")

sensor = one(messages, "home/pico/pico_petzone_01/sensor/food_weight")
for field in ["device_id", "sensor_type", "value", "unit", "battery", "rssi", "timestamp"]:
    require(field in sensor, f"sensor missing {field}")
require(sensor["sensor_type"] == "food_weight", "sensor_type must be food_weight")

status = one(messages, "home/pico/pico_petzone_01/status")
for field in ["device_id", "status", "firmware_version", "ip", "uptime_sec", "timestamp"]:
    require(field in status, f"status missing {field}")
require(status["status"] == "online", "status must be online")
print("PASS runtime sensor/status topics match proposal")

detection = one(messages, "home/camera/pc_webcam_01/detection")
for field in ["camera_id", "detected_type", "confidence", "bbox", "zone", "track_id", "timestamp"]:
    require(field in detection, f"detection missing {field}")
require(detection["detected_type"] == "dog", "detection type must be dog")
require(detection["zone"] == "food_bowl", "detection zone must be food_bowl")
for field in ["x", "y", "w", "h"]:
    require(field in detection["bbox"], f"bbox missing {field}")
print("PASS runtime webcam detection payload matches proposal")

behavior = one(messages, "home/camera/pc_webcam_01/behavior")
require(behavior.get("type") == "dashboard_update", "behavior wrapper type must be dashboard_update")
behavior_payload = behavior.get("payload", {})
for field in [
    "subject_type",
    "subject_id",
    "behavior_type",
    "zone_id",
    "confidence",
    "duration_sec",
    "message",
    "created_at",
]:
    require(field in behavior_payload, f"behavior missing {field}")
require(behavior_payload["behavior_type"] == "eating", "behavior must be eating")
require(behavior_payload["zone_id"] == "food_bowl", "behavior zone must be food_bowl")
print("PASS runtime ROI eating behavior matches proposal")

entrance = anomaly(messages, "entrance_risk")
require(entrance["severity"] == "danger", "entrance_risk must be danger")
no_meal = anomaly(messages, "no_meal_12h")
require(no_meal["severity"] == "warning", "no_meal_12h must be warning")
fall = anomaly(messages, "fall_suspected")
require(fall["severity"] == "danger", "fall_suspected must be danger")
print("PASS runtime anomaly payloads match proposal")

trigger = one(messages, "home/pico/pico_petzone_01/camera_trigger")
require(trigger["trigger_camera"] is True, "camera trigger must be active")
require(trigger["reason"] == "motion", "camera trigger reason must be motion")
print("PASS runtime trigger event contains camera wake reason")
