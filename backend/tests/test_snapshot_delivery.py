from __future__ import annotations

import base64
import hashlib
import threading
import time
from datetime import UTC, datetime

import httpx
import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from app.agent_client import SignedSnapshotClient
from app.snapshot_delivery import SnapshotDeliveryWorker


def _b64url(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).decode("ascii").rstrip("=")


def test_upload_signs_exact_snapshot_request_and_accepts_private_no_store_204() -> None:
    private_key = Ed25519PrivateKey.generate()
    public_key = private_key.public_key()
    body = b'{"activeAlertCount":0}'
    captured: list[httpx.Request] = []

    def respond(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        return httpx.Response(204, headers={"Cache-Control": "private, no-store"})

    client = SignedSnapshotClient(
        origin="https://dashboard.example",
        agent_id="agent-1",
        private_key=private_key,
        transport=httpx.MockTransport(respond),
        now=lambda: datetime(2026, 7, 27, tzinfo=UTC),
        nonce=lambda: "AQIDBAUGBwgJCgsMDQ4PEA",
    )

    client.upload(body)

    request = captured.pop()
    digest = _b64url(hashlib.sha256(body).digest())
    canonical = (
        "PETCARE-SNAPSHOT-V1\n"
        "POST\n"
        "/api/petcare/agent/snapshot\n"
        "agent-1\n"
        "1785110400\n"
        "AQIDBAUGBwgJCgsMDQ4PEA\n"
        f"{digest}\n"
    ).encode()
    assert request.url.path == "/api/petcare/agent/snapshot"
    assert request.content == body
    assert request.headers["Content-Length"] == str(len(body))
    assert request.headers["X-PetCare-Content-SHA256"] == digest
    assert request.headers["X-PetCare-Nonce"] == "AQIDBAUGBwgJCgsMDQ4PEA"
    assert set(request.extensions["timeout"].values()) == {1.5}
    public_key.verify(
        base64.urlsafe_b64decode(request.headers["X-PetCare-Signature"] + "=="), canonical
    )
    client.close()


def test_worker_retries_only_at_next_two_second_cadence_and_keeps_newest_summary() -> None:
    attempted: list[bytes] = []
    current = [b'{"version":1}']
    first_attempt = threading.Event()
    release_first = threading.Event()

    class Client:
        def upload(self, summary_bytes: bytes) -> None:
            attempted.append(summary_bytes)
            first_attempt.set()
            release_first.wait(1)
            if len(attempted) == 1:
                raise RuntimeError("offline")

        def close(self) -> None:
            pass

    worker = SnapshotDeliveryWorker(
        Client(),
        lambda: current[0],
        cadence_seconds=0.02,
    )
    worker.start()
    assert first_attempt.wait(1)
    current[0] = b'{"version":2}'
    current[0] = b'{"version":3}'
    release_first.set()
    deadline = time.monotonic() + 1
    while len(attempted) < 2 and time.monotonic() < deadline:
        time.sleep(0.005)
    worker.stop(timeout_seconds=1)
    assert attempted[:2] == [b'{"version":1}', b'{"version":3}']


def test_worker_stop_cancels_the_next_cadence_and_closes_once() -> None:
    attempted: list[bytes] = []
    closed: list[None] = []

    class Client:
        def upload(self, summary_bytes: bytes) -> None:
            attempted.append(summary_bytes)

        def close(self) -> None:
            closed.append(None)

    worker = SnapshotDeliveryWorker(
        Client(),
        lambda: b'{"version":1}',
        cadence_seconds=2.0,
    )
    worker.start()
    deadline = time.monotonic() + 1
    while not attempted and time.monotonic() < deadline:
        time.sleep(0.005)
    worker.stop(timeout_seconds=0.2)
    worker.stop(timeout_seconds=0.2)

    assert attempted == [b'{"version":1}']
    assert closed == [None]


def test_worker_timeout_closes_once_when_the_inflight_request_finishes() -> None:
    entered = threading.Event()
    release = threading.Event()
    closed: list[None] = []

    class Client:
        def upload(self, _summary_bytes: bytes) -> None:
            entered.set()
            release.wait(1)

        def close(self) -> None:
            closed.append(None)

    worker = SnapshotDeliveryWorker(Client(), lambda: b"{}", cadence_seconds=2.0)
    worker.start()
    assert entered.wait(1)
    with pytest.raises(TimeoutError, match="snapshot delivery shutdown timed out"):
        worker.stop(timeout_seconds=0)
    assert closed == []

    release.set()
    deadline = time.monotonic() + 1
    while not closed and time.monotonic() < deadline:
        time.sleep(0.005)
    assert closed == [None]
    worker.stop(timeout_seconds=1)
    assert closed == [None]
