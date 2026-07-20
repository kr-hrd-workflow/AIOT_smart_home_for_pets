from __future__ import annotations

import importlib
from datetime import UTC, datetime, timedelta

from app.config import AppConfig
from app.contracts import BedCalibrationError


def test_calibration_overlap_is_positive_area_half_open() -> None:
    bed = importlib.import_module("app.bed")
    zone = (320, 180, 630, 470)

    assert bed.box_intersects_zone((319, 179, 2, 2), zone)
    assert not bed.box_intersects_zone((300, 180, 20, 20), zone)
    assert not bed.box_intersects_zone((630, 180, 20, 20), zone)


def test_calibration_uses_exact_window_medians_and_first_error_precedence() -> None:
    bed = importlib.import_module("app.bed")
    now = datetime(2026, 7, 20, 1, 0, tzinfo=UTC)
    config = AppConfig(database_url="postgresql+psycopg://petcare:x@127.0.0.1:55432/petcare_test")

    def channel(base: int, count: int = 45) -> list[tuple[datetime, int]]:
        return [(now - timedelta(seconds=count - index - 1), base + index % 3) for index in range(count)]

    samples = {"left": channel(100), "center": channel(200), "right": channel(300)}
    snapshot = bed.evaluate_calibration(
        samples,
        now=now,
        camera_available=True,
        pet_boxes=(),
        zone=(320, 180, 630, 470),
        config=config,
    )

    assert snapshot.counts == (45, 45, 45)
    assert snapshot.baselines == (101.0, 201.0, 301.0)
    assert snapshot.window_start == now - timedelta(seconds=60)
    assert snapshot.window_end == now

    failures = {
        "left": [],
        "center": channel(200, 44),
        "right": channel(300),
    }
    error = bed.evaluate_calibration(
        failures,
        now=now,
        camera_available=False,
        pet_boxes=((320, 180, 1, 1),),
        zone=(320, 180, 630, 470),
        config=config.model_copy(update={"fsr_stability_counts_right": 0}),
    )
    assert error == BedCalibrationError(
        code="sensor_unavailable",
        message="Bed pressure sensor is unavailable",
        channels=["left"],
    )


def test_pressure_bootstrap_candidates_and_unavailable_reset_are_exact() -> None:
    bed = importlib.import_module("app.bed")
    now = datetime(2026, 7, 20, 2, 0, tzinfo=UTC)
    calibration = bed.CalibrationSnapshot(
        window_start=now - timedelta(seconds=60),
        window_end=now,
        counts=(45, 45, 45),
        baselines=(100.0, 100.0, 100.0),
        polarities=(1, 1, 1),
        stability_limits=(40, 40, 40),
        entry_threshold=450,
        exit_threshold=250,
    )
    state = bed.BedState()
    state.load_calibration(calibration, restart=True)

    for channel in ("left", "center", "right"):
        state.observe_pressure(bed.PressureFact(1, channel, 200, now, now, 10.0))
    assert state.evaluate(now, 10.0).pressure_state == "empty"

    for channel in ("left", "center", "right"):
        state.observe_pressure(bed.PressureFact(2, channel, 250, now, now, 10.0))
    assert state.evaluate(now + timedelta(seconds=1, milliseconds=999), 11.999).pressure_state == "empty"
    assert state.evaluate(now + timedelta(seconds=2), 12.0).pressure_state == "occupied"

    for channel in ("left", "center", "right"):
        state.observe_pressure(bed.PressureFact(3, channel, 100, now + timedelta(seconds=2), now + timedelta(seconds=2), 12.0))
    for elapsed in (4.0, 6.0, 8.999):
        at = now + timedelta(seconds=elapsed)
        for channel in ("left", "center", "right"):
            state.observe_pressure(bed.PressureFact(3, channel, 100, at, at, 10.0 + elapsed))
    assert state.evaluate(now + timedelta(seconds=8, milliseconds=999), 18.999).pressure_state == "occupied"
    at = now + timedelta(seconds=9)
    for channel in ("left", "center", "right"):
        state.observe_pressure(bed.PressureFact(3, channel, 100, at, at, 19.0))
    assert state.evaluate(at, 19.0).pressure_state == "empty"
    assert state.evaluate(at + timedelta(seconds=3, microseconds=1), 22.000001).pressure_state == "unavailable"


def test_camera_confirmed_is_true_only_for_confirmed_rest_fusion() -> None:
    bed = importlib.import_module("app.bed")
    now = datetime(2026, 7, 20, 3, 0, tzinfo=UTC)
    calibration = bed.CalibrationSnapshot(
        window_start=now - timedelta(seconds=60),
        window_end=now,
        counts=(45, 45, 45),
        baselines=(100.0, 100.0, 100.0),
        polarities=(1, 1, 1),
        stability_limits=(40, 40, 40),
        entry_threshold=450,
        exit_threshold=250,
    )
    state = bed.BedState()
    state.load_calibration(calibration, restart=False)
    for channel in ("left", "center", "right"):
        state.observe_pressure(bed.PressureFact(1, channel, 300, now, now, 1.0))
    state.observe_camera(bed.CameraFact(now, now, 1.0, ("dog_001",), "dog_001", {"dog_001": 9}))

    confirmed = state.evaluate(now + timedelta(seconds=2), 3.0)
    assert (confirmed.fusion_state, confirmed.camera_confirmed) == ("confirmed_rest", True)

    state.observe_camera(bed.CameraFact(now + timedelta(seconds=2), now + timedelta(seconds=2), 3.0, (), None, {}))
    mismatch = state.evaluate(now + timedelta(seconds=2), 3.0)
    assert (mismatch.fusion_state, mismatch.camera_confirmed) == ("unconfirmed_pressure", False)
