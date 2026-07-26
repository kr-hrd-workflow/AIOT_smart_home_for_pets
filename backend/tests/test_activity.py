from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from math import hypot
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import create_engine, event, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.activity import activity_statuses, record_activity
from app.contracts import CameraDetectionIn
from app.models import ActivityObservation, AnomalyEvent


REPETITIVE_MOTION_MESSAGE = "짧은 시간에 반복 이동이 관측됐습니다. 건강 판단이 아닌 카메라 관측 알림입니다."


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
        connection.exec_driver_sql(
            "CREATE TABLE anomaly_events ("
            "id INTEGER PRIMARY KEY, subject_id TEXT, anomaly_type TEXT NOT NULL, severity TEXT NOT NULL, "
            "mismatch_kind TEXT, source_behavior_event_id INTEGER, source_key TEXT NOT NULL, "
            "message TEXT NOT NULL, occurred_at DATETIME NOT NULL, "
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


def anomalies(session: Session) -> list[AnomalyEvent]:
    return session.execute(select(AnomalyEvent).order_by(AnomalyEvent.id)).scalars().all()


def observation(subject_id: str, observed_at: datetime, *, moving: bool) -> ActivityObservation:
    return ActivityObservation(
        camera_id="pc-webcam-01",
        subject_id=subject_id,
        observed_at=observed_at,
        center_x=100,
        center_y=100,
        moving=moving,
        distance=0,
    )


def test_activity_statuses_use_seoul_day_and_exact_freshness_boundary(session: Session) -> None:
    now = datetime(2026, 7, 26, tzinfo=UTC)
    day_start = datetime(2026, 7, 25, 15, tzinfo=UTC)
    session.add_all(
        [
            observation("dog_001", day_start - timedelta(seconds=1), moving=True),
            observation("dog_001", day_start, moving=True),
            observation("dog_001", now, moving=False),
            observation("cat_001", now - timedelta(seconds=3), moving=True),
            observation("cat_001", now + timedelta(seconds=1), moving=False),
        ]
    )
    session.commit()

    statuses = activity_statuses(session, now)

    assert [status.model_dump() for status in statuses] == [
        {
            "subject_id": "dog_001",
            "today_active_seconds": 1,
            "today_observed_seconds": 2,
            "current_state": "still",
            "last_observed_at": now,
        },
        {
            "subject_id": "cat_001",
            "today_active_seconds": 1,
            "today_observed_seconds": 1,
            "current_state": "active",
            "last_observed_at": now - timedelta(seconds=3),
        },
    ]


def test_activity_statuses_keep_stale_timestamp_and_report_empty_as_unknown(session: Session) -> None:
    now = datetime(2026, 7, 26, tzinfo=UTC)
    observed_at = now - timedelta(seconds=4)
    session.add(observation("dog_001", observed_at, moving=True))
    session.commit()

    statuses = activity_statuses(session, now)

    assert [status.model_dump() for status in statuses] == [
        {
            "subject_id": "dog_001",
            "today_active_seconds": 1,
            "today_observed_seconds": 1,
            "current_state": "unknown",
            "last_observed_at": observed_at,
        },
        {
            "subject_id": "cat_001",
            "today_active_seconds": 0,
            "today_observed_seconds": 0,
            "current_state": "unknown",
            "last_observed_at": None,
        },
    ]


def test_activity_statuses_do_not_flush_or_include_pending_observations(session: Session) -> None:
    now = datetime(2026, 7, 26, tzinfo=UTC)
    pending = observation("dog_001", now, moving=True)
    session.add(pending)

    statuses = activity_statuses(session, now)

    assert [(status.today_active_seconds, status.today_observed_seconds) for status in statuses] == [(0, 0), (0, 0)]
    assert pending in session.new


def qualifying_history(
    at: datetime,
    *,
    subject_id: str = "dog_001",
) -> tuple[list[ActivityObservation], CameraDetectionIn]:
    center_x, center_y = 200, 200
    history = [
        ActivityObservation(
            camera_id="pc-webcam-01",
            subject_id=subject_id,
            observed_at=at + timedelta(seconds=offset),
            center_x=center_x,
            center_y=center_y,
            moving=False,
            distance=0,
        )
        for offset in range(-29, -12)
    ]
    for offset, step in zip(range(-12, -5), (54, -54, 54, -54, 54, -54, 54), strict=True):
        center_x += step
        history.append(
            ActivityObservation(
                camera_id="pc-webcam-01",
                subject_id=subject_id,
                observed_at=at + timedelta(seconds=offset),
                center_x=center_x,
                center_y=center_y,
                moving=True,
                distance=54,
            )
        )
    history.append(
        ActivityObservation(
            camera_id="pc-webcam-01",
            subject_id=subject_id,
            observed_at=at + timedelta(seconds=-5),
            center_x=center_x,
            center_y=center_y,
            moving=False,
            distance=0,
        )
    )
    for offset, step in zip(range(-4, 0), (54, 52, 52, 52), strict=True):
        center_x += step
        history.append(
            ActivityObservation(
                camera_id="pc-webcam-01",
                subject_id=subject_id,
                observed_at=at + timedelta(seconds=offset),
                center_x=center_x,
                center_y=center_y,
                moving=True,
                distance=step,
            )
        )
    return history, detection(at, subject_id=subject_id, center_x=center_x + 52, center_y=center_y)


def dot_history(at: datetime, second_x: int) -> tuple[list[ActivityObservation], CameraDetectionIn]:
    history, _current = qualifying_history(at)
    center_x, center_y = 200, 200
    vectors = ((30, 0), (second_x, 120), (-second_x, -120), (second_x, 120), (-second_x, -120), (second_x, 120), (-second_x, -120))
    for row, (dx, dy) in zip(history[17:24], vectors, strict=True):
        center_x += dx
        center_y += dy
        row.center_x, row.center_y, row.distance = center_x, center_y, hypot(dx, dy)
    history[24].center_x, history[24].center_y = center_x, center_y
    for row, step in zip(history[25:], (54, 52, 52, 52), strict=True):
        center_x += step
        row.center_x, row.center_y, row.distance = center_x, center_y, step
    return history, detection(at, center_x=center_x + 52, center_y=center_y)


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


def test_repeated_motion_emits_at_exact_observation_movement_travel_and_reversal_boundaries(session: Session) -> None:
    at = datetime(2026, 7, 26, 1, 0, tzinfo=UTC)
    history, current = qualifying_history(at)
    session.add_all(history)
    session.flush()

    row = record_activity(session, current)

    assert row is not None and row.moving is True
    [anomaly] = anomalies(session)
    assert (
        anomaly.subject_id,
        anomaly.anomaly_type,
        anomaly.severity,
        anomaly.mismatch_kind,
        anomaly.source_behavior_event_id,
        anomaly.message,
        anomaly.occurred_at,
    ) == (
        "dog_001",
        "repetitive_motion",
        "warning",
        None,
        None,
        REPETITIVE_MOTION_MESSAGE,
        at.replace(tzinfo=None),
    )
    assert anomaly.source_key == "repetitive_motion:dog_001:2026-07-26T01:00:00+00:00"


@pytest.mark.parametrize("threshold", ("observations", "moving", "travel", "reversals"))
def test_repeated_motion_requires_each_threshold_independently(session: Session, threshold: str) -> None:
    at = datetime(2026, 7, 26, 1, 0, tzinfo=UTC)
    history, current = qualifying_history(at)
    if threshold == "observations":
        history.pop(0)
    elif threshold == "moving":
        next(row for row in history if row.moving).moving = False
    elif threshold == "travel":
        next(row for row in history if row.distance == 52).distance = 51
    else:
        history[23].center_x = 146
        history[24].center_x = 146
    session.add_all(history)
    session.flush()

    record_activity(session, current)

    assert anomalies(session) == []


@pytest.mark.parametrize(("second_x", "emits"), ((-90, True), (-88, False)))
def test_repeated_motion_uses_dot_product_boundary(session: Session, second_x: int, emits: bool) -> None:
    at = datetime(2026, 7, 26, 1, 0, tzinfo=UTC)
    history, current = dot_history(at, second_x)
    session.add_all(history)
    session.flush()

    record_activity(session, current)

    assert bool(anomalies(session)) is emits


@pytest.mark.parametrize(
    ("subject_id", "outside_at"),
    (("dog_001", timedelta(seconds=-120)), ("dog_001", timedelta(seconds=1)), ("cat_001", timedelta(seconds=-29))),
)
def test_repeated_motion_excludes_window_boundaries_future_and_other_subjects(
    session: Session,
    subject_id: str,
    outside_at: timedelta,
) -> None:
    at = datetime(2026, 7, 26, 1, 0, tzinfo=UTC)
    history, current = qualifying_history(at)
    session.add_all(
        [
            *history[1:],
            ActivityObservation(
                camera_id="pc-webcam-01",
                subject_id=subject_id,
                observed_at=at + outside_at,
                center_x=200,
                center_y=200,
                moving=False,
                distance=0,
            ),
        ]
    )
    session.flush()

    record_activity(session, current)

    assert anomalies(session) == []


@pytest.mark.parametrize(
    ("age", "expected"),
    ((timedelta(minutes=15) - timedelta(microseconds=1), 1), (timedelta(minutes=15), 2)),
)
def test_repeated_motion_dedupes_strictly_inside_fifteen_minutes(
    session: Session,
    age: timedelta,
    expected: int,
) -> None:
    at = datetime(2026, 7, 26, 1, 0, tzinfo=UTC)
    history, current = qualifying_history(at)
    session.add_all(history)
    session.add(
        AnomalyEvent(
            subject_id="dog_001",
            anomaly_type="repetitive_motion",
            severity="warning",
            source_key=f"existing:{age}",
            message="existing",
            occurred_at=at - age,
        )
    )
    session.flush()

    record_activity(session, current)

    assert len(anomalies(session)) == expected


def test_repeated_motion_includes_pending_buckets_without_flushing(session: Session) -> None:
    flushes: list[None] = []
    event.listen(session, "before_flush", lambda *_: flushes.append(None))
    at = datetime(2026, 7, 26, 1, 0, tzinfo=UTC)
    history, current = qualifying_history(at)
    session.add_all(history)

    record_activity(session, current)

    assert flushes == []
    assert [row for row in session.new if isinstance(row, AnomalyEvent)]


def test_repeated_motion_does_not_reevaluate_later_moving_same_second_updates(session: Session) -> None:
    at = datetime(2026, 7, 26, 1, 0, tzinfo=UTC)
    history, current = qualifying_history(at)
    session.add_all(history)
    session.flush()
    first = detection(at, center_x=history[-1].center_x, center_y=history[-1].center_y)

    record_activity(session, first)
    assert anomalies(session) == []
    record_activity(session, current.model_copy(update={"center_x": history[-1].center_x + 24}))
    record_activity(session, current.model_copy(update={"center_x": history[-1].center_x + 76}))

    assert anomalies(session) == []


def test_repeated_motion_deduplication_is_per_subject(session: Session) -> None:
    at = datetime(2026, 7, 26, 1, 0, tzinfo=UTC)
    history, current = qualifying_history(at)
    session.add_all(history)
    session.add(
        AnomalyEvent(
            subject_id="cat_001",
            anomaly_type="repetitive_motion",
            severity="warning",
            source_key="cat-existing",
            message="existing",
            occurred_at=at,
        )
    )
    session.flush()

    record_activity(session, current)

    assert {row.subject_id for row in anomalies(session)} == {"dog_001", "cat_001"}
