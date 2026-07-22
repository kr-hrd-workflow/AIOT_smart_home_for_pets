from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import create_engine, select, text
from sqlalchemy.dialects import postgresql
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import sessionmaker

from app.clip_outbox import SqlAlchemyClipOutboxRepository, enqueue_clip_trigger, oldest_due_unaccepted_statement
from app.config import AppConfig
from app.models import AnomalyEvent, BehaviorEvent, ClipTriggerOutbox
from app.rules import RuleEngine


NOW = datetime(2026, 7, 21, 1, 2, 3, tzinfo=UTC)


@pytest.mark.parametrize(
    ("event", "event_type", "occurred_at"),
    [
        (BehaviorEvent(id=41, behavior_type="eating", started_at=NOW), "eating", NOW),
        (BehaviorEvent(id=42, behavior_type="resting", started_at=NOW), "resting", NOW),
        (AnomalyEvent(id=7, anomaly_type="bed_sensor_mismatch", occurred_at=NOW), "bed_sensor_mismatch", NOW),
    ],
)
def test_enqueue_clip_trigger_maps_only_eligible_events_with_explicit_commit_time(
    event: BehaviorEvent | AnomalyEvent,
    event_type: str,
    occurred_at: datetime,
) -> None:
    class Session:
        added: list[object] = []

        def add(self, row: object) -> None:
            self.added.append(row)

    session = Session()
    row = enqueue_clip_trigger(session, event, created_at=NOW)  # type: ignore[arg-type]

    assert session.added == [row]
    assert (row.event_type, row.event_id, row.occurred_at) == (event_type, event.id, occurred_at)
    assert (row.created_at, row.deadline_at, row.next_attempt_at) == (NOW, NOW + timedelta(seconds=3), NOW)
    assert (row.attempts, row.processed_at, row.terminal_reason) == (0, None, None)


def test_enqueue_clip_trigger_rejects_no_meal_and_non_utc_commit_time() -> None:
    class Session:
        def add(self, _row: object) -> None:
            raise AssertionError("ineligible row must not be added")

    no_meal = AnomalyEvent(id=9, anomaly_type="no_meal_12h", occurred_at=NOW)
    with pytest.raises(ValueError, match="eligible"):
        enqueue_clip_trigger(Session(), no_meal, created_at=NOW)  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="UTC"):
        enqueue_clip_trigger(
            Session(),
            BehaviorEvent(id=41, behavior_type="eating", started_at=NOW),
            created_at=NOW.replace(tzinfo=None),
        )  # type: ignore[arg-type]


def test_oldest_due_unaccepted_statement_uses_due_order_and_skip_locked() -> None:
    sql = str(
        oldest_due_unaccepted_statement(NOW).compile(
            dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True}
        )
    )

    assert "clip_trigger_outbox.processed_at IS NULL" in sql
    assert "clip_trigger_outbox.terminal_reason IS NULL" in sql
    assert "clip_trigger_outbox.accepted_at IS NULL" in sql
    assert "clip_trigger_outbox.next_attempt_at <= '2026-07-21 01:02:03+00:00'" in sql
    assert "ORDER BY clip_trigger_outbox.next_attempt_at, clip_trigger_outbox.id" in sql
    assert "LIMIT 1 FOR UPDATE SKIP LOCKED" in sql
    assert ClipTriggerOutbox.__table__.c.created_at.server_default is None


def test_rule_commit_stamps_all_intents_once_immediately_before_commit() -> None:
    calls: list[object] = []

    class Session:
        def add(self, row: object) -> None:
            calls.append(row)

        def commit(self) -> None:
            calls.append("commit")

    def now() -> datetime:
        calls.append("clock")
        return NOW

    events = [
        BehaviorEvent(id=41, behavior_type="eating", started_at=NOW - timedelta(seconds=30)),
        AnomalyEvent(id=7, anomaly_type="bed_sensor_mismatch", occurred_at=NOW - timedelta(seconds=1)),
    ]
    engine = RuleEngine(
        config=AppConfig(database_url="postgresql+psycopg://petcare:x@127.0.0.1:55432/petcare"),
        camera_service=object(),
        outbox_now=now,
    )

    engine._commit_with_clip_intents(Session(), events)  # type: ignore[arg-type]

    assert calls[0] == "clock"
    assert [(row.event_type, row.created_at) for row in calls[1:-1]] == [
        ("eating", NOW),
        ("bed_sensor_mismatch", NOW),
    ]
    assert calls[-1] == "commit"


