from __future__ import annotations

import struct

import pytest

from app import pico_provision
from app.pico_provision import PicoProvisioningError, diagnose_pico, provision_pico


class FakePort:
    def __init__(self, product: str, *, fail_config: bool = False) -> None:
        self.product = product
        self.fail_config = fail_config
        self.frames: list[bytes] = []
        self.closed = False

    def exchange(self, frame: bytes | bytearray) -> bytes:
        copied = bytes(frame)
        self.frames.append(copied)
        if copied[5] == 1:
            response, _checksum = pico_provision._frame(
                1,
                b"\x01" + self.product.encode(),
            )
            return bytes(response)
        if copied[5] == 5:
            response, _checksum = pico_provision._frame(
                5,
                b"\x01\x04\x00\x03\x00",
            )
            return bytes(response)
        if self.fail_config:
            raise PicoProvisioningError("timeout")
        checksum = struct.unpack_from("<I", copied, len(copied) - 4)[0]
        response, _response_checksum = pico_provision._frame(
            3,
            b"\x01" + self.product.encode() + struct.pack("<I", checksum),
        )
        return bytes(response)

    def close(self) -> None:
        self.closed = True


def test_provision_pico_identifies_product_before_sending_secrets() -> None:
    ports = {
        "COM4": FakePort("petzone-01"),
        "COM5": FakePort("entrance-01"),
    }

    provision_pico(
        product="entrance-01",
        wifi_ssid="test-network",
        wifi_password="test-password",
        mqtt_host="192.168.0.20",
        mqtt_port=18883,
        mqtt_username="petcare",
        mqtt_password="mqtt-password",
        ports=ports,
        open_port=ports.__getitem__,
    )

    assert len(ports["COM4"].frames) == 1
    assert ports["COM4"].frames[0][5] == 1
    assert len(ports["COM5"].frames) == 2
    assert [frame[5] for frame in ports["COM5"].frames] == [1, 2]
    assert all(port.closed for port in ports.values())


def test_provision_pico_reports_uncertain_after_config_ack_timeout() -> None:
    port = FakePort("entrance-01", fail_config=True)

    with pytest.raises(PicoProvisioningError, match="uncertain") as caught:
        provision_pico(
            product="entrance-01",
            wifi_ssid="test-network",
            wifi_password="test-password",
            mqtt_host="192.168.0.20",
            mqtt_port=18883,
            mqtt_username="petcare",
            mqtt_password="mqtt-password",
            ports=["COM4"],
            open_port=lambda _port: port,
        )

    assert caught.value.code == "uncertain"
    assert port.closed is True


def test_diagnose_pico_returns_validated_runtime_state() -> None:
    ports = {
        "COM4": FakePort("petzone-01"),
        "COM5": FakePort("entrance-01"),
    }

    diagnostics = diagnose_pico(
        product="entrance-01",
        ports=ports,
        open_port=ports.__getitem__,
    )

    assert diagnostics == {
        "status": "online",
        "error": "none",
        "wifi_link_status": 3,
        "watchdog_reboot": False,
    }
    assert [frame[5] for frame in ports["COM4"].frames] == [1]
    assert [frame[5] for frame in ports["COM5"].frames] == [1, 5]
    assert all(port.closed for port in ports.values())


def test_diagnostics_rejects_unknown_runtime_values() -> None:
    frame, _checksum = pico_provision._frame(
        5,
        b"\x01\xff\x00\x03\x00",
    )

    with pytest.raises(PicoProvisioningError, match="protocol"):
        pico_provision._verify_diagnostics(frame)


def test_config_rejects_mqtt_hostname_before_usb_write() -> None:
    with pytest.raises(PicoProvisioningError, match="validation"):
        pico_provision._config_frame(
            product="entrance-01",
            wifi_ssid="test-network",
            wifi_password="test-password",
            mqtt_host="mqtt.local",
            mqtt_port=18883,
            mqtt_username="petcare",
            mqtt_password="mqtt-password",
        )
