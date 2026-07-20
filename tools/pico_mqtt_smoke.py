from __future__ import annotations

import argparse
import ipaddress
import json
import math
import os
import re
import threading
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Protocol


ROOT = Path(__file__).resolve().parents[1]
DEVICE_SENSORS = {
    "entrance-01": {
        "temperature": ("C", "number"),
        "humidity": ("%", "number"),
        "presence_moving": ("bool", "bool"),
        "presence_stationary": ("bool", "bool"),
    },
    "petzone-01": {
        "temperature": ("C", "number"),
        "humidity": ("%", "number"),
        "presence_moving": ("bool", "bool"),
        "presence_stationary": ("bool", "bool"),
        "food_weight": ("g", "number"),
        "water_weight": ("g", "number"),
        "bed_pressure_left": ("adc", "adc"),
        "bed_pressure_center": ("adc", "adc"),
        "bed_pressure_right": ("adc", "adc"),
    },
}
SENSOR_KEYS = ["device_id", "sensor_type", "value", "unit", "observed_at"]
STATUS_KEYS = ["device_id", "status", "observed_at"]
UTC_MILLISECONDS = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3}Z$")
RFC1918 = tuple(
    ipaddress.ip_network(network)
    for network in ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16")
)


class DuplicateJsonField(ValueError):
    pass


@dataclass(frozen=True, repr=False)
class Endpoint:
    host: str
    port: int
    username: str
    password: str


@dataclass(frozen=True, repr=False)
class SmokeConfig:
    device_id: str
    endpoint: Endpoint
    timeout: float
    require_reconnect: bool


@dataclass(frozen=True)
class SmokeResult:
    device_id: str
    required_sensors: frozenset[str]
    seen_sensors: frozenset[str]
    heartbeat: bool
    offline_status: bool
    reconnect: bool
    errors: tuple[str, ...]

    @property
    def ok(self) -> bool:
        return not self.errors and self.required_sensors <= self.seen_sensors and self.heartbeat

    @property
    def exit_code(self) -> int:
        return 0 if self.ok else 1


class MqttClient(Protocol):
    on_connect: Any
    on_message: Any

    def username_pw_set(self, username: str, password: str) -> None: ...
    def connect_async(self, host: str, port: int, keepalive: int) -> Any: ...
    def subscribe(self, topic: str, qos: int) -> Any: ...
    def loop_start(self) -> None: ...
    def loop_stop(self) -> None: ...
    def disconnect(self) -> Any: ...


