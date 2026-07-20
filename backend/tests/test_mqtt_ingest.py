from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest
from alembic import command
from alembic.config import Config
from fastapi import FastAPI
from pydantic import ValidationError
from sqlalchemy import create_engine, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import sessionmaker

import app.main as main_module
from app.config import AppConfig, load_config
from app.events import DeviceStatusCommitted, SensorReadingCommitted
from app.mqtt_ingest import (
    EXACT_SUBSCRIPTIONS,
    IngestResult,
    MqttEndpoint,
    MqttIngestor,
    ensure_devices,
    handle_mqtt_message,
    load_mqtt_endpoint,
)
from app.rule_ingress import IngressTicket, RuleIngress


NOW = datetime(2026, 7, 16, 12, 0, tzinfo=UTC)


def payload(data: dict[str, object]) -> bytes:
    return json.dumps(data, separators=(",", ":")).encode()


def sensor(**overrides: object) -> bytes:
    data: dict[str, object] = {
        "device_id": "entrance-01",
        "sensor_type": "temperature",
        "value": 23.5,
        "unit": "C",
        "observed_at": NOW.isoformat().replace("+00:00", "Z"),
    }
    data.update(overrides)
    return payload(data)


def status(**overrides: object) -> bytes:
    data: dict[str, object] = {
        "device_id": "entrance-01",
        "status": "online",
        "observed_at": NOW.isoformat().replace("+00:00", "Z"),
    }
    data.update(overrides)
    return payload(data)


class FakeSession:
    def __init__(self, *, fail_flush: str | None = None) -> None:
        self.fail_flush = fail_flush
        self.added: list[object] = []
        self.committed = False
        self.rolled_back = False
        self.closed = False

    def add(self, row: object) -> None:
        self.added.append(row)

    def get(self, _model: object, key: str) -> object:
        return SimpleNamespace(device_id=key)

    def execute(self, _statement: object) -> object:
        return SimpleNamespace(all=lambda: [])

    def flush(self) -> None:
        if self.fail_flush:
            constraint = "uq_sensor_readings_device_type_observed" if self.fail_flush == "duplicate" else "fk_sensor_readings_device_id_devices"
            original = Exception(self.fail_flush)
            original.diag = SimpleNamespace(constraint_name=constraint)  # type: ignore[attr-defined]
            raise IntegrityError("insert", {}, original)
        if self.added:
            self.added[-1].id = 41

    def commit(self) -> None:
        self.committed = True

    def rollback(self) -> None:
        self.rolled_back = True

    def close(self) -> None:
        self.closed = True


class RecordingIngress:
    def __init__(self) -> None:
        self.committed: list[tuple[IngressTicket, object]] = []
        self.tombstones: list[tuple[IngressTicket, str]] = []
        self.session: FakeSession | None = None
        self.calls: list[str] = []

    def begin(self, source: str) -> IngressTicket:
        self.calls.append(f"begin:{source}")
        return IngressTicket(1, NOW, 50.0)

    def resolve_committed(self, ticket: IngressTicket, event: object) -> None:
        assert self.session is not None and self.session.committed and self.session.closed
        self.committed.append((ticket, event))

    def resolve_tombstone(self, ticket: IngressTicket, reason: str) -> None:
        self.tombstones.append((ticket, reason))


def run_message(
    topic: str,
    body: bytes,
    *,
    received_at: datetime = NOW,
    watermarks: dict[tuple[str, str], datetime] | None = None,
    seen: set[tuple[str, str, datetime]] | None = None,
    fail_flush: str | None = None,
) -> tuple[IngestResult, RecordingIngress, FakeSession]:
    ingress = RecordingIngress()
    session = FakeSession(fail_flush=fail_flush)
    ingress.session = session
    result = handle_mqtt_message(
        topic,
        body,
        IngressTicket(1, received_at, 50.0),
        ingress,
        lambda: session,
        watermarks if watermarks is not None else {},
        seen if seen is not None else set(),
    )
    return result, ingress, session


