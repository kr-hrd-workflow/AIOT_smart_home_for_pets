import copy
import json
import subprocess
import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[2]
MANIFEST = ROOT / "tools" / "platform-manifest.json"
VALIDATOR = ROOT / "tools" / "validate_platform_manifest.py"

EXPECTED = {
    "schema_version": 1,
    "authority": "petcare-sites-mvp",
    "managed_exact": {
        "git": {"windows_id": "Git.Git", "version": "2.55.0.2"},
        "uv": {
            "windows_id": "astral-sh.uv",
            "version": "0.11.28",
            "release_date": "2026-07-07",
            "windows": {
                "url": "https://github.com/astral-sh/uv/releases/download/0.11.28/uv-x86_64-pc-windows-msvc.zip",
                "sha256": "0A23463216D09C6A72FF80EF5DC5A795F07DC1575CB84D24596C2F124A441B7B",
            },
            "linux": {
                "url": "https://github.com/astral-sh/uv/releases/download/0.11.28/uv-x86_64-unknown-linux-gnu.tar.gz",
                "sha256": "E490A6464492183C5D4534A5527FB4440F7F2BB2F228162AD7E4AFE076DC0224",
            },
        },
        "python": {
            "version": "3.12.13",
            "build": "20260623",
            "windows": {
                "identity": "cpython-3.12.13-windows-x86_64-none",
                "url": "https://github.com/astral-sh/python-build-standalone/releases/download/20260623/cpython-3.12.13%2B20260623-x86_64-pc-windows-msvc-install_only_stripped.tar.gz",
                "sha256": "DE3E362376859B060FA8B856C434EFA81FCF6D4EDE3D6E177C7E2169670CAC50",
            },
            "linux": {
                "identity": "cpython-3.12.13-linux-x86_64-gnu",
                "url": "https://github.com/astral-sh/python-build-standalone/releases/download/20260623/cpython-3.12.13%2B20260623-x86_64-unknown-linux-gnu-install_only_stripped.tar.gz",
                "sha256": "10A452CAAC7041357805F0C19A60576DF53F1AB06D1ABFC9200F1F0157CB3BD1",
            },
        },
        "node": {
            "windows_id": "OpenJS.NodeJS.22",
            "version": "22.23.1",
            "windows": {
                "url": "https://nodejs.org/dist/v22.23.1/node-v22.23.1-x64.msi",
                "sha256": "4E41D4FEA6661EB330FA88B1CDCE2BA5E6B07D93F689C9D549CB9DD09CB9B2B0",
            },
            "linux": {
                "url": "https://nodejs.org/dist/v22.23.1/node-v22.23.1-linux-x64.tar.xz",
                "sha256": "9749E988F437343B7FA832C69DED82A312E41A03116D766797AC14F6F9EEE578",
            },
        },
        "cmake": {
            "windows_id": "Kitware.CMake",
            "version": "4.3.4",
            "windows": {
                "url": "https://github.com/Kitware/CMake/releases/download/v4.3.4/cmake-4.3.4-windows-x86_64.zip",
                "sha256": "86E5FCAFB38BDF58346A78B187C7B6B4F252AE5242CFFE24C463A92BBD2E77D1",
            },
            "linux": {
                "url": "https://github.com/Kitware/CMake/releases/download/v4.3.4/cmake-4.3.4-linux-x86_64.tar.gz",
                "sha256": "CA6F08CCBD5E6B0A9068D33317D0D1AFF7278D08CCCAED4529B8FBEAD7942A68",
            },
        },
        "ninja": {
            "windows_id": "Ninja-build.Ninja",
            "version": "1.13.2",
            "windows": {
                "url": "https://github.com/ninja-build/ninja/releases/download/v1.13.2/ninja-win.zip",
                "sha256": "07FC8261B42B20E71D1720B39068C2E14FFCEE6396B76FB7A795FB460B78DC65",
            },
            "linux": {
                "url": "https://github.com/ninja-build/ninja/releases/download/v1.13.2/ninja-linux.zip",
                "sha256": "5749CBC4E668273514150A80E387A957F933C6ED3F5F11E03FB30955E2BBEAD6",
            },
        },
        "visual_studio": {
            "windows_id": "Microsoft.VisualStudio.2022.BuildTools",
            "version": "17.14.35",
            "components": [
                "Microsoft.VisualStudio.Workload.VCTools",
                "Microsoft.VisualStudio.Component.VC.14.44.17.14.x86.x64",
                "Microsoft.VisualStudio.Component.Windows11SDK.26100",
            ],
        },
        "arm_gnu": {
            "windows_id": "Arm.GnuArmEmbeddedToolchain",
            "version": "14.2.Rel1",
            "windows": {
                "url": "https://developer.arm.com/-/media/Files/downloads/gnu/14.2.rel1/binrel/arm-gnu-toolchain-14.2.rel1-mingw-w64-x86_64-arm-none-eabi.zip",
                "sha256": "F074615953F76036E9A51B87F6577FDB4ED8E77D3322A6F68214E92E7859888F",
            },
            "linux": {
                "url": "https://developer.arm.com/-/media/Files/downloads/gnu/14.2.rel1/binrel/arm-gnu-toolchain-14.2.rel1-x86_64-arm-none-eabi.tar.xz",
                "sha256": "62A63B981FE391A9CBAD7EF51B17E49AEAA3E7B0D029B36CA1E9C3B2A9B78823",
            },
        },
        "postgresql": {"windows_id": "PostgreSQL.PostgreSQL.17", "version": "17.10-2"},
        "mosquitto": {"windows_id": "EclipseFoundation.Mosquitto", "version": "2.1.2"},
        "pico_sdk": {
            "url": "https://github.com/raspberrypi/pico-sdk.git",
            "tag": "2.1.1",
            "commit": "bddd20f928ce76142793bef434d4f75f4af6e433",
            "board": "pico2_w",
            "platform": "rp2350",
            "resolved_platform": "rp2350-arm-s",
        },
        "model": {
            "package": "ultralytics",
            "version": "8.3.0",
            "file": "yolo11n.pt",
            "url": "https://github.com/ultralytics/assets/releases/download/v8.3.0/yolo11n.pt",
            "bytes": 5613764,
            "sha256": "0EBBC80D4A7680D14987A577CD21342B65ECFD94632BD9A8DA63AE6417644EE1",
        },
        "containers": {
            "postgres": "postgres:17.10@sha256:0af65001d05296a2ead57ac4a6412433d8913d1bb5d0c88435a7d1e1ee5cb04b",
            "mosquitto": "eclipse-mosquitto:2.0.22@sha256:212f89e1eaeb2c322d6441b64396e3346026674db8fa9c27beac293405c32b3c",
        },
        "actions": {"actions/checkout": "93cb6efe18208431cddfb8368fd83d5badbf9bfd"},
        "sites_plugin": {"version": "0.1.27", "starter": "vinext"},
        "backend_dependencies": {
            "fastapi": "0.139.0", "pydantic": "2.13.4", "SQLAlchemy": "2.0.51",
            "alembic": "1.18.5", "psycopg[binary]": "3.3.4", "paho-mqtt": "2.1.0",
            "uvicorn": "0.51.0", "ultralytics": "8.3.0", "numpy": "1.26.4",
            "opencv-python": "4.10.0.84", "pytest": "9.1.1", "pytest-asyncio": "1.4.0",
            "httpx": "0.28.1",
        },
        "sites_dependencies": {
            "drizzle-orm": "0.45.2", "next": "16.2.6", "react": "19.2.6",
            "react-dom": "19.2.6", "react-loading-skeleton": "3.5.0",
            "@cloudflare/vite-plugin": "1.37.1", "@tailwindcss/postcss": "4.2.1",
            "@types/node": "22.19.19", "@types/react": "19.2.14", "@types/react-dom": "19.2.3",
            "@vitejs/plugin-react": "6.0.2", "@vitejs/plugin-rsc": "0.5.26",
            "drizzle-kit": "0.31.10", "eslint": "9.39.4", "eslint-config-next": "16.2.6",
            "react-server-dom-webpack": "19.2.6", "tailwindcss": "4.2.1",
            "typescript": "5.9.3", "vinext": "0.0.50", "vite": "8.0.13", "wrangler": "4.92.0",
        },
        "test_dependencies": {
            "vitest": "4.1.10", "@testing-library/react": "16.3.2",
            "@testing-library/user-event": "14.6.1", "@testing-library/jest-dom": "6.9.1",
            "jsdom": "29.1.1", "@playwright/test": "1.61.1", "@axe-core/playwright": "4.12.1",
        },
        "chromium": {
            "package": "@playwright/test",
            "version": "1.61.1",
            "browser": "chromium",
            "install_cli": "dashboard/node_modules/playwright/cli.js",
            "runtime_manifest": ".runtime/playwright.json",
            "runtime_fields": ["executable_path", "package_version", "browser_revision", "executable_sha256"],
        },
    },
    "runner_capability": {
        "ubuntu-24.04": {
            "git": {"minimum": "2.43"}, "bash": {"minimum": "5.2"},
            "gnu_c_cpp": {"minimum": "13.3"}, "gnu_binutils": {"minimum": "2.42"},
            "docker": {"minimum": "26.1"}, "compose": {"minimum": "2.27"},
        }
    },
}