class SmokeVerifier:
    def __init__(
        self,
        device_id: str,
        *,
        now: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
        monotonic: Callable[[], float] = time.monotonic,
        require_reconnect: bool = False,
    ) -> None:
        if device_id not in DEVICE_SENSORS:
            raise ValueError("device must be entrance-01 or petzone-01")
        self.device_id = device_id
        self.required_sensors = frozenset(DEVICE_SENSORS[device_id])
        self.seen_sensors: set[str] = set()
        self.heartbeat = False
        self.offline_status = False
        self.reconnect = False
        self.errors: list[str] = []
        self._now = now
        self._monotonic = monotonic
        self._require_reconnect = require_reconnect
        self._last_status: str | None = None
        self._last_online_at: datetime | None = None
        self._last_online_received: float | None = None

    @property
    def complete(self) -> bool:
        return (
            not self.errors
            and self.required_sensors <= self.seen_sensors
            and self.heartbeat
            and (not self._require_reconnect or self.reconnect)
        )

    def process(self, topic: str, payload: bytes) -> None:
        def object_without_duplicates(pairs: list[tuple[str, object]]) -> dict[str, object]:
            value = dict(pairs)
            if len(value) != len(pairs):
                raise DuplicateJsonField
            return value

        try:
            value = json.loads(payload, object_pairs_hook=object_without_duplicates)
        except DuplicateJsonField:
            self.errors.append(f"duplicate JSON field on {topic}")
            return
        except (UnicodeDecodeError, json.JSONDecodeError):
            self.errors.append(f"malformed JSON on {topic}")
            return
        if not isinstance(value, dict):
            self.errors.append(f"payload must be a JSON object on {topic}")
            return

        status_topic = f"home/pico/{self.device_id}/status"
        sensor_prefix = f"home/pico/{self.device_id}/sensor/"
        try:
            if topic == status_topic:
                self._status(value, topic)
            elif topic.startswith(sensor_prefix):
                self._sensor(topic[len(sensor_prefix):], value, topic)
            else:
                raise ValueError(f"unexpected topic {topic}")
        except ValueError as exc:
            self.errors.append(str(exc))

    def result(self, timed_out: bool = False) -> SmokeResult:
        errors = list(self.errors)
        missing = sorted(self.required_sensors - self.seen_sensors)
        if timed_out:
            errors.append("timeout before complete evidence")
        if missing:
            errors.append(f"missing sensors: {', '.join(missing)}")
        if not self.heartbeat:
            errors.append("missing 10-second heartbeat evidence")
        if self._require_reconnect and not self.reconnect:
            errors.append("missing offline-to-online reconnect evidence")
        return SmokeResult(
            device_id=self.device_id,
            required_sensors=self.required_sensors,
            seen_sensors=frozenset(self.seen_sensors),
            heartbeat=self.heartbeat,
            offline_status=self.offline_status,
            reconnect=self.reconnect,
            errors=tuple(dict.fromkeys(errors)),
        )

    def _sensor(self, sensor_type: str, payload: dict[str, object], topic: str) -> None:
        if list(payload) != SENSOR_KEYS:
            raise ValueError(f"malformed sensor fields on {topic}")
        if sensor_type not in DEVICE_SENSORS[self.device_id]:
            raise ValueError(f"unexpected sensor type on {topic}")
        if payload["device_id"] != self.device_id or payload["sensor_type"] != sensor_type:
            raise ValueError(f"sensor identity mismatch on {topic}")
        unit, kind = DEVICE_SENSORS[self.device_id][sensor_type]
        if payload["unit"] != unit:
            raise ValueError(f"invalid unit on {topic}")
        sensor_value = payload["value"]
        if kind == "bool" and type(sensor_value) is not bool:
            raise ValueError(f"invalid boolean value on {topic}")
        if kind == "number" and (
            type(sensor_value) not in (int, float) or not math.isfinite(sensor_value)
        ):
            raise ValueError(f"invalid numeric value on {topic}")
        if kind == "adc" and (type(sensor_value) is not int or not 0 <= sensor_value <= 4095):
            raise ValueError(f"invalid ADC value on {topic}")
        self._fresh(payload["observed_at"], topic)
        self.seen_sensors.add(sensor_type)

    def _status(self, payload: dict[str, object], topic: str) -> None:
        received_at = self._monotonic()
        if list(payload) != STATUS_KEYS:
            raise ValueError(f"malformed status fields on {topic}")
        if payload["device_id"] != self.device_id or payload["status"] not in ("online", "offline"):
            raise ValueError(f"invalid status payload on {topic}")
        observed_at = self._timestamp(payload["observed_at"], topic)
        if (observed_at - self._now()).total_seconds() > 5:
            raise ValueError(f"future timestamp on {topic}")

        state = str(payload["status"])
        if state == "online":
            if (self._now() - observed_at).total_seconds() > 30:
                raise ValueError(f"stale online status on {topic}")
            if (
                self._last_status == "online"
                and self._last_online_at is not None
                and self._last_online_received is not None
            ):
                payload_interval = (observed_at - self._last_online_at).total_seconds()
                receipt_interval = received_at - self._last_online_received
                self.heartbeat |= 8 <= payload_interval <= 12 and 8 <= receipt_interval <= 12
            if self.offline_status:
                self.reconnect = True
            self._last_online_at = observed_at
            self._last_online_received = received_at
        else:
            self.offline_status = True
        self._last_status = state

    def _fresh(self, value: object, topic: str) -> datetime:
        observed_at = self._timestamp(value, topic)
        age = (self._now() - observed_at).total_seconds()
        if age > 30:
            raise ValueError(f"stale sensor data on {topic}")
        if age < -5:
            raise ValueError(f"future timestamp on {topic}")
        return observed_at

    @staticmethod
    def _timestamp(value: object, topic: str) -> datetime:
        if not isinstance(value, str) or not UTC_MILLISECONDS.fullmatch(value):
            raise ValueError(f"invalid observed_at on {topic}")
        try:
            return datetime.strptime(value, "%Y-%m-%dT%H:%M:%S.%fZ").replace(tzinfo=timezone.utc)
        except ValueError as exc:
            raise ValueError(f"invalid observed_at on {topic}") from exc


