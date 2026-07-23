from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import SecretStr

import app.setup as setup_impl
from app.api import install_api
from app.mqtt_ingest import MqttEndpoint
from app.setup import install_setup


ORIGIN = "http://127.0.0.1:8000"
MQTT_PASSWORD = "mqtt-secret-sentinel"


def make_client(
    *,
    agent_config_path: Path | None = None,
    jetson_config_path: Path | None = None,
    pico_provisioner=None,
) -> TestClient:
    application = FastAPI()
    application.state.config = SimpleNamespace(
        mqtt_enabled=True,
        mqtt_username=SecretStr("petcare"),
        mqtt_password=SecretStr(MQTT_PASSWORD),
    )
    application.state.mqtt_endpoint = MqttEndpoint("192.168.0.20", 18883)
    application.state.agent_config_path = agent_config_path
    application.state.jetson_config_path = jetson_config_path
    if pico_provisioner is not None:
        application.state.pico_provisioner = pico_provisioner
    install_api(application)
    install_setup(application)
    return TestClient(application, base_url=ORIGIN)


def assert_security_headers(response) -> None:
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["referrer-policy"] == "no-referrer"
    assert "default-src 'none'" in response.headers["content-security-policy"]
    assert "access-control-allow-origin" not in response.headers


def test_setup_is_loopback_session_only() -> None:
    with make_client() as client:
        response = client.get("/setup")

    assert response.status_code == 200
    assert "현관 Pico 연결" in response.text
    assert "생활공간 Pico 연결" in response.text
    assert "/setup/api/pico/" in response.text
    assert "/setup/api/bootstrap" not in response.text
    assert "requestPort" not in response.text
    assert "Home Agent가 USB로 연결된 Pico를 자동으로 찾습니다." in response.text
    pagehide_handler = response.text.split(
        'window.addEventListener("pagehide"',
        maxsplit=1,
    )[1]
    assert "closeSession()" not in pagehide_handler.split("});", maxsplit=1)[0]
    cookie = response.headers["set-cookie"].lower()
    assert "httponly" in cookie
    assert "samesite=strict" in cookie
    assert "max-age=600" in cookie
    assert "path=/setup" in cookie
    assert "script-src 'nonce-" in response.headers["content-security-policy"]
    assert "unsafe-inline" not in response.headers["content-security-policy"]
    assert "__NONCE__" not in response.text
    assert_security_headers(response)

    with make_client() as client:
        forbidden = client.get(
            "/setup",
            headers={"host": "example.invalid:8000"},
        )
    assert forbidden.status_code == 403
    assert_security_headers(forbidden)


def test_pico_provisioning_is_web_driven_and_returns_no_secret(caplog) -> None:
    calls: list[dict[str, object]] = []

    def provision(**kwargs) -> None:
        calls.append(kwargs)

    with make_client(pico_provisioner=provision) as client:
        assert client.get("/setup").status_code == 200
        response = client.post(
            "/setup/api/pico/entrance-01",
            json={"wifi_ssid": "test-network", "wifi_password": "test-password"},
            headers={"origin": ORIGIN},
        )

    assert response.status_code == 200
    assert response.json() == {"status": "provisioned", "product": "entrance-01"}
    assert calls == [
        {
            "product": "entrance-01",
            "wifi_ssid": "test-network",
            "wifi_password": "test-password",
            "mqtt_host": "192.168.0.20",
            "mqtt_port": 18883,
            "mqtt_username": "petcare",
            "mqtt_password": MQTT_PASSWORD,
        }
    ]
    assert "test-password" not in response.text
    assert MQTT_PASSWORD not in response.text
    assert "test-password" not in caplog.text
    assert MQTT_PASSWORD not in caplog.text
    assert_security_headers(response)


