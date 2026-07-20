from __future__ import annotations

import importlib
from datetime import UTC, datetime, timedelta
from pathlib import Path

from alembic import command as alembic_command
from alembic.config import Config
import pytest
from sqlalchemy import create_engine, select, text
from sqlalchemy.orm import sessionmaker

from app.config import AppConfig
from app.events import CalibrateBedCommand, CameraFrameCommitted, SensorReadingCommitted
from app.models import AnomalyEvent, BedCalibration, BehaviorEvent, Camera, CameraEvent, Device, RestSession, SensorReading


NOW = datetime(2026, 7, 20, 4, 0, tzinfo=UTC)


def test_eating_confirms_only_at_exact_dwell_and_weight_boundaries() -> None:
    rules = importlib.import_module("app.rules")
    state = rules.EatingState()

    for index, seconds in enumerate((-4, -3, -2, -1, 0), 1):
        at = NOW + timedelta(seconds=seconds)
        state.observe_food(rules.FoodFact(index, 100.0, at, at, 10.0 + seconds))
    state.observe_camera(rules.BowlCameraFact(NOW, NOW, 10.0, {"dog_001": 11}))
    for index, seconds in enumerate((26, 27, 28), 20):
        at = NOW + timedelta(seconds=seconds)
        state.observe_food(rules.FoodFact(index, 95.0, at, at, 10.0 + seconds))
    almost = NOW + timedelta(seconds=29, milliseconds=999)
    state.observe_camera(rules.BowlCameraFact(almost, almost, 39.999, {"dog_001": 12}))

    assert state.evaluate(almost, 39.999) == []

    boundary = NOW + timedelta(seconds=30)
    state.observe_food(rules.FoodFact(23, 95.0, boundary, boundary, 40.0))
    state.observe_camera(rules.BowlCameraFact(boundary, boundary, 40.0, {"dog_001": 13}))
    actions = state.evaluate(boundary, 40.0)
    assert actions == [
        rules.OpenEating(
            subject_id="dog_001",
            started_at=NOW,
            source_camera_event_id=13,
            source_sensor_reading_id=23,
            source_key="eating:dog_001:13:23",
        )
    ]


def test_rest_owner_is_retained_then_hands_off_after_exact_absence() -> None:
    rules = importlib.import_module("app.rules")
    bed = importlib.import_module("app.bed")
    state = rules.RestState()

    def evaluation(subjects: tuple[str, ...], selected: str | None) -> object:
        return bed.BedEvaluation(
            sensor_state="ready",
            pressure_state="occupied",
            fusion_state="confirmed_rest",
            camera_confirmed=True,
            aggregate_delta=500.0,
            bed_subject_ids=subjects,
            selected_bed_subject_id=selected,
        )

    dog = evaluation(("dog_001",), "dog_001")
    assert state.evaluate(dog, NOW, 10.0, {"dog_001": 31}, 41, None) == []
    assert state.evaluate(dog, NOW + timedelta(seconds=1, milliseconds=999), 11.999, {"dog_001": 32}, 42, None) == []
    opened = state.evaluate(dog, NOW + timedelta(seconds=2), 12.0, {"dog_001": 33}, 43, None)
    assert opened == [rules.OpenRest("dog_001", NOW + timedelta(seconds=2), 33, 43, "resting:dog_001:33:43")]
    state.mark_open(opened[0])

    both = evaluation(("dog_001", "cat_001"), "cat_001")
    assert state.evaluate(both, NOW + timedelta(seconds=2, milliseconds=500), 12.5, {"dog_001": 34, "cat_001": 35}, 44, None) == []

    cat = evaluation(("cat_001",), "cat_001")
    assert state.evaluate(cat, NOW + timedelta(seconds=3), 13.0, {"cat_001": 36}, 45, None) == []
    assert state.evaluate(cat, NOW + timedelta(seconds=5), 15.0, {"cat_001": 37}, 46, None) == []
    handoff = state.evaluate(cat, NOW + timedelta(seconds=6), 16.0, {"cat_001": 38}, 47, None)
    assert handoff == [
        rules.CloseRest(NOW + timedelta(seconds=3), "camera_exit"),
        rules.OpenRest("cat_001", NOW + timedelta(seconds=5), 37, 46, "resting:cat_001:37:46"),
    ]


