from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import create_engine, event, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.activity import record_activity
from app.contracts import CameraDetectionIn
from app.models import ActivityObservation


@pytest.fixture()
def session() -> Iterator[Session]:
    engine = create_engine(
        "sqlite+pysqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    with engine.begin() as connection:
        connection.exec_driver_sql(
            "CREATE TABLE activity_observations ("
            "id INTEGER PRIMARY KEY, camera_id TEXT NOT NULL, subject_id TEXT NOT NULL, "
            "observed_at DATETIME NOT NULL, center_x INTEGER NOT NULL, center_y INTEGER NOT NULL, "
            "moving BOOLEAN NOT NULL, distance FLOAT NOT NULL, "
            "created_at DATETIME DEFAULT CURRENT_TIMESTAMP NOT NULL)"
        )
    current = sessionmaker(bind=engine, expire_on_commit=False)()
    try:
        yield current
    finally:
        current.close()
        engine.dispose()


def detection(
    observed_at: datetime,
    *,
    subject_id: str | None = "dog_001",
    center_x: int = 100,
    center_y: int = 100,
) -> CameraDetectionIn:
    detected_type = {None: "person", "dog_001": "dog", "cat_001": "cat"}[subject_id]
    return CameraDetectionIn(
        camera_id="pc-webcam-01",
        subject_id=subject_id,
        detected_type=detected_type,
        confidence=0.9,
        bbox_x=0,
        bbox_y=0,
        bbox_width=640,
        bbox_height=480,
        center_x=center_x,
        center_y=center_y,
        zone_name=None,
        observed_at=observed_at,
    )


def rows(session: Session) -> list[ActivityObservation]:
    return session.execute(
        select(ActivityObservation).order_by(ActivityObservation.subject_id, ActivityObservation.observed_at)
    ).scalars().all()


def test_person_detection_is_ignored(session: Session) -> None:
    assert record_activity(session, detection(datetime(2026, 7, 26, tzinfo=UTC), subject_id=None)) is None
    assert rows(session) == []


def test_first_detection_floors_to_utc_second_without_committing(session: Session) -> None:
    row = record_activity(
        session,
        detection(datetime(2026, 7, 26, 9, 0, 0, 900_000, tzinfo=ZoneInfo("Asia/Seoul"))),
    )
    assert row is not None
    assert (row.observed_at, row.center_x, row.center_y, row.distance, row.moving) == (
        datetime(2026, 7, 26, 0, 0, tzinfo=UTC),
        100,
        100,
        0,
        False,
    )
    assert session.in_transaction()


def test_latest_preceding_same_subject_within_three_seconds_is_moving(session: Session) -> None:
    at = datetime(2026, 7, 26, tzinfo=UTC)
    record_activity(session, detection(at))
    record_activity(session, detection(at + timedelta(seconds=3), subject_id="cat_001", center_x=400, center_y=400))
    row = record_activity(session, detection(at + timedelta(seconds=3, microseconds=1), center_x=118, center_y=118))
    assert row is not None
    assert (row.observed_at, row.distance, row.moving) == (
        at + timedelta(seconds=3),
        pytest.approx(25.4558441227),
        True,
    )


def test_gap_over_three_seconds_starts_stationary_bucket_without_filling(session: Session) -> None:
    at = datetime(2026, 7, 26, tzinfo=UTC)
    record_activity(session, detection(at))
    row = record_activity(session, detection(at + timedelta(seconds=4), center_x=300, center_y=300))
    assert row is not None
    assert (row.observed_at, row.distance, row.moving) == (at + timedelta(seconds=4), 0, False)
    assert len(rows(session)) == 2


def test_same_second_keeps_latest_center_max_distance_and_moving(session: Session) -> None:
    at = datetime(2026, 7, 26, tzinfo=UTC)
    record_activity(session, detection(at, center_x=100))
    record_activity(session, detection(at + timedelta(microseconds=1), center_x=124))
    row = record_activity(session, detection(at + timedelta(microseconds=2), center_x=110))
    assert row is not None
    assert len(rows(session)) == 1
    assert (row.center_x, row.center_y, row.distance, row.moving) == (110, 100, 24, True)


@pytest.mark.parametrize("autoflush", [True, False])
def test_pending_preceding_and_current_buckets_do_not_flush(session: Session, autoflush: bool) -> None:
    flushes: list[None] = []
    event.listen(session, "before_flush", lambda *_: flushes.append(None))
    session.autoflush = autoflush
    at = datetime(2026, 7, 26, tzinfo=UTC)
    record_activity(session, detection(at, center_x=100))
    preceding = record_activity(session, detection(at + timedelta(seconds=3), center_x=124))
    current = record_activity(session, detection(at + timedelta(seconds=3, microseconds=1), center_x=110))
    assert preceding is not None and current is preceding
    assert (current.center_x, current.distance, current.moving) == (110, 24, True)
    assert flushes == []
    assert len(session.new) == 2


def test_uses_latest_of_multiple_same_subject_preceding_buckets(session: Session) -> None:
    at = datetime(2026, 7, 26, tzinfo=UTC)
    session.add_all(
        [
            ActivityObservation(
                camera_id="pc-webcam-01",
                subject_id="dog_001",
                observed_at=at,
                center_x=100,
                center_y=100,
                moving=False,
                distance=0,
            ),
            ActivityObservation(
                camera_id="pc-webcam-01",
                subject_id="dog_001",
                observed_at=at + timedelta(seconds=2),
                center_x=200,
                center_y=100,
                moving=False,
                distance=0,
            ),
        ]
    )
    session.flush()
    row = record_activity(session, detection(at + timedelta(seconds=3), center_x=218))
    assert row is not None
    assert (row.distance, row.moving) == (18, False)
