from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import UTC, datetime
from queue import Empty
from threading import Event, Thread
from time import sleep

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from app.api import DEFAULT_ALLOWED_ORIGINS, install_api
from app.contracts import (
    AnomalyAlert,
    AnomalyEventOut,
    BedChannelStatus,
    BedStatus,
    BedStatusMessage,
    DashboardSummary,
    DashboardUpdate,
    DeviceOut,
    HealthOut,
    SevenDayComparison,
    CameraStatus,
)
from app.dashboard_hub import DashboardHub


NOW = datetime(2026, 7, 20, 12, 0, tzinfo=UTC)


def test_uvicorn_has_a_websocket_protocol() -> None:
    from uvicorn.protocols.websockets.auto import AutoWebSocketsProtocol

    assert AutoWebSocketsProtocol is not None


def bed_status(raw: int = 100) -> BedStatus:
    return BedStatus(
        device_id="petzone-01",
        sensor_state="ready",
        pressure_state="empty",
        fusion_state="empty",
        camera_confirmed=False,
        channels=[
            BedChannelStatus(
                channel=channel,
                raw=raw,
                baseline=100.0,
                delta=float(max(0, raw - 100)),
                polarity=1,
                available=True,
                observed_at=NOW,
            )
            for channel in ("left", "center", "right")
        ],
        current_rest_seconds=0,
        today_rest_seconds=0,
        nighttime_exit_count=0,
        seven_day=SevenDayComparison(
            status="insufficient_data",
            today_seconds=0,
            baseline_seconds=None,
            difference_seconds=None,
            percent_change=None,
            complete_days=0,
        ),
        calibrated_at=NOW,
    )


def summary(raw: int = 100) -> DashboardSummary:
    return DashboardSummary(
        generated_at=NOW,
        health=HealthOut(
            status="healthy",
            database="up",
            mqtt="disabled",
            camera="online",
            queue="ok",
            worker="running",
        ),
        devices=[DeviceOut(device_id="petzone-01", status="online", last_seen_at=NOW)],
        latest_sensors=[],
        camera=CameraStatus(
            state="online", fps=5.0, inference_ms=20.0, last_frame_at=NOW, reason=None
        ),
        bed=bed_status(raw),
        behaviors=[],
        anomalies=[],
    )


def anomaly(identifier: int = 1) -> AnomalyAlert:
    return AnomalyAlert(
        type="anomaly_alert",
        payload=AnomalyEventOut(
            id=identifier,
            subject_id=None,
            anomaly_type="bed_sensor_mismatch",
            severity="warning",
            mismatch_kind="unconfirmed_pressure",
            message="check",
            occurred_at=NOW,
        ),
    )


def test_hub_coalesces_summary_and_bed_to_latest_when_full() -> None:
    hub = DashboardHub(capacity=1)
    first = DashboardUpdate(type="dashboard_update", payload=summary(100))
    latest = DashboardUpdate(type="dashboard_update", payload=summary(300))
    bed = BedStatusMessage(type="bed_status", payload=bed_status(200))

    assert hub.publish_from_worker(first)
    assert not hub.publish_from_worker(DashboardUpdate(type="dashboard_update", payload=summary(200)))
    assert not hub.publish_from_worker(latest)
    assert not hub.publish_from_worker(bed)
    assert hub.queue_full

    assert hub.get_for_broadcast(timeout=0.1) == first
    assert hub.get_for_broadcast(timeout=0.1) == latest
    assert hub.get_for_broadcast(timeout=0.1) == bed
    assert not hub.queue_full


def test_hub_never_replays_stale_coalesced_value_after_a_slot_opens(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    hub = DashboardHub(capacity=1)
    queued = DashboardUpdate(type="dashboard_update", payload=summary(100))
    stale = BedStatusMessage(type="bed_status", payload=bed_status(200))
    newest = BedStatusMessage(type="bed_status", payload=bed_status(300))
    assert hub.publish_from_worker(queued)
    assert not hub.publish_from_worker(stale)

    slot_open = Event()
    resume_promotion = Event()
    original_promote = hub._promote_coalesced

    def paused_promote() -> None:
        slot_open.set()
        assert resume_promotion.wait(1)
        original_promote()

    monkeypatch.setattr(hub, "_promote_coalesced", paused_promote)
    received: list[object] = []
    consumer = Thread(target=lambda: received.append(hub.get_for_broadcast(timeout=0.5)))
    consumer.start()
    assert slot_open.wait(1)
    try:
        hub.publish_from_worker(newest)
    finally:
        resume_promotion.set()
    consumer.join(1)

    assert received == [queued]
    assert hub.get_for_broadcast(timeout=0.1) == newest
    with pytest.raises(Empty):
        hub.get_for_broadcast(timeout=0.01)


def test_hub_retries_committed_anomaly_until_capacity_or_shutdown() -> None:
    hub = DashboardHub(capacity=1)
    first = DashboardUpdate(type="dashboard_update", payload=summary())
    alert = anomaly()
    assert hub.publish_from_worker(first)

    result: list[bool] = []
    publisher = Thread(target=lambda: result.append(hub.publish_from_worker(alert)))
    publisher.start()
    sleep(0.05)
    assert publisher.is_alive()
    assert hub.queue_full
    assert hub.get_for_broadcast(timeout=0.1) == first
    publisher.join(1)
    assert result == [True]
    assert hub.get_for_broadcast(timeout=0.1) == alert

    assert hub.publish_from_worker(first)
    result.clear()
    publisher = Thread(target=lambda: result.append(hub.publish_from_worker(alert)))
    publisher.start()
    sleep(0.05)
    hub.shutdown()
    publisher.join(1)
    assert result == [False]


def test_hub_rejects_open_or_unknown_message_unions() -> None:
    hub = DashboardHub(capacity=1)
    with pytest.raises((TypeError, ValueError)):
        hub.publish_from_worker({"type": "unknown", "payload": {}})
    with pytest.raises((TypeError, ValueError)):
        hub.publish_from_worker(
            {"type": "anomaly_alert", "payload": anomaly().payload.model_dump(), "extra": True}
        )


def websocket_app(hub: DashboardHub) -> FastAPI:
    @asynccontextmanager
    async def lifespan(_application: FastAPI):
        task = hub.start_broadcaster()
        yield
        hub.shutdown()
        await task

    application = FastAPI(docs_url=None, redoc_url=None, openapi_url=None, lifespan=lifespan)
    application.state.dashboard_hub = hub
    install_api(application, allowed_origins=DEFAULT_ALLOWED_ORIGINS)
    return application


def test_allowed_websocket_receives_closed_union_and_cleans_up() -> None:
    hub = DashboardHub(capacity=4)
    with TestClient(websocket_app(hub)) as client:
        with client.websocket_connect(
            "/ws/dashboard", headers={"Origin": "http://localhost:3000"}
        ) as websocket:
            assert hub.subscriber_count == 1
            hub.publish_from_worker(anomaly(7))
            assert websocket.receive_json() == anomaly(7).model_dump(mode="json")
        assert hub.subscriber_count == 0


@pytest.mark.parametrize("headers", [{}, {"Origin": "null"}, {"Origin": "https://evil.example"}])
def test_missing_null_or_hostile_websocket_origin_closes_1008_without_subscriber(
    headers: dict[str, str],
) -> None:
    hub = DashboardHub(capacity=1)
    with TestClient(websocket_app(hub)) as client:
        with pytest.raises(WebSocketDisconnect) as caught:
            with client.websocket_connect("/ws/dashboard", headers=headers):
                pass
    assert caught.value.code == 1008
    assert hub.subscriber_count == 0
