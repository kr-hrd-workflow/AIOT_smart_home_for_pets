from __future__ import annotations

import json
from collections.abc import Callable, MutableMapping, MutableSet
from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import Enum
from pathlib import Path
from threading import Event
from typing import Any, Literal, cast

import paho.mqtt.client as mqtt
from pydantic import ValidationError
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.orm import Session

from .contracts import DeviceStatusIn, SensorReadingIn
from .events import DeviceStatusCommitted, SensorReadingCommitted
from .models import Device, SensorReading
from .rule_ingress import IngressTicket, RuleIngress


SENSOR_KEYS = ("device_id", "sensor_type", "value", "unit", "observed_at")
STATUS_KEYS = ("device_id", "status", "observed_at")
SENSOR_TYPES = (
    "temperature",
    "humidity",
    "presence_moving",
    "presence_stationary",
    "food_weight",
    "water_weight",
    "bed_pressure_left",
    "bed_pressure_center",
    "bed_pressure_right",
)
EXACT_SUBSCRIPTIONS = (
    ("home/pico/+/sensor/+", 1),
    ("home/pico/+/status", 1),
)


class IngestResult(str, Enum):
    accepted_live = "accepted_live"
    stored_out_of_order = "stored_out_of_order"
    stale_online = "stale_online"
    duplicate = "duplicate"
    rejected = "rejected"


@dataclass(frozen=True, slots=True)
class MqttEndpoint:
    host: str
    port: int


def load_mqtt_endpoint(path: str | Path, profile: str) -> MqttEndpoint:
    try:
        document = json.loads(Path(path).read_text(encoding="utf-8"))
        selected = document["mqtt_profiles"][profile]
        host = selected["client_host"]
        port = selected["port"]
    except (OSError, KeyError, TypeError, json.JSONDecodeError) as error:
        raise ValueError(f"MQTT profile {profile!r} is unavailable") from error
    if type(host) is not str or not host or type(port) is not int or not 1 <= port <= 65535:
        raise ValueError(f"MQTT profile {profile!r} has an invalid endpoint")
    return MqttEndpoint(host, port)


def _reject_constant(value: str) -> None:
    raise ValueError(f"invalid JSON constant {value}")


def _parse_pairs(payload: bytes, keys: tuple[str, ...]) -> dict[str, object]:
    pairs = json.loads(payload, object_pairs_hook=lambda value: value, parse_constant=_reject_constant)
    if not isinstance(pairs, list) or any(not isinstance(pair, tuple) or len(pair) != 2 for pair in pairs):
        raise ValueError("payload must be a JSON object")
    if tuple(pair[0] for pair in pairs) != keys:
        raise ValueError("payload keys or order do not match")
    return dict(pairs)


def _parse_message(topic: str, payload: bytes) -> SensorReadingIn | DeviceStatusIn:
    parts = topic.split("/")
    if len(parts) == 5 and parts[:2] == ["home", "pico"] and parts[3] == "sensor":
        device_id, sensor_type = parts[2], parts[4]
        if sensor_type not in SENSOR_TYPES:
            raise ValueError("invalid sensor topic")
        model = SensorReadingIn.model_validate(_parse_pairs(payload, SENSOR_KEYS))
        if model.device_id != device_id or model.sensor_type != sensor_type:
            raise ValueError("sensor topic and payload do not match")
        return model
    if len(parts) == 4 and parts[:2] == ["home", "pico"] and parts[3] == "status":
        model = DeviceStatusIn.model_validate(_parse_pairs(payload, STATUS_KEYS))
        if model.device_id != parts[2]:
            raise ValueError("status topic and payload do not match")
        return model
    raise ValueError("unsupported MQTT topic")


def _is_sensor_duplicate(error: IntegrityError) -> bool:
    diagnostics = getattr(error.orig, "diag", None)
    return getattr(diagnostics, "constraint_name", None) == "uq_sensor_readings_device_type_observed"


def _sensor_row(model: SensorReadingIn, ticket: IngressTicket) -> SensorReading:
    boolean = model.value if isinstance(model.value, bool) else None
    number = None if isinstance(model.value, bool) else model.value
    return SensorReading(
        device_id=model.device_id,
        sensor_type=model.sensor_type,
        value_number=number,
        value_boolean=boolean,
        unit=model.unit,
        observed_at=model.observed_at,
        received_at=ticket.received_at_utc,
    )


