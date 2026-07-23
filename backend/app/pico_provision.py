from __future__ import annotations

import os
import re
import struct
import time
import zlib
from collections.abc import Callable, Iterable
from typing import Any


MAX_FRAME_BYTES = 768
PRODUCTS = frozenset({"entrance-01", "petzone-01"})
_COM_PORT = re.compile(r"COM([1-9][0-9]{0,2})\Z", re.IGNORECASE)
_PICO_PNP_PREFIXES = (
    "USB\\VID_2E8A&PID_0009\\",
    "USB\\VID_2E8A&PID_0009&MI_00\\",
)


class PicoProvisioningError(RuntimeError):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def _field(value: str, minimum: int, maximum: int) -> bytes:
    if not isinstance(value, str) or "\0" in value:
        raise PicoProvisioningError("validation")
    encoded = value.encode("utf-8")
    if not minimum <= len(encoded) <= maximum:
        raise PicoProvisioningError("validation")
    return struct.pack("<H", len(encoded)) + encoded


def _frame(kind: int, payload: bytes | bytearray = b"") -> tuple[bytearray, int]:
    size = 12 + len(payload)
    if size > MAX_FRAME_BYTES:
        raise PicoProvisioningError("validation")
    frame = bytearray(size)
    frame[:4] = b"PET1"
    frame[4] = 1
    frame[5] = kind
    struct.pack_into("<H", frame, 6, len(payload))
    frame[8 : 8 + len(payload)] = payload
    checksum = zlib.crc32(frame[:-4]) & 0xFFFFFFFF
    struct.pack_into("<I", frame, size - 4, checksum)
    return frame, checksum


def _parse_frame(frame: bytes | bytearray) -> tuple[int, bytes]:
    if not 12 <= len(frame) <= MAX_FRAME_BYTES:
        raise PicoProvisioningError("protocol")
    if frame[:4] != b"PET1" or frame[4] != 1:
        raise PicoProvisioningError("protocol")
    payload_size = struct.unpack_from("<H", frame, 6)[0]
    if len(frame) != 12 + payload_size:
        raise PicoProvisioningError("protocol")
    expected = struct.unpack_from("<I", frame, len(frame) - 4)[0]
    actual = zlib.crc32(frame[:-4]) & 0xFFFFFFFF
    if expected != actual:
        raise PicoProvisioningError("crc")
    if frame[5] == 4:
        raise PicoProvisioningError("device")
    return frame[5], bytes(frame[8:-4])


def _verify_hello(frame: bytes | bytearray) -> str:
    kind, payload = _parse_frame(frame)
    if kind != 1 or len(payload) < 2 or payload[0] != 1:
        raise PicoProvisioningError("protocol")
    try:
        product = payload[1:].decode("utf-8", errors="strict")
    except UnicodeDecodeError as error:
        raise PicoProvisioningError("protocol") from error
    if product not in PRODUCTS:
        raise PicoProvisioningError("protocol")
    return product


def _verify_ack(
    frame: bytes | bytearray,
    *,
    product: str,
    expected_checksum: int,
) -> None:
    kind, payload = _parse_frame(frame)
    product_bytes = product.encode("utf-8")
    if (
        kind != 3
        or len(payload) != 1 + len(product_bytes) + 4
        or payload[0] != 1
        or payload[1 : 1 + len(product_bytes)] != product_bytes
        or struct.unpack_from("<I", payload, 1 + len(product_bytes))[0]
        != expected_checksum
    ):
        raise PicoProvisioningError("ack")


def _config_frame(
    *,
    product: str,
    wifi_ssid: str,
    wifi_password: str,
    mqtt_host: str,
    mqtt_port: int,
    mqtt_username: str,
    mqtt_password: str,
) -> tuple[bytearray, int]:
    if product not in PRODUCTS or not 1 <= mqtt_port <= 65535:
        raise PicoProvisioningError("validation")
    payload = bytearray(
        b"".join(
            (
                _field(product, 1, 11),
                _field(wifi_ssid, 1, 32),
                _field(wifi_password, 8, 63),
                _field(mqtt_host, 1, 253),
                _field(mqtt_username, 1, 64),
                _field(mqtt_password, 1, 128),
                struct.pack("<H", mqtt_port),
            )
        )
    )
    try:
        return _frame(2, payload)
    finally:
        payload[:] = b"\0" * len(payload)


