from __future__ import annotations

from copy import deepcopy
from collections import deque
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal
from statistics import median
from threading import Lock
from time import monotonic
from typing import Callable, Literal
from zoneinfo import ZoneInfo

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .bed import (
    CHANNELS,
    BedEvaluation,
    BedState,
    CalibrationSnapshot,
    CameraFact,
    PressureFact,
    evaluate_calibration,
)
from .config import AppConfig
from .clip_outbox import enqueue_clip_trigger, utc_now
from .contracts import (
    AnomalyEventOut,
    BedCalibrationChannel,
    BedCalibrationError,
    BedCalibrationSuccess,
    BedChannelStatus,
    BedStatus,
    MismatchKind,
    RestCloseReason,
    SevenDayComparison,
    SubjectId,
)
from .events import CameraFrameCommitted, CalibrateBedCommand, DeviceStatusCommitted, SensorReadingCommitted
from .models import (
    AnomalyEvent,
    BedCalibration,
    BehaviorEvent,
    CameraEvent,
    Device,
    RestSession,
    SensorReading,
    Zone,
)


_CLIP_CLOCK_DISCONTINUITY_SECONDS = 0.025
_PROCESSED_IDENTITY_LIMIT = 4096


class _RecentIdentities:
    def __init__(self, limit: int) -> None:
        if limit < 1:
            raise ValueError("limit must be positive")
        self._limit = limit
        self._seen: set[object] = set()
        self._order: deque[object] = deque()

    def __contains__(self, identity: object) -> bool:
        return identity in self._seen

    def __len__(self) -> int:
        return len(self._seen)

    def add(self, identity: object) -> None:
        if identity in self._seen:
            return
        self._seen.add(identity)
        self._order.append(identity)
        if len(self._order) > self._limit:
            self._seen.remove(self._order.popleft())


@dataclass(frozen=True, slots=True)
class FoodFact:
    reading_id: int
    grams: float
    observed_at: datetime
    received_at_utc: datetime
    received_at_monotonic: float


@dataclass(frozen=True, slots=True)
class BowlCameraFact:
    observed_at: datetime
    received_at_utc: datetime
    received_at_monotonic: float
    subject_camera_event_ids: Mapping[SubjectId, int]


@dataclass(frozen=True, slots=True)
class OpenEating:
    subject_id: SubjectId
    started_at: datetime
    source_camera_event_id: int
    source_sensor_reading_id: int
    source_key: str


@dataclass(frozen=True, slots=True)
class CloseEating:
    ended_at: datetime


@dataclass(frozen=True, slots=True)
class OpenRest:
    subject_id: SubjectId
    started_at: datetime
    source_camera_event_id: int
    source_sensor_reading_id: int
    source_key: str


@dataclass(frozen=True, slots=True)
class CloseRest:
    ended_at: datetime
    close_reason: RestCloseReason


@dataclass(slots=True)
class _RestCandidate:
    subject_id: SubjectId
    started_at: datetime
    started_monotonic: float
    confirmed: OpenRest | None = None


class RestState:
    def __init__(self) -> None:
        self.owner: SubjectId | None = None
        self.last_confirmed_at: datetime | None = None
        self.candidate: _RestCandidate | None = None
        self.absence_started_at: datetime | None = None
        self.absence_started_monotonic: float | None = None

    def evaluate(
        self,
        bed: BedEvaluation,
        now_utc: datetime,
        now_monotonic: float,
        camera_event_ids: Mapping[SubjectId, int],
        pressure_sensor_id: int | None,
        pressure_exit_at: datetime | None,
    ) -> list[OpenRest | CloseRest]:
        if self.owner is None:
            if bed.fusion_state != "confirmed_rest" or bed.selected_bed_subject_id is None:
                self.candidate = None
                return []
            action = self._advance_candidate(
                bed.selected_bed_subject_id,
                now_utc,
                now_monotonic,
                camera_event_ids,
                pressure_sensor_id,
            )
            return [action] if action is not None else []
        if bed.fusion_state == "unavailable":
            self.candidate = None
            self.absence_started_at = None
            self.absence_started_monotonic = None
            if self.last_confirmed_at is None:
                return []
            reason: RestCloseReason = "sensor_loss" if bed.sensor_state != "ready" else "camera_loss"
            return [CloseRest(self.last_confirmed_at, reason)]
        if bed.pressure_state == "empty":
            self.candidate = None
            self.absence_started_at = None
            self.absence_started_monotonic = None
            ended_at = pressure_exit_at or self.last_confirmed_at
            return [CloseRest(ended_at, "pressure_exit")] if ended_at is not None else []
        if self.owner in bed.bed_subject_ids:
            self.last_confirmed_at = now_utc
            self.candidate = None
            self.absence_started_at = None
            self.absence_started_monotonic = None
            return []
        if self.absence_started_monotonic is None:
            self.absence_started_at = now_utc
            self.absence_started_monotonic = now_monotonic
        if bed.selected_bed_subject_id is None:
            self.candidate = None
        else:
            self._advance_candidate(
                bed.selected_bed_subject_id,
                now_utc,
                now_monotonic,
                camera_event_ids,
                pressure_sensor_id,
            )
        if now_monotonic - self.absence_started_monotonic < 3.0:
            return []
        assert self.absence_started_at is not None
        actions: list[OpenRest | CloseRest] = [CloseRest(self.absence_started_at, "camera_exit")]
        if self.candidate is not None and self.candidate.confirmed is not None:
            actions.append(self.candidate.confirmed)
        return actions

    def _advance_candidate(
        self,
        subject_id: SubjectId,
        now_utc: datetime,
        now_monotonic: float,
        camera_event_ids: Mapping[SubjectId, int],
        pressure_sensor_id: int | None,
    ) -> OpenRest | None:
        if self.candidate is None or self.candidate.subject_id != subject_id:
            self.candidate = _RestCandidate(subject_id, now_utc, now_monotonic)
        candidate = self.candidate
        if candidate.confirmed is not None:
            return candidate.confirmed
        if now_monotonic - candidate.started_monotonic < 2.0:
            return None
        camera_id = camera_event_ids.get(subject_id)
        if camera_id is None or pressure_sensor_id is None:
            return None
        candidate.confirmed = OpenRest(
            subject_id,
            now_utc,
            camera_id,
            pressure_sensor_id,
            f"resting:{subject_id}:{camera_id}:{pressure_sensor_id}",
        )
        return candidate.confirmed

    def mark_open(self, action: OpenRest) -> None:
        self.owner = action.subject_id
        self.last_confirmed_at = action.started_at
        self.candidate = None
        self.absence_started_at = None
        self.absence_started_monotonic = None

    def mark_closed(self) -> None:
        self.owner = None
        self.last_confirmed_at = None
        self.candidate = None
        self.absence_started_at = None
        self.absence_started_monotonic = None

    def shutdown(self, now_utc: datetime, *, still_confirmed: bool) -> CloseRest | None:
        if self.owner is None:
            return None
        ended_at = now_utc if still_confirmed else self.last_confirmed_at
        return CloseRest(ended_at, "shutdown") if ended_at is not None else None

    def deadline_requests(self) -> list[tuple[str, float, datetime]]:
        requests: list[tuple[str, float, datetime]] = []
        if self.candidate is not None and self.candidate.confirmed is None:
            requests.append(
                (
                    "rest_candidate",
                    self.candidate.started_monotonic + 2.0,
                    self.candidate.started_at + timedelta(seconds=2),
                )
            )
        if self.absence_started_monotonic is not None and self.absence_started_at is not None:
            requests.append(
                (
                    "rest_absence",
                    self.absence_started_monotonic + 3.0,
                    self.absence_started_at + timedelta(seconds=3),
                )
            )
        return requests