def test_rule_engine_calibration_persists_one_atomic_snapshot(database_url: str) -> None:
    rules = importlib.import_module("app.rules")
    alembic = Config(str(Path(__file__).parents[1] / "alembic.ini"))
    alembic.set_main_option("script_location", str(Path(__file__).parents[1] / "migrations"))
    alembic.set_main_option("sqlalchemy.url", database_url)
    alembic_command.upgrade(alembic, "head")
    database = create_engine(database_url)
    sessions = sessionmaker(bind=database, expire_on_commit=False)
    with database.begin() as connection:
        connection.execute(
            text(
                "TRUNCATE TABLE anomaly_events, rest_sessions, behavior_events, bed_calibrations, "
                "camera_events, sensor_readings, cameras, devices CASCADE"
            )
        )
    now = NOW + timedelta(hours=1)
    with sessions() as session:
        session.add(Device(device_id="petzone-01"))
        for channel, base in (("left", 100), ("center", 200), ("right", 300)):
            for index in range(45):
                session.add(
                    SensorReading(
                        device_id="petzone-01",
                        sensor_type=f"bed_pressure_{channel}",
                        value_number=float(base + index % 3),
                        value_boolean=None,
                        unit="adc",
                        observed_at=now - timedelta(seconds=44 - index),
                        received_at=now - timedelta(seconds=44 - index),
                    )
                )
        session.commit()

    class Camera:
        def available_for(self, _start: datetime, _end: datetime) -> bool:
            return True

    class Scheduler:
        def schedule(self, *_args: object) -> None:
            pass

        def cancel(self, *_args: object) -> None:
            pass

    engine = rules.RuleEngine(
        config=AppConfig(database_url=database_url),
        camera_service=Camera(),
    )
    with sessions() as session:
        result = engine.command(session, CalibrateBedCommand(device_id="petzone-01"), now, 50.0, Scheduler())
    accepted = engine.bed.calibration
    with sessions() as session, pytest.raises(rules.BedCalibrationRejected):
        engine.command(
            session,
            CalibrateBedCommand(device_id="petzone-01"),
            now + timedelta(seconds=121),
            171.0,
            Scheduler(),
        )
    with database.connect() as connection:
        row = connection.execute(
            text("SELECT left_baseline,center_baseline,right_baseline FROM bed_calibrations")
        ).one()
        count = connection.execute(select(BedCalibration.id)).all()
    database.dispose()

    assert [channel.baseline for channel in result.channels] == [101.0, 201.0, 301.0]
    assert tuple(row) == (101.0, 201.0, 301.0)
    assert engine.bed.calibration == accepted
    assert len(count) == 1


