from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

from tools.pico_mqtt_smoke import (
    SmokeVerifier,
    format_report,
    load_config,
    run_smoke,
)


NOW = datetime(2026, 7, 20, 6, 0, 30, tzinfo=timezone.utc)


def observed(seconds: int) -> str:
    return datetime.fromtimestamp(NOW.timestamp() + seconds, timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def sensor(device_id: str, sensor_type: str, value: object, unit: str, seconds: int = 0) -> bytes:
    return json.dumps(
        {
            "device_id": device_id,
            "sensor_type": sensor_type,
            "value": value,
            "unit": unit,
            "observed_at": observed(seconds),
        },
        separators=(",", ":"),
    ).encode()


def status(device_id: str, state: str, seconds: int) -> bytes:
    return json.dumps(
        {"device_id": device_id, "status": state, "observed_at": observed(seconds)},
        separators=(",", ":"),
    ).encode()


ENTRANCE = {
    "temperature": (22.5, "C"),
    "humidity": (51.0, "%"),
    "presence_moving": (True, "bool"),
    "presence_stationary": (False, "bool"),
}
PETZONE = {
    **ENTRANCE,
    "food_weight": (80.0, "g"),
    "water_weight": (70.0, "g"),
    "bed_pressure_left": (100, "adc"),
    "bed_pressure_center": (200, "adc"),
    "bed_pressure_right": (300, "adc"),
}


WireMessage = tuple[str, bytes, int, bool]


class FakeClient:
    def __init__(self, messages: list[WireMessage]) -> None:
        self.messages = messages
        self.subscriptions: list[tuple[str, int]] = []
        self.auth: tuple[str, str] | None = None
        self.on_connect = None
        self.on_message = None
        self.connected: tuple[str, int, int] | None = None
        self.stopped = False

    def username_pw_set(self, username: str, password: str) -> None:
        self.auth = (username, password)

    def connect_async(self, host: str, port: int, keepalive: int) -> None:
        self.connected = (host, port, keepalive)

    def subscribe(self, topic: str, qos: int) -> tuple[int, int]:
        self.subscriptions.append((topic, qos))
        return (0, len(self.subscriptions))

    def loop_start(self) -> None:
        assert self.on_connect is not None and self.on_message is not None
        self.on_connect(self, None, None, 0, None)
        for topic, payload, qos, retain in self.messages:
            self.on_message(self, None, SimpleNamespace(topic=topic, payload=payload, qos=qos, retain=retain))

    def loop_stop(self) -> None:
        self.stopped = True

    def disconnect(self) -> None:
        pass


def wire(topic: str, payload: bytes, *, qos: int = 1, retain: bool = False) -> WireMessage:
    return topic, payload, qos, retain


def complete_messages(device_id: str, sensors: dict[str, tuple[object, str]]) -> list[WireMessage]:
    messages = [
        wire(f"home/pico/{device_id}/status", status(device_id, "online", -20), retain=True),
        wire(f"home/pico/{device_id}/status", status(device_id, "online", -10)),
    ]
    messages.extend(
        wire(f"home/pico/{device_id}/sensor/{name}", sensor(device_id, name, value, unit, -1))
        for name, (value, unit) in sensors.items()
    )
    return messages


def immediate_wait(done, timeout: float) -> bool:
    return done.is_set()


def monotonic_values(*values: float):
    remaining = iter(values)
    return lambda: next(remaining)


def test_load_config_uses_only_hardware_manifest_and_redacts_credentials(tmp_path: Path) -> None:
    manifest = tmp_path / "services.json"
    manifest.write_text(
        json.dumps(
            {
                "mqtt_profiles": {
                    "hardware": {"bind_host": "192.168.10.8", "client_host": "192.168.10.8", "port": 18883}
                }
            }
        ),
        encoding="utf-8",
    )
    env = {
        "PETCARE_MQTT_SERVICES_MANIFEST": str(manifest),
        "PETCARE_MQTT_PROFILE": "hardware",
        "PETCARE_MQTT_USERNAME": "pico-user",
        "PETCARE_MQTT_PASSWORD": "top-secret",
        "PETCARE_MQTT_SMOKE_TIMEOUT": "45",
    }

    config = load_config("entrance-01", env)

    assert (config.endpoint.host, config.endpoint.port) == ("192.168.10.8", 18883)
    assert config.endpoint.username == "pico-user"
    assert config.endpoint.password == "top-secret"
    assert "pico-user" not in repr(config)
    assert "top-secret" not in repr(config)
    assert config.timeout == 45


def test_load_config_rejects_non_hardware_profile_without_echoing_secret(tmp_path: Path) -> None:
    manifest = tmp_path / "services.json"
    manifest.write_text(json.dumps({"mqtt_profiles": {"local_live": {"client_host": "127.0.0.1", "port": 18883}}}))
    env = {
        "PETCARE_MQTT_SERVICES_MANIFEST": str(manifest),
        "PETCARE_MQTT_PROFILE": "local_live",
        "PETCARE_MQTT_USERNAME": "u",
        "PETCARE_MQTT_PASSWORD": "must-not-appear",
    }

    with pytest.raises(ValueError, match="hardware") as error:
        load_config("entrance-01", env)

    assert "must-not-appear" not in str(error.value)


def test_load_config_accepts_explicit_public_hardware_endpoint(tmp_path: Path) -> None:
    manifest = tmp_path / "services.json"
    manifest.write_text(
        json.dumps(
            {
                "mqtt_profiles": {
                    "hardware": {
                        "bind_host": "1.1.1.1",
                        "client_host": "1.1.1.1",
                        "port": 18883,
                        "allow_public_network": True,
                    }
                }
            }
        ),
        encoding="utf-8",
    )
    env = {
        "PETCARE_MQTT_SERVICES_MANIFEST": str(manifest),
        "PETCARE_MQTT_PROFILE": "hardware",
        "PETCARE_MQTT_USERNAME": "pico-user",
        "PETCARE_MQTT_PASSWORD": "top-secret",
    }

    config = load_config("entrance-01", env)

    assert (config.endpoint.host, config.endpoint.port) == ("1.1.1.1", 18883)


def test_load_config_rejects_public_hardware_endpoint_without_opt_in(tmp_path: Path) -> None:
    manifest = tmp_path / "services.json"
    manifest.write_text(
        json.dumps(
            {
                "mqtt_profiles": {
                    "hardware": {
                        "bind_host": "1.1.1.1",
                        "client_host": "1.1.1.1",
                        "port": 18883,
                    }
                }
            }
        ),
        encoding="utf-8",
    )
    env = {
        "PETCARE_MQTT_SERVICES_MANIFEST": str(manifest),
        "PETCARE_MQTT_PROFILE": "hardware",
        "PETCARE_MQTT_USERNAME": "pico-user",
        "PETCARE_MQTT_PASSWORD": "top-secret",
    }

    with pytest.raises(ValueError, match="explicit public-network opt-in"):
        load_config("entrance-01", env)


def test_entrance_smoke_subscribes_exact_topics_and_reports_heartbeat() -> None:
    verifier = SmokeVerifier("entrance-01", now=lambda: NOW, monotonic=monotonic_values(100, 110))
    client = FakeClient(complete_messages("entrance-01", ENTRANCE))

    result = run_smoke(
        client,
        host="192.168.10.8",
        port=18883,
        username="pico-user",
        password="top-secret",
        verifier=verifier,
        timeout=45,
        wait=immediate_wait,
    )

    assert result.ok
    assert client.subscriptions == [
        ("home/pico/entrance-01/status", 1),
        ("home/pico/entrance-01/sensor/+", 1),
    ]
    report = format_report(result)
    assert "heartbeat=PASS" in report
    assert "sensors=4/4" in report
    assert "top-secret" not in report


def test_petzone_requires_all_nine_sensors_and_returns_nonzero_result_when_missing() -> None:
    verifier = SmokeVerifier("petzone-01", now=lambda: NOW, monotonic=monotonic_values(100, 110))
    client = FakeClient(complete_messages("petzone-01", ENTRANCE))

    result = run_smoke(
        client,
        host="192.168.10.8",
        port=18883,
        username="u",
        password="secret",
        verifier=verifier,
        timeout=12,
        wait=immediate_wait,
    )

    assert not result.ok
    assert result.exit_code != 0
    assert "food_weight" in format_report(result)
    assert "sensors=4/9" in format_report(result)


@pytest.mark.parametrize(
    ("topic", "payload", "message"),
    [
        (
            "home/pico/entrance-01/sensor/temperature",
            b'{"device_id":"entrance-01","sensor_type":"temperature","value":22.5,"unit":"F","observed_at":"2026-07-20T06:00:29.000Z"}',
            "unit",
        ),
        (
            "home/pico/entrance-01/status",
            status("entrance-01", "online", -31),
            "stale",
        ),
        (
            "home/pico/entrance-01/sensor/temperature",
            b"not-json",
            "JSON",
        ),
    ],
)
def test_malformed_or_stale_messages_fail(topic: str, payload: bytes, message: str) -> None:
    verifier = SmokeVerifier("entrance-01", now=lambda: NOW)
    client = FakeClient([wire(topic, payload)])

    result = run_smoke(
        client,
        host="192.168.10.8",
        port=18883,
        username="u",
        password="secret",
        verifier=verifier,
        timeout=12,
        wait=immediate_wait,
    )

    assert not result.ok
    assert message in format_report(result)


def test_duplicate_json_key_is_malformed() -> None:
    verifier = SmokeVerifier("entrance-01", now=lambda: NOW)

    verifier.process(
        "home/pico/entrance-01/sensor/temperature",
        b'{"device_id":"entrance-01","sensor_type":"temperature","value":22.5,"unit":"F","unit":"C","observed_at":"2026-07-20T06:00:29.000Z"}',
        qos=1,
        retain=False,
    )

    assert verifier.errors == ["duplicate JSON field on home/pico/entrance-01/sensor/temperature"]


def test_delayed_online_payloads_do_not_count_as_live_heartbeat() -> None:
    verifier = SmokeVerifier(
        "entrance-01",
        now=lambda: NOW,
        monotonic=monotonic_values(100, 100.1),
    )
    client = FakeClient(complete_messages("entrance-01", ENTRANCE))

    result = run_smoke(
        client,
        host="192.168.10.8",
        port=18883,
        username="u",
        password="secret",
        verifier=verifier,
        timeout=12,
        wait=immediate_wait,
    )

    assert not result.ok
    assert "missing 10-second heartbeat evidence" in format_report(result)


def test_non_retained_offline_does_not_count_as_lwt_or_reconnect() -> None:
    device = "entrance-01"
    messages = complete_messages(device, ENTRANCE)
    messages.append(wire(f"home/pico/{device}/status", status(device, "offline", -200)))
    messages.extend(complete_messages(device, ENTRANCE))
    verifier = SmokeVerifier(
        device,
        now=lambda: NOW,
        monotonic=monotonic_values(100, 110, 111, 120, 130),
        require_reconnect=True,
    )
    client = FakeClient(messages)

    result = run_smoke(
        client,
        host="192.168.10.8",
        port=18883,
        username="u",
        password="secret",
        verifier=verifier,
        timeout=90,
        wait=immediate_wait,
    )

    assert not result.ok
    report = format_report(result)
    assert "lwt=NOT_CONFIRMED" in report
    assert "reconnect=NOT_OBSERVED" in report
    assert client.subscriptions.count((f"home/pico/{device}/status", 1)) == 2


def test_confirmed_lwt_discards_pre_outage_evidence() -> None:
    device = "entrance-01"
    offline = status(device, "offline", -200)
    messages = complete_messages(device, ENTRANCE)
    messages.extend(
        [
            wire(f"home/pico/{device}/status", offline),
            wire(f"home/pico/{device}/status", offline, retain=True),
            wire(f"home/pico/{device}/status", status(device, "online", -10)),
            wire(f"home/pico/{device}/status", status(device, "online", 0)),
        ]
    )
    verifier = SmokeVerifier(
        device,
        now=lambda: NOW,
        monotonic=monotonic_values(100, 110, 111, 112, 120, 130),
        require_reconnect=True,
    )

    result = run_smoke(
        FakeClient(messages),
        host="192.168.10.8",
        port=18883,
        username="u",
        password="secret",
        verifier=verifier,
        timeout=90,
        wait=immediate_wait,
    )

    assert not result.ok
    assert "lwt=CONFIRMED" in format_report(result)
    assert "sensors=0/4" in format_report(result)


def test_required_cycle_reports_post_reconnect_profile_and_heartbeat() -> None:
    device = "entrance-01"
    offline = status(device, "offline", -200)
    messages = complete_messages(device, ENTRANCE)
    messages.extend(
        [
            wire(f"home/pico/{device}/status", offline),
            wire(f"home/pico/{device}/status", offline, retain=True),
            *complete_messages(device, ENTRANCE),
        ]
    )
    verifier = SmokeVerifier(
        device,
        now=lambda: NOW,
        monotonic=monotonic_values(100, 110, 111, 112, 120, 130),
        require_reconnect=True,
    )

    result = run_smoke(
        FakeClient(messages),
        host="192.168.10.8",
        port=18883,
        username="u",
        password="secret",
        verifier=verifier,
        timeout=90,
        wait=immediate_wait,
    )

    assert result.ok
    report = format_report(result)
    assert "lwt=CONFIRMED" in report
    assert "reconnect=PASS" in report
    assert "heartbeat=PASS" in report
    assert "sensors=4/4" in report


def test_wait_is_bounded_by_configured_timeout() -> None:
    observed_timeouts: list[float] = []

    def timeout_wait(done, timeout: float) -> bool:
        observed_timeouts.append(timeout)
        return False

    result = run_smoke(
        FakeClient([]),
        host="192.168.10.8",
        port=18883,
        username="u",
        password="secret",
        verifier=SmokeVerifier("entrance-01", now=lambda: NOW),
        timeout=17,
        wait=timeout_wait,
    )

    assert observed_timeouts == [17]
    assert not result.ok
    assert "timeout" in format_report(result)