def test_pico_provisioning_rejects_invalid_or_unauthorized_requests() -> None:
    calls: list[object] = []

    def provision(**kwargs) -> None:
        calls.append(kwargs)

    with make_client(pico_provisioner=provision) as client:
        missing_session = client.post(
            "/setup/api/pico/entrance-01",
            json={"wifi_ssid": "network", "wifi_password": "password"},
            headers={"origin": ORIGIN},
        )
        assert client.get("/setup").status_code == 200
        wrong_origin = client.post(
            "/setup/api/pico/entrance-01",
            json={"wifi_ssid": "network", "wifi_password": "password"},
            headers={"origin": "https://example.invalid"},
        )
        wrong_product = client.post(
            "/setup/api/pico/unknown",
            json={"wifi_ssid": "network", "wifi_password": "password"},
            headers={"origin": ORIGIN},
        )
        extra_field = client.post(
            "/setup/api/pico/entrance-01",
            json={
                "wifi_ssid": "network",
                "wifi_password": "password",
                "mqtt_password": "must-not-come-from-browser",
            },
            headers={"origin": ORIGIN},
        )
        invalid_utf8_size = client.post(
            "/setup/api/pico/entrance-01",
            json={"wifi_ssid": "가" * 11, "wifi_password": "password"},
            headers={"origin": ORIGIN},
        )

    assert missing_session.status_code == 401
    assert wrong_origin.status_code == 403
    assert wrong_product.status_code == 404
    assert extra_field.status_code == 400
    assert invalid_utf8_size.status_code == 400
    assert calls == []
    for response in (
        missing_session,
        wrong_origin,
        wrong_product,
        extra_field,
        invalid_utf8_size,
    ):
        assert_security_headers(response)


def test_pico_endpoint_rejects_missing_expired_and_cross_origin_session(
    monkeypatch,
) -> None:
    now = [100.0]
    monkeypatch.setattr(setup_impl.time, "monotonic", lambda: now[0])
    with make_client() as client:
        missing = client.post(
            "/setup/api/pico/entrance-01",
            json={"wifi_ssid": "network", "wifi_password": "password"},
            headers={"origin": ORIGIN},
        )
        assert missing.status_code == 401

        page = client.get("/setup")
        assert page.status_code == 200
        no_origin = client.post(
            "/setup/api/pico/entrance-01",
            json={"wifi_ssid": "network", "wifi_password": "password"},
        )
        assert no_origin.status_code == 403
        cross_origin = client.post(
            "/setup/api/pico/entrance-01",
            json={"wifi_ssid": "network", "wifi_password": "password"},
            headers={"origin": "https://example.invalid"},
        )
        assert cross_origin.status_code == 403

        now[0] += 601
        expired = client.post(
            "/setup/api/pico/entrance-01",
            json={"wifi_ssid": "network", "wifi_password": "password"},
            headers={"origin": ORIGIN},
        )
        assert expired.status_code == 401

    for response in (missing, no_origin, cross_origin, expired):
        assert_security_headers(response)


def test_retired_bootstrap_endpoint_is_absent() -> None:
    with make_client() as client:
        assert client.get("/setup").status_code == 200
        response = client.post(
            "/setup/api/bootstrap",
            headers={"origin": ORIGIN},
        )

    assert response.status_code == 404
    assert MQTT_PASSWORD not in response.text
    assert_security_headers(response)


def test_delete_session_invalidates_cookie() -> None:
    with make_client() as client:
        assert client.get("/setup").status_code == 200
        deleted = client.delete(
            "/setup/api/session",
            headers={"origin": ORIGIN},
        )
        rejected = client.post(
            "/setup/api/pico/entrance-01",
            json={"wifi_ssid": "network", "wifi_password": "password"},
            headers={"origin": ORIGIN},
        )

    assert deleted.status_code == 204
    assert "max-age=0" in deleted.headers["set-cookie"].lower()
    assert rejected.status_code == 401
    assert_security_headers(deleted)
    assert_security_headers(rejected)