def load_config(device_id: str, environ: Mapping[str, str] = os.environ) -> SmokeConfig:
    if device_id not in DEVICE_SENSORS:
        raise ValueError("device must be entrance-01 or petzone-01")
    if environ.get("PETCARE_MQTT_PROFILE", "hardware") != "hardware":
        raise ValueError("PETCARE_MQTT_PROFILE must be hardware")
    manifest_path = Path(environ.get("PETCARE_MQTT_SERVICES_MANIFEST", ROOT / ".runtime" / "services.json"))
    try:
        document = json.loads(manifest_path.read_text(encoding="utf-8"))
        endpoint = document["mqtt_profiles"]["hardware"]
        host = endpoint["client_host"]
        bind_host = endpoint["bind_host"]
        port = endpoint["port"]
    except (OSError, UnicodeError, json.JSONDecodeError, KeyError, TypeError) as exc:
        raise ValueError("hardware MQTT profile is missing or malformed") from exc
    try:
        address = ipaddress.ip_address(host)
    except ValueError as exc:
        raise ValueError("hardware MQTT client_host must be RFC1918 IPv4") from exc
    if address.version != 4 or not any(address in network for network in RFC1918) or host != bind_host or port != 18883:
        raise ValueError("hardware MQTT endpoint must use one RFC1918 address on port 18883")

    username = environ.get("PETCARE_MQTT_USERNAME", "")
    password = environ.get("PETCARE_MQTT_PASSWORD", "")
    if not username or not password:
        raise ValueError("PETCARE_MQTT_USERNAME and PETCARE_MQTT_PASSWORD are required")
    try:
        timeout = float(environ.get("PETCARE_MQTT_SMOKE_TIMEOUT", "45"))
    except ValueError as exc:
        raise ValueError("PETCARE_MQTT_SMOKE_TIMEOUT must be a number") from exc
    if not 12 <= timeout <= 300:
        raise ValueError("PETCARE_MQTT_SMOKE_TIMEOUT must be between 12 and 300 seconds")
    reconnect_value = environ.get("PETCARE_MQTT_REQUIRE_RECONNECT", "0")
    if reconnect_value not in ("0", "1"):
        raise ValueError("PETCARE_MQTT_REQUIRE_RECONNECT must be 0 or 1")
    return SmokeConfig(
        device_id=device_id,
        endpoint=Endpoint(host=host, port=port, username=username, password=password),
        timeout=timeout,
        require_reconnect=reconnect_value == "1",
    )


def run_smoke(
    client: MqttClient,
    *,
    host: str,
    port: int,
    username: str,
    password: str,
    verifier: SmokeVerifier,
    timeout: float,
    wait: Callable[[threading.Event, float], bool] = lambda done, seconds: done.wait(seconds),
) -> SmokeResult:
    done = threading.Event()

    def on_connect(connected: MqttClient, _userdata: Any, _flags: Any, reason_code: Any, _properties: Any) -> None:
        if reason_code != 0:
            verifier.errors.append("MQTT connection rejected")
            done.set()
            return
        for topic in (
            f"home/pico/{verifier.device_id}/status",
            f"home/pico/{verifier.device_id}/sensor/+",
        ):
            subscription = connected.subscribe(topic, qos=1)
            if isinstance(subscription, tuple) and subscription[0] != 0:
                verifier.errors.append(f"MQTT subscription failed for {topic}")
                done.set()

    def on_message(_client: MqttClient, _userdata: Any, message: Any) -> None:
        verifier.process(str(message.topic), bytes(message.payload))
        if verifier.complete or verifier.errors:
            done.set()

    client.on_connect = on_connect
    client.on_message = on_message
    client.username_pw_set(username, password)
    started = False
    try:
        client.connect_async(host, port, keepalive=30)
        client.loop_start()
        started = True
        signaled = wait(done, timeout)
    except Exception as exc:
        verifier.errors.append(f"MQTT client failure: {type(exc).__name__}")
        signaled = True
    finally:
        if started:
            client.loop_stop()
        try:
            client.disconnect()
        except Exception:
            pass
    return verifier.result(timed_out=not signaled)


def format_report(result: SmokeResult) -> str:
    missing = sorted(result.required_sensors - result.seen_sensors)
    lines = [
        f"{'PASS' if result.ok else 'FAIL'} Pico MQTT smoke: device={result.device_id}",
        f"sensors={len(result.seen_sensors)}/{len(result.required_sensors)}",
        f"heartbeat={'PASS' if result.heartbeat else 'FAIL'}",
        f"offline_status={'OBSERVED' if result.offline_status else 'NOT_OBSERVED'}",
        f"reconnect={'PASS' if result.reconnect else 'NOT_OBSERVED'}",
    ]
    if missing:
        lines.append(f"missing={','.join(missing)}")
    lines.extend(f"error={error}" for error in result.errors)
    return "\n".join(lines)


def _new_client() -> MqttClient:
    import paho.mqtt.client as mqtt

    return mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id="petcare-pico-smoke")


def main() -> int:
    parser = argparse.ArgumentParser(description="Verify one externally powered Pico 2 W MQTT profile")
    parser.add_argument("device_id", choices=tuple(DEVICE_SENSORS))
    args = parser.parse_args()
    try:
        config = load_config(args.device_id)
        verifier = SmokeVerifier(
            config.device_id,
            require_reconnect=config.require_reconnect,
        )
        result = run_smoke(
            _new_client(),
            host=config.endpoint.host,
            port=config.endpoint.port,
            username=config.endpoint.username,
            password=config.endpoint.password,
            verifier=verifier,
            timeout=config.timeout,
        )
    except (ImportError, ValueError) as exc:
        print(f"FAIL Pico MQTT smoke: {exc}")
        return 1
    print(format_report(result))
    return result.exit_code


if __name__ == "__main__":
    raise SystemExit(main())