def test_rule_commit_suppresses_only_clip_intents_for_60_seconds_after_clock_discontinuity() -> None:
    added: list[ClipTriggerOutbox] = []
    commit_count = 0

    class Session:
        def add(self, row: ClipTriggerOutbox) -> None:
            added.append(row)

        def commit(self) -> None:
            nonlocal commit_count
            commit_count += 1

    wall_times = iter(
        (
            NOW,
            NOW + timedelta(seconds=1, milliseconds=24),
            NOW + timedelta(seconds=2, milliseconds=50),
            NOW + timedelta(seconds=62, milliseconds=49),
            NOW + timedelta(seconds=62, milliseconds=50),
        )
    )
    monotonic_times = iter((100.0, 101.0, 102.0, 161.999, 162.0))
    engine = RuleEngine(
        config=AppConfig(database_url="postgresql+psycopg://petcare:x@127.0.0.1:55432/petcare"),
        camera_service=object(),
        outbox_now=lambda: next(wall_times),
        outbox_monotonic=lambda: next(monotonic_times),
    )

    engine._commit_with_clip_intents(Session(), [])  # type: ignore[arg-type]
    engine._commit_with_clip_intents(
        Session(),
        [BehaviorEvent(id=41, behavior_type="eating", started_at=NOW)],
    )  # type: ignore[arg-type]
    engine._commit_with_clip_intents(
        Session(),
        [BehaviorEvent(id=42, behavior_type="eating", started_at=NOW)],
    )  # type: ignore[arg-type]
    engine._commit_with_clip_intents(
        Session(),
        [BehaviorEvent(id=43, behavior_type="eating", started_at=NOW)],
    )  # type: ignore[arg-type]
    engine._commit_with_clip_intents(
        Session(),
        [BehaviorEvent(id=44, behavior_type="eating", started_at=NOW)],
    )  # type: ignore[arg-type]

    assert commit_count == 5
    assert [(row.event_id, row.created_at) for row in added] == [
        (41, NOW + timedelta(seconds=1, milliseconds=24)),
        (44, NOW + timedelta(seconds=62, milliseconds=50)),
    ]


def test_repository_claims_with_leases_and_records_acceptance(database_url: str) -> None:
    database = create_engine(database_url)
    sessions = sessionmaker(bind=database, expire_on_commit=False)
    with database.begin() as connection:
        connection.execute(text("TRUNCATE clip_trigger_outbox RESTART IDENTITY"))
    with sessions() as session:
        session.add_all(
            [
                ClipTriggerOutbox(
                    event_type="eating",
                    event_id=41,
                    occurred_at=NOW,
                    created_at=NOW,
                    deadline_at=NOW + timedelta(seconds=3),
                    next_attempt_at=NOW,
                    attempts=0,
                ),
                ClipTriggerOutbox(
                    event_type="resting",
                    event_id=42,
                    occurred_at=NOW,
                    created_at=NOW,
                    deadline_at=NOW + timedelta(seconds=3),
                    next_attempt_at=NOW,
                    attempts=0,
                ),
            ]
        )
        session.commit()

    first_repo = SqlAlchemyClipOutboxRepository(sessions)
    second_repo = SqlAlchemyClipOutboxRepository(sessions)
    first = first_repo.claim_unaccepted(NOW, NOW + timedelta(seconds=3))
    second = second_repo.claim_unaccepted(NOW, NOW + timedelta(seconds=3))

    assert first is not None and second is not None and (first.event_id, second.event_id) == (41, 42)
    command = "a" * 32
    boot = "b" * 32
    persisted = first_repo.persist_command(first.outbox_id, command)
    assert persisted.remote_command_id == command
    assert first_repo.mark_put_started(first.outbox_id, NOW + timedelta(milliseconds=500)) is True
    first_repo.record_acceptance(first.outbox_id, boot, command, NOW + timedelta(seconds=1))
    with pytest.raises(RuntimeError, match="receipt conflict"):
        first_repo.record_acceptance(first.outbox_id, boot, command, NOW + timedelta(seconds=2))
    accepted = first_repo.claim_accepted(NOW + timedelta(seconds=1), NOW + timedelta(seconds=51))
    assert accepted is not None
    assert (accepted.remote_boot_id, accepted.remote_command_id, accepted.accepted_at) == (
        boot,
        command,
        NOW + timedelta(seconds=1),
    )
    first_repo.mark_terminal(first.outbox_id, "clip_missed", NOW + timedelta(seconds=3), "clip_gone")
    with sessions() as session:
        terminal = session.get(ClipTriggerOutbox, first.outbox_id)
        assert terminal is not None
        assert (terminal.accepted_at, terminal.remote_command_id, terminal.terminal_reason) == (
            NOW + timedelta(seconds=1),
            command,
            "clip_missed",
        )
    database.dispose()