def test_setup_secures_unknown_paths_and_rejects_host_origin_confusion() -> None:
    with make_client() as client:
        unknown = client.get("/setup/unknown")
        wrong_method = client.put("/setup")
        wrong_host = client.post(
            "/setup/api/pico/entrance-01",
            json={"wifi_ssid": "network", "wifi_password": "password"},
            headers={"host": "example.invalid:8000", "origin": ORIGIN},
        )
        assert client.get("/setup", headers={"host": "localhost:8000"}).status_code == 200
        mismatched_origin = client.post(
            "/setup/api/pico/entrance-01",
            json={"wifi_ssid": "network", "wifi_password": "password"},
            headers={"host": "localhost:8000", "origin": ORIGIN},
        )

    assert unknown.status_code == 404
    assert wrong_method.status_code == 405
    assert wrong_host.status_code == 403
    assert mismatched_origin.status_code == 403
    for response in (unknown, wrong_method, wrong_host, mismatched_origin):
        assert_security_headers(response)


def test_jetson_pairing_uses_an_owner_only_temporary_and_returns_no_secret(
    tmp_path: Path,
    monkeypatch,
    caplog,
) -> None:
    agent_path = (tmp_path / "agent.json").resolve()
    jetson_path = (tmp_path / "jetson.json").resolve()
    agent_path.write_text("{}", encoding="utf-8")
    secret = "jetson-pairing-secret-sentinel"
    bundle = json.dumps(
        {
            "url": "https://100.64.0.10:9443",
            "certificate_pem": "certificate",
            "psk_base64url": secret,
        }
    ).encode()
    calls: list[tuple[Path, Path, Path]] = []

    def write_private(path: Path, content: bytes) -> None:
        assert path.parent == agent_path.parent
        path.write_bytes(content)

    def pair(config: Path, temporary: Path, target: Path) -> object:
        calls.append((config, temporary, target))
        assert temporary.read_bytes() == bundle
        return object()

    monkeypatch.setattr(setup_impl, "_write_private_file", write_private)
    monkeypatch.setattr(setup_impl, "pair_jetson", pair)

    with make_client(
        agent_config_path=agent_path,
        jetson_config_path=jetson_path,
    ) as client:
        assert client.get("/setup").status_code == 200
        response = client.post(
            "/setup/api/jetson",
            content=bundle,
            headers={"origin": ORIGIN, "content-type": "application/json"},
        )

    assert response.status_code == 200
    assert response.json() == {"status": "paired", "restart_required": True}
    assert calls[0][0] == agent_path
    assert calls[0][2] == jetson_path
    assert not calls[0][1].exists()
    assert secret not in response.text
    assert secret not in caplog.text
    assert_security_headers(response)


def test_bad_jetson_bundle_preserves_existing_runtime_files(
    tmp_path: Path,
    monkeypatch,
) -> None:
    agent_path = (tmp_path / "agent.json").resolve()
    jetson_path = (tmp_path / "jetson.json").resolve()
    existing = {
        jetson_path: b"existing-config",
        jetson_path.with_name("jetson.crt"): b"existing-certificate",
        jetson_path.with_name("jetson.psk"): b"existing-psk",
    }
    agent_path.write_text("{}", encoding="utf-8")
    for path, content in existing.items():
        path.write_bytes(content)

    monkeypatch.setattr(
        setup_impl,
        "_write_private_file",
        lambda path, content: path.write_bytes(content),
    )
    monkeypatch.setattr(
        setup_impl,
        "pair_jetson",
        lambda *_args: (_ for _ in ()).throw(ValueError("invalid pairing")),
    )

    with make_client(
        agent_config_path=agent_path,
        jetson_config_path=jetson_path,
    ) as client:
        assert client.get("/setup").status_code == 200
        response = client.post(
            "/setup/api/jetson",
            content=b"{}",
            headers={"origin": ORIGIN, "content-type": "application/json"},
        )

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "pairing_rejected"
    assert all(path.read_bytes() == content for path, content in existing.items())
    assert not list(tmp_path.glob(".jetson-pairing.*.tmp"))
    assert_security_headers(response)
