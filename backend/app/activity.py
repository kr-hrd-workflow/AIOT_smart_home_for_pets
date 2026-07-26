from __future__ import annotations

from datetime import UTC, datetime, timedelta
from math import hypot

from sqlalchemy import select
from sqlalchemy.orm import Session

from .contracts import CameraDetectionIn
from .models import ActivityObservation


def _utc(observed_at: datetime) -> datetime:
    if observed_at.tzinfo is None or observed_at.utcoffset() is None:
        return observed_at.replace(tzinfo=UTC)
    return observed_at.astimezone(UTC)


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
            previous.center_x = detection.center_x
            previous.center_y = detection.center_y
            previous.distance = max(previous.distance, distance)
            previous.moving = previous.moving or distance >= 24
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
    return row