def handle_mqtt_message(
    topic: str,
    payload: bytes,
    ticket: IngressTicket,
    ingress: RuleIngress,
    session_factory: Callable[[], Session],
    watermarks: MutableMapping[tuple[str, str], datetime],
    status_seen: MutableSet[tuple[str, str, datetime]],
) -> IngestResult:
    result = IngestResult.rejected
    reason = "validation_error"
    event: SensorReadingCommitted | DeviceStatusCommitted | None = None
    session: Session | None = None
    watermark_update: tuple[tuple[str, str], datetime] | None = None
    status_update: tuple[str, str, datetime] | None = None
    try:
        model = _parse_message(topic, payload)
        if model.observed_at - ticket.received_at_utc > timedelta(seconds=5):
            reason = "future_timestamp"
        elif isinstance(model, SensorReadingIn):
            key = (model.device_id, model.sensor_type)
            watermark = watermarks.get(key)
            if watermark is not None and model.observed_at == watermark:
                result, reason = IngestResult.duplicate, "duplicate"
            else:
                session = session_factory()
                row = _sensor_row(model, ticket)
                session.add(row)
                session.flush()
                session.commit()
                if watermark is not None and model.observed_at < watermark:
                    result, reason = IngestResult.stored_out_of_order, "stored_out_of_order"
                else:
                    result, reason = IngestResult.accepted_live, ""
                    watermark_update = key, model.observed_at
                    event = SensorReadingCommitted(
                        reading_id=cast(int, row.id),
                        device_id=model.device_id,
                        sensor_type=model.sensor_type,
                        observed_at=model.observed_at,
                    )
        else:
            status_key = (model.device_id, model.status, model.observed_at)
            if status_key in status_seen:
                result, reason = IngestResult.duplicate, "duplicate"
            elif model.status == "online" and ticket.received_at_utc - model.observed_at > timedelta(seconds=30):
                result, reason = IngestResult.stale_online, "stale_online"
            else:
                session = session_factory()
                session.commit()
                result, reason = IngestResult.accepted_live, ""
                status_update = status_key
                event = DeviceStatusCommitted(
                    device_id=model.device_id,
                    status=model.status,
                    observed_at=model.observed_at,
                )
    except (UnicodeDecodeError, ValueError, ValidationError, json.JSONDecodeError):
        result, reason = IngestResult.rejected, "validation_error"
    except IntegrityError as error:
        if session is not None:
            session.rollback()
        if _is_sensor_duplicate(error):
            result, reason = IngestResult.duplicate, "duplicate"
        else:
            result, reason = IngestResult.rejected, "database_rollback"
    except SQLAlchemyError:
        if session is not None:
            session.rollback()
        result, reason = IngestResult.rejected, "database_rollback"
    finally:
        if session is not None:
            session.close()
        if event is None:
            ingress.resolve_tombstone(ticket, reason)
        else:
            if watermark_update is not None:
                watermarks[watermark_update[0]] = watermark_update[1]
            if status_update is not None:
                status_seen.add(status_update)
            ingress.resolve_committed(ticket, event)
    return result


def load_sensor_watermarks(session_factory: Callable[[], Session]) -> dict[tuple[str, str], datetime]:
    session = session_factory()
    try:
        rows = session.execute(
            select(SensorReading.device_id, SensorReading.sensor_type, func.max(SensorReading.observed_at)).group_by(
                SensorReading.device_id, SensorReading.sensor_type
            )
        ).all()
        return {(device_id, sensor_type): observed_at for device_id, sensor_type, observed_at in rows}
    finally:
        session.close()


def ensure_devices(session_factory: Callable[[], Session]) -> None:
    session = session_factory()
    try:
        for device_id in ("entrance-01", "petzone-01"):
            if session.get(Device, device_id) is None:
                session.add(Device(device_id=device_id))
        session.commit()
    except SQLAlchemyError:
        session.rollback()
        raise
    finally:
        session.close()


class MqttIngestor:
    def __init__(
        self,
        *,
        ingress: RuleIngress | None,
        session_factory: Callable[[], Session] | None,
        endpoint: MqttEndpoint | None,
        username: str | None,
        password: str | None,
        client_factory: Callable[[], Any] | None = None,
    ) -> None:
        self._ingress = ingress
        self._session_factory = session_factory
        self._endpoint = endpoint
        self._username = username
        self._password = password
        self._client_factory = client_factory or self._new_client
        self._client: Any | None = None
        self._watermarks: dict[tuple[str, str], datetime] = {}
        self._status_seen: set[tuple[str, str, datetime]] = set()
        self._started = False
        self._connected = Event()
        self.last_result: IngestResult | None = None
        self.last_error: str | None = None

    @classmethod
    def disabled(cls) -> MqttIngestor:
        return cls(ingress=None, session_factory=None, endpoint=None, username=None, password=None)

    @property
    def enabled(self) -> bool:
        return all((self._ingress, self._session_factory, self._endpoint, self._username, self._password))

    @property
    def connected(self) -> bool:
        return self._connected.is_set()

    @staticmethod
    def _new_client() -> mqtt.Client:
        return mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id="petcare-backend")

    def start(self) -> None:
        if not self.enabled or self._started:
            return
        assert self._session_factory is not None and self._endpoint is not None
        ensure_devices(self._session_factory)
        self._watermarks = load_sensor_watermarks(self._session_factory)
        client = self._client_factory()
        client.username_pw_set(self._username, self._password)
        client.reconnect_delay_set(1, 30)
        client.on_connect = self._on_connect
        client.on_disconnect = self._on_disconnect
        client.on_message = self._on_message
        client.connect_async(self._endpoint.host, self._endpoint.port, 30)
        client.loop_start()
        self._client = client
        self._started = True

    def _on_connect(self, client: Any, _userdata: object, _flags: object, reason_code: object, _properties: object) -> None:
        if reason_code == 0:
            self._connected.set()
            self.last_error = None
            client.subscribe(list(EXACT_SUBSCRIPTIONS))
        else:
            self._connected.clear()
            self.last_error = f"MQTT connection failed: {reason_code}"

    def _on_disconnect(
        self,
        _client: Any,
        _userdata: object,
        _disconnect_flags: object,
        _reason_code: object,
        _properties: object,
    ) -> None:
        self._connected.clear()

    def _on_message(self, _client: Any, _userdata: object, message: object) -> None:
        assert self._ingress is not None and self._session_factory is not None
        ticket = self._ingress.begin("mqtt")
        try:
            self.last_result = handle_mqtt_message(
                message.topic,
                message.payload,
                ticket,
                self._ingress,
                self._session_factory,
                self._watermarks,
                self._status_seen,
            )
        except Exception as error:
            self.last_error = type(error).__name__
            raise

    def stop(self) -> None:
        if not self._started or self._client is None:
            return
        self._client.disconnect()
        self._client.loop_stop()
        self._connected.clear()
        self._client = None
        self._started = False
