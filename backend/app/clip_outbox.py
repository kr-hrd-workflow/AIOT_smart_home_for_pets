from __future__ import annotations

import re
from collections.abc import Callable
from datetime import UTC, datetime, timedelta

from sqlalchemy import Select, or_, select
from sqlalchemy.orm import Session

from .clip_contracts import ClipDeliveryIdentity, ClipEventMetadata, ClipIntent, utc_text
from .models import AnomalyEvent, BehaviorEvent, ClipTriggerOutbox


def enqueue_clip_trigger(
    session: Session,
    event: BehaviorEvent | AnomalyEvent,
    *,
    created_at: datetime,
) -> ClipTriggerOutbox:
    if created_at.tzinfo is None or created_at.utcoffset() != timedelta(0):
        raise ValueError("created_at must be UTC")
    if isinstance(event, BehaviorEvent) and event.behavior_type in {"eating", "resting"}:
        event_type = event.behavior_type
        occurred_at = event.started_at
    elif isinstance(event, AnomalyEvent) and event.anomaly_type == "bed_sensor_mismatch":
        event_type = event.anomaly_type
        occurred_at = event.occurred_at
    else:
        raise ValueError("eligible event required")
    if type(event.id) is not int or event.id <= 0:
        raise ValueError("persisted eligible event required")
    row = ClipTriggerOutbox(
        event_type=event_type,
        event_id=event.id,
        occurred_at=occurred_at,
        created_at=created_at,
        deadline_at=created_at + timedelta(seconds=3),
        next_attempt_at=created_at,
        attempts=0,
    )
    session.add(row)
    return row


def oldest_due_unaccepted_statement(now: datetime) -> Select[tuple[ClipTriggerOutbox]]:
    return (
        select(ClipTriggerOutbox)
        .where(
            ClipTriggerOutbox.processed_at.is_(None),
            ClipTriggerOutbox.terminal_reason.is_(None),
            ClipTriggerOutbox.accepted_at.is_(None),
            ClipTriggerOutbox.next_attempt_at <= now,
        )
        .order_by(ClipTriggerOutbox.next_attempt_at, ClipTriggerOutbox.id)
        .limit(1)
        .with_for_update(skip_locked=True)
    )


def oldest_due_accepted_statement(now: datetime) -> Select[tuple[ClipTriggerOutbox]]:
    return (
        select(ClipTriggerOutbox)
        .where(
            ClipTriggerOutbox.processed_at.is_(None),
            ClipTriggerOutbox.terminal_reason.is_(None),
            ClipTriggerOutbox.accepted_at.is_not(None),
            ClipTriggerOutbox.next_attempt_at <= now,
        )
        .order_by(ClipTriggerOutbox.next_attempt_at, ClipTriggerOutbox.id)
        .limit(1)
        .with_for_update(skip_locked=True)
    )


