from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from statistics import median
from typing import Literal

from .config import AppConfig
from .contracts import BedCalibrationError, ChannelName


CHANNELS: tuple[ChannelName, ...] = ("left", "center", "right")


@dataclass(frozen=True, slots=True)
class CalibrationSnapshot:
    window_start: datetime
    window_end: datetime
    counts: tuple[int, int, int]
    baselines: tuple[float, float, float]
    polarities: tuple[int, int, int]
    stability_limits: tuple[int, int, int]
    entry_threshold: int
    exit_threshold: int


@dataclass(frozen=True, slots=True)
class PressureFact:
    reading_id: int
    channel: ChannelName
    raw: int
    observed_at: datetime
    received_at_utc: datetime
    received_at_monotonic: float


@dataclass(frozen=True, slots=True)
class CameraFact:
    observed_at: datetime
    received_at_utc: datetime
    received_at_monotonic: float
    bed_subject_ids: tuple[Literal["dog_001", "cat_001"], ...]
    selected_bed_subject_id: Literal["dog_001", "cat_001"] | None
    camera_event_ids: Mapping[str, int]


@dataclass(frozen=True, slots=True)
class BedEvaluation:
    sensor_state: Literal["unavailable", "uncalibrated", "ready"]
    pressure_state: Literal["unavailable", "uncalibrated", "empty", "occupied"]
    fusion_state: Literal["unavailable", "empty", "confirmed_rest", "unconfirmed_pressure", "sensor_check"]
    camera_confirmed: bool
    aggregate_delta: float | None
    bed_subject_ids: tuple[Literal["dog_001", "cat_001"], ...]
    selected_bed_subject_id: Literal["dog_001", "cat_001"] | None


class BedState:
    def __init__(self) -> None:
        self.calibration: CalibrationSnapshot | None = None
        self.pressure_facts: dict[ChannelName, PressureFact] = {}
        self.camera_fact: CameraFact | None = None
        self._calibration_ready_until: datetime | None = None
        self._stable_pressure: Literal["empty", "occupied"] | None = None
        self._bootstrapped = False
        self._occupied_candidate: tuple[datetime, float] | None = None
        self._empty_candidate: tuple[datetime, float] | None = None
        self.pressure_transition_at: datetime | None = None

    def load_calibration(self, calibration: CalibrationSnapshot, *, restart: bool) -> None:
        self.calibration = calibration
        self.pressure_facts.clear()
        self.camera_fact = None
        self._calibration_ready_until = None if restart else calibration.window_end + timedelta(seconds=3)
        self._stable_pressure = None if restart else "empty"
        self._bootstrapped = not restart
        self.pressure_transition_at = None if restart else calibration.window_end
        self._reset_pressure_candidates()

    def observe_pressure(self, fact: PressureFact) -> None:
        self.pressure_facts[fact.channel] = fact
        self._evaluate_pressure(fact.received_at_utc, fact.received_at_monotonic)

    def observe_camera(self, fact: CameraFact) -> None:
        self.camera_fact = fact

    def evaluate(self, now_utc: datetime, now_monotonic: float) -> BedEvaluation:
        sensor_state, pressure_state, aggregate = self._evaluate_pressure(now_utc, now_monotonic)
        camera = self.camera_fact
        camera_fresh = camera is not None and timedelta(0) <= now_utc - camera.observed_at <= timedelta(seconds=3)
        if sensor_state != "ready" or pressure_state not in {"empty", "occupied"} or not camera_fresh:
            fusion = "unavailable"
            subjects: tuple[Literal["dog_001", "cat_001"], ...] = ()
            selected = None
        else:
            assert camera is not None
            subjects = camera.bed_subject_ids
            selected = camera.selected_bed_subject_id
            if pressure_state == "empty":
                fusion = "sensor_check" if subjects else "empty"
            else:
                fusion = "confirmed_rest" if subjects else "unconfirmed_pressure"
        return BedEvaluation(
            sensor_state=sensor_state,
            pressure_state=pressure_state,
            fusion_state=fusion,
            camera_confirmed=fusion == "confirmed_rest",
            aggregate_delta=aggregate,
            bed_subject_ids=subjects,
            selected_bed_subject_id=selected,
        )

    def _evaluate_pressure(
        self, now_utc: datetime, now_monotonic: float
    ) -> tuple[str, str, float | None]:
        calibration = self.calibration
        if calibration is None:
            self._reset_pressure_candidates()
            return "uncalibrated", "uncalibrated", None
        if any(channel not in self.pressure_facts for channel in CHANNELS):
            self._reset_pressure_candidates()
            if self._calibration_ready_until is not None and now_utc <= self._calibration_ready_until:
                return "ready", "empty", 0.0
            self._calibration_ready_until = None
            return "unavailable", "unavailable", None
        facts = tuple(self.pressure_facts[channel] for channel in CHANNELS)
        self._calibration_ready_until = None
        if any(not timedelta(0) <= now_utc - fact.observed_at <= timedelta(seconds=3) for fact in facts):
            self._reset_pressure_candidates()
            return "unavailable", "unavailable", None
        aggregate = sum(
            max(0.0, polarity * (fact.raw - baseline))
            for fact, baseline, polarity in zip(facts, calibration.baselines, calibration.polarities, strict=True)
        )
        if not self._bootstrapped:
            self._stable_pressure = "occupied" if aggregate >= calibration.entry_threshold else "empty"
            self._bootstrapped = True
            self.pressure_transition_at = now_utc
            self._reset_pressure_candidates()
        elif aggregate >= calibration.entry_threshold:
            self._empty_candidate = None
            if self._stable_pressure != "occupied":
                self._occupied_candidate = self._occupied_candidate or (now_utc, now_monotonic)
                if now_monotonic - self._occupied_candidate[1] >= 2.0:
                    self._stable_pressure = "occupied"
                    self.pressure_transition_at = self._occupied_candidate[0]
                    self._occupied_candidate = None
            else:
                self._occupied_candidate = None
        elif aggregate <= calibration.exit_threshold:
            self._occupied_candidate = None
            if self._stable_pressure != "empty":
                self._empty_candidate = self._empty_candidate or (now_utc, now_monotonic)
                if now_monotonic - self._empty_candidate[1] >= 7.0:
                    self._stable_pressure = "empty"
                    self.pressure_transition_at = self._empty_candidate[0]
                    self._empty_candidate = None
            else:
                self._empty_candidate = None
        else:
            self._reset_pressure_candidates()
        assert self._stable_pressure is not None
        return "ready", self._stable_pressure, aggregate

    def _reset_pressure_candidates(self) -> None:
        self._occupied_candidate = None
        self._empty_candidate = None

    def pressure_evidence_id(self) -> int | None:
        if any(channel not in self.pressure_facts for channel in CHANNELS):
            return None
        ranks = {channel: -index for index, channel in enumerate(CHANNELS)}
        return max(
            self.pressure_facts.values(),
            key=lambda fact: (fact.observed_at, ranks[fact.channel]),
        ).reading_id

    def deadline_requests(self) -> list[tuple[str, float, datetime]]:
        requests: list[tuple[str, float, datetime]] = []
        if self._occupied_candidate is not None:
            started_at, started_mono = self._occupied_candidate
            requests.append(("bed_pressure_candidate", started_mono + 2.0, started_at + timedelta(seconds=2)))
        if self._empty_candidate is not None:
            started_at, started_mono = self._empty_candidate
            requests.append(("bed_pressure_candidate", started_mono + 7.0, started_at + timedelta(seconds=7)))
        for channel, fact in self.pressure_facts.items():
            effective = fact.observed_at + timedelta(seconds=3, microseconds=1)
            due = fact.received_at_monotonic + max(0.0, (effective - fact.received_at_utc).total_seconds())
            requests.append((f"bed_pressure_stale:{channel}", due, effective))
        if self.camera_fact is not None:
            effective = self.camera_fact.observed_at + timedelta(seconds=3, microseconds=1)
            due = self.camera_fact.received_at_monotonic + max(
                0.0, (effective - self.camera_fact.received_at_utc).total_seconds()
            )
            requests.append(("bed_camera_stale", due, effective))
        return requests