def utc_key(value: datetime) -> str:
    return value.astimezone(ZoneInfo("UTC")).isoformat(timespec="microseconds").replace("+00:00", "Z")


@dataclass(frozen=True, slots=True)
class MismatchAttempt:
    mismatch_kind: MismatchKind
    subject_id: SubjectId | None
    episode_started_at: datetime
    occurred_at: datetime
    source_key: str


class MismatchState:
    def __init__(self) -> None:
        self.kind: MismatchKind | None = None
        self.subject_id: SubjectId | None = None
        self.started_at: datetime | None = None
        self.started_monotonic: float | None = None
        self.handled = False

    def observe(
        self,
        kind: MismatchKind,
        subject_id: SubjectId | None,
        started_at: datetime,
        started_monotonic: float,
    ) -> None:
        if (kind, subject_id) == (self.kind, self.subject_id):
            return
        self.kind = kind
        self.subject_id = subject_id
        self.started_at = started_at
        self.started_monotonic = started_monotonic
        self.handled = False

    def clear(self) -> None:
        self.kind = None
        self.subject_id = None
        self.started_at = None
        self.started_monotonic = None
        self.handled = False

    def evaluate(self, now_utc: datetime, now_monotonic: float) -> MismatchAttempt | None:
        if self.kind is None or self.started_at is None or self.started_monotonic is None or self.handled:
            return None
        if now_monotonic - self.started_monotonic < 30.0:
            return None
        identity = self.subject_id or "petzone-01"
        attempt = MismatchAttempt(
            mismatch_kind=self.kind,
            subject_id=self.subject_id,
            episode_started_at=self.started_at,
            occurred_at=now_utc,
            source_key=f"{identity}:bed_sensor_mismatch:{self.kind}:{utc_key(self.started_at)}",
        )
        self.handled = True
        return attempt

    def deadline_requests(self) -> list[tuple[str, float, datetime]]:
        if self.started_at is None or self.started_monotonic is None or self.handled:
            return []
        return [
            (
                "bed_mismatch",
                self.started_monotonic + 30.0,
                self.started_at + timedelta(seconds=30),
            )
        ]


@dataclass(frozen=True, slots=True)
class MetricSession:
    started_at: datetime
    ended_at: datetime | None
    close_reason: str | None


@dataclass(frozen=True, slots=True)
class RestMetrics:
    today_seconds: int
    nighttime_exit_count: int
    seven_day: SevenDayComparison


def local_day_metrics(
    sessions: list[MetricSession],
    *,
    earliest_pressure_at: datetime | None,
    now: datetime,
    timezone: ZoneInfo,
) -> RestMetrics:
    local_now = now.astimezone(timezone)
    local_midnight = datetime.combine(local_now.date(), datetime.min.time(), tzinfo=timezone)
    today_start = local_midnight.astimezone(ZoneInfo("UTC"))
    baseline_bounds = [
        ((local_midnight - timedelta(days=offset)).astimezone(ZoneInfo("UTC"))) for offset in range(7, -1, -1)
    ]

    def total(start: datetime, end: datetime) -> Decimal:
        seconds = Decimal(0)
        for session in sessions:
            session_end = now if session.ended_at is None else session.ended_at
            session_start = max(session.started_at, today_start) if session.ended_at is None else session.started_at
            overlap_start = max(start, session_start)
            overlap_end = min(end, session_end)
            if overlap_end > overlap_start:
                seconds += Decimal(str((overlap_end - overlap_start).total_seconds()))
        return seconds

    today_seconds = int(total(today_start, now).quantize(Decimal("1"), rounding=ROUND_HALF_UP))
    history_start = baseline_bounds[0]
    if earliest_pressure_at is None or earliest_pressure_at > history_start:
        complete_days = 0
        if earliest_pressure_at is not None:
            complete_days = sum(1 for start in baseline_bounds[:-1] if start >= earliest_pressure_at)
        seven_day = SevenDayComparison(
            status="insufficient_data",
            today_seconds=today_seconds,
            baseline_seconds=None,
            difference_seconds=None,
            percent_change=None,
            complete_days=min(6, complete_days),
        )
    else:
        totals = [total(baseline_bounds[index], baseline_bounds[index + 1]) for index in range(7)]
        baseline = int((sum(totals, Decimal(0)) / Decimal(7)).quantize(Decimal("1"), rounding=ROUND_HALF_UP))
        difference = today_seconds - baseline
        if baseline == 0:
            seven_day = SevenDayComparison(
                status="zero_baseline",
                today_seconds=today_seconds,
                baseline_seconds=0,
                difference_seconds=today_seconds,
                percent_change=None,
                complete_days=7,
            )
        else:
            percent = float(
                (Decimal(100) * Decimal(difference) / Decimal(baseline)).quantize(
                    Decimal("0.1"), rounding=ROUND_HALF_UP
                )
            )
            seven_day = SevenDayComparison(
                status="ready",
                today_seconds=today_seconds,
                baseline_seconds=baseline,
                difference_seconds=difference,
                percent_change=percent,
                complete_days=7,
            )
    nighttime = sum(
        1
        for session in sessions
        if session.ended_at is not None
        and session.close_reason == "pressure_exit"
        and session.ended_at.astimezone(timezone).date() == local_now.date()
        and (
            session.ended_at.astimezone(timezone).hour >= 22
            or session.ended_at.astimezone(timezone).hour < 6
        )
    )
    return RestMetrics(today_seconds, nighttime, seven_day)