@pytest.mark.parametrize(
    ("topic", "body"),
    [
        ("home/pico/entrance-01/sensor/temperature", sensor(value="23.5")),
        ("home/pico/entrance-01/sensor/temperature", sensor(extra=True)),
        ("home/pico/entrance-01/sensor/temperature", b'{"sensor_type":"temperature","device_id":"entrance-01","value":23.5,"unit":"C","observed_at":"2026-07-16T12:00:00Z"}'),
        ("home/pico/entrance-01/sensor/humidity", sensor()),
        ("home/pico/petzone-01/sensor/food_weight", sensor(device_id="entrance-01", sensor_type="food_weight", unit="g")),
        ("home/pico/entrance-01/status", status(legacy=True)),
        ("home/camera/pc-webcam-01/detection", sensor()),
    ],
)
def test_strict_parsing_rejects_coercion_extras_order_and_topic_mismatch(topic: str, body: bytes) -> None:
    result, ingress, session = run_message(topic, body)
    assert result is IngestResult.rejected
    assert ingress.committed == []
    assert ingress.tombstones[0][1] == "validation_error"
    assert session.added == []


@pytest.mark.parametrize(
    ("device_id", "sensor_type", "value", "unit"),
    [
        ("entrance-01", "temperature", 21, "C"),
        ("entrance-01", "humidity", 50.5, "%"),
        ("entrance-01", "presence_moving", True, "bool"),
        ("entrance-01", "presence_stationary", False, "bool"),
        ("petzone-01", "temperature", 22.0, "C"),
        ("petzone-01", "humidity", 48, "%"),
        ("petzone-01", "presence_moving", False, "bool"),
        ("petzone-01", "presence_stationary", True, "bool"),
        ("petzone-01", "food_weight", 410.5, "g"),
        ("petzone-01", "water_weight", 620, "g"),
        ("petzone-01", "bed_pressure_left", 101, "adc"),
        ("petzone-01", "bed_pressure_center", 102, "adc"),
        ("petzone-01", "bed_pressure_right", 103, "adc"),
    ],
)
def test_all_authoritative_sensor_profiles_map_to_exact_database_value_kind(
    device_id: str, sensor_type: str, value: object, unit: str
) -> None:
    topic = f"home/pico/{device_id}/sensor/{sensor_type}"
    result, ingress, session = run_message(
        topic,
        sensor(device_id=device_id, sensor_type=sensor_type, value=value, unit=unit),
    )
    assert result is IngestResult.accepted_live
    assert isinstance(ingress.committed[0][1], SensorReadingCommitted)
    row = session.added[0]
    assert (row.value_boolean, row.value_number) == ((value, None) if isinstance(value, bool) else (None, value))


@pytest.mark.parametrize(
    "body",
    [
        b'{"device_id":"entrance-01","device_id":"entrance-01","sensor_type":"temperature","value":23.5,"unit":"C","observed_at":"2026-07-16T12:00:00Z"}',
        b'{"device_id":"entrance-01","sensor_type":"temperature","value":NaN,"unit":"C","observed_at":"2026-07-16T12:00:00Z"}',
    ],
)
def test_duplicate_json_keys_and_nonstandard_numbers_are_rejected(body: bytes) -> None:
    result, ingress, session = run_message("home/pico/entrance-01/sensor/temperature", body)
    assert result is IngestResult.rejected
    assert ingress.tombstones[0][1] == "validation_error"
    assert session.added == []