def box_intersects_zone(box: tuple[int, int, int, int], zone: tuple[int, int, int, int]) -> bool:
    x, y, width, height = box
    x1, y1, x2, y2 = zone
    return x < x2 and x1 < x + width and y < y2 and y1 < y + height


def evaluate_calibration(
    samples: Mapping[str, Sequence[tuple[datetime, int]]],
    *,
    now: datetime,
    camera_available: bool,
    pet_boxes: Sequence[tuple[int, int, int, int]],
    zone: tuple[int, int, int, int] | None,
    config: AppConfig,
) -> CalibrationSnapshot | BedCalibrationError:
    window_start = now - timedelta(seconds=60)
    selected = {
        channel: [(observed_at, raw) for observed_at, raw in samples.get(channel, ()) if window_start < observed_at <= now]
        for channel in CHANNELS
    }
    unavailable = [
        channel
        for channel in CHANNELS
        if not selected[channel] or now - max(observed_at for observed_at, _raw in selected[channel]) > timedelta(seconds=3)
    ]
    if unavailable:
        return BedCalibrationError(
            code="sensor_unavailable",
            message="Bed pressure sensor is unavailable",
            channels=unavailable,
        )
    insufficient = [channel for channel in CHANNELS if len(selected[channel]) < 45]
    if insufficient:
        return BedCalibrationError(
            code="insufficient_samples",
            message="Bed pressure samples are insufficient",
            channels=insufficient,
        )
    if not camera_available:
        return BedCalibrationError(code="camera_unavailable", message="Camera is unavailable", channels=[])
    if zone is not None and any(box_intersects_zone(box, zone) for box in pet_boxes):
        return BedCalibrationError(code="occupied", message="Bed is occupied", channels=[])
    stability_limits = config.fsr_stability_counts
    unstable = [
        channel
        for channel, limit in zip(CHANNELS, stability_limits, strict=True)
        if max(raw for _time, raw in selected[channel]) - min(raw for _time, raw in selected[channel]) > limit
    ]
    if unstable:
        return BedCalibrationError(code="unstable", message="Bed pressure is unstable", channels=unstable)
    return CalibrationSnapshot(
        window_start=window_start,
        window_end=now,
        counts=tuple(len(selected[channel]) for channel in CHANNELS),
        baselines=tuple(float(median(raw for _time, raw in selected[channel])) for channel in CHANNELS),
        polarities=config.fsr_polarities,
        stability_limits=stability_limits,
        entry_threshold=config.fsr_entry_threshold,
        exit_threshold=config.fsr_exit_threshold,
    )