@dataclass(slots=True)
class _Dwell:
    started_at: datetime
    started_monotonic: float
    pre_entry_weight_g: float


class EatingState:
    def __init__(self) -> None:
        self.food_facts: deque[FoodFact] = deque()
        self.camera_fact: BowlCameraFact | None = None
        self.dwells: dict[SubjectId, _Dwell] = {}
        self.open_subject_id: SubjectId | None = None
        self.open_started_at: datetime | None = None
        self.last_inside_at: datetime | None = None
        self.outside_started_monotonic: float | None = None
        self.last_jointly_fresh_at: datetime | None = None
        self.armed = True
        self.blocked_subjects: set[SubjectId] = set()
        self.empty_started_monotonic: float | None = None
        self.empty_started_at: datetime | None = None

    def observe_food(self, fact: FoodFact) -> None:
        self.food_facts.append(fact)
        cutoff = fact.received_at_utc - timedelta(seconds=130)
        while self.food_facts and self.food_facts[0].observed_at <= cutoff:
            self.food_facts.popleft()

    def observe_camera(self, fact: BowlCameraFact) -> None:
        self.camera_fact = fact
        present = set(fact.subject_camera_event_ids)
        if self.open_subject_id is not None:
            if self.open_subject_id in present:
                self.last_inside_at = fact.observed_at
                self.outside_started_monotonic = None
            elif self.outside_started_monotonic is None:
                self.outside_started_monotonic = fact.received_at_monotonic
        elif self.armed:
            for subject_id in ("dog_001", "cat_001"):
                if subject_id not in present:
                    self.dwells.pop(subject_id, None)
                    continue
                if subject_id not in self.dwells and subject_id not in self.blocked_subjects:
                    pre = self._median_window(fact.observed_at - timedelta(seconds=10), fact.observed_at, minimum=5)
                    if pre is not None:
                        self.dwells[subject_id] = _Dwell(fact.observed_at, fact.received_at_monotonic, pre[0])
        if present:
            self.empty_started_monotonic = None
            self.empty_started_at = None
        elif self.empty_started_monotonic is None:
            self.empty_started_monotonic = fact.received_at_monotonic
            self.empty_started_at = fact.observed_at

    def evaluate(self, now_utc: datetime, now_monotonic: float) -> list[OpenEating | CloseEating]:
        camera = self.camera_fact
        latest_food = self.food_facts[-1] if self.food_facts else None
        if self.open_subject_id is not None:
            camera_fresh = self._fresh(camera.observed_at if camera else None, now_utc)
            food_fresh = self._fresh(latest_food.observed_at if latest_food else None, now_utc)
            if not camera_fresh or not food_fresh:
                return [CloseEating(self.last_jointly_fresh_at)] if self.last_jointly_fresh_at is not None else []
            assert camera is not None and latest_food is not None
            self.last_jointly_fresh_at = max(camera.observed_at, latest_food.observed_at)
            if self.outside_started_monotonic is not None and now_monotonic - self.outside_started_monotonic >= 5.0:
                assert self.last_inside_at is not None
                return [CloseEating(self.last_inside_at)]
            return []
        if (not self.armed or self.blocked_subjects) and self.empty_started_monotonic is not None:
            if now_monotonic - self.empty_started_monotonic >= 10.0:
                assert self.empty_started_at is not None
                if len([fact for fact in self.food_facts if self.empty_started_at < fact.observed_at <= now_utc]) >= 5:
                    self.armed = True
                    self.blocked_subjects.clear()
                    self.dwells.clear()
        if not self.armed:
            return []
        if camera is None or latest_food is None or not self._fresh(camera.observed_at, now_utc) or not self._fresh(
            latest_food.observed_at, now_utc
        ):
            self.dwells.clear()
            return []
        current = self._median_window(now_utc - timedelta(seconds=5), now_utc, minimum=4)
        if current is None:
            return []
        current_weight, source_sensor_id = current
        candidates: list[tuple[datetime, Literal["dog_001", "cat_001"], _Dwell]] = []
        for subject_id in ("dog_001", "cat_001"):
            dwell = self.dwells.get(subject_id)
            if dwell is None:
                continue
            elapsed = now_monotonic - dwell.started_monotonic
            if elapsed > 120.0:
                self.dwells.pop(subject_id, None)
                self.blocked_subjects.add(subject_id)
                continue
            if elapsed >= 30.0 and dwell.pre_entry_weight_g - current_weight >= 5.0:
                candidates.append((dwell.started_at, subject_id, dwell))
        if not candidates:
            return []
        _started, subject_id, dwell = min(candidates, key=lambda item: (item[0], 0 if item[1] == "dog_001" else 1))
        camera_id = camera.subject_camera_event_ids.get(subject_id)
        if camera_id is None:
            return []
        return [
            OpenEating(
                subject_id=subject_id,
                started_at=dwell.started_at,
                source_camera_event_id=camera_id,
                source_sensor_reading_id=source_sensor_id,
                source_key=f"eating:{subject_id}:{camera_id}:{source_sensor_id}",
            )
        ]

    def mark_open(self, action: OpenEating) -> None:
        self.open_subject_id = action.subject_id
        self.open_started_at = action.started_at
        self.last_inside_at = self.camera_fact.observed_at if self.camera_fact else action.started_at
        latest_food = self.food_facts[-1] if self.food_facts else None
        self.last_jointly_fresh_at = max(
            self.last_inside_at,
            latest_food.observed_at if latest_food is not None else action.started_at,
        )
        self.outside_started_monotonic = None
        self.dwells.clear()
        self.blocked_subjects.clear()

    def mark_closed(self) -> None:
        self.open_subject_id = None
        self.open_started_at = None
        self.last_inside_at = None
        self.last_jointly_fresh_at = None
        self.outside_started_monotonic = None
        self.armed = False
        self.dwells.clear()
        self.blocked_subjects.clear()

    def expire_dwell(self, subject_id: SubjectId) -> None:
        if subject_id in self.dwells:
            self.dwells.pop(subject_id)
            self.blocked_subjects.add(subject_id)

    def _median_window(self, start_exclusive: datetime, end_inclusive: datetime, *, minimum: int) -> tuple[float, int] | None:
        selected = [fact for fact in self.food_facts if start_exclusive < fact.observed_at <= end_inclusive]
        if len(selected) < minimum or not self._fresh(selected[-1].observed_at, end_inclusive):
            return None
        newest = max(selected, key=lambda fact: (fact.observed_at, fact.reading_id))
        return float(median(fact.grams for fact in selected)), newest.reading_id

    @staticmethod
    def _fresh(observed_at: datetime | None, evaluation_at: datetime) -> bool:
        return observed_at is not None and timedelta(0) <= evaluation_at - observed_at <= timedelta(seconds=3)

    def deadline_requests(self) -> list[tuple[str, float, datetime]]:
        requests: list[tuple[str, float, datetime]] = []
        if self.camera_fact is not None:
            effective = self.camera_fact.observed_at + timedelta(seconds=3, microseconds=1)
            requests.append(
                (
                    "eating_camera_stale",
                    self.camera_fact.received_at_monotonic
                    + max(0.0, (effective - self.camera_fact.received_at_utc).total_seconds()),
                    effective,
                )
            )
        if self.food_facts:
            fact = self.food_facts[-1]
            effective = fact.observed_at + timedelta(seconds=3, microseconds=1)
            requests.append(
                (
                    "eating_food_stale",
                    fact.received_at_monotonic + max(0.0, (effective - fact.received_at_utc).total_seconds()),
                    effective,
                )
            )
        for subject_id, dwell in self.dwells.items():
            requests.append(
                (
                    f"eating_dwell:{subject_id}",
                    dwell.started_monotonic + 30.0,
                    dwell.started_at + timedelta(seconds=30),
                )
            )
            requests.append(
                (
                    f"eating_timeout:{subject_id}",
                    dwell.started_monotonic + 120.0,
                    dwell.started_at + timedelta(seconds=120),
                )
            )
        if self.outside_started_monotonic is not None and self.last_inside_at is not None:
            requests.append(
                (
                    "eating_exit",
                    self.outside_started_monotonic + 5.0,
                    self.last_inside_at + timedelta(seconds=5),
                )
            )
        if not self.armed and self.empty_started_monotonic is not None and self.empty_started_at is not None:
            requests.append(
                (
                    "eating_rearm",
                    self.empty_started_monotonic + 10.0,
                    self.empty_started_at + timedelta(seconds=10),
                )
            )
        return requests


