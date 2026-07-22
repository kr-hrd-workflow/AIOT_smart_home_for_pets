from __future__ import annotations

import builtins
import os
import sys
from datetime import UTC, datetime
from pathlib import Path

import numpy as np
import pytest


ROOT = Path(__file__).resolve().parents[3]
BACKEND_ROOT = ROOT / "backend"
for import_root in (ROOT, BACKEND_ROOT):
    if str(import_root) not in sys.path:
        sys.path.insert(0, str(import_root))

from app.vision import MockFrameSource, VisionPipeline
from tools.e2e_check import (
    FixtureDetector,
    load_vision_sequence,
    prepare_camera_files,
    run_production_handler_sequence,
    serve_backend,
)


FIXTURE = ROOT / "backend" / "tests" / "fixtures" / "vision_sequence.json"


def test_prepare_camera_files_supports_unicode_paths(tmp_path: Path) -> None:
    import cv2

    valid = tmp_path / "한글 경로" / "valid.png"
    invalid = tmp_path / "한글 경로" / "invalid.png"

    prepare_camera_files(valid, invalid)

    valid_frame = cv2.imdecode(np.frombuffer(valid.read_bytes(), dtype=np.uint8), cv2.IMREAD_COLOR)
    invalid_frame = cv2.imdecode(np.frombuffer(invalid.read_bytes(), dtype=np.uint8), cv2.IMREAD_COLOR)
    assert valid_frame.shape == (480, 640, 3)
    assert invalid_frame.shape == (479, 640, 3)


def test_serve_backend_imports_application_before_starting_listener(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import threading
    import uvicorn

    events: list[str] = []
    real_import = builtins.__import__

    def recording_import(name: str, *args: object, **kwargs: object) -> object:
        if name == "app.main":
            events.append("app-import")
        return real_import(name, *args, **kwargs)

    class FakeThread:
        def __init__(self, **_kwargs: object) -> None:
            pass

        def start(self) -> None:
            events.append("listener-start")

    class FakeServer:
        should_exit = False

        def __init__(self, _config: object) -> None:
            pass

        def run(self) -> None:
            events.append("server-run")

    def fake_config(application: object, **_kwargs: object) -> object:
        assert not isinstance(application, str)
        events.append("config")
        return object()

    monkeypatch.setattr(builtins, "__import__", recording_import)
    monkeypatch.setattr(threading, "Thread", FakeThread)
    monkeypatch.setattr(uvicorn, "Config", fake_config)
    monkeypatch.setattr(uvicorn, "Server", FakeServer)

    assert serve_backend() == 2
    assert events == ["app-import", "config", "listener-start", "server-run"]


def test_vision_sequence_uses_exact_production_geometry_and_thresholds() -> None:
    sequence = load_vision_sequence(FIXTURE)

    assert sequence.frame_shape == (480, 640, 3)
    assert sequence.thresholds == {
        "calibration_window": 60,
        "eating_dwell": 30,
        "pressure_entry": 2,
        "pressure_exit": 7,
        "owner_exit": 3,
        "mismatch": 30,
    }
    assert sequence.zones == {
        "food_bowl": (40, 260, 260, 470),
        "pet_bed": (320, 180, 630, 470),
    }
    assert sequence.phase("empty_calibration").duration_seconds >= 60
    assert sequence.phase("dog_food_and_unconfirmed_pressure").duration_seconds >= 30
    assert sequence.phase("cat_sensor_check").duration_seconds >= 37


def test_fixture_detections_flow_through_the_production_vision_pipeline() -> None:
    sequence = load_vision_sequence(FIXTURE)
    detector = FixtureDetector(sequence)
    pipeline = VisionPipeline(
        detector,
        sequence.zones,
        source=MockFrameSource(np.zeros(sequence.frame_shape, dtype=np.uint8)),
    )

    detector.select("dog_food_and_unconfirmed_pressure")
    assert pipeline.source is not None
    dog_food = pipeline.process(pipeline.source.read(), datetime.now(UTC))
    assert [(item.subject_id, item.zone_name) for item in dog_food.detections] == [
        ("dog_001", "food_bowl")
    ]
    assert dog_food.bed_subject_ids == ()

    detector.select("dog_owner_retained")
    both = pipeline.process(pipeline.source.read(), datetime.now(UTC))
    assert both.bed_subject_ids == ("dog_001", "cat_001")
    assert both.selected_bed_subject_id == "cat_001"

    detector.select("cat_handoff")
    cat = pipeline.process(pipeline.source.read(), datetime.now(UTC))
    assert cat.bed_subject_ids == ("cat_001",)
    assert cat.selected_bed_subject_id == "cat_001"


@pytest.mark.skipif(
    os.environ.get("PETCARE_LIVE_FIXTURE") != "1",
    reason="run through tools/run_integration.ps1 with real local services",
)
def test_real_postgres_mqtt_production_handlers_drive_the_full_sequence(database_url: str) -> None:
    result = run_production_handler_sequence(
        load_vision_sequence(FIXTURE),
        database_url=database_url,
        services_manifest=Path(os.environ["PETCARE_SERVICES_MANIFEST"]),
        mqtt_username=os.environ["PETCARE_MQTT_USERNAME"],
        mqtt_password=os.environ["PETCARE_MQTT_PASSWORD"],
    )

    assert result["calibration_seconds"] >= 60
    assert result["eating_dwell_seconds"] >= 30
    assert result["pressure_entry_seconds"] >= 2
    assert result["pressure_exit_seconds"] >= 7
    assert result["mismatch_seconds"] >= 30
    assert result["behaviors"] == [
        ["eating", "dog_001"],
        ["resting", "dog_001"],
        ["resting", "cat_001"],
    ]
    assert result["mismatches"] == [
        ["unconfirmed_pressure", None],
        ["sensor_check", "cat_001"],
    ]
    assert result["open_behaviors"] == 0
    assert result["open_rest_sessions"] == 0
    assert result["worker_joined"] is True