class SqlAlchemyClipOutboxRepository:
    def __init__(self, session_factory: Callable[[], Session]) -> None:
        self._session_factory = session_factory

    def claim_unaccepted(self, now: datetime, lease_until: datetime) -> ClipIntent | None:
        return self._claim(oldest_due_unaccepted_statement(now), now, lease_until)

    def claim_accepted(self, now: datetime, lease_until: datetime) -> ClipIntent | None:
        return self._claim(oldest_due_accepted_statement(now), now, lease_until)

    def _claim(
        self,
        statement: Select[tuple[ClipTriggerOutbox]],
        now: datetime,
        lease_until: datetime,
    ) -> ClipIntent | None:
        _utc(now)
        _utc(lease_until)
        if lease_until <= now:
            raise ValueError("lease must end after claim time")
        with self._session_factory() as session:
            row = session.execute(statement).scalar_one_or_none()
            if row is None:
                return None
            row.next_attempt_at = lease_until
            intent = _intent(row)
            session.commit()
            return intent

    def persist_command(self, outbox_id: int, command_id: str) -> ClipIntent:
        _remote_id(command_id)
        with self._session_factory() as session:
            row = self._row_for_update(session, outbox_id)
            if row.processed_at is not None or row.terminal_reason is not None or row.accepted_at is not None:
                raise RuntimeError("outbox row is not awaiting admission")
            if row.remote_command_id not in (None, command_id):
                raise RuntimeError("outbox command conflict")
            row.remote_command_id = command_id
            row.last_error = None
            session.flush()
            intent = _intent(row)
            session.commit()
            return intent

    def record_acceptance(
        self,
        outbox_id: int,
        boot_id: str,
        command_id: str,
        accepted_at: datetime,
    ) -> None:
        _remote_id(boot_id)
        _remote_id(command_id)
        _utc(accepted_at)
        with self._session_factory() as session:
            row = self._row_for_update(session, outbox_id)
            if row.remote_command_id != command_id or row.processed_at is not None or row.terminal_reason is not None:
                raise RuntimeError("outbox command is unavailable")
            if row.put_started_at is None:
                raise RuntimeError("outbox PUT was not durably started")
            if row.accepted_at is not None:
                if row.remote_boot_id == boot_id and row.accepted_at == accepted_at:
                    return
                raise RuntimeError("outbox receipt conflict")
            row.remote_boot_id = boot_id
            row.accepted_at = accepted_at
            row.next_attempt_at = accepted_at
            row.last_error = None
            session.commit()

    def mark_put_started(self, outbox_id: int, started_at: datetime) -> bool:
        _utc(started_at)
        with self._session_factory() as session:
            row = self._row_for_update(session, outbox_id)
            if (
                row.remote_command_id is None
                or row.accepted_at is not None
                or row.processed_at is not None
                or row.terminal_reason is not None
            ):
                raise RuntimeError("outbox row cannot start PUT")
            if row.put_started_at is not None:
                return False
            if not row.created_at <= started_at < row.deadline_at:
                raise RuntimeError("outbox admission deadline expired")
            row.put_started_at = started_at
            session.commit()
            return True

    def defer_admission(self, outbox_id: int, next_attempt_at: datetime, error: str) -> None:
        self._defer(outbox_id, next_attempt_at, error, accepted=False)

    def defer_delivery(self, outbox_id: int, next_attempt_at: datetime, error: str) -> None:
        self._defer(outbox_id, next_attempt_at, error, accepted=True)

    def _defer(self, outbox_id: int, next_attempt_at: datetime, error: str, *, accepted: bool) -> None:
        _utc(next_attempt_at)
        with self._session_factory() as session:
            row = self._row_for_update(session, outbox_id)
            if (row.accepted_at is not None) is not accepted or row.processed_at is not None or row.terminal_reason is not None:
                raise RuntimeError("outbox row cannot be deferred")
            row.attempts += 1
            row.next_attempt_at = next_attempt_at
            row.last_error = _safe_error(error)
            session.commit()

    def mark_terminal(
        self,
        outbox_id: int,
        reason: str,
        processed_at: datetime,
        error: str,
    ) -> None:
        if reason != "clip_missed":
            raise ValueError("invalid terminal reason")
        _utc(processed_at)
        with self._session_factory() as session:
            row = self._row_for_update(session, outbox_id)
            if row.processed_at is not None or row.terminal_reason is not None:
                return
            if row.accepted_at is not None and error != "clip_gone":
                raise RuntimeError("accepted outbox row can only terminate after clip_gone")
            row.terminal_reason = reason
            row.processed_at = processed_at
            row.last_error = _safe_error(error)
            session.commit()

    def resolve_accepted_clip(
        self,
        command_id: str,
        boot_id: str,
        canonical_events: str,
    ) -> ClipDeliveryIdentity | None:
        _remote_id(command_id)
        _remote_id(boot_id)
        identities = _event_identities(canonical_events)
        with self._session_factory() as session:
            rows = session.execute(
                select(ClipTriggerOutbox).where(
                    ClipTriggerOutbox.processed_at.is_(None),
                    ClipTriggerOutbox.terminal_reason.is_(None),
                    or_(
                        *(
                            (ClipTriggerOutbox.event_type == event_type)
                            & (ClipTriggerOutbox.event_id == event_id)
                            for event_type, event_id in identities
                        )
                    )
                )
            ).scalars().all()
        if (
            len(rows) != len(identities)
            or {(row.event_type, row.event_id) for row in rows} != set(identities)
            or any(row.remote_boot_id != boot_id or row.remote_command_id is None or row.accepted_at is None for row in rows)
            or command_id not in {row.remote_command_id for row in rows}
        ):
            return None
        events = tuple(
            ClipEventMetadata(event_type, event_id, utc_text(next(row.occurred_at for row in rows if (row.event_type, row.event_id) == (event_type, event_id))))
            for event_type, event_id in identities
        )
        by_command = sorted((row.remote_command_id, row.accepted_at) for row in rows)
        return ClipDeliveryIdentity(
            events,
            tuple(command for command, _accepted_at in by_command),
            tuple(accepted_at for _command, accepted_at in by_command),
        )

    def command_processed(self, command_id: str) -> bool:
        _remote_id(command_id)
        with self._session_factory() as session:
            rows = session.execute(
                select(ClipTriggerOutbox.processed_at).where(ClipTriggerOutbox.remote_command_id == command_id)
            ).scalars().all()
        return bool(rows) and all(processed_at is not None for processed_at in rows)

    def mark_commands_processed(self, command_ids: tuple[str, ...], processed_at: datetime) -> None:
        _utc(processed_at)
        if not command_ids or command_ids != tuple(sorted(set(command_ids))):
            raise ValueError("canonical command IDs required")
        for command_id in command_ids:
            _remote_id(command_id)
        with self._session_factory() as session:
            rows = session.execute(
                select(ClipTriggerOutbox)
                .where(ClipTriggerOutbox.remote_command_id.in_(command_ids))
                .with_for_update()
            ).scalars().all()
            if len(rows) != len(command_ids) or {row.remote_command_id for row in rows} != set(command_ids) or any(
                row.accepted_at is None or row.terminal_reason is not None for row in rows
            ):
                raise RuntimeError("accepted commands are unavailable")
            if any(row.processed_at is not None for row in rows):
                if all(row.processed_at is not None for row in rows):
                    return
                raise RuntimeError("accepted command set is inconsistent")
            for row in rows:
                row.processed_at = processed_at
                row.last_error = None
            session.commit()

    def reset_command_for_readmission(
        self,
        outbox_id: int,
        expected_command_id: str,
        next_attempt_at: datetime,
    ) -> None:
        _remote_id(expected_command_id)
        _utc(next_attempt_at)
        with self._session_factory() as session:
            row = self._row_for_update(session, outbox_id)
            if row.remote_command_id != expected_command_id or row.processed_at is not None or row.terminal_reason is not None:
                raise RuntimeError("outbox command is unavailable")
            row.remote_boot_id = None
            row.remote_command_id = None
            row.put_started_at = None
            row.accepted_at = None
            row.next_attempt_at = next_attempt_at
            row.last_error = None
            session.commit()

    def renew_delivery(
        self,
        outbox_id: int,
        expected_lease_until: datetime,
        lease_until: datetime,
    ) -> datetime | None:
        _utc(expected_lease_until)
        _utc(lease_until)
        if lease_until <= expected_lease_until:
            raise ValueError("renewed lease must extend the current lease")
        with self._session_factory() as session:
            row = self._row_for_update(session, outbox_id)
            if (
                row.accepted_at is None
                or row.processed_at is not None
                or row.terminal_reason is not None
                or row.next_attempt_at != expected_lease_until
            ):
                return None
            row.next_attempt_at = lease_until
            session.commit()
            return lease_until

    @staticmethod
    def _row_for_update(session: Session, outbox_id: int) -> ClipTriggerOutbox:
        if type(outbox_id) is not int or outbox_id <= 0:
            raise ValueError("outbox_id must be positive")
        row = session.execute(
            select(ClipTriggerOutbox).where(ClipTriggerOutbox.id == outbox_id).with_for_update()
        ).scalar_one_or_none()
        if row is None:
            raise RuntimeError("outbox row is unavailable")
        return row