def test_rule_engine_persists_one_eating_row_and_closes_at_last_inside_frame(database_url: str) -> None:
    rules = importlib.import_module("app.rules")
    database = create_engine(database_url)
    sessions = sessionmaker(bind=database, expire_on_commit=False)
    with database.begin() as connection:
        connection.execute(
            text(
                "TRUNCATE TABLE anomaly_events, rest_sessions, behavior_events, bed_calibrations, "
                "camera_events, sensor_readings, cameras, devices CASCADE"
            )
        )
    with sessions() as session:
        session.add_all([Device(device_id="petzone-01"), Camera(camera_id="pc-webcam-01")])
        session.commit()

    class CameraAvailability:
        def available_for(self, _start: datetime, _end: datetime) -> bool:
            return True

    class Scheduler:
        effective_monotonic = 0.0

        def schedule(self, *_args: object) -> None:
            pass

        def cancel(self, *_args: object) -> None:
            pass

    scheduler = Scheduler()
    engine = rules.RuleEngine(config=AppConfig(database_url=database_url), camera_service=CameraAvailability())
    with sessions() as session:
        engine.startup(session, scheduler, NOW - timedelta(seconds=5))

    def food(at: datetime, grams: float) -> None:
        with sessions() as session:
            row = SensorReading(
                device_id="petzone-01",
                sensor_type="food_weight",
                value_number=grams,
                value_boolean=None,
                unit="g",
                observed_at=at,
                received_at=at,
            )
            session.add(row)
            session.commit()
            elapsed = (at - NOW).total_seconds()
            engine.apply(
                session,
                SensorReadingCommitted(
                    reading_id=row.id,
                    device_id="petzone-01",
                    sensor_type="food_weight",
                    observed_at=at,
                ),
                at,
                100.0 + elapsed,
                scheduler,
            )

    def frame(at: datetime, *, dog_inside: bool) -> None:
        with sessions() as session:
            ids: tuple[int, ...] = ()
            if dog_inside:
                row = CameraEvent(
                    camera_id="pc-webcam-01",
                    subject_id="dog_001",
                    detected_type="dog",
                    confidence=0.9,
                    bbox_x=40,
                    bbox_y=260,
                    bbox_width=100,
                    bbox_height=100,
                    center_x=90,
                    center_y=310,
                    zone_name="food_bowl",
                    observed_at=at,
                )
                session.add(row)
                session.commit()
                ids = (row.id,)
            elapsed = (at - NOW).total_seconds()
            engine.apply(
                session,
                CameraFrameCommitted(
                    camera_id="pc-webcam-01",
                    observed_at=at,
                    detection_ids=ids,
                    bed_subject_ids=(),
                    selected_bed_subject_id=None,
                ),
                at,
                100.0 + elapsed,
                scheduler,
            )

    for seconds in (-4, -3, -2, -1, 0):
        food(NOW + timedelta(seconds=seconds), 100.0)
    frame(NOW, dog_inside=True)
    for seconds in range(1, 31):
        at = NOW + timedelta(seconds=seconds)
        food(at, 100.0 if seconds <= 25 else 95.0)
        frame(at, dog_inside=True)
    assert engine.eating.open_subject_id == "dog_001"
    for seconds in range(31, 37):
        at = NOW + timedelta(seconds=seconds)
        food(at, 95.0)
        frame(at, dog_inside=False)

    with sessions() as session:
        rows = session.execute(select(BehaviorEvent).where(BehaviorEvent.behavior_type == "eating")).scalars().all()
    database.dispose()

    assert len(rows) == 1
    assert (rows[0].subject_id, rows[0].started_at, rows[0].ended_at, rows[0].duration_seconds) == (
        "dog_001",
        NOW,
        NOW + timedelta(seconds=30),
        30,
    )


def test_rule_engine_opens_and_atomically_closes_confirmed_rest(database_url: str) -> None:
    rules = importlib.import_module("app.rules")
    bed = importlib.import_module("app.bed")
    database = create_engine(database_url)
    sessions = sessionmaker(bind=database, expire_on_commit=False)
    with database.begin() as connection:
        connection.execute(
            text(
                "TRUNCATE TABLE anomaly_events, rest_sessions, behavior_events, bed_calibrations, "
                "camera_events, sensor_readings, cameras, devices CASCADE"
            )
        )
    with sessions() as session:
        session.add_all([Device(device_id="petzone-01"), Camera(camera_id="pc-webcam-01")])
        session.commit()

    class CameraAvailability:
        def available_for(self, _start: datetime, _end: datetime) -> bool:
            return True

    class Scheduler:
        effective_monotonic = 0.0

        def schedule(self, *_args: object) -> None:
            pass

        def cancel(self, *_args: object) -> None:
            pass

    scheduler = Scheduler()
    engine = rules.RuleEngine(config=AppConfig(database_url=database_url), camera_service=CameraAvailability())
    with sessions() as session:
        engine.startup(session, scheduler, NOW - timedelta(seconds=1))
    engine.bed.load_calibration(
        bed.CalibrationSnapshot(
            NOW - timedelta(seconds=60),
            NOW,
            (45, 45, 45),
            (100.0, 100.0, 100.0),
            (1, 1, 1),
            (40, 40, 40),
            450,
            250,
        ),
        restart=False,
    )

    def pressures(at: datetime, raw: int) -> None:
        for channel in ("left", "center", "right"):
            with sessions() as session:
                row = SensorReading(
                    device_id="petzone-01",
                    sensor_type=f"bed_pressure_{channel}",
                    value_number=float(raw),
                    value_boolean=None,
                    unit="adc",
                    observed_at=at,
                    received_at=at,
                )
                session.add(row)
                session.commit()
                elapsed = (at - NOW).total_seconds()
                engine.apply(
                    session,
                    SensorReadingCommitted(
                        reading_id=row.id,
                        device_id="petzone-01",
                        sensor_type=f"bed_pressure_{channel}",
                        observed_at=at,
                    ),
                    at,
                    200.0 + elapsed,
                    scheduler,
                )

    def bed_frame(at: datetime, subject: str | None) -> None:
        with sessions() as session:
            ids: tuple[int, ...] = ()
            if subject is not None:
                detected_type = "dog" if subject == "dog_001" else "cat"
                row = CameraEvent(
                    camera_id="pc-webcam-01",
                    subject_id=subject,
                    detected_type=detected_type,
                    confidence=0.9,
                    bbox_x=320,
                    bbox_y=180,
                    bbox_width=100,
                    bbox_height=100,
                    center_x=370,
                    center_y=230,
                    zone_name="pet_bed",
                    observed_at=at,
                )
                session.add(row)
                session.commit()
                ids = (row.id,)
            elapsed = (at - NOW).total_seconds()
            engine.apply(
                session,
                CameraFrameCommitted(
                    camera_id="pc-webcam-01",
                    observed_at=at,
                    detection_ids=ids,
                    bed_subject_ids=(() if subject is None else (subject,)),
                    selected_bed_subject_id=subject,
                ),
                at,
                200.0 + elapsed,
                scheduler,
            )

    for seconds in range(5):
        at = NOW + timedelta(seconds=seconds)
        pressures(at, 300)
        bed_frame(at, "dog_001")
    with sessions() as session:
        open_pair = session.execute(
            select(BehaviorEvent, RestSession)
            .join(RestSession, RestSession.behavior_event_id == BehaviorEvent.id)
            .where(BehaviorEvent.behavior_type == "resting", BehaviorEvent.ended_at.is_(None))
        ).one()
        assert open_pair[0].subject_id == open_pair[1].subject_id == "dog_001"

    for seconds in range(5, 9):
        at = NOW + timedelta(seconds=seconds)
        pressures(at, 300)
        bed_frame(at, None)
    with sessions() as session:
        behavior, rest = session.execute(
            select(BehaviorEvent, RestSession)
            .join(RestSession, RestSession.behavior_event_id == BehaviorEvent.id)
            .where(BehaviorEvent.behavior_type == "resting")
        ).one()
    database.dispose()

    assert (behavior.ended_at, behavior.duration_seconds) == (rest.ended_at, rest.duration_seconds)
    assert (rest.ended_at, rest.close_reason) == (NOW + timedelta(seconds=5), "camera_exit")


