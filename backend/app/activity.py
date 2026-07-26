from __future__ import annotations

from datetime import UTC, datetime, timedelta
from math import hypot, isfinite

from sqlalchemy import select
from sqlalchemy.orm import Session

from .contracts import CameraDetectionIn
from .models import ActivityObservation, AnomalyEvent


REPETITIVE_MOTION_MESSAGE = "짧은 시간에 반복 이동이 관측됐습니다. 건강 판단이 아닌 카메라 관측 알림입니다."


def _utc(observed_at: datetime) -> datetime:
    if observed_at.tzinfo is None or observed_at.utcoffset() is None:
        return observed_at.replace(tzinfo=UTC)
    return observed_at.astimezone(UTC)


def _activity_window(session: Session, subject_id: str, observed_at: datetime) -> list[ActivityObservation]:
    with session.no_autoflush:
        persisted = session.execute(
            select(ActivityObservation).where(
                ActivityObservation.subject_id == subject_id,
                ActivityObservation.observed_at > observed_at - timedelta(seconds=120),
                ActivityObservation.observed_at <= observed_at,
            )
        ).scalars()
    pending = (
        row
        for row in session.new
        if isinstance(row, ActivityObservation)
        and row.subject_id == subject_id
        and observed_at - timedelta(seconds=120) < _utc(row.observed_at) <= observed_at
    )
    return sorted((*persisted, *pending), key=lambda row: (_utc(row.observed_at), row.id or 0))


def _has_recent_repetitive_motion(session: Session, subject_id: str, observed_at: datetime) -> bool:
    cutoff = observed_at - timedelta(minutes=15)
    with session.no_autoflush:
        persisted = session.execute(
            select(AnomalyEvent.id).where(
                AnomalyEvent.subject_id == subject_id,
                AnomalyEvent.anomaly_type == "repetitive_motion",
                AnomalyEvent.occurred_at > cutoff,
                AnomalyEvent.occurred_at <= observed_at,
            )
        ).first()
    return persisted is not None or any(
        isinstance(row, AnomalyEvent)
        and row.subject_id == subject_id
        and row.anomaly_type == "repetitive_motion"
        and cutoff < _utc(row.occurred_at) <= observed_at
        for row in session.new
    )


def _is_repetitive_motion(rows: list[ActivityObservation]) -> bool:
    if len(rows) < 30:
        return False
    moving = [row for row in rows if row.moving]
    if len(moving) < 12:
        return False

    travel = 0.0
    reversals = 0
    previous_row: ActivityObservation | None = None
    previous_vector: tuple[float, float] | None = None
    for row in rows:
        if previous_row is None:
            previous_row = row
            continue
        gap = _utc(row.observed_at) - _utc(previous_row.observed_at)
        dx = row.center_x - previous_row.center_x
        dy = row.center_y - previous_row.center_y
        length = hypot(dx, dy)
        valid_gap = timedelta() < gap <= timedelta(seconds=3)
        if valid_gap and isfinite(row.distance) and row.distance >= 0:
            travel += row.distance
        if not (
            row.moving
            and valid_gap
            and isfinite(row.distance)
            and isfinite(length)
            and length > 0
        ):
            previous_vector = None
        else:
            vector = (dx / length, dy / length)
            if previous_vector is not None and previous_vector[0] * vector[0] + previous_vector[1] * vector[1] <= -0.6:
                reversals += 1
            previous_vector = vector
        previous_row = row
    return travel >= 640 and reversals >= 6


def _record_repetitive_motion(session: Session, row: ActivityObservation) -> None:
    if not _is_repetitive_motion(_activity_window(session, row.subject_id, _utc(row.observed_at))):
        return
    observed_at = _utc(row.observed_at)
    if _has_recent_repetitive_motion(session, row.subject_id, observed_at):
        return
    session.add(
        AnomalyEvent(
            subject_id=row.subject_id,
            anomaly_type="repetitive_motion",
            severity="warning",
            mismatch_kind=None,
            source_behavior_event_id=None,
            source_key=f"repetitive_motion:{row.subject_id}:{observed_at.isoformat()}",
            message=REPETITIVE_MOTION_MESSAGE,
            occurred_at=observed_at,
        )
    )


def record_activity(session: Session, detection: CameraDetectionIn) -> ActivityObservation | None:
    if detection.subject_id is None:
        return None

    observed_at = detection.observed_at.astimezone(UTC).replace(microsecond=0)
    with session.no_autoflush:
        persisted = session.execute(
            select(ActivityObservation)
            .where(
                ActivityObservation.camera_id == detection.camera_id,
                ActivityObservation.subject_id == detection.subject_id,
                ActivityObservation.observed_at <= observed_at,
            )
            .order_by(ActivityObservation.observed_at.desc(), ActivityObservation.id.desc())
            .limit(1)
        ).scalar_one_or_none()
    pending = (
        row
        for row in session.new
        if isinstance(row, ActivityObservation)
        and row.camera_id == detection.camera_id
        and row.subject_id == detection.subject_id
        and _utc(row.observed_at) <= observed_at
    )
    previous = max(
        (row for row in (persisted, *pending) if row is not None),
        key=lambda row: (_utc(row.observed_at), row.id or 0),
        default=None,
    )
    distance = 0
    moving = False
    if previous is not None:
        previous_at = _utc(previous.observed_at)
        distance = hypot(detection.center_x - previous.center_x, detection.center_y - previous.center_y)
        if previous_at == observed_at:
            was_moving = previous.moving
            previous.center_x = detection.center_x
            previous.center_y = detection.center_y
            previous.distance = max(previous.distance, distance)
            previous.moving = previous.moving or distance >= 24
            if not was_moving and previous.moving:
                _record_repetitive_motion(session, previous)
            return previous
        if timedelta() < observed_at - previous_at <= timedelta(seconds=3):
            moving = distance >= 24
        else:
            distance = 0

    row = ActivityObservation(
        camera_id=detection.camera_id,
        subject_id=detection.subject_id,
        observed_at=observed_at,
        center_x=detection.center_x,
        center_y=detection.center_y,
        moving=moving,
        distance=distance,
    )
    session.add(row)
    _record_repetitive_motion(session, row)
    return row
