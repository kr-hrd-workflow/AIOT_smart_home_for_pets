from __future__ import annotations

import base64
import hashlib
from collections.abc import Iterator
from datetime import UTC, datetime

import httpx
import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.account_cleanup import (
    ActivityCleanupCommand,
    ActivityCleanupError,
    ActivityCleanupRepository,
    ActivityCleanupWorker,
)
from app.agent_client import SignedActivityCleanupClient, b64url
from app.models import ActivityCleanupState, ActivityObservation


NOW = datetime(2026, 7, 26, 12, 0, tzinfo=UTC)
COMMAND_ID = "clc_" + "1" * 32
OTHER_COMMAND_ID = "clc_" + "2" * 32


@pytest.fixture()
def session_factory() -> Iterator[sessionmaker[Session]]:
    engine = create_engine(
        "sqlite+pysqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    with engine.begin() as connection:
        connection.exec_driver_sql(
            "CREATE TABLE activity_cleanup_state ("
            "singleton INTEGER PRIMARY KEY, agent_id TEXT, activity_enabled BOOLEAN NOT NULL, "
            "command_id TEXT, applied_at DATETIME, updated_at DATETIME NOT NULL)"
        )
        connection.exec_driver_sql(
            "INSERT INTO activity_cleanup_state(singleton, activity_enabled, updated_at) "
            "VALUES (1, TRUE, CURRENT_TIMESTAMP)"
        )
        connection.exec_driver_sql(
            "CREATE TABLE activity_observations ("
            "id INTEGER PRIMARY KEY, camera_id TEXT NOT NULL, subject_id TEXT NOT NULL, "
            "observed_at DATETIME NOT NULL, center_x INTEGER NOT NULL, center_y INTEGER NOT NULL, "
            "moving BOOLEAN NOT NULL, distance FLOAT NOT NULL, "
            "created_at DATETIME DEFAULT CURRENT_TIMESTAMP NOT NULL)"
        )
        connection.exec_driver_sql("CREATE TABLE retained_marker (value TEXT PRIMARY KEY)")
        connection.exec_driver_sql("INSERT INTO retained_marker(value) VALUES ('keep')")
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    try:
        yield factory
    finally:
        engine.dispose()


def seed_activity(factory: sessionmaker[Session]) -> None:
    with factory.begin() as session:
        session.add_all(
            [
                ActivityObservation(
                    camera_id="pc-webcam-01",
                    subject_id="dog_001",
                    observed_at=NOW,
                    center_x=100,
                    center_y=100,
                    moving=False,
                    distance=0,
                ),
                ActivityObservation(
                    camera_id="pc-webcam-01",
                    subject_id="cat_001",
                    observed_at=NOW,
                    center_x=200,
                    center_y=200,
                    moving=True,
                    distance=24,
                ),
            ]
        )


def test_repository_deletes_only_activity_and_persists_idempotent_command(
    session_factory: sessionmaker[Session],
) -> None:
    seed_activity(session_factory)
    repository = ActivityCleanupRepository(session_factory)
    repository.bind_agent("agent_01", NOW)

    assert repository.apply("agent_01", COMMAND_ID, NOW) is True
    assert repository.apply("agent_01", COMMAND_ID, NOW) is False

    with session_factory() as session:
        state = session.get(ActivityCleanupState, 1)
        assert state is not None
        assert (
            state.agent_id,
            state.activity_enabled,
            state.command_id,
        ) == ("agent_01", False, COMMAND_ID)
        assert state.applied_at is not None
        assert state.applied_at.replace(tzinfo=UTC) == NOW
        assert session.scalar(select(func.count()).select_from(ActivityObservation)) == 0
        assert session.execute(select(func.count()).select_from(ActivityCleanupState)).scalar_one() == 1
        assert session.connection().exec_driver_sql(
            "SELECT value FROM retained_marker"
        ).scalar_one() == "keep"

    with pytest.raises(ActivityCleanupError, match="conflicting cleanup command"):
        repository.apply("agent_01", OTHER_COMMAND_ID, NOW)


def test_binding_a_fresh_agent_reenables_activity_after_old_cleanup(
    session_factory: sessionmaker[Session],
) -> None:
    repository = ActivityCleanupRepository(session_factory)
    repository.bind_agent("agent_old", NOW)
    repository.apply("agent_old", COMMAND_ID, NOW)

    repository.bind_agent("agent_new", NOW)

    with session_factory() as session:
        state = session.get(ActivityCleanupState, 1)
        assert state is not None
        assert (
            state.agent_id,
            state.activity_enabled,
            state.command_id,
            state.applied_at,
        ) == ("agent_new", True, None, None)


def test_signed_client_uses_exact_cleanup_domain_bodies_and_idempotent_ack() -> None:
    private_key = Ed25519PrivateKey.generate()
    public_key = private_key.public_key()
    nonces = iter(("AAAAAAAAAAAAAAAAAAAAAA", "AQEBAQEBAQEBAQEBAQEBAQ"))
    requests: list[bytes] = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = request.read()
        requests.append(body)
        assert request.url.path == "/api/petcare/agent/cleanup"
        assert request.headers["Content-Type"] == "application/json"
        assert request.headers["X-PetCare-Agent-Id"] == "agent_01"
        assert request.headers["X-PetCare-Timestamp"] == str(int(NOW.timestamp()))
        assert request.headers["X-PetCare-Content-SHA256"] == b64url(hashlib.sha256(body).digest())
        canonical = "\n".join(
            (
                "PETCARE-CLEANUP-V1",
                "POST",
                request.url.path,
                request.headers["X-PetCare-Agent-Id"],
                request.headers["X-PetCare-Timestamp"],
                request.headers["X-PetCare-Nonce"],
                request.headers["X-PetCare-Content-SHA256"],
                "",
            )
        ).encode()
        public_key.verify(
            base64.urlsafe_b64decode(request.headers["X-PetCare-Signature"] + "=="),
            canonical,
        )
        if body == b'{"action":"poll"}':
            return httpx.Response(
                200,
                content=b'{"commandId":"' + COMMAND_ID.encode() + b'","type":"delete_activity_observations"}',
                headers={"Content-Type": "application/json", "Cache-Control": "private, no-store"},
            )
        assert body == b'{"action":"ack","commandId":"' + COMMAND_ID.encode() + b'"}'
        return httpx.Response(204, headers={"Cache-Control": "private, no-store"})

    client = SignedActivityCleanupClient(
        origin="https://petcare.example",
        agent_id="agent_01",
        private_key=private_key,
        transport=httpx.MockTransport(handler),
        now=lambda: NOW,
        nonce=lambda: next(nonces),
    )
    try:
        assert client.poll() == ActivityCleanupCommand(COMMAND_ID, "delete_activity_observations")
        client.ack(COMMAND_ID)
    finally:
        client.close()

    assert requests == [
        b'{"action":"poll"}',
        b'{"action":"ack","commandId":"' + COMMAND_ID.encode() + b'"}',
    ]


def test_signed_client_accepts_private_no_store_empty_poll() -> None:
    client = SignedActivityCleanupClient(
        origin="https://petcare.example",
        agent_id="agent_01",
        private_key=Ed25519PrivateKey.generate(),
        transport=httpx.MockTransport(
            lambda _request: httpx.Response(204, headers={"Cache-Control": "private, no-store"})
        ),
        now=lambda: NOW,
        nonce=lambda: "AAAAAAAAAAAAAAAAAAAAAA",
    )
    try:
        assert client.poll() is None
    finally:
        client.close()


@pytest.mark.parametrize(
    ("status", "body"),
    [
        (200, b"not-json"),
        (200, b'{"type":"delete_activity_observations","commandId":"' + COMMAND_ID.encode() + b'"}'),
        (200, b'{"commandId":"' + OTHER_COMMAND_ID.encode() + b'","type":"other"}'),
        (200, b'{"commandId":"' + COMMAND_ID.encode() + b'","type":"delete_activity_observations","extra":1}'),
        (204, b"unexpected"),
    ],
)
def test_signed_client_rejects_noncanonical_poll_responses_without_echoing_body(
    status: int,
    body: bytes,
) -> None:
    client = SignedActivityCleanupClient(
        origin="https://petcare.example",
        agent_id="agent_01",
        private_key=Ed25519PrivateKey.generate(),
        transport=httpx.MockTransport(lambda _request: httpx.Response(status, content=body)),
        now=lambda: NOW,
        nonce=lambda: "AAAAAAAAAAAAAAAAAAAAAA",
    )
    try:
        with pytest.raises(ActivityCleanupError) as caught:
            client.poll()
        assert body.decode(errors="ignore") not in str(caught.value)
    finally:
        client.close()


def test_signed_client_rejects_generic_unauthorized_poll() -> None:
    client = SignedActivityCleanupClient(
        origin="https://petcare.example",
        agent_id="agent_01",
        private_key=Ed25519PrivateKey.generate(),
        transport=httpx.MockTransport(
            lambda _request: httpx.Response(
                401,
                content=b'{"error":"invalid_agent_signature"}',
            )
        ),
        now=lambda: NOW,
        nonce=lambda: "AAAAAAAAAAAAAAAAAAAAAA",
    )
    try:
        with pytest.raises(ActivityCleanupError):
            client.poll()
    finally:
        client.close()


def test_worker_retries_ack_loss_without_reapplying_local_cleanup(
    session_factory: sessionmaker[Session],
) -> None:
    seed_activity(session_factory)
    calls: list[str] = []

    class Client:
        def poll(self) -> ActivityCleanupCommand:
            calls.append("poll")
            return ActivityCleanupCommand(COMMAND_ID, "delete_activity_observations")

        def ack(self, command_id: str) -> None:
            calls.append(f"ack:{command_id}")
            if calls.count(f"ack:{command_id}") == 1:
                raise ActivityCleanupError("ack unavailable")

        def close(self) -> None:
            calls.append("close")

    worker = ActivityCleanupWorker(
        ActivityCleanupRepository(session_factory),
        Client(),  # type: ignore[arg-type]
        agent_id="agent_01",
        now=lambda: NOW,
    )
    worker.bind_agent()
    with pytest.raises(ActivityCleanupError, match="ack unavailable"):
        worker.process_once()
    worker.process_once()

    with session_factory() as session:
        assert session.scalar(select(func.count()).select_from(ActivityObservation)) == 0
        state = session.get(ActivityCleanupState, 1)
        assert state is not None and state.command_id == COMMAND_ID
    assert calls == ["poll", f"ack:{COMMAND_ID}", "poll", f"ack:{COMMAND_ID}"]