class BedCalibrationRejected(RuntimeError):
    def __init__(self, error: BedCalibrationError) -> None:
        super().__init__(error.message)
        self.error = error


class RuleEngine:
    def __init__(
        self,
        *,
        config: AppConfig,
        camera_service: object,
        publisher: object | None = None,
        outbox_now: Callable[[], datetime] = utc_now,
        outbox_monotonic: Callable[[], float] = monotonic,
    ) -> None:
        self.config = config
        self.camera_service = camera_service
        self.publisher = publisher
        self._outbox_now = outbox_now
        self._outbox_monotonic = outbox_monotonic
        self._clip_clock_sample: tuple[datetime, float] | None = None
        self._clip_intents_suppressed_until = 0.0
        self.dashboard_publisher: object | None = None
        self.eating = EatingState()
        self.bed = BedState()
        self.rest = RestState()
        self.mismatch = MismatchState()
        self._camera_event_ids: dict[SubjectId, int] = {}
        self._state_deadline_keys: set[str] = set()
        self._processed_sensor_ids = _RecentIdentities(_PROCESSED_IDENTITY_LIMIT)
        self._processed_frames = _RecentIdentities(_PROCESSED_IDENTITY_LIMIT)
        self._processed_status = _RecentIdentities(_PROCESSED_IDENTITY_LIMIT)
        self._dashboard_snapshot_lock = Lock()
        self._bed_status_snapshot: BedStatus | None = None

    @property
    def bed_status_snapshot(self) -> BedStatus | None:
        with self._dashboard_snapshot_lock:
            return None if self._bed_status_snapshot is None else self._bed_status_snapshot.model_copy(deep=True)

    def startup(self, session: Session, scheduler: object, now: datetime) -> None:
        for device_id in ("entrance-01", "petzone-01"):
            if session.get(Device, device_id) is None:
                session.add(Device(device_id=device_id))
        session.flush()
        self._close_orphan_eating(session, now)
        self._close_orphan_rest(session)
        calibration = session.execute(
            select(BedCalibration)
            .where(BedCalibration.device_id == "petzone-01")
            .order_by(BedCalibration.calibrated_at.desc(), BedCalibration.id.desc())
            .limit(1)
        ).scalar_one_or_none()
        session.commit()
        if calibration is not None:
            self.bed.load_calibration(self._snapshot(calibration), restart=True)
        for subject_id in ("dog_001", "cat_001"):
            self._schedule_no_meal(session, scheduler, subject_id, now, getattr(scheduler, "effective_monotonic", 0.0))
        self._sync_state_deadlines(scheduler)
        self.refresh_dashboard_snapshot(session, now, getattr(scheduler, "effective_monotonic", 0.0))

    def command(
        self,
        session: Session,
        command: CalibrateBedCommand,
        received_at_utc: datetime,
        received_at_monotonic: float,
        scheduler: object,
    ) -> BedCalibrationSuccess:
        window_start = received_at_utc - timedelta(seconds=60)
        samples: dict[str, list[tuple[datetime, int]]] = {}
        for channel in CHANNELS:
            rows = session.execute(
                select(SensorReading.observed_at, SensorReading.value_number)
                .where(
                    SensorReading.device_id == command.device_id,
                    SensorReading.sensor_type == f"bed_pressure_{channel}",
                    SensorReading.observed_at > window_start,
                    SensorReading.observed_at <= received_at_utc,
                )
                .order_by(SensorReading.observed_at, SensorReading.id)
            ).all()
            samples[channel] = [(observed_at, int(raw)) for observed_at, raw in rows if raw is not None]
        zone = session.get(Zone, "pet_bed")
        zone_box = None if zone is None or not zone.enabled else (zone.x1, zone.y1, zone.x2, zone.y2)
        pet_boxes = session.execute(
            select(CameraEvent.bbox_x, CameraEvent.bbox_y, CameraEvent.bbox_width, CameraEvent.bbox_height).where(
                CameraEvent.subject_id.in_(("dog_001", "cat_001")),
                CameraEvent.observed_at > window_start,
                CameraEvent.observed_at <= received_at_utc,
            )
        ).all()
        snapshot = evaluate_calibration(
            samples,
            now=received_at_utc,
            camera_available=bool(self.camera_service.available_for(window_start, received_at_utc)),
            pet_boxes=tuple(tuple(row) for row in pet_boxes),
            zone=zone_box,
            config=self.config,
        )
        if isinstance(snapshot, BedCalibrationError):
            raise BedCalibrationRejected(snapshot)
        row = BedCalibration(
            device_id=command.device_id,
            calibrated_at=received_at_utc,
            window_start=snapshot.window_start,
            window_end=snapshot.window_end,
            left_sample_count=snapshot.counts[0],
            left_baseline=snapshot.baselines[0],
            left_polarity=snapshot.polarities[0],
            left_stability_limit=snapshot.stability_limits[0],
            center_sample_count=snapshot.counts[1],
            center_baseline=snapshot.baselines[1],
            center_polarity=snapshot.polarities[1],
            center_stability_limit=snapshot.stability_limits[1],
            right_sample_count=snapshot.counts[2],
            right_baseline=snapshot.baselines[2],
            right_polarity=snapshot.polarities[2],
            right_stability_limit=snapshot.stability_limits[2],
            entry_threshold=snapshot.entry_threshold,
            exit_threshold=snapshot.exit_threshold,
        )
        rollback_snapshot = self._state_snapshot()
        session.add(row)
        try:
            session.flush()
        except BaseException:
            session.rollback()
            raise
        self.bed.load_calibration(snapshot, restart=False)
        self._evaluate(
            session,
            received_at_utc,
            received_at_monotonic,
            scheduler,
            rollback_snapshot=rollback_snapshot,
        )
        return BedCalibrationSuccess(
            device_id="petzone-01",
            calibrated_at=received_at_utc,
            window_start=snapshot.window_start,
            window_end=snapshot.window_end,
            channels=[
                BedCalibrationChannel(
                    channel=channel,
                    sample_count=snapshot.counts[index],
                    baseline=snapshot.baselines[index],
                    polarity=snapshot.polarities[index],
                )
                for index, channel in enumerate(CHANNELS)
            ],
        )

    def apply(
        self,
        session: Session,
        event: object,
        received_at_utc: datetime,
        received_at_monotonic: float,
        scheduler: object,
    ) -> None:
        rollback_snapshot = self._state_snapshot()
        processed_sensor_id: int | None = None
        processed_frame: tuple[str, datetime] | None = None
        processed_status: tuple[str, str, datetime] | None = None
        if isinstance(event, SensorReadingCommitted):
            if event.reading_id in self._processed_sensor_ids:
                return
            row = session.get(SensorReading, event.reading_id)
            if row is None:
                return
            processed_sensor_id = event.reading_id
            device = session.get(Device, event.device_id)
            if device is not None:
                device.status = "online"
                device.last_seen_at = max(filter(None, (device.last_seen_at, event.observed_at)))
                device.updated_at = received_at_utc
            self._schedule_device_offline(scheduler, event.device_id, received_at_utc, received_at_monotonic)
            if event.sensor_type == "food_weight" and row.value_number is not None:
                self.eating.observe_food(
                    FoodFact(row.id, float(row.value_number), row.observed_at, received_at_utc, received_at_monotonic)
                )
            elif event.sensor_type.startswith("bed_pressure_") and row.value_number is not None:
                channel = event.sensor_type.removeprefix("bed_pressure_")
                self.bed.observe_pressure(
                    PressureFact(row.id, channel, int(row.value_number), row.observed_at, received_at_utc, received_at_monotonic)
                )
        elif isinstance(event, DeviceStatusCommitted):
            identity = (event.device_id, event.status, event.observed_at)
            if identity in self._processed_status:
                return
            processed_status = identity
            device = session.get(Device, event.device_id)
            if device is not None:
                device.status = event.status
                device.last_seen_at = max(filter(None, (device.last_seen_at, event.observed_at)))
                device.updated_at = received_at_utc
            if event.status == "online":
                self._schedule_device_offline(scheduler, event.device_id, received_at_utc, received_at_monotonic)
            else:
                scheduler.cancel("device_offline", event.device_id)
        elif isinstance(event, CameraFrameCommitted):
            identity = (event.camera_id, event.observed_at)
            if identity in self._processed_frames:
                return
            processed_frame = identity
            rows = session.execute(select(CameraEvent).where(CameraEvent.id.in_(event.detection_ids))).scalars().all()
            by_subject = {row.subject_id: row for row in rows if row.subject_id in {"dog_001", "cat_001"}}
            bowl_ids = {
                subject_id: row.id
                for subject_id, row in by_subject.items()
                if row.zone_name == "food_bowl"
            }
            self.eating.observe_camera(
                BowlCameraFact(event.observed_at, received_at_utc, received_at_monotonic, bowl_ids)
            )
            self._camera_event_ids = {
                subject_id: by_subject[subject_id].id
                for subject_id in event.bed_subject_ids
                if subject_id in by_subject
            }
            self.bed.observe_camera(
                CameraFact(
                    event.observed_at,
                    received_at_utc,
                    received_at_monotonic,
                    event.bed_subject_ids,
                    event.selected_bed_subject_id,
                    self._camera_event_ids,
                )
            )
        else:
            raise TypeError("unsupported domain event")
        self._evaluate(
            session,
            received_at_utc,
            received_at_monotonic,
            scheduler,
            rollback_snapshot=rollback_snapshot,
        )
        if processed_sensor_id is not None:
            self._processed_sensor_ids.add(processed_sensor_id)
        if processed_frame is not None:
            self._processed_frames.add(processed_frame)
        if processed_status is not None:
            self._processed_status.add(processed_status)

    def deadline(
        self,
        session: Session,
        kind: str,
        key: str,
        effective_at: datetime,
        scheduler: object,
    ) -> None:
        now_monotonic = getattr(scheduler, "effective_monotonic")
        if kind == "device_offline":
            device = session.get(Device, key)
            if device is not None:
                device.status = "offline"
                device.updated_at = effective_at
                session.commit()
            self._refresh_dashboard_snapshot_after_commit(session, effective_at, now_monotonic)
            return
        if kind == "no_meal":
            self._emit_no_meal(session, key, effective_at)
            return
        rollback_snapshot = self._state_snapshot()
        if kind == "rule_state" and key.startswith("eating_timeout:"):
            self.eating.expire_dwell(key.removeprefix("eating_timeout:"))
        self._evaluate(
            session,
            effective_at,
            now_monotonic,
            scheduler,
            rollback_snapshot=rollback_snapshot,
        )

    def controlled_shutdown(self, session: Session, effective_at: datetime, scheduler: object) -> None:
        now_monotonic = getattr(scheduler, "effective_monotonic")
        evaluation = self.bed.evaluate(effective_at, now_monotonic)
        rest_close = self.rest.shutdown(effective_at, still_confirmed=evaluation.fusion_state == "confirmed_rest")
        if rest_close is not None:
            self._close_rest(session, rest_close)
            self.rest.mark_closed()
        if self.eating.open_subject_id is not None:
            actions = self.eating.evaluate(effective_at, now_monotonic)
            close = next((action for action in actions if isinstance(action, CloseEating)), None)
            if close is None and self.eating.last_inside_at is not None:
                close = CloseEating(self.eating.last_inside_at)
            if close is not None:
                self._close_eating(session, close)
                self.eating.mark_closed()
        session.commit()
        self._refresh_dashboard_snapshot_after_commit(session, effective_at, now_monotonic)

    def _evaluate(
        self,
        session: Session,
        now_utc: datetime,
        now_monotonic: float,
        scheduler: object,
        *,
        rollback_snapshot: tuple[object, ...] | None = None,
    ) -> None:
        snapshot = self._state_snapshot() if rollback_snapshot is None else rollback_snapshot
        try:
            pending_publish, pending_no_meal = self._evaluate_and_commit(
                session,
                now_utc,
                now_monotonic,
            )
        except BaseException:
            session.rollback()
            self._restore_state(snapshot)
            raise
        self._refresh_dashboard_snapshot_after_commit(session, now_utc, now_monotonic)
        for operation, subject_id in pending_no_meal:
            if operation == "cancel":
                scheduler.cancel("no_meal", subject_id)
            else:
                self._schedule_no_meal(session, scheduler, subject_id, now_utc, now_monotonic)
        for row in pending_publish:
            self._publish_anomaly(row)
        self._sync_state_deadlines(scheduler)

    def _evaluate_and_commit(
        self,
        session: Session,
        now_utc: datetime,
        now_monotonic: float,
    ) -> tuple[list[AnomalyEvent], list[tuple[str, SubjectId]]]:
        pending_publish: list[AnomalyEvent] = []
        pending_no_meal: list[tuple[str, SubjectId]] = []
        clip_events: list[BehaviorEvent | AnomalyEvent] = []
        for action in self.eating.evaluate(now_utc, now_monotonic):
            if isinstance(action, OpenEating):
                row = self._open_eating(session, action)
                if row is not None:
                    clip_events.append(row)
                    self.eating.mark_open(action)
                    pending_no_meal.append(("cancel", action.subject_id))
            else:
                subject_id = self.eating.open_subject_id
                if self._close_eating(session, action):
                    self.eating.mark_closed()
                    if subject_id is not None:
                        pending_no_meal.append(("schedule", subject_id))
        bed = self.bed.evaluate(now_utc, now_monotonic)
        rest_actions = self.rest.evaluate(
            bed,
            now_utc,
            now_monotonic,
            self._camera_event_ids,
            self.bed.pressure_evidence_id(),
            self.bed.pressure_transition_at,
        )
        for action in rest_actions:
            if isinstance(action, CloseRest):
                if self._close_rest(session, action):
                    self.rest.mark_closed()
            else:
                row = self._open_rest(session, action)
                if row is not None:
                    clip_events.append(row)
                    self.rest.mark_open(action)
        if self.rest.owner is not None and bed.fusion_state == "confirmed_rest" and self.rest.owner in bed.bed_subject_ids:
            open_session = session.execute(select(RestSession).where(RestSession.ended_at.is_(None))).scalar_one_or_none()
            if open_session is not None:
                open_session.last_confirmed_at = now_utc
        if bed.fusion_state == "unconfirmed_pressure":
            self.mismatch.observe("unconfirmed_pressure", None, now_utc, now_monotonic)
        elif bed.fusion_state == "sensor_check" and bed.selected_bed_subject_id is not None:
            self.mismatch.observe("sensor_check", bed.selected_bed_subject_id, now_utc, now_monotonic)
        else:
            self.mismatch.clear()
        attempt = self.mismatch.evaluate(now_utc, now_monotonic)
        if attempt is not None:
            row = self._persist_mismatch(session, attempt)
            if row is not None:
                clip_events.append(row)
                pending_publish.append(row)
        self._commit_with_clip_intents(session, clip_events)
        return pending_publish, pending_no_meal

    def _commit_with_clip_intents(
        self,
        session: Session,
        events: list[BehaviorEvent | AnomalyEvent],
    ) -> None:
        created_at = self._outbox_now()
        sampled_monotonic = self._outbox_monotonic()
        previous = self._clip_clock_sample
        self._clip_clock_sample = (created_at, sampled_monotonic)
        if previous is not None:
            wall_elapsed = (created_at - previous[0]).total_seconds()
            monotonic_elapsed = sampled_monotonic - previous[1]
            if (
                monotonic_elapsed < 0
                or abs(wall_elapsed - monotonic_elapsed) > _CLIP_CLOCK_DISCONTINUITY_SECONDS
            ):
                self._clip_intents_suppressed_until = sampled_monotonic + 60.0
        if events and sampled_monotonic >= self._clip_intents_suppressed_until:
            for event in events:
                enqueue_clip_trigger(session, event, created_at=created_at)
        session.commit()

    def _state_snapshot(self) -> tuple[object, ...]:
        return deepcopy(
            (
                self.eating,
                self.bed,
                self.rest,
                self.mismatch,
                self._camera_event_ids,
                self._state_deadline_keys,
            )
        )

    def _restore_state(self, snapshot: tuple[object, ...]) -> None:
        (
            self.eating,
            self.bed,
            self.rest,
            self.mismatch,
            self._camera_event_ids,
            self._state_deadline_keys,
        ) = snapshot

    def _open_eating(self, session: Session, action: OpenEating) -> BehaviorEvent | None:
        if session.execute(
            select(BehaviorEvent.id).where(BehaviorEvent.behavior_type == "eating", BehaviorEvent.ended_at.is_(None))
        ).scalar_one_or_none() is not None:
            return None
        if session.execute(select(BehaviorEvent.id).where(BehaviorEvent.source_key == action.source_key)).scalar_one_or_none():
            return None
        row = BehaviorEvent(
            subject_id=action.subject_id,
            behavior_type="eating",
            source_camera_event_id=action.source_camera_event_id,
            source_sensor_reading_id=action.source_sensor_reading_id,
            source_key=action.source_key,
            started_at=action.started_at,
        )
        session.add(row)
        session.flush()
        return row

    def _close_eating(self, session: Session, action: CloseEating) -> bool:
        row = session.execute(
            select(BehaviorEvent).where(BehaviorEvent.behavior_type == "eating", BehaviorEvent.ended_at.is_(None))
        ).scalar_one_or_none()
        if row is None:
            return False
        ended_at = max(row.started_at, action.ended_at)
        row.ended_at = ended_at
        row.duration_seconds = _duration(row.started_at, ended_at)
        row.updated_at = ended_at
        return True

    def _open_rest(self, session: Session, action: OpenRest) -> BehaviorEvent | None:
        if session.execute(select(RestSession.id).where(RestSession.ended_at.is_(None))).scalar_one_or_none() is not None:
            return None
        if session.execute(select(BehaviorEvent.id).where(BehaviorEvent.source_key == action.source_key)).scalar_one_or_none():
            return None
        behavior = BehaviorEvent(
            subject_id=action.subject_id,
            behavior_type="resting",
            source_camera_event_id=action.source_camera_event_id,
            source_sensor_reading_id=action.source_sensor_reading_id,
            source_key=action.source_key,
            started_at=action.started_at,
        )
        session.add(behavior)
        session.flush()
        session.add(
            RestSession(
                subject_id=action.subject_id,
                behavior_event_id=behavior.id,
                started_at=action.started_at,
                last_confirmed_at=action.started_at,
            )
        )
        session.flush()
        return behavior

    def _close_rest(self, session: Session, action: CloseRest) -> bool:
        row = session.execute(select(RestSession).where(RestSession.ended_at.is_(None))).scalar_one_or_none()
        if row is None:
            return False
        behavior = session.get(BehaviorEvent, row.behavior_event_id)
        if behavior is None:
            return False
        ended_at = max(row.started_at, action.ended_at)
        duration = _duration(row.started_at, ended_at)
        row.ended_at = ended_at
        row.duration_seconds = duration
        row.close_reason = action.close_reason
        row.updated_at = ended_at
        behavior.ended_at = ended_at
        behavior.duration_seconds = duration
        behavior.updated_at = ended_at
        return True

    def _persist_mismatch(self, session: Session, attempt: MismatchAttempt) -> AnomalyEvent | None:
        subject_filter = (
            AnomalyEvent.subject_id.is_(None)
            if attempt.subject_id is None
            else AnomalyEvent.subject_id == attempt.subject_id
        )
        duplicate = session.execute(
            select(AnomalyEvent.id).where(
                AnomalyEvent.anomaly_type == "bed_sensor_mismatch",
                subject_filter,
                AnomalyEvent.occurred_at > attempt.occurred_at - timedelta(minutes=15),
            )
        ).scalar_one_or_none()
        if duplicate is not None:
            return None
        row = AnomalyEvent(
            subject_id=attempt.subject_id,
            anomaly_type="bed_sensor_mismatch",
            severity="warning",
            mismatch_kind=attempt.mismatch_kind,
            source_behavior_event_id=None,
            source_key=attempt.source_key,
            message="Bed sensor and camera need checking",
            occurred_at=attempt.occurred_at,
        )
        session.add(row)
        session.flush()
        return row

    def _emit_no_meal(self, session: Session, subject_id: str, eligible_at: datetime) -> None:
        if session.execute(
            select(BehaviorEvent.id).where(
                BehaviorEvent.behavior_type == "eating",
                BehaviorEvent.subject_id == subject_id,
                BehaviorEvent.ended_at.is_(None),
            )
        ).scalar_one_or_none() is not None:
            return
        meal = session.execute(
            select(BehaviorEvent)
            .where(
                BehaviorEvent.behavior_type == "eating",
                BehaviorEvent.subject_id == subject_id,
                BehaviorEvent.ended_at.is_not(None),
            )
            .order_by(BehaviorEvent.ended_at.desc(), BehaviorEvent.id.desc())
            .limit(1)
        ).scalar_one_or_none()
        if meal is None or meal.ended_at + timedelta(hours=12) != eligible_at:
            return
        source_key = f"{subject_id}:no_meal_12h:{utc_key(eligible_at)}"
        if session.execute(select(AnomalyEvent.id).where(AnomalyEvent.source_key == source_key)).scalar_one_or_none():
            return
        if session.execute(
            select(AnomalyEvent.id).where(
                AnomalyEvent.subject_id == subject_id,
                AnomalyEvent.anomaly_type == "no_meal_12h",
                AnomalyEvent.occurred_at > eligible_at - timedelta(minutes=15),
            )
        ).scalar_one_or_none():
            return
        row = AnomalyEvent(
            subject_id=subject_id,
            anomaly_type="no_meal_12h",
            severity="warning",
            mismatch_kind=None,
            source_behavior_event_id=meal.id,
            source_key=source_key,
            message="No meal has been recorded for 12 hours",
            occurred_at=eligible_at,
        )
        session.add(row)
        session.commit()
        self._publish_anomaly(row)

    def _schedule_no_meal(
        self,
        session: Session,
        scheduler: object,
        subject_id: str,
        now_utc: datetime,
        now_monotonic: float,
    ) -> None:
        if session.execute(
            select(BehaviorEvent.id).where(
                BehaviorEvent.behavior_type == "eating",
                BehaviorEvent.subject_id == subject_id,
                BehaviorEvent.ended_at.is_(None),
            )
        ).scalar_one_or_none() is not None:
            scheduler.cancel("no_meal", subject_id)
            return
        meal = session.execute(
            select(BehaviorEvent)
            .where(
                BehaviorEvent.behavior_type == "eating",
                BehaviorEvent.subject_id == subject_id,
                BehaviorEvent.ended_at.is_not(None),
            )
            .order_by(BehaviorEvent.ended_at.desc(), BehaviorEvent.id.desc())
            .limit(1)
        ).scalar_one_or_none()
        if meal is None:
            scheduler.cancel("no_meal", subject_id)
            return
        eligible = meal.ended_at + timedelta(hours=12)
        due = now_monotonic + max(0.0, (eligible - now_utc).total_seconds())
        scheduler.schedule("no_meal", subject_id, due, eligible)

    def _schedule_device_offline(
        self, scheduler: object, device_id: str, received_at_utc: datetime, received_at_monotonic: float
    ) -> None:
        scheduler.schedule(
            "device_offline",
            device_id,
            received_at_monotonic + 30.0,
            received_at_utc + timedelta(seconds=30),
        )

    def _sync_state_deadlines(self, scheduler: object) -> None:
        requests = self.eating.deadline_requests() + self.bed.deadline_requests() + self.rest.deadline_requests() + self.mismatch.deadline_requests()
        current = {key for key, _due, _effective in requests}
        for key in self._state_deadline_keys - current:
            scheduler.cancel("rule_state", key)
        for key, due, effective in requests:
            scheduler.schedule("rule_state", key, due, effective)
        self._state_deadline_keys = current

    def _close_orphan_eating(self, session: Session, now: datetime) -> None:
        row = session.execute(
            select(BehaviorEvent).where(BehaviorEvent.behavior_type == "eating", BehaviorEvent.ended_at.is_(None))
        ).scalar_one_or_none()
        if row is None:
            return
        jointly_fresh_at = session.execute(
            select(func.max(func.greatest(CameraEvent.observed_at, SensorReading.observed_at)))
            .select_from(CameraEvent)
            .join(
                SensorReading,
                func.abs(func.extract("epoch", CameraEvent.observed_at - SensorReading.observed_at)) <= 3,
            )
            .where(
                CameraEvent.subject_id == row.subject_id,
                CameraEvent.zone_name == "food_bowl",
                CameraEvent.observed_at <= now,
                SensorReading.device_id == "petzone-01",
                SensorReading.sensor_type == "food_weight",
                SensorReading.observed_at <= now,
            )
        ).scalar_one_or_none()
        ended_at = max(row.started_at, min(jointly_fresh_at or row.started_at, now))
        row.ended_at = ended_at
        row.duration_seconds = _duration(row.started_at, ended_at)
        row.updated_at = ended_at

    def _close_orphan_rest(self, session: Session) -> None:
        row = session.execute(select(RestSession).where(RestSession.ended_at.is_(None))).scalar_one_or_none()
        if row is None:
            return
        behavior = session.get(BehaviorEvent, row.behavior_event_id)
        if behavior is None:
            return
        ended_at = max(row.started_at, row.last_confirmed_at)
        duration = _duration(row.started_at, ended_at)
        row.ended_at = ended_at
        row.duration_seconds = duration
        row.close_reason = "restart"
        row.updated_at = ended_at
        behavior.ended_at = ended_at
        behavior.duration_seconds = duration
        behavior.updated_at = ended_at

    @staticmethod
    def _snapshot(row: BedCalibration) -> CalibrationSnapshot:
        return CalibrationSnapshot(
            row.window_start,
            row.window_end,
            (row.left_sample_count, row.center_sample_count, row.right_sample_count),
            (row.left_baseline, row.center_baseline, row.right_baseline),
            (row.left_polarity, row.center_polarity, row.right_polarity),
            (row.left_stability_limit, row.center_stability_limit, row.right_stability_limit),
            row.entry_threshold,
            row.exit_threshold,
        )

    def rest_metrics(self, session: Session, now: datetime) -> RestMetrics:
        sessions = [
            MetricSession(row.started_at, row.ended_at, row.close_reason)
            for row in session.execute(select(RestSession)).scalars()
        ]
        earliest = session.execute(
            select(func.min(SensorReading.observed_at)).where(
                SensorReading.device_id == "petzone-01",
                SensorReading.sensor_type.in_(
                    ("bed_pressure_left", "bed_pressure_center", "bed_pressure_right")
                ),
            )
        ).scalar_one_or_none()
        return local_day_metrics(sessions, earliest_pressure_at=earliest, now=now, timezone=self.config.timezone)

    def _refresh_dashboard_snapshot_after_commit(
        self,
        session: Session,
        now: datetime,
        now_monotonic: float,
    ) -> None:
        try:
            self.refresh_dashboard_snapshot(session, now, now_monotonic)
        except Exception:
            pass

    def refresh_dashboard_snapshot(self, session: Session, now: datetime, now_monotonic: float) -> None:
        evaluation = self.bed.evaluate(now, now_monotonic)
        metrics = self.rest_metrics(session, now)
        open_rest = session.execute(select(RestSession).where(RestSession.ended_at.is_(None))).scalar_one_or_none()
        current_rest_seconds = 0 if open_rest is None else _duration(open_rest.started_at, now)
        calibration = self.bed.calibration
        channels: list[BedChannelStatus] = []
        for index, channel in enumerate(CHANNELS):
            fact = self.bed.pressure_facts.get(channel)
            baseline = None if calibration is None else float(calibration.baselines[index])
            polarity = None if calibration is None else calibration.polarities[index]
            available = (
                fact is not None
                and timedelta(0) <= now - fact.observed_at <= timedelta(seconds=self.config.sensor_ttl_seconds)
            )
            delta = None
            if fact is not None and baseline is not None and polarity is not None:
                delta = float(max(0.0, polarity * (fact.raw - baseline)))
            channels.append(
                BedChannelStatus(
                    channel=channel,
                    raw=None if fact is None else fact.raw,
                    baseline=baseline,
                    delta=delta,
                    polarity=polarity,
                    available=available,
                    observed_at=None if fact is None else fact.observed_at,
                )
            )
        snapshot = BedStatus(
            device_id="petzone-01",
            sensor_state=evaluation.sensor_state,
            pressure_state=evaluation.pressure_state,
            fusion_state=evaluation.fusion_state,
            camera_confirmed=evaluation.camera_confirmed,
            channels=channels,
            current_rest_seconds=current_rest_seconds,
            today_rest_seconds=metrics.today_seconds,
            nighttime_exit_count=metrics.nighttime_exit_count,
            seven_day=metrics.seven_day,
            calibrated_at=None if calibration is None else calibration.window_end,
        )
        with self._dashboard_snapshot_lock:
            self._bed_status_snapshot = snapshot
        if self.dashboard_publisher is not None:
            try:
                self.dashboard_publisher({"type": "bed_status", "payload": snapshot.model_copy(deep=True)})
            except Exception:
                pass

    def _publish_anomaly(self, row: AnomalyEvent) -> None:
        if self.publisher is None:
            return
        payload = AnomalyEventOut(
            id=row.id,
            subject_id=row.subject_id,
            anomaly_type=row.anomaly_type,
            severity=row.severity,
            mismatch_kind=row.mismatch_kind,
            message=row.message,
            occurred_at=row.occurred_at,
        )
        try:
            self.publisher({"type": "anomaly_alert", "payload": payload})
        except Exception:
            pass


def _duration(started_at: datetime, ended_at: datetime) -> int:
    seconds = max(0.0, (ended_at - started_at).total_seconds())
    return int(Decimal(str(seconds)).quantize(Decimal("1"), rounding=ROUND_HALF_UP))