def test_sensor_future_boundary_and_watermark_dispositions() -> None:
    topic = "home/pico/entrance-01/sensor/temperature"
    at_5000 = NOW + timedelta(seconds=5)
    accepted, ingress, session = run_message(topic, sensor(observed_at=at_5000.isoformat()), received_at=NOW)
    assert accepted is IngestResult.accepted_live
    event = ingress.committed[0][1]
    assert isinstance(event, SensorReadingCommitted) and event.reading_id == 41
    assert session.added[0].received_at == NOW

    at_5001 = NOW + timedelta(seconds=5, milliseconds=1)
    rejected, ingress, session = run_message(topic, sensor(observed_at=at_5001.isoformat()), received_at=NOW)
    assert rejected is IngestResult.rejected
    assert ingress.tombstones[0][1] == "future_timestamp"
    assert session.added == []

    watermarks = {("entrance-01", "temperature"): NOW}
    duplicate, ingress, session = run_message(topic, sensor(), watermarks=watermarks)
    assert duplicate is IngestResult.duplicate
    assert ingress.tombstones[0][1] == "duplicate"
    assert session.added == []

    older = NOW - timedelta(seconds=1)
    stored, ingress, session = run_message(topic, sensor(observed_at=older.isoformat()), watermarks=watermarks)
    assert stored is IngestResult.stored_out_of_order
    assert ingress.tombstones[0][1] == "stored_out_of_order"
    assert session.committed and len(session.added) == 1
    assert watermarks[("entrance-01", "temperature")] == NOW


def test_status_staleness_offline_and_process_local_duplicates() -> None:
    topic = "home/pico/entrance-01/status"
    exactly_30 = NOW - timedelta(seconds=30)
    result, ingress, session = run_message(topic, status(observed_at=exactly_30.isoformat()), received_at=NOW)
    assert result is IngestResult.accepted_live
    assert isinstance(ingress.committed[0][1], DeviceStatusCommitted)
    assert session.committed

    over_30 = NOW - timedelta(seconds=30, milliseconds=1)
    result, ingress, _ = run_message(topic, status(observed_at=over_30.isoformat()), received_at=NOW)
    assert result is IngestResult.stale_online
    assert ingress.tombstones[0][1] == "stale_online"

    old = NOW - timedelta(days=10)
    seen: set[tuple[str, str, datetime]] = set()
    result, ingress, _ = run_message(topic, status(status="offline", observed_at=old.isoformat()), received_at=NOW, seen=seen)
    assert result is IngestResult.accepted_live
    assert ingress.committed[0][1].status == "offline"
    result, ingress, session = run_message(topic, status(status="offline", observed_at=old.isoformat()), received_at=NOW, seen=seen)
    assert result is IngestResult.duplicate
    assert ingress.tombstones[0][1] == "duplicate"
    assert not session.committed


def test_database_rollback_resolves_explicit_tombstone() -> None:
    result, ingress, session = run_message(
        "home/pico/entrance-01/sensor/temperature",
        sensor(observed_at=(NOW + timedelta(seconds=1)).isoformat()),
        fail_flush="duplicate",
    )
    assert result is IngestResult.duplicate
    assert session.rolled_back and session.closed and not session.committed
    assert ingress.tombstones[0][1] == "duplicate"

    result, ingress, session = run_message(
        "home/pico/entrance-01/sensor/temperature",
        sensor(observed_at=(NOW + timedelta(seconds=1)).isoformat()),
        fail_flush="foreign_key",
    )
    assert result is IngestResult.rejected
    assert session.rolled_back and session.closed
    assert ingress.tombstones[0][1] == "database_rollback"


def test_manifest_selects_exact_endpoint_and_rejects_wrong_profile(tmp_path: Path) -> None:
    manifest = tmp_path / "services.json"
    manifest.write_text(
        json.dumps({"mqtt_profiles": {"local_live": {"bind_host": "127.0.0.1", "port": 18883, "client_host": "127.0.0.1"}}}),
        encoding="utf-8",
    )
    assert load_mqtt_endpoint(manifest, "local_live") == MqttEndpoint("127.0.0.1", 18883)
    with pytest.raises(ValueError, match="profile"):
        load_mqtt_endpoint(manifest, "hardware")