def test_repository_resolves_and_processes_canonical_clip_identity(database_url: str) -> None:
    database = create_engine(database_url)
    sessions = sessionmaker(bind=database, expire_on_commit=False)
    command = "c" * 32
    boot = "d" * 32
    with database.begin() as connection:
        connection.execute(text("TRUNCATE clip_trigger_outbox RESTART IDENTITY"))
    with sessions() as session:
        session.add(
            ClipTriggerOutbox(
                event_type="bed_sensor_mismatch",
                event_id=7,
                occurred_at=NOW,
                created_at=NOW,
                deadline_at=NOW + timedelta(seconds=3),
                next_attempt_at=NOW,
                attempts=0,
                remote_boot_id=boot,
                remote_command_id=command,
                put_started_at=NOW + timedelta(milliseconds=500),
                accepted_at=NOW + timedelta(seconds=1),
            )
        )
        session.commit()

    repo = SqlAlchemyClipOutboxRepository(sessions)
    with sessions() as session:
        outbox_id = session.execute(select(ClipTriggerOutbox.id)).scalar_one()
    repo.defer_delivery(outbox_id, NOW + timedelta(seconds=2), "clip_not_ready")
    renewed = repo.renew_delivery(
        outbox_id,
        NOW + timedelta(seconds=2),
        NOW + timedelta(seconds=92),
    )
    assert renewed == NOW + timedelta(seconds=92)
    assert repo.renew_delivery(
        outbox_id,
        NOW + timedelta(seconds=2),
        NOW + timedelta(seconds=182),
    ) is None
    identity = repo.resolve_accepted_clip(command, boot, "bed_sensor_mismatch:7")
    assert identity is not None
    assert identity.canonical_events == "bed_sensor_mismatch:7"
    assert identity.remote_command_ids == (command,)
    assert repo.command_processed(command) is False

    repo.mark_commands_processed((command,), NOW + timedelta(seconds=2))
    repo.mark_commands_processed((command,), NOW + timedelta(seconds=3))
    assert repo.command_processed(command) is True
    assert repo.resolve_accepted_clip(command, boot, "bed_sensor_mismatch:7") is None
    repo.mark_terminal(outbox_id, "clip_missed", NOW + timedelta(seconds=3), "clip_gone")
    with sessions() as session:
        row = session.execute(select(ClipTriggerOutbox)).scalar_one()
        assert row.processed_at == NOW + timedelta(seconds=2)
        assert (row.terminal_reason, row.last_error) == (None, None)
    database.dispose()


def test_repository_persists_put_marker_before_io_without_extending_deadline(database_url: str) -> None:
    database = create_engine(database_url)
    sessions = sessionmaker(bind=database, expire_on_commit=False)
    command = "1" * 32
    with database.begin() as connection:
        connection.execute(text("TRUNCATE clip_trigger_outbox RESTART IDENTITY"))
    with sessions() as session:
        row = ClipTriggerOutbox(
            event_type="eating",
            event_id=41,
            occurred_at=NOW,
            created_at=NOW,
            deadline_at=NOW + timedelta(seconds=3),
            next_attempt_at=NOW,
            attempts=0,
            remote_command_id=command,
        )
        session.add(row)
        session.commit()
        outbox_id = row.id

    repo = SqlAlchemyClipOutboxRepository(sessions)
    claimed = repo.claim_unaccepted(NOW, NOW + timedelta(seconds=1))
    assert claimed is not None and claimed.remote_command_id == command
    assert repo.mark_put_started(outbox_id, NOW + timedelta(milliseconds=500)) is True
    assert repo.claim_unaccepted(
        NOW + timedelta(milliseconds=999),
        NOW + timedelta(seconds=1, milliseconds=999),
    ) is None
    reclaimed = repo.claim_unaccepted(
        NOW + timedelta(seconds=1),
        NOW + timedelta(seconds=2),
    )
    assert reclaimed is not None and reclaimed.remote_command_id == command
    assert repo.mark_put_started(outbox_id, NOW + timedelta(seconds=2)) is False
    with sessions() as session:
        row = session.get(ClipTriggerOutbox, outbox_id)
        assert row is not None
        assert (row.put_started_at, row.deadline_at) == (
            NOW + timedelta(milliseconds=500),
            NOW + timedelta(seconds=3),
        )
    database.dispose()


