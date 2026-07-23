"""Validate PetCare's machine-readable operator documentation blocks."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any

try:
    from tools.validate_platform_manifest import validate as validate_platform_manifest
except ModuleNotFoundError:  # Direct `python tools/docs_check.py` execution.
    from validate_platform_manifest import validate as validate_platform_manifest


BLOCK_PATTERN = re.compile(
    r"<!--\s*petcare-docs:([a-z0-9-]+)\s*-->\s*```json\s*(.*?)\s*```",
    re.DOTALL,
)

BLOCK_LOCATIONS = {
    "architecture": Path("README.md"),
    "operations": Path("docs/setup.md"),
    "pico-contract": Path("docs/pico-wiring.md"),
    "hardware-gate": Path("docs/hardware-acceptance.md"),
    "workbook": Path("docs/hardware-acceptance.md"),
    "demo-contract": Path("docs/demo-runbook.md"),
    "privacy-contract": Path("docs/privacy.md"),
    "delivery-status": Path("docs/implementation-plan.md"),
}

HARDWARE_COMPONENTS = (
    "entrance_serial_boot",
    "petzone_serial_boot",
    "authenticated_sensor_subscription",
    "authenticated_status_subscription",
    "mqtt_reconnect",
    "entrance_sht31_installed",
    "petzone_sht31_installed",
    "spare_sht31_test",
    "entrance_ld2410c_installed",
    "petzone_ld2410c_installed",
    "food_bowl_calibrated",
    "water_bowl_calibrated",
    "fsr_left_raw",
    "fsr_center_raw",
    "fsr_right_raw",
    "webcam_fps_frame",
    "empty_bed_calibration",
    "dashboard_reflection",
)


class DocsCheckError(ValueError):
    pass


@dataclass(frozen=True)
class DocsCheckResult:
    checked_blocks: int
    hardware_status: str
    workbook_sha256: str


def parse_structured_blocks(path: Path) -> dict[str, Any]:
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as error:
        raise DocsCheckError(f"missing documentation file: {path}") from error
    blocks: dict[str, Any] = {}
    for name, raw in BLOCK_PATTERN.findall(text):
        if name in blocks:
            raise DocsCheckError(f"duplicate structured block {name} in {path}")
        try:
            blocks[name] = json.loads(raw)
        except json.JSONDecodeError as error:
            raise DocsCheckError(f"invalid JSON in structured block {name}: {error}") from error
    return blocks


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise DocsCheckError(message)


def _expect(actual: Any, expected: Any, label: str) -> None:
    if actual != expected:
        raise DocsCheckError(f"{label} does not match the repository authority")


def _cpp_integer(source: str, name: str) -> int:
    match = re.search(rf"\b{name}\s*=\s*([0-9A-Fa-fxX']+);", source)
    if match is None:
        raise DocsCheckError(f"unable to read firmware integer {name}")
    return int(match.group(1).replace("'", ""), 0)


def _cpp_string(source: str, name: str) -> str:
    match = re.search(rf"\b{name}\[\]\s*=\s*\"([^\"]+)\";", source)
    if match is None:
        match = re.search(rf"\b{name}\s*=\s*\"([^\"]+)\";", source)
    if match is None:
        raise DocsCheckError(f"unable to read firmware string {name}")
    return match.group(1)


def _load_blocks(root: Path) -> dict[str, Any]:
    by_file: dict[Path, dict[str, Any]] = {}
    for relative in set(BLOCK_LOCATIONS.values()):
        by_file[relative] = parse_structured_blocks(root / relative)
    blocks: dict[str, Any] = {}
    for name, relative in BLOCK_LOCATIONS.items():
        file_blocks = by_file[relative]
        _require(name in file_blocks, f"missing structured block {name} in {relative}")
        blocks[name] = file_blocks[name]
    extras = {
        name
        for relative, file_blocks in by_file.items()
        for name in file_blocks
        if BLOCK_LOCATIONS.get(name) != relative
    }
    _require(not extras, f"unexpected or misplaced structured blocks: {sorted(extras)}")
    return blocks


def _validate_architecture(block: Any) -> None:
    expected = {
        "pico_nodes": ["entrance-01", "petzone-01"],
        "camera_id": "pc-webcam-01",
        "camera_sources": ["usb", "file", "jetson", "disabled"],
        "subjects": ["dog_001", "cat_001"],
        "zones": ["food_bowl", "pet_bed"],
        "behaviors": ["eating", "resting"],
        "anomalies": ["no_meal_12h", "bed_sensor_mismatch"],
        "pico_emits_raw_fsr_only": True,
        "backend_owns_fsr_interpretation": True,
        "notification_channels": [],
    }
    _expect(block, expected, "architecture block")


def _validate_operations(block: Any, manifest: dict[str, Any], compose: str) -> None:
    managed = manifest["managed_exact"]
    expected_commands = {
        "bootstrap_toolchain": "powershell -NoProfile -ExecutionPolicy Bypass -File tools/bootstrap_toolchain.ps1",
        "bootstrap_pico_sdk": "powershell -NoProfile -ExecutionPolicy Bypass -File tools/bootstrap_pico_sdk.ps1",
        "bootstrap_services": "powershell -NoProfile -ExecutionPolicy Bypass -File tools/bootstrap_services.ps1",
        "provision_model": "powershell -NoProfile -ExecutionPolicy Bypass -File tools/provision_vision_model.ps1",
        "build_pico": "powershell -NoProfile -ExecutionPolicy Bypass -File tools/build_pico.ps1 -Profile all",
        "local_integration": "powershell -NoProfile -ExecutionPolicy Bypass -File tools/run_integration.ps1 -Provider Native",
        "full_check": "powershell -NoProfile -ExecutionPolicy Bypass -File tools/check_all.ps1",
        "docs_check": "$runtime = Get-Content -Raw .runtime/toolchain.json | ConvertFrom-Json; & $runtime.paths.python_path tools/docs_check.py --root .",
    }
    expected_pins = {
        "python": f"{managed['python']['version']}+{managed['python']['build']}",
        "uv": managed["uv"]["version"],
        "node": managed["node"]["version"],
        "pico_sdk": {
            key: managed["pico_sdk"][key]
            for key in ("tag", "commit", "board", "platform", "resolved_platform")
        },
        "model": {
            key: managed["model"][key]
            for key in ("package", "version", "file", "bytes", "sha256")
        },
        "containers": managed["containers"],
        "chromium": {
            "package": managed["chromium"]["package"],
            "version": managed["chromium"]["version"],
            "runtime_manifest": managed["chromium"]["runtime_manifest"],
        },
        "actions_checkout": managed["actions"]["actions/checkout"],
        "sites": managed["sites_plugin"],
    }
    _expect(block, {"commands": expected_commands, "pins": expected_pins}, "operations block")
    for image in managed["containers"].values():
        _require(image in compose, f"container digest missing from compose.yml: {image}")


def _validate_pico_contract(block: Any, config: str, mqtt: str) -> None:
    expected = {
        "board": "pico2_w",
        "platform": "rp2350",
        "resolved_platform": "rp2350-arm-s",
        "profiles": {
            "entrance-01": ["temperature", "humidity", "presence_moving", "presence_stationary"],
            "petzone-01": [
                "temperature", "humidity", "presence_moving", "presence_stationary",
                "food_weight", "water_weight", "bed_pressure_left", "bed_pressure_center", "bed_pressure_right",
            ],
        },
        "pins": {
            "sht31": {"i2c": _cpp_integer(config, "sht31_i2c_index"), "sda": _cpp_integer(config, "sht31_sda_pin"), "scl": _cpp_integer(config, "sht31_scl_pin"), "address": _cpp_integer(config, "sht31_address")},
            "ld2410c": {"uart": _cpp_integer(config, "ld2410c_uart_index"), "rx": _cpp_integer(config, "ld2410c_rx_pin")},
            "food_hx711": {"dout": _cpp_integer(config, "food_hx711_dout_pin"), "sck": _cpp_integer(config, "food_hx711_sck_pin")},
            "water_hx711": {"dout": _cpp_integer(config, "water_hx711_dout_pin"), "sck": _cpp_integer(config, "water_hx711_sck_pin")},
            "fsr": {"left": _cpp_integer(config, "fsr_left_pin"), "center": _cpp_integer(config, "fsr_center_pin"), "right": _cpp_integer(config, "fsr_right_pin")},
        },
        "electrical": {
            "logic_mv": _cpp_integer(config, "sensor_logic_supply_mv"),
            "gpio_max_mv": _cpp_integer(config, "gpio_input_max_mv"),
            "ld2410c_supply_mv": _cpp_integer(config, "ld2410c_supply_mv"),
            "ld2410c_uart_tx_mv": _cpp_integer(config, "ld2410c_uart_tx_mv"),
            "ld2410c_min_supply_ma": _cpp_integer(config, "ld2410c_min_supply_ma"),
            "fsr_supply_mv": _cpp_integer(config, "fsr_supply_mv"),
            "fsr_fixed_resistor_ohms": _cpp_integer(config, "fsr_fixed_resistor_ohms"),
            "fsr_adc_max": _cpp_integer(config, "fsr_adc_max"),
        },
        "cadence_ms": {
            "sht31": _cpp_integer(config, "sht31_cadence_ms"),
            "presence": _cpp_integer(config, "presence_cadence_ms"),
            "weight": _cpp_integer(config, "weight_cadence_ms"),
            "fsr": _cpp_integer(config, "fsr_cadence_ms"),
            "status": _cpp_integer(mqtt, "heartbeat_ms"),
        },
        "mqtt": {"qos": _cpp_integer(mqtt, "qos"), "sensor_retain": False, "status_retain": True},
        "sntp": {"primary": _cpp_string(mqtt, "primary_server"), "fallback": _cpp_string(mqtt, "fallback_server"), "retry_ms": _cpp_integer(mqtt, "retry_ms"), "resync_ms": _cpp_integer(mqtt, "resync_ms")},
        "status_payload_keys": ["device_id", "status", "observed_at"],
        "status_values": ["online", "offline"],
        "timestamp_format": "YYYY-MM-DDTHH:mm:ss.SSSZ",
        "fsr_payload": {"unit": "adc", "range": [0, _cpp_integer(config, "fsr_adc_max")], "interpretation_owner": "backend"},
    }
    _expect(block, expected, "pico-contract block")


def _validate_hardware(block: Any) -> str:
    _require(isinstance(block, dict), "hardware-gate block must be an object")
    _expect(block.get("aggregate"), "NOT RUN", "hardware aggregate")
    nodes = block.get("nodes")
    _expect(nodes, {"entrance-01": "NOT RUN", "petzone-01": "NOT RUN", "home-camera": "NOT RUN"}, "hardware node statuses")
    components = block.get("components")
    _require(isinstance(components, list), "hardware components must be a list")
    expected = [{"id": name, "status": "NOT RUN", "evidence": None} for name in HARDWARE_COMPONENTS]
    _expect(components, expected, "hardware component statuses")
    return "NOT RUN"


def _validate_workbook(root: Path, block: Any) -> str:
    _require(isinstance(block, dict), "workbook block must be an object")
    name = block.get("path")
    _require(isinstance(name, str), "workbook path must be a string")
    path = root / name
    _require(path.is_file(), "tracked workbook is missing")
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    _expect(block, {"path": name, "bytes": path.stat().st_size, "sha256": digest, "modified": False}, "workbook block")
    git_path = os.environ.get("PETCARE_TEST_GIT", "git")
    tracked = subprocess.run(
        [git_path, "-C", str(root), "ls-files", "--error-unmatch", "--", name],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=False,
    )
    _require(tracked.returncode == 0, "workbook is not tracked by Git")
    return digest


def _validate_demo(block: Any, root: Path, manifest: dict[str, Any], hosting: dict[str, Any]) -> None:
    model_source = (root / "backend/app/models.py").read_text(encoding="utf-8")
    main_source = (root / "backend/app/main.py").read_text(encoding="utf-8")
    tables = re.findall(r"__tablename__\s*=\s*\"([^\"]+)\"", model_source)
    expected = {
        "api_routes": [
            "GET /api/health", "GET /api/dashboard/summary", "GET /api/devices",
            "GET /api/sensors/latest", "GET /api/behaviors", "GET /api/anomalies",
            "GET /api/camera/status", "GET /api/video_feed", "GET /api/bed/status",
            "POST /api/bed/calibration", "GET /api/zones", "PUT /api/zones/{zone_name}",
            "WS /ws/dashboard",
        ],
        "zones": {
            "allowed": ["food_bowl", "pet_bed"],
            "frame": {"width": 640, "height": 480},
            "seed": {
                "food_bowl": [40, 260, 260, 470],
                "pet_bed": [320, 180, 630, 470],
            },
            "enabled_zones_must_not_overlap": True,
        },
        "rules": {
            "subjects": ["dog_001", "cat_001"],
            "eating": "30-second camera dwell; pre-entry 10-second median minus current 5-second median is at least 5 g",
            "bed_selection": "highest-confidence pet_bed detection; dog wins an exact confidence tie",
            "rest_owner": "one owner is retained until exit or handoff completes",
            "mismatch": ["sensor_check", "unconfirmed_pressure"],
            "anomalies": ["no_meal_12h", "bed_sensor_mismatch"],
        },
        "schema": {
            "application_tables": tables,
            "metadata_table": "alembic_version",
            "core_tables_before_clip_outbox": 9,
            "global_open_constraints": [
                "one open behavior event per behavior_type",
                "one open rest session globally",
            ],
        },
        "shutdown_order": ["stop ingress", "stop MQTT", "drain rule worker", "stop camera", "stop agent components", "stop dashboard hub", "dispose database"],
        "restart_disposition": {"eating": "close at last jointly fresh camera/sensor fact", "resting": "close at last_confirmed_at with close_reason restart", "replay": False},
        "sites": {
            "plugin_version": manifest["managed_exact"]["sites_plugin"]["version"],
            "starter": manifest["managed_exact"]["sites_plugin"]["starter"],
            "bindings": {"d1": hosting["d1"], "r2": hosting["r2"]},
            "project_id_present": isinstance(hosting.get("project_id"), str) and bool(hosting["project_id"]),
            "source_chain": ["dashboard subtree split", "tree equality", "per-command source credential", "vinext build", "Sites archive", "saved version", "private deployment", "status poll", "owner-authenticated / and /demo"],
            "access": "private",
            "environment_mutation": False,
            "demo_network": "document and same-origin static assets only",
        },
    }
    _expect(block, expected, "demo-contract block")
    order_markers = (
        "ingress.stop_accepting()",
        "ingestor.stop()",
        "await run_in_threadpool(worker.shutdown)",
        "camera_service.shutdown()",
        "await run_in_threadpool(stop_agent_components, agent_components)",
        "hub.shutdown()",
        "dispose_database()",
    )
    positions = [main_source.index(marker) for marker in order_markers]
    _require(positions == sorted(positions), "documented shutdown order no longer matches backend/app/main.py")
    _require("uq_behavior_events_one_open_per_type" in model_source and "uq_rest_sessions_one_open" in model_source, "global open-session constraints are missing")


def _validate_privacy(block: Any, root: Path) -> None:
    api_source = (root / "backend/app/api.py").read_text(encoding="utf-8")
    expected = {
        "local_bindings": {"postgresql": "127.0.0.1:55432", "mqtt": "127.0.0.1:18883", "backend": "127.0.0.1:8000", "dashboard": "127.0.0.1:3000"},
        "allowed_origins": ["http://127.0.0.1:3000", "http://localhost:3000"],
        "secrets": {"sources": ["process environment", "owner-only runtime files"], "docs": False, "logs": False, "git": False},
        "camera": {"default_source": "usb", "frames_persisted_by_default": False, "docker_webcam_claim": False},
        "sites_demo": {"fixture_only": True, "petcare_client": False, "api_or_websocket": False, "loopback_requests": False, "cross_origin_images": False},
        "claims": {"medical_diagnosis": False, "weight_reliability": False, "sleep_quality_reliability": False, "danger_detection": False},
    }
    _expect(block, expected, "privacy-contract block")
    for origin in expected["allowed_origins"]:
        _require(origin in api_source, f"documented Origin is missing from backend policy: {origin}")


def _validate_delivery(block: Any) -> None:
    expected = {
        "implemented": ["pico firmware", "backend", "dashboard", "local-live integration", "CI workflow", "private Sites deployment configuration"],
        "sites_access": "private",
        "physical_hardware": "NOT RUN",
        "deferred": ["physical installation evidence"],
    }
    _expect(block, expected, "delivery-status block")


def validate_repository_docs(root: Path) -> DocsCheckResult:
    root = root.resolve()
    blocks = _load_blocks(root)
    manifest_path = root / "tools/platform-manifest.json"
    errors = validate_platform_manifest(manifest_path)
    _require(not errors, f"invalid platform manifest: {errors}")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    compose = (root / "compose.yml").read_text(encoding="utf-8")
    hosting = json.loads((root / "dashboard/.openai/hosting.json").read_text(encoding="utf-8"))
    config = (root / "firmware/pico_pet_node/pico/include/petcare_config.hpp").read_text(encoding="utf-8")
    mqtt = (root / "firmware/pico_pet_node/pico/include/mqtt_publisher.hpp").read_text(encoding="utf-8")

    _validate_architecture(blocks["architecture"])
    _validate_operations(blocks["operations"], manifest, compose)
    _validate_pico_contract(blocks["pico-contract"], config, mqtt)
    hardware_status = _validate_hardware(blocks["hardware-gate"])
    workbook_sha256 = _validate_workbook(root, blocks["workbook"])
    _validate_demo(blocks["demo-contract"], root, manifest, hosting)
    _validate_privacy(blocks["privacy-contract"], root)
    _validate_delivery(blocks["delivery-status"])
    return DocsCheckResult(len(blocks), hardware_status, workbook_sha256)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    arguments = parser.parse_args()
    try:
        result = validate_repository_docs(arguments.root)
    except DocsCheckError as error:
        print(f"PETCARE_DOCS=FAIL {error}")
        return 1
    print(
        f"PETCARE_DOCS=PASS blocks={result.checked_blocks} "
        f"hardware={result.hardware_status} workbook_sha256={result.workbook_sha256}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
