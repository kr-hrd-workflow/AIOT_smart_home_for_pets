from __future__ import annotations

import base64
import ipaddress
import json
import socket
import ssl
from datetime import UTC, datetime, timedelta
from pathlib import Path
from threading import Thread

import httpx
import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID

from pydantic import ValidationError

import app.config as config_module
import app.jetson_client as client_module
from app.agent_config import protect_runtime_file
from app.config import JetsonConfig, load_jetson_config
from app.jetson_client import JetsonClientError, JetsonVisionClient, pinned_ssl_context


ROOT = Path(__file__).resolve().parents[2]
FIXTURE = json.loads((ROOT / "contracts" / "petcare-jetson-wire-v1.json").read_text(encoding="utf-8"))
NOW = datetime(2026, 7, 20, 4, 0, 0, 100000, tzinfo=UTC)
PSK = base64.urlsafe_b64decode(FIXTURE["auth"]["secret_base64url"] + "=")
ZONES = {"pet_bed": (320, 180, 640, 480)}


def certificate_authority(name: str) -> tuple[rsa.RSAPrivateKey, x509.Certificate]:
    ca_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    ca_name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, f"{name} CA")])
    current = datetime.now(UTC)
    ca = (
        x509.CertificateBuilder()
        .subject_name(ca_name).issuer_name(ca_name).public_key(ca_key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(current - timedelta(days=1)).not_valid_after(current + timedelta(days=30))
        .add_extension(x509.BasicConstraints(ca=True, path_length=0), critical=True)
        .sign(ca_key, hashes.SHA256())
    )
    return ca_key, ca


def server_certificate(
    tmp_path: Path, name: str, san: str, ca_key: rsa.RSAPrivateKey, ca: x509.Certificate
) -> tuple[Path, Path]:
    server_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    current = datetime.now(UTC)
    server = (
        x509.CertificateBuilder()
        .subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, san)]))
        .issuer_name(ca.subject).public_key(server_key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(current - timedelta(days=1)).not_valid_after(current + timedelta(days=30))
        .add_extension(x509.SubjectAlternativeName([x509.IPAddress(ipaddress.ip_address(san))]), critical=False)
        .sign(ca_key, hashes.SHA256())
    )
    cert_path, key_path = tmp_path / f"{name}.crt", tmp_path / f"{name}.key"
    cert_path.write_bytes(server.public_bytes(serialization.Encoding.PEM))
    key_path.write_bytes(server_key.private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()
    ))
    return cert_path, key_path


def handshake(context: ssl.SSLContext, cert_path: Path, key_path: Path, hostname: str) -> bool:
    server_context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    server_context.load_cert_chain(cert_path, key_path)
    server_socket, client_socket = socket.socketpair()

    def serve() -> None:
        try:
            with server_context.wrap_socket(server_socket, server_side=True) as connection:
                connection.recv(1)
        except (OSError, ssl.SSLError):
            pass

    worker = Thread(target=serve)
    worker.start()
    try:
        with context.wrap_socket(client_socket, server_hostname=hostname) as connection:
            connection.sendall(b"x")
        return True
    except ssl.SSLError:
        return False
    finally:
        client_socket.close()
        worker.join(2)


def config(tmp_path: Path) -> JetsonConfig:
    ca = tmp_path / "jetson.crt"
    psk = tmp_path / "jetson.psk"
    ca.write_text("fixture cert", encoding="ascii")
    psk.write_bytes(PSK)
    protect_runtime_file(ca)
    protect_runtime_file(psk)
    return JetsonConfig(
        url="https://192.168.50.20:9443",
        home_ip="192.168.50.10",
        ca_cert_path=ca,
        psk_path=psk,
    )