def test_database_rejects_duplicate_command_and_invalid_outbox_states(database_url: str) -> None:
    database = create_engine(database_url)
    sessions = sessionmaker(bind=database, expire_on_commit=False)
    command = "2" * 32
    with database.begin() as connection:
        connection.execute(text("TRUNCATE clip_trigger_outbox RESTART IDENTITY"))

    def row(event_id: int, **values: object) -> ClipTriggerOutbox:
        return ClipTriggerOutbox(
            event_type="eating",
            event_id=event_id,
            occurred_at=NOW,
            created_at=NOW,
            deadline_at=NOW + timedelta(seconds=3),
            next_attempt_at=NOW,
            attempts=0,
            **values,
        )

    with sessions() as session:
        session.add_all(
            [
                row(1, remote_command_id=command),
                row(
                    7,
                    remote_command_id="7" * 32,
                    remote_boot_id="8" * 32,
                    put_started_at=NOW,
                    accepted_at=NOW - timedelta(milliseconds=200),
                ),
            ]
        )
        session.commit()
    invalid_rows = (
        row(2, remote_command_id=command),
        row(3, remote_boot_id="3" * 32),
        row(4, remote_command_id="4" * 32, remote_boot_id="5" * 32, put_started_at=NOW + timedelta(seconds=1), accepted_at=NOW + timedelta(seconds=4)),
        row(5, last_error="unsafe error with spaces"),
        row(6, processed_at=NOW + timedelta(seconds=1)),
    )
    for invalid in invalid_rows:
        with sessions() as session:
            session.add(invalid)
            with pytest.raises(IntegrityError):
                session.commit()
    database.dispose()


def test_repository_resets_for_readmission_and_marks_expired_intent_terminal(database_url: str) -> None:
    database = create_engine(database_url)
    sessions = sessionmaker(bind=database, expire_on_commit=False)
    command = "e" * 32
    boot = "f" * 32
    with database.begin() as connection:
        connection.execute(text("TRUNCATE clip_trigger_outbox RESTART IDENTITY"))
    with sessions() as session:
        row = ClipTriggerOutbox(
            event_type="eating",
            event_id=41,
            occurred_at=NOW,
            created_at=NOW,
            deadline_at=NOW + timedelta(seconds=3),
            next_attempt_at=NOW,
            attempts=0,
            remote_boot_id=boot,
            remote_command_id=command,
            put_started_at=NOW + timedelta(milliseconds=500),
            accepted_at=NOW + timedelta(seconds=1),
        )
        session.add(row)
        session.commit()
        outbox_id = row.id

    repo = SqlAlchemyClipOutboxRepository(sessions)
    repo.reset_command_for_readmission(outbox_id, command, NOW + timedelta(seconds=2))
    repo.defer_admission(outbox_id, NOW + timedelta(seconds=3), "clock_uncertain")
    repo.mark_terminal(outbox_id, "clip_missed", NOW + timedelta(seconds=3), "command_expired")
    with sessions() as session:
        row = session.get(ClipTriggerOutbox, outbox_id)
        assert row is not None
        assert (row.remote_boot_id, row.remote_command_id, row.accepted_at) == (None, None, None)
        assert (row.attempts, row.last_error, row.terminal_reason, row.processed_at) == (
            1,
            "command_expired",
            "clip_missed",
            NOW + timedelta(seconds=3),
        )
    database.dispose()