def run_validator(path: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(VALIDATOR), "--manifest", str(path)],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )


def leaf_paths(value, prefix=()):
    if isinstance(value, dict):
        for key, child in value.items():
            yield from leaf_paths(child, prefix + (key,))
    elif isinstance(value, list):
        for index, child in enumerate(value):
            yield from leaf_paths(child, prefix + (index,))
    else:
        yield prefix


def mutate(data, path):
    target = data
    for key in path[:-1]:
        target = target[key]
    key = path[-1]
    value = target[key]
    target[key] = value + 1 if isinstance(value, int) else value + "-mutated"


def test_authority_manifest_is_the_complete_sealed_contract():
    assert json.loads(MANIFEST.read_text(encoding="utf-8")) == EXPECTED
    result = run_validator(MANIFEST)
    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize("leaf", list(leaf_paths(EXPECTED)), ids=lambda path: ".".join(map(str, path)))
def test_every_authority_leaf_mutation_is_rejected(tmp_path, leaf):
    baseline = tmp_path / "baseline.json"
    baseline.write_text(json.dumps(EXPECTED), encoding="utf-8")
    baseline_result = run_validator(baseline)
    assert baseline_result.returncode == 0, baseline_result.stderr

    changed = copy.deepcopy(EXPECTED)
    mutate(changed, leaf)
    altered = tmp_path / "altered.json"
    altered.write_text(json.dumps(changed), encoding="utf-8")
    assert run_validator(altered).returncode != 0


@pytest.mark.parametrize("change", ["missing", "extra", "alias", "wrong-shape"])
def test_malformed_or_weakened_authority_is_rejected(tmp_path, change):
    data = copy.deepcopy(EXPECTED)
    if change == "missing":
        del data["managed_exact"]["python"]["linux"]["sha256"]
    elif change == "extra":
        data["managed_exact"]["python"]["allow_system_fallback"] = True
    elif change == "alias":
        data["managed_exact"]["backend_dependencies"]["psycopg"] = data["managed_exact"]["backend_dependencies"].pop("psycopg[binary]")
    else:
        data["runner_capability"]["ubuntu-24.04"] = []
    path = tmp_path / "manifest.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    assert run_validator(path).returncode != 0