def _pico_ports() -> list[str]:
    if os.name != "nt":
        return []
    import pythoncom
    import win32com.client

    ports: set[str] = set()
    service = None
    devices = None
    device = None
    pythoncom.CoInitialize()
    try:
        service = win32com.client.GetObject(
            r"winmgmts:{impersonationLevel=impersonate}!\\.\root\cimv2"
        )
        devices = service.ExecQuery(
            "SELECT DeviceID,PNPDeviceID,ConfigManagerErrorCode "
            "FROM Win32_SerialPort"
        )
        for device in devices:
            port = getattr(device, "DeviceID", None)
            pnp_device_id = getattr(device, "PNPDeviceID", None)
            error_code = getattr(device, "ConfigManagerErrorCode", None)
            if (
                error_code == 0
                and isinstance(port, str)
                and _COM_PORT.fullmatch(port)
                and isinstance(pnp_device_id, str)
                and pnp_device_id.upper().startswith(_PICO_PNP_PREFIXES)
            ):
                ports.add(port.upper())
    except pythoncom.com_error:
        return []
    finally:
        device = None
        devices = None
        service = None
        pythoncom.CoUninitialize()
    return sorted(ports, key=lambda value: int(value[3:]))


class _SerialPort:
    def __init__(self, port: str) -> None:
        import win32con
        import win32file

        self._win32file = win32file
        self._handle = win32file.CreateFile(
            rf"\\.\{port}",
            win32con.GENERIC_READ | win32con.GENERIC_WRITE,
            0,
            None,
            win32con.OPEN_EXISTING,
            0,
            None,
        )
        try:
            win32file.SetupComm(self._handle, 1024, 1024)
            dcb = win32file.DCB()
            win32file.BuildCommDCB(
                "baud=115200 parity=N data=8 stop=1 "
                "xon=off odsr=off octs=off dtr=on rts=on",
                dcb,
            )
            win32file.SetCommState(self._handle, dcb)
            win32file.SetCommTimeouts(self._handle, (50, 0, 250, 0, 1000))
            win32file.PurgeComm(
                self._handle,
                win32file.PURGE_RXABORT
                | win32file.PURGE_RXCLEAR
                | win32file.PURGE_TXABORT
                | win32file.PURGE_TXCLEAR,
            )
        except BaseException:
            self.close()
            raise

    def close(self) -> None:
        handle = getattr(self, "_handle", None)
        if handle is not None:
            self._handle = None
            handle.Close()

    def exchange(self, frame: bytes | bytearray) -> bytes:
        offset = 0
        while offset < len(frame):
            error, written = self._win32file.WriteFile(
                self._handle,
                frame[offset:],
            )
            if error != 0 or not isinstance(written, int) or written <= 0:
                raise PicoProvisioningError("disconnect")
            offset += written

        deadline = time.monotonic() + 5.0
        buffered = bytearray()
        while time.monotonic() < deadline:
            error, chunk = self._win32file.ReadFile(
                self._handle,
                MAX_FRAME_BYTES - len(buffered),
            )
            if error != 0:
                raise PicoProvisioningError("disconnect")
            if chunk:
                buffered.extend(chunk)
            if len(buffered) >= 8:
                payload_size = struct.unpack_from("<H", buffered, 6)[0]
                total = 12 + payload_size
                if total > MAX_FRAME_BYTES:
                    raise PicoProvisioningError("protocol")
                if len(buffered) >= total:
                    if len(buffered) != total:
                        raise PicoProvisioningError("protocol")
                    return bytes(buffered)
        raise PicoProvisioningError("timeout")


def provision_pico(
    *,
    product: str,
    wifi_ssid: str,
    wifi_password: str,
    mqtt_host: str,
    mqtt_port: int,
    mqtt_username: str,
    mqtt_password: str,
    ports: Iterable[str] | None = None,
    open_port: Callable[[str], Any] = _SerialPort,
) -> None:
    hello, _checksum = _frame(1)
    config, config_checksum = _config_frame(
        product=product,
        wifi_ssid=wifi_ssid,
        wifi_password=wifi_password,
        mqtt_host=mqtt_host,
        mqtt_port=mqtt_port,
        mqtt_username=mqtt_username,
        mqtt_password=mqtt_password,
    )
    candidates = list(_pico_ports() if ports is None else ports)
    if not candidates:
        config[:] = b"\0" * len(config)
        raise PicoProvisioningError("unavailable")

    saw_other_product = False
    last_error: PicoProvisioningError | None = None
    try:
        for port_name in candidates:
            serial_port = None
            matched_product = False
            try:
                serial_port = open_port(port_name)
                actual_product = _verify_hello(serial_port.exchange(hello))
                if actual_product != product:
                    saw_other_product = True
                    continue
                matched_product = True
                try:
                    response = serial_port.exchange(config)
                    _verify_ack(
                        response,
                        product=product,
                        expected_checksum=config_checksum,
                    )
                except PicoProvisioningError as error:
                    if error.code != "device":
                        raise PicoProvisioningError("uncertain") from error
                    raise
                return
            except PicoProvisioningError as error:
                if matched_product:
                    raise
                last_error = error
            except OSError:
                continue
            finally:
                if serial_port is not None:
                    serial_port.close()
    finally:
        hello[:] = b"\0" * len(hello)
        config[:] = b"\0" * len(config)

    if saw_other_product:
        raise PicoProvisioningError("wrong_product")
    if last_error is not None:
        raise last_error
    raise PicoProvisioningError("unavailable")