def test_rule_engine_emits_one_no_meal_warning_at_exact_twelve_hours(database_url: str) -> None:
    rules = importlib.import_module("app.rules")
    database = create_engine(database_url)
    sessions = sessionmaker(bind=database, expire_on_commit=False)
    with database.begin() as connection:
        connection.execute(
            text(
                "TRUNCATE TABLE anomaly_events, rest_sessions, behavior_events, bed_calibrations, "
                "camera_events, sensor_readings, cameras, devices CASCADE"
            )
        )
    meal_ended_at = NOW
    with sessions() as session:
        session.add_all([Device(device_id="petzone-01"), Camera(camera_id="pc-webcam-01")])
        sensor = SensorReading(
            device_id="petzone-01",
            sensor_type="food_weight",
            value_number=95.0,
            value_boolean=None,
            unit="g",
            observed_at=meal_ended_at,
            received_at=meal_ended_at,
        )
        camera = CameraEvent(
            camera_id="pc-webcam-01",
            subject_id="dog_001",
            detected_type="dog",
            confidence=0.9,
            bbox_x=40,
            bbox_y=260,
            bbox_width=100,
            bbox_height=100,
            center_x=90,
            center_y=310,
            zone_name="food_bowl",
            observed_at=meal_ended_at,
        )
        session.add_all([sensor, camera])
        session.flush()
        meal = BehaviorEvent(
            subject_id="dog_001",
            behavior_type="eating",
            source_camera_event_id=camera.id,
            source_sensor_reading_id=sensor.id,
            source_key="meal:dog_001:proof",
            started_at=meal_ended_at - timedelta(seconds=30),
            ended_at=meal_ended_at,
            duration_seconds=30,
        )
        session.add(meal)
        session.commit()
        meal_id = meal.id

    published: list[object] = []

    class CameraAvailability:
        def available_for(self, _start: datetime, _end: datetime) -> bool:
            return True

    class Scheduler:
        effective_monotonic = 500.0

    eligible_at = meal_ended_at + timedelta(hours=12)
    engine = rules.RuleEngine(
        config=AppConfig(database_url=database_url),
        camera_service=CameraAvailability(),
        publisher=published.append,
    )
    with sessions() as session:
        engine.deadline(session, "no_meal", "dog_001", eligible_at, Scheduler())
    with sessions() as session:
        engine.deadline(session, "no_meal", "dog_001", eligible_at, Scheduler())
    with sessions() as session:
        later_meal = BehaviorEvent(
            subject_id="dog_001",
            behavior_type="eating",
            source_camera_event_id=camera.id,
            source_sensor_reading_id=sensor.id,
            source_key="meal:dog_001:later-proof",
            started_at=meal_ended_at + timedelta(minutes=14, seconds=30),
            ended_at=meal_ended_at + timedelta(minutes=15),
            duration_seconds=30,
        )
        session.add(later_meal)
        session.commit()
        later_meal_id = later_meal.id
    later_eligible_at = eligible_at + timedelta(minutes=15)
    with sessions() as session:
        engine.deadline(session, "no_meal", "dog_001", later_eligible_at, Scheduler())
    with sessions() as session:
        warnings = session.execute(select(AnomalyEvent).order_by(AnomalyEvent.occurred_at)).scalars().all()
    database.dispose()

    assert len(warnings) == len(published) == 2
    assert [
        (warning.subject_id, warning.occurred_at, warning.source_behavior_event_id)
        for warning in warnings
    ] == [
        ("dog_001", eligible_at, meal_id),
        ("dog_001", later_eligible_at, later_meal_id),
    ]


