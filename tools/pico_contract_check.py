from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
from pathlib import Path
from typing import TypeAlias, cast


JsonScalar: TypeAlias = None | bool | int | float | str
JsonValue: TypeAlias = JsonScalar | list["JsonValue"] | dict[str, "JsonValue"]
JsonObject: TypeAlias = dict[str, JsonValue]

ROOT = Path(__file__).resolve().parents[1]
RUNTIME = ROOT / ".runtime" / "toolchain.json"
BUILD_DIR = ROOT / ".runtime" / "pico-contract-build"


def require(condition: bool, label: str) -> None:
    if not condition:
        raise SystemExit(f"FAIL {label}")


def check_sensor_target_links() -> None:
    cmake = (ROOT / "firmware" / "pico_pet_node" / "pico" / "CMakeLists.txt").read_text(encoding="utf-8")
    function = cmake[cmake.index("function(add_petcare_node"):cmake.index("endfunction()")]
    missing = [
        target for target in ("entrance-01", "petzone-01")
        if "src/sensors.cpp" not in function or f"add_petcare_node({target} " not in cmake
    ]
    require(
        not missing,
        f"targets missing sensors.cpp source/link edge: {', '.join(missing)}",
    )


def check_fsr_ownership() -> None:
    pico = ROOT / "firmware" / "pico_pet_node" / "pico"
    config = (pico / "include" / "petcare_config.hpp").read_text(encoding="utf-8")
    expected = {
        "fsr_left_pin", "fsr_center_pin", "fsr_right_pin", "fsr_cadence_ms",
        "fsr_supply_mv", "fsr_fixed_resistor_ohms", "fsr_adc_max",
    }
    require(set(re.findall(r"\bfsr_[a-z0-9_]+\b", config)) == expected, "FSR firmware config ownership")
    firmware = "\n".join(
        path.read_text(encoding="utf-8")
        for path in (
            pico / "include" / "petcare_config.hpp",
            pico / "include" / "sensors.hpp",
            pico / "src" / "sensors.cpp",
            pico / "src" / "main.cpp",
        )
    ).lower()
    for term in ("baseline", "polarity", "stability", "threshold", "occupancy", "fusion", "entry", "exit"):
        require(not re.search(rf"(?:fsr[^\n;]*{term}|{term}[^\n;]*fsr)", firmware), f"Pico FSR {term} ownership")


def build_and_run() -> str:
    powershell = Path(os.environ["SystemRoot"]) / "System32" / "WindowsPowerShell" / "v1.0" / "powershell.exe"
    result = subprocess.run(
        [str(powershell), "-NoProfile", "-ExecutionPolicy", "Bypass", "-File",
         str(ROOT / "tools" / "build_pico_host.ps1"), "-RuntimePath", str(RUNTIME), "-BuildDir", str(BUILD_DIR)],
        cwd=ROOT, text=True, encoding="utf-8", errors="replace",
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, check=False,
    )
    require(result.returncode == 0, result.stdout)
    require("100% tests passed" in result.stdout, "CTest did not pass")
    demo = subprocess.run(
        [str(BUILD_DIR / "pet_node_demo.exe")], cwd=ROOT, text=True, encoding="utf-8", errors="strict",
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, check=False,
    )
    require(demo.returncode == 0, demo.stdout)
    return demo.stdout


def messages(output: str) -> dict[str, JsonObject]:
    lines = [line for line in output.splitlines() if line]
    require(len(lines) % 2 == 0, "demo output must be topic/payload pairs")
    parsed: dict[str, JsonObject] = {}
    for index in range(0, len(lines), 2):
        value = json.loads(lines[index + 1])
        require(isinstance(value, dict), f"payload after {lines[index]} must be an object")
        parsed[lines[index]] = cast(JsonObject, value)
    return parsed


def validate_sensor(topic: str, payload: JsonObject, expected_kind: type[object]) -> None:
    require(list(payload) == ["device_id", "sensor_type", "value", "unit", "observed_at"], f"sensor keys {topic}")
    value = payload["value"]
    require(type(value) is expected_kind, f"sensor scalar kind {topic}")


parser = argparse.ArgumentParser()
parser.add_argument("--check-fsr-ownership", action="store_true")
args = parser.parse_args()
check_sensor_target_links()
if args.check_fsr_ownership:
    check_fsr_ownership()

result = messages(build_and_run())
expected = {
    "entrance-01": {
        "temperature": ("C", float), "humidity": ("%", int),
        "presence_moving": ("bool", bool), "presence_stationary": ("bool", bool),
    },
    "petzone-01": {
        "temperature": ("C", float), "humidity": ("%", int),
        "presence_moving": ("bool", bool), "presence_stationary": ("bool", bool),
        "food_weight": ("g", int), "water_weight": ("g", int),
        "bed_pressure_left": ("adc", int), "bed_pressure_center": ("adc", int), "bed_pressure_right": ("adc", int),
    },
}
for device_id, sensors in expected.items():
    for sensor_type, (unit, kind) in sensors.items():
        topic = f"home/pico/{device_id}/sensor/{sensor_type}"
        require(topic in result, f"missing {topic}")
        payload = result[topic]
        validate_sensor(topic, payload, kind)
        require(payload["device_id"] == device_id, f"device {topic}")
        require(payload["sensor_type"] == sensor_type, f"sensor type {topic}")
        require(payload["unit"] == unit, f"unit {topic}")

for device_id in expected:
    topic = f"home/pico/{device_id}/status"
    require(topic in result, f"missing {topic}")
    payload = result[topic]
    require(list(payload) == ["device_id", "status", "observed_at"], f"status keys {topic}")
    require(payload["status"] in ("online", "offline"), f"status value {topic}")

serialized = json.dumps(result, separators=(",", ":"))
for retired in ("bed_weight", "light_lux", "motion", "door_open", "firmware_version", "ip", "uptime_sec", "timestamp"):
    require(f'"{retired}"' not in serialized, f"retired field {retired}")
print("PASS Pico two-profile sensor/status contract")