def test_config_keeps_mqtt_disabled_by_default_and_secrets_redacted(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("DATABASE_URL", "postgresql+psycopg://petcare:secret@127.0.0.1:55432/petcare")
    for name in ("PETCARE_MQTT_PROFILE", "PETCARE_MQTT_USERNAME", "PETCARE_MQTT_PASSWORD"):
        monkeypatch.delenv(name, raising=False)
    assert not load_config().mqtt_enabled

    manifest = tmp_path / "services.json"
    manifest.write_text("{}", encoding="utf-8")
    monkeypatch.setenv("PETCARE_MQTT_PROFILE", "local_live")
    monkeypatch.setenv("PETCARE_MQTT_USERNAME", "petcare")
    monkeypatch.setenv("PETCARE_MQTT_PASSWORD", "mqtt-secret-sentinel")
    monkeypatch.setenv("PETCARE_SERVICES_MANIFEST", str(manifest))
    config = load_config()
    assert config.mqtt_enabled and config.mqtt_profile == "local_live"
    assert "mqtt-secret-sentinel" not in repr(config)
    assert config.mqtt_password is not None and config.mqtt_password.get_secret_value() == "mqtt-secret-sentinel"

    monkeypatch.delenv("PETCARE_MQTT_PASSWORD")
    with pytest.raises(ValidationError):
        load_config()
    for field in ("mqtt_username", "mqtt_password"):
        values = {
            "database_url": "postgresql+psycopg://petcare:secret@127.0.0.1:55432/petcare",
            "mqtt_profile": "local_live",
            "mqtt_username": "petcare",
            "mqtt_password": "secret",
        }
        values[field] = ""
        with pytest.raises(ValidationError):
            AppConfig.model_validate(values)


@pytest.mark.asyncio
async def test_lifespan_disposes_database_when_mqtt_startup_configuration_fails(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    calls: list[str] = []
    config = AppConfig(
        database_url="postgresql+psycopg://petcare:secret@127.0.0.1:55432/petcare",
        mqtt_profile="local_live",
        mqtt_services_manifest=str(tmp_path / "missing-services.json"),
        mqtt_username="petcare",
        mqtt_password="secret",
    )
    monkeypatch.setattr(main_module, "load_config", lambda: config)
    monkeypatch.setattr(main_module, "configure_database", lambda _url: calls.append("configure"))
    monkeypatch.setattr(main_module, "dispose_database", lambda: calls.append("dispose"))

    with pytest.raises(ValueError, match="profile"):
        async with main_module.lifespan(FastAPI()):
            pass
    assert calls == ["configure", "dispose"]


@pytest.mark.asyncio
async def test_lifespan_defers_mqtt_intake_until_rule_worker_exists(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[str] = []
    config = AppConfig(
        database_url="postgresql+psycopg://petcare:secret@127.0.0.1:55432/petcare",
        mqtt_profile="local_live",
        mqtt_username="petcare",
        mqtt_password="secret",
        camera_source="disabled",
    )

    class FakeIngestor:
        @classmethod
        def disabled(cls) -> "FakeIngestor":
            return cls()

        def __init__(self, **_kwargs: object) -> None:
            pass

        def start(self) -> None:
            calls.append("start")

        def stop(self) -> None:
            calls.append("stop")

    class FakeRuleWorker:
        def __init__(self, **_kwargs: object) -> None:
            pass

        def start(self) -> None:
            calls.append("worker:start")

        def shutdown(self) -> None:
            calls.append("worker:shutdown")

    monkeypatch.setattr(main_module, "load_config", lambda: config)
    monkeypatch.setattr(main_module, "configure_database", lambda _url: None)
    monkeypatch.setattr(main_module, "dispose_database", lambda: None)
    monkeypatch.setattr(main_module, "load_mqtt_endpoint", lambda *_args: MqttEndpoint("127.0.0.1", 18883))
    monkeypatch.setattr(main_module, "MqttIngestor", FakeIngestor)
    monkeypatch.setattr(main_module, "RuleEngine", lambda **_kwargs: object())
    monkeypatch.setattr(main_module, "RuleWorker", FakeRuleWorker)

    async with main_module.lifespan(FastAPI()):
        assert calls == ["worker:start", "start"]
    assert calls == ["worker:start", "start", "stop", "worker:shutdown"]


class FakeClient:
    def __init__(self) -> None:
        self.calls: list[tuple[object, ...]] = []
        self.on_connect = None
        self.on_message = None

    def username_pw_set(self, username: str, password: str) -> None:
        self.calls.append(("credentials", username, password))

    def reconnect_delay_set(self, minimum: int, maximum: int) -> None:
        self.calls.append(("reconnect", minimum, maximum))

    def connect_async(self, host: str, port: int, keepalive: int) -> None:
        self.calls.append(("connect", host, port, keepalive))

    def loop_start(self) -> None:
        self.calls.append(("loop_start",))

    def subscribe(self, topics: list[tuple[str, int]]) -> None:
        self.calls.append(("subscribe", tuple(topics)))

    def disconnect(self) -> None:
        self.calls.append(("disconnect",))

    def loop_stop(self) -> None:
        self.calls.append(("loop_stop",))


def test_paho_callback_begins_before_validation_and_lifecycle_is_owned() -> None:
    ingress = RecordingIngress()
    session = FakeSession()
    ingress.session = session
    client = FakeClient()
    ingestor = MqttIngestor(
        ingress=ingress,
        session_factory=lambda: session,
        endpoint=MqttEndpoint("127.0.0.1", 18883),
        username="petcare",
        password="secret",
        client_factory=lambda: client,
    )
    ingestor.start()
    client.on_connect(client, None, {}, 0, None)
    message = SimpleNamespace(topic="invalid", payload=b"{}")
    client.on_message(client, None, message)
    ingestor.stop()

    assert ingress.calls[0] == "begin:mqtt"
    assert ("reconnect", 1, 30) in client.calls
    assert ("subscribe", EXACT_SUBSCRIPTIONS) in client.calls
    assert client.calls[-2:] == [("disconnect",), ("loop_stop",)]


def test_disabled_ingestor_has_no_client_or_replay() -> None:
    ingestor = MqttIngestor.disabled()
    ingestor.start()
    ingestor.stop()
    assert not ingestor.enabled
    assert ingestor.last_result is None


def test_postgresql_round_trip_bootstraps_unknown_device_and_persists_sensor(
    database_url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("DATABASE_URL", database_url)
    alembic = Config(str(Path(__file__).parents[1] / "alembic.ini"))
    alembic.set_main_option("script_location", str(Path(__file__).parents[1] / "migrations"))
    alembic.set_main_option("sqlalchemy.url", database_url)
    command.upgrade(alembic, "head")
    engine = create_engine(database_url)
    sessions = sessionmaker(bind=engine, expire_on_commit=False)
    with engine.begin() as connection:
        connection.execute(text("TRUNCATE TABLE sensor_readings, devices CASCADE"))

    ensure_devices(sessions)
    ingress = RuleIngress()
    ticket = ingress.begin("mqtt")
    watermarks: dict[tuple[str, str], datetime] = {}
    result = handle_mqtt_message(
        "home/pico/entrance-01/sensor/temperature",
        sensor(observed_at=ticket.received_at_utc.isoformat()),
        ticket,
        ingress,
        sessions,
        watermarks,
        set(),
    )

    assert result is IngestResult.accepted_live
    assert isinstance(ingress.get(timeout=0.1).event, SensorReadingCommitted)
    with engine.connect() as connection:
        assert connection.execute(text("SELECT status FROM devices WHERE device_id='entrance-01'")).scalar_one() == "unknown"
        stored = connection.execute(
            text("SELECT value_number,value_boolean,received_at FROM sensor_readings WHERE device_id='entrance-01'")
        ).one()
        assert stored[0] == 23.5 and stored[1] is None and stored[2] == ticket.received_at_utc
    engine.dispose()