def test_rule_engine_persists_one_warning_for_a_continuous_pressure_mismatch(database_url: str) -> None:
    rules = importlib.import_module("app.rules")
    bed = importlib.import_module("app.bed")
    database = create_engine(database_url)
    sessions = sessionmaker(bind=database, expire_on_commit=False)
    with database.begin() as connection:
        connection.execute(text("TRUNCATE TABLE anomaly_events CASCADE"))

    published: list[object] = []

    class CameraAvailability:
        def available_for(self, _start: datetime, _end: datetime) -> bool:
            return True

    class Scheduler:
        effective_monotonic = 600.0

        def schedule(self, *_args: object) -> None:
            pass

        def cancel(self, *_args: object) -> None:
            pass

    scheduler = Scheduler()
    engine = rules.RuleEngine(
        config=AppConfig(database_url=database_url),
        camera_service=CameraAvailability(),
        publisher=published.append,
    )
    engine.bed.load_calibration(
        bed.CalibrationSnapshot(
            NOW - timedelta(seconds=60),
            NOW,
            (45, 45, 45),
            (100.0, 100.0, 100.0),
            (1, 1, 1),
            (40, 40, 40),
            450,
            250,
        ),
        restart=False,
    )

    reading_id = 0

    def tick(at: datetime, subject_id: str | None) -> None:
        nonlocal reading_id
        monotonic = 600.0 + (at - NOW).total_seconds()
        scheduler.effective_monotonic = monotonic
        for channel in ("left", "center", "right"):
            reading_id += 1
            engine.bed.observe_pressure(bed.PressureFact(reading_id, channel, 300, at, at, monotonic))
        subjects = () if subject_id is None else (subject_id,)
        camera_ids = {} if subject_id is None else {subject_id: 999}
        engine.bed.observe_camera(bed.CameraFact(at, at, monotonic, subjects, subject_id, camera_ids))
        with sessions() as session:
            engine.deadline(session, "mismatch_tick", "petzone-01", at, scheduler)

    for seconds in range(41):
        tick(NOW + timedelta(seconds=seconds), None)
    tick(NOW + timedelta(seconds=41), "dog_001")
    second_episode_started_at = NOW + timedelta(minutes=15, seconds=2)
    for seconds in range(31):
        tick(second_episode_started_at + timedelta(seconds=seconds), None)

    with sessions() as session:
        warnings = session.execute(
            select(AnomalyEvent).where(AnomalyEvent.anomaly_type == "bed_sensor_mismatch")
        ).scalars().all()
    database.dispose()

    assert len(warnings) == len(published) == 2
    assert [
        (warning.subject_id, warning.mismatch_kind, warning.occurred_at)
        for warning in warnings
    ] == [
        (None, "unconfirmed_pressure", NOW + timedelta(seconds=32)),
        (None, "unconfirmed_pressure", NOW + timedelta(seconds=32, minutes=15)),
    ]