def _intent(row: ClipTriggerOutbox) -> ClipIntent:
    return ClipIntent(
        row.id,
        row.event_type,
        row.event_id,
        row.occurred_at,
        row.created_at,
        row.deadline_at,
        row.attempts,
        row.remote_boot_id,
        row.remote_command_id,
        row.accepted_at,
    )


def _event_identities(value: str) -> tuple[tuple[str, int], ...]:
    if type(value) is not str or not value:
        raise ValueError("canonical events required")
    identities: list[tuple[str, int]] = []
    for item in value.split(","):
        match = re.fullmatch(r"(eating|resting|bed_sensor_mismatch):([1-9][0-9]*)", item)
        if match is None:
            raise ValueError("canonical events required")
        identities.append((match.group(1), int(match.group(2))))
    result = tuple(identities)
    if result != tuple(sorted(set(result))):
        raise ValueError("canonical events required")
    return result


def _remote_id(value: str) -> None:
    if type(value) is not str or re.fullmatch(r"[0-9a-f]{32}", value) is None:
        raise ValueError("invalid remote identifier")


def _utc(value: datetime) -> None:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() != timedelta(0):
        raise ValueError("UTC timestamp required")


def _safe_error(value: str) -> str:
    return value if type(value) is str and re.fullmatch(r"[a-z0-9_]{1,64}", value) else "internal_error"


def utc_now() -> datetime:
    return datetime.now(UTC)