def test_direct_config_construction_cannot_bypass_owner_only_trust_files(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ca = tmp_path / "jetson.crt"
    psk = tmp_path / "jetson.psk"
    ca.write_text("fixture cert", encoding="ascii")
    psk.write_bytes(PSK)
    monkeypatch.setattr(config_module, "_owner_only_descriptor", lambda _descriptor, _status: False)

    with pytest.raises(ValidationError):
        JetsonConfig(
            url="https://192.168.50.20:9443", home_ip="192.168.50.10",
            ca_cert_path=ca, psk_path=psk,
        )


def test_runtime_json_loads_string_paths_after_owner_only_checks(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    expected = config(tmp_path)
    runtime = tmp_path / "jetson.json"
    runtime.write_text(json.dumps({
        "url": expected.url,
        "home_ip": expected.home_ip,
        "ca_cert_path": str(expected.ca_cert_path),
        "psk_path": str(expected.psk_path),
    }), encoding="utf-8")
    checked: list[Path] = []
    secure_read = config_module._secure_read
    monkeypatch.setattr(
        config_module,
        "_secure_read",
        lambda path, *, owner_only: checked.append(Path(path)) or secure_read(Path(path), owner_only=False),
    )

    assert load_jetson_config(runtime) == expected
    assert checked == [runtime, expected.ca_cert_path, expected.psk_path]


def clients(handler: object) -> tuple[httpx.Client, httpx.Client, httpx.Client]:
    transport = httpx.MockTransport(handler)  # type: ignore[arg-type]
    return tuple(httpx.Client(base_url="https://192.168.50.20:9443", transport=transport) for _ in range(3))  # type: ignore[return-value]


def test_constructor_builds_three_independent_one_connection_pools_with_pinned_tls(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    created: list[dict[str, object]] = []
    class Context:
        def load_verify_locations(self, *, cadata: str) -> None:
            assert cadata == "fixture cert"

    context = Context()
    monkeypatch.setattr(client_module, "pinned_ssl_context", lambda ca_pem: context if ca_pem == b"fixture cert" else None)

    class Client:
        def __init__(self, **kwargs: object) -> None:
            created.append(kwargs)

        def close(self) -> None:
            pass

    JetsonVisionClient(config(tmp_path), client_factory=Client)  # type: ignore[arg-type]

    assert len(created) == 3
    assert len({id(item["limits"]) for item in created}) == 3
    assert all(item["verify"] is context for item in created)
    assert all(item["base_url"] == "https://192.168.50.20:9443" for item in created)
    assert all(item["limits"].max_connections == item["limits"].max_keepalive_connections == 1 for item in created)  # type: ignore[union-attr]


def test_pinned_tls_accepts_only_the_configured_ca_and_exact_ip_san(tmp_path: Path) -> None:
    ca_key, ca_cert = certificate_authority("correct")
    _other_key, other_cert = certificate_authority("other")
    cert, key = server_certificate(tmp_path, "correct", "192.168.50.20", ca_key, ca_cert)
    wrong_san_cert, wrong_san_key = server_certificate(
        tmp_path, "wrong-san", "192.168.50.21", ca_key, ca_cert
    )
    ca = ca_cert.public_bytes(serialization.Encoding.PEM)
    other_ca = other_cert.public_bytes(serialization.Encoding.PEM)

    assert handshake(pinned_ssl_context(ca), cert, key, "192.168.50.20")
    assert not handshake(pinned_ssl_context(other_ca), cert, key, "192.168.50.20")
    assert not handshake(pinned_ssl_context(ca), wrong_san_cert, wrong_san_key, "192.168.50.20")


def test_next_frame_uses_control_only_and_derives_home_subject_center_zone(tmp_path: Path) -> None:
    requests: list[httpx.Request] = []
    preview = FIXTURE["observation"]["preview"]

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.path == "/v1/status":
            return httpx.Response(200, json=FIXTURE["status"])
        if request.url.path == "/v1/observations":
            return httpx.Response(200, json=FIXTURE["observation"]["body"])
        if request.url.path == "/v1/preview.jpg":
            return httpx.Response(200, headers=preview["headers"], content=base64.b64decode(preview["body_base64"]))
        raise AssertionError(request.url)

    control, admission, media = clients(handler)
    client = JetsonVisionClient(
        config(tmp_path), clients=(control, admission, media), now=lambda: NOW, monotonic=lambda: 10.0,
        nonce=lambda: FIXTURE["auth"]["request"]["nonce"],
    )
    frame = client.next_frame(ZONES)

    assert [request.url.path for request in requests] == ["/v1/status", "/v1/observations", "/v1/preview.jpg"]
    assert len(frame.jpeg) == 2097
    assert frame.observed_at == NOW
    assert frame.detections[0].subject_id == "dog_001"
    assert (frame.detections[0].center_x, frame.detections[0].center_y, frame.detections[0].zone_name) == (210, 210, None)
    assert frame.bed_subject_ids == ()


def test_malformed_preview_time_is_normalized_and_response_is_closed(tmp_path: Path) -> None:
    preview = FIXTURE["observation"]["preview"]
    responses: list[httpx.Response] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/v1/status":
            return httpx.Response(200, json=FIXTURE["status"])
        if request.url.path == "/v1/observations":
            return httpx.Response(200, json=FIXTURE["observation"]["body"])
        response = httpx.Response(
            200,
            headers=preview["headers"] | {"X-PetCare-Jetson-Observed-At": "not-a-time"},
            content=base64.b64decode(preview["body_base64"]),
        )
        responses.append(response)
        return response

    client = JetsonVisionClient(config(tmp_path), clients=clients(handler), now=lambda: NOW, monotonic=lambda: 10.0)
    with pytest.raises(JetsonClientError, match="invalid_preview_headers"):
        client.next_frame(ZONES)
    assert responses[0].is_closed


def test_preview_content_length_is_rejected_before_stream_body_read(tmp_path: Path) -> None:
    reads = 0

    class Body(httpx.SyncByteStream):
        def __iter__(self):
            nonlocal reads
            reads += 1
            yield b"not read"

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/v1/status":
            return httpx.Response(200, json=FIXTURE["status"])
        if request.url.path == "/v1/observations":
            return httpx.Response(200, json=FIXTURE["observation"]["body"])
        return httpx.Response(
            200,
            headers=FIXTURE["observation"]["preview"]["headers"] | {"Content-Length": "1048577"},
            stream=Body(),
        )

    client = JetsonVisionClient(config(tmp_path), clients=clients(handler), now=lambda: NOW, monotonic=lambda: 10.0)
    with pytest.raises(JetsonClientError, match="preview"):
        client.next_frame(ZONES)
    assert reads == 0


def test_observation_is_rechecked_after_preview_before_persistence(tmp_path: Path) -> None:
    preview = FIXTURE["observation"]["preview"]

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/v1/status":
            return httpx.Response(200, json=FIXTURE["status"])
        if request.url.path == "/v1/observations":
            return httpx.Response(200, json=FIXTURE["observation"]["body"])
        return httpx.Response(200, headers=preview["headers"], content=base64.b64decode(preview["body_base64"]))

    times = iter((NOW.replace(microsecond=0) + __import__("datetime").timedelta(seconds=3),
                  NOW.replace(microsecond=0) + __import__("datetime").timedelta(seconds=3, microseconds=100001)))
    client = JetsonVisionClient(
        config(tmp_path), clients=clients(handler), now=lambda: next(times),
        now_seconds=lambda: NOW.timestamp(), monotonic=lambda: 10.0,
    )
    with pytest.raises(JetsonClientError, match="observation"):
        client.next_frame(ZONES)


def test_every_first_put_calibrates_on_isolated_admission_pool(tmp_path: Path) -> None:
    requests: list[tuple[str, str]] = []
    wall_values = iter((1784520000.001, 1784520000.099, 1784520000.1))
    mono_values = iter((10.0, 10.098, 10.1))

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append((request.method, request.url.path))
        if request.url.path == "/v1/status":
            return httpx.Response(200, json=FIXTURE["status"])
        if request.method == "PUT":
            return httpx.Response(201, json=FIXTURE["command"]["response"])
        raise AssertionError(request.url)

    client = JetsonVisionClient(
        config(tmp_path), clients=clients(handler), now_seconds=lambda: next(wall_values),
        monotonic=lambda: next(mono_values), nonce=lambda: FIXTURE["auth"]["request"]["nonce"],
    )
    result = client.put_clip(FIXTURE["command"]["response"]["command_id"], FIXTURE["command"]["request"])

    assert result.status_code == 201
    assert result.receipt.command_id == FIXTURE["command"]["response"]["command_id"]
    assert requests == [("GET", "/v1/status"), ("PUT", "/v1/clips/fedcba9876543210fedcba9876543210")]


def test_close_closes_all_three_clients(tmp_path: Path) -> None:
    closed: list[int] = []

    class Client:
        def __init__(self, number: int) -> None:
            self.number = number

        def close(self) -> None:
            closed.append(self.number)

    client = JetsonVisionClient(config(tmp_path), clients=tuple(Client(i) for i in range(3)))  # type: ignore[arg-type]
    client.close()
    assert closed == [0, 1, 2]


def test_close_continues_after_one_pool_close_fails(tmp_path: Path) -> None:
    closed: list[int] = []

    class Client:
        def __init__(self, number: int) -> None:
            self.number = number

        def close(self) -> None:
            closed.append(self.number)
            if self.number == 0:
                raise RuntimeError("close failed")

    client = JetsonVisionClient(config(tmp_path), clients=tuple(Client(i) for i in range(3)))  # type: ignore[arg-type]
    with pytest.raises(RuntimeError, match="close failed"):
        client.close()
    assert closed == [0, 1, 2]


def test_partial_client_construction_closes_already_created_pool(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    closed: list[int] = []
    calls = 0
    class Context:
        def load_verify_locations(self, *, cadata: str) -> None:
            assert cadata == "fixture cert"

    context = Context()
    monkeypatch.setattr(client_module, "pinned_ssl_context", lambda ca_pem: context if ca_pem == b"fixture cert" else None)

    class Client:
        def __init__(self, number: int) -> None:
            self.number = number

        def close(self) -> None:
            closed.append(self.number)

    def factory(**_kwargs: object) -> Client:
        nonlocal calls
        calls += 1
        if calls == 2:
            raise RuntimeError("construction failed")
        return Client(calls)

    with pytest.raises(RuntimeError, match="construction failed"):
        JetsonVisionClient(config(tmp_path), client_factory=factory)  # type: ignore[arg-type]
    assert closed == [1]


@pytest.mark.parametrize(
    "url",
    [
        "http://192.168.50.20:9443",
        "https://8.8.8.8:9443",
        "https://127.0.0.1:9443",
        "https://192.0.0.1:9443",
        "https://192.168.50.20:443",
        "https://jetson.local:9443",
    ],
)
def test_config_rejects_unpinned_or_unsafe_origins(tmp_path: Path, url: str) -> None:
    values = config(tmp_path).model_dump()
    with pytest.raises(ValidationError):
        JetsonConfig.model_validate(values | {"url": url})


def test_config_rejects_mixed_lan_and_tailscale_pair(tmp_path: Path) -> None:
    values = config(tmp_path).model_dump()
    with pytest.raises(ValidationError):
        JetsonConfig.model_validate(
            values
            | {
                "url": "https://100.64.0.10:9443",
                "home_ip": "192.168.50.10",
            }
        )


def test_new_boot_reauthenticates_with_bootstrap_and_resets_sequence(tmp_path: Path) -> None:
    requests: list[tuple[str, str]] = []
    preview = FIXTURE["observation"]["preview"]
    new_boot = "f" * 32
    status_count = 0
    observation_count = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal status_count, observation_count
        requests.append((request.url.path, request.headers["x-petcare-jetson-boot-id"]))
        if request.url.path == "/v1/status":
            status_count += 1
            body = FIXTURE["status"] if status_count == 1 else FIXTURE["status"] | {"boot_id": new_boot}
            return httpx.Response(200, json=body)
        if request.url.path == "/v1/observations":
            observation_count += 1
            if observation_count == 2:
                return httpx.Response(401, json=FIXTURE["errors"]["unauthorized"]["body"])
            body = FIXTURE["observation"]["body"]
            if observation_count == 3:
                body = body | {"boot_id": new_boot, "sequence": 1}
            return httpx.Response(200, json=body)
        if request.url.path == "/v1/preview.jpg":
            sequence = "42" if observation_count == 1 else "1"
            headers = preview["headers"] | {
                "X-PetCare-Jetson-Boot-Id": FIXTURE["status"]["boot_id"] if sequence == "42" else new_boot,
                "X-PetCare-Jetson-Sequence": sequence,
            }
            return httpx.Response(200, headers=headers, content=base64.b64decode(preview["body_base64"]))
        raise AssertionError(request.url)

    ticks = iter((10.0, 10.0, 10.6, 10.6))
    client = JetsonVisionClient(config(tmp_path), clients=clients(handler), now=lambda: NOW, monotonic=lambda: next(ticks))
    assert client.next_frame(ZONES).observed_at == NOW
    assert client.next_frame(ZONES).detections[0].detected_type == "dog"
    assert client.boot_id == new_boot
    assert ("/v1/status", "bootstrap") in requests[3:]


def test_put_reauthenticates_after_jetson_reboot_and_reports_http_status(tmp_path: Path) -> None:
    old_boot = FIXTURE["status"]["boot_id"]
    new_boot = "f" * 32
    requests: list[tuple[str, str]] = []
    status_count = 0
    put_count = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal status_count, put_count
        requests.append((request.url.path, request.headers["x-petcare-jetson-boot-id"]))
        if request.url.path == "/v1/status":
            status_count += 1
            return httpx.Response(200, json=FIXTURE["status"] | {"boot_id": old_boot if status_count == 1 else new_boot})
        put_count += 1
        if put_count == 1:
            return httpx.Response(401, json=FIXTURE["errors"]["unauthorized"]["body"])
        return httpx.Response(201, json=FIXTURE["command"]["response"] | {"accepted_boot_id": new_boot})

    wall_values = iter((1784520000.001, 1784520000.099, 1784520000.1,
                        1784520000.101, 1784520000.199, 1784520000.2))
    mono_values = iter((10.0, 10.098, 10.1, 10.101, 10.199, 10.2))
    client = JetsonVisionClient(
        config(tmp_path), clients=clients(handler), now_seconds=lambda: next(wall_values),
        monotonic=lambda: next(mono_values), nonce=lambda: FIXTURE["auth"]["request"]["nonce"],
    )

    result = client.put_clip(FIXTURE["command"]["response"]["command_id"], FIXTURE["command"]["request"])

    assert result.status_code == 201
    assert result.receipt.accepted_boot_id == new_boot
    assert requests == [
        ("/v1/status", "bootstrap"),
        ("/v1/clips/fedcba9876543210fedcba9876543210", old_boot),
        ("/v1/status", "bootstrap"),
        ("/v1/clips/fedcba9876543210fedcba9876543210", new_boot),
    ]


def test_put_rejects_mismatched_401_without_retrying(tmp_path: Path) -> None:
    put_count = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal put_count
        if request.url.path == "/v1/status":
            return httpx.Response(200, json=FIXTURE["status"])
        put_count += 1
        return httpx.Response(401, json={"code": "clip_gone", "message": "gone"})

    wall_values = iter((1784520000.001, 1784520000.099, 1784520000.1))
    mono_values = iter((10.0, 10.098, 10.1))
    client = JetsonVisionClient(
        config(tmp_path), clients=clients(handler), now_seconds=lambda: next(wall_values),
        monotonic=lambda: next(mono_values),
    )
    with pytest.raises(JetsonClientError, match="invalid_response"):
        client.put_clip(FIXTURE["command"]["response"]["command_id"], FIXTURE["command"]["request"])
    assert put_count == 1


def test_observation_rejects_mismatched_401_without_bootstrap_retry(tmp_path: Path) -> None:
    observation_count = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal observation_count
        if request.url.path == "/v1/status":
            return httpx.Response(200, json=FIXTURE["status"])
        observation_count += 1
        return httpx.Response(401, json={"code": "clip_gone", "message": "gone"})

    client = JetsonVisionClient(config(tmp_path), clients=clients(handler), now=lambda: NOW)
    with pytest.raises(JetsonClientError, match="invalid_response"):
        client.next_frame(ZONES)
    assert observation_count == 1


def test_clock_discontinuity_blocks_first_put_before_admission_request(tmp_path: Path) -> None:
    requests: list[str] = []
    wall_values = iter((1784520000.0, 1784520000.2))
    mono_values = iter((10.0, 10.1))

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request.url.path)
        return httpx.Response(200, json=FIXTURE["status"])

    client = JetsonVisionClient(
        config(tmp_path), clients=clients(handler), now_seconds=lambda: next(wall_values),
        monotonic=lambda: next(mono_values),
    )
    with pytest.raises(JetsonClientError, match="clock_uncertain"):
        client.put_clip(FIXTURE["command"]["response"]["command_id"], FIXTURE["command"]["request"])
    assert requests == ["/v1/status"]


def test_clock_step_after_good_calibration_still_blocks_put(tmp_path: Path) -> None:
    requests: list[str] = []
    wall_values = iter((1784520000.001, 1784520000.099, 1784520000.3))
    mono_values = iter((10.0, 10.098, 10.1))

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request.url.path)
        if request.url.path == "/v1/status":
            return httpx.Response(200, json=FIXTURE["status"])
        return httpx.Response(201, json=FIXTURE["command"]["response"])

    client = JetsonVisionClient(
        config(tmp_path), clients=clients(handler), now_seconds=lambda: next(wall_values),
        monotonic=lambda: next(mono_values),
    )
    with pytest.raises(JetsonClientError, match="clock_uncertain"):
        client.put_clip(FIXTURE["command"]["response"]["command_id"], FIXTURE["command"]["request"])
    assert requests == ["/v1/status"]


def test_download_streams_exact_bounded_mp4_to_private_destination(tmp_path: Path) -> None:
    clip = FIXTURE["clip"]

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/v1/status":
            return httpx.Response(200, json=FIXTURE["status"])
        return httpx.Response(200, headers=clip["headers"], content=base64.b64decode(clip["body_base64"]))

    client = JetsonVisionClient(config(tmp_path), clients=clients(handler), now=lambda: NOW)
    client.status()
    destination = tmp_path / "clip.partial.mp4"
    metadata = client.download_clip(FIXTURE["command"]["response"]["command_id"], destination)
    assert destination.read_bytes() == b"mp4-bytes"
    assert (metadata.frame_count, metadata.events) == (300, "eating:41")


def test_download_rejects_oversize_before_reading_and_leaves_no_partial(tmp_path: Path) -> None:
    reads = 0

    class Body(httpx.SyncByteStream):
        def __iter__(self):
            nonlocal reads
            reads += 1
            yield b"not read"

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/v1/status":
            return httpx.Response(200, json=FIXTURE["status"])
        return httpx.Response(
            200, headers=FIXTURE["clip"]["headers"] | {"Content-Length": "268435457"}, stream=Body()
        )

    client = JetsonVisionClient(config(tmp_path), clients=clients(handler), now=lambda: NOW)
    client.status()
    destination = tmp_path / "clip.partial.mp4"
    with pytest.raises(JetsonClientError, match="clip"):
        client.download_clip(FIXTURE["command"]["response"]["command_id"], destination)
    assert reads == 0 and not destination.exists()


def test_download_never_deletes_a_preexisting_destination(tmp_path: Path) -> None:
    clip = FIXTURE["clip"]

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/v1/status":
            return httpx.Response(200, json=FIXTURE["status"])
        return httpx.Response(200, headers=clip["headers"], content=base64.b64decode(clip["body_base64"]))

    client = JetsonVisionClient(config(tmp_path), clients=clients(handler), now=lambda: NOW)
    client.status()
    destination = tmp_path / "clip.partial.mp4"
    destination.write_bytes(b"keep-me")
    with pytest.raises(FileExistsError):
        client.download_clip(FIXTURE["command"]["response"]["command_id"], destination)
    assert destination.read_bytes() == b"keep-me"


def test_error_code_must_match_its_frozen_http_status(tmp_path: Path) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/v1/status":
            return httpx.Response(200, json=FIXTURE["status"])
        return httpx.Response(500, json={"code": "clip_gone", "message": "gone"})

    client = JetsonVisionClient(config(tmp_path), clients=clients(handler), now=lambda: NOW)
    client.status()
    with pytest.raises(JetsonClientError, match="invalid_response"):
        client.download_clip(FIXTURE["command"]["response"]["command_id"], tmp_path / "clip.partial.mp4")


def test_observation_error_code_must_match_its_frozen_http_status(tmp_path: Path) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/v1/status":
            return httpx.Response(200, json=FIXTURE["status"])
        return httpx.Response(500, json={"code": "clip_gone", "message": "gone"})

    client = JetsonVisionClient(config(tmp_path), clients=clients(handler), now=lambda: NOW)
    with pytest.raises(JetsonClientError, match="invalid_response"):
        client.next_frame(ZONES)
