from __future__ import annotations

import hashlib
import json
import re
import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "tools" / "install_consumer_home_agent.ps1"
DOWNLOADS = ROOT / "dashboard" / "public" / "downloads"


def test_consumer_installer_keeps_cloud_and_household_secrets_out_of_arguments() -> None:
    source = SCRIPT.read_text(encoding="utf-8")

    assert "SUPABASE_" not in source
    assert "--code" not in source
    assert "EnrollmentCode" not in source
    assert "sb_secret" not in source
    assert "PETCARE_POSTGRES_PASSWORD" in source
    assert "PETCARE_MQTT_PASSWORD" in source
    assert "app.agent_runtime enroll --origin $SiteOrigin --config $ConfigPath" in source


def test_consumer_installer_uses_exact_private_network_and_service_surface() -> None:
    source = SCRIPT.read_text(encoding="utf-8")

    for name in ("PetCarePostgres", "PetCareMqtt", "PetCareHomeAgent"):
        assert name in source
    assert "New-NetFirewallRule" in source
    assert "-LocalPort 18883" in source
    assert "-RemoteAddress LocalSubnet" in source
    assert "-Profile Private" in source
    assert "Test-Rfc1918" in source
    assert "allow_public_network = $false" in source
    assert "PetCarePostgres/PetCareMqtt" in source


def test_consumer_installer_rolls_back_every_exposed_surface_on_failure() -> None:
    source = SCRIPT.read_text(encoding="utf-8")
    start = source.index("$temporaryServicesStarted = $true")
    invoke = source.index("-Action Start", start)
    rollback = source.index("catch {\n    if ($temporaryServicesStarted)")

    assert start < invoke < rollback
    assert "$temporaryServicesStarted" in source[rollback:]
    assert "-Action Stop" in source[rollback:]
    for name in ("PetCareHomeAgent", "PetCareMqtt", "PetCarePostgres"):
        assert name in source[rollback:]
    assert "Remove-NetFirewallRule" in source[rollback:]
    assert r"HKLM:\Software\PetCare\HomeAgent" in source[rollback:]


def test_consumer_installer_reuses_pinned_runtime_without_the_local_yolo_stack() -> None:
    source = SCRIPT.read_text(encoding="utf-8")

    assert "platform-manifest.json" in source
    assert "managed artifact SHA-256 mismatch" in source
    assert "EclipseFoundation.Mosquitto" not in source
    assert "$Manifest.managed_exact.mosquitto.windows_id" in source
    assert "requirements-home-agent.lock" in source
    assert "--require-hashes --requirement $dependencyLock" in source
    assert "petcare-home-agent.pth" in source


def test_consumer_dependency_inputs_match_the_platform_authority_and_lock_has_hashes() -> None:
    manifest = json.loads((ROOT / "tools" / "platform-manifest.json").read_text(encoding="utf-8"))
    authority = manifest["managed_exact"]["backend_dependencies"]
    requirements = {}
    for line in (ROOT / "backend" / "requirements-home-agent.in").read_text(encoding="utf-8").splitlines():
        name, version = line.split("==", 1)
        requirements[name] = version

    assert requirements
    assert requirements.items() <= authority.items()
    assert "ultralytics" not in requirements

    lock = (ROOT / "backend" / "requirements-home-agent.lock").read_text(encoding="utf-8")
    for name, version in requirements.items():
        normalized = re.escape(name.split("[", 1)[0].lower().replace("_", "-"))
        assert re.search(rf"(?m)^{normalized}=={re.escape(version)} \\", lock.lower())
    assert lock.count("--hash=sha256:") >= len(requirements)


def test_consumer_installer_fixture_is_non_mutating_and_jetson_optional(tmp_path: Path) -> None:
    parse = subprocess.run(
        [
            "powershell.exe",
            "-NoProfile",
            "-NonInteractive",
            "-Command",
            f"[scriptblock]::Create((Get-Content -Raw -LiteralPath '{SCRIPT}')) | Out-Null",
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert parse.returncode == 0, parse.stderr

    result = subprocess.run(
        [
            "powershell.exe",
            "-NoProfile",
            "-NonInteractive",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(SCRIPT),
            "-Action",
            "Fixture",
            "-FixtureRoot",
            str(tmp_path),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr

    fixture = json.loads((tmp_path / "consumer-installer-fixture.json").read_text(encoding="utf-8"))
    assert fixture == {
        "action": "Fixture",
        "mutates_system": False,
        "services": ["PetCarePostgres", "PetCareMqtt", "PetCareHomeAgent"],
        "firewall": {
            "name": "PetCare-Pico-MQTT",
            "local_port": 18883,
            "profile": "Private",
            "remote_address": "LocalSubnet",
        },
        "site_origin": "https://kr-hrd-petcare-aiot-team.cpark333333.chatgpt.site",
        "jetson_optional": True,
        "supabase_customer_configuration": False,
    }


def test_public_installer_is_self_contained_and_hashed() -> None:
    setup = DOWNLOADS / "PetCare-Home-Agent-Setup.exe"
    checksums = DOWNLOADS / "PetCare-Home-Agent-Setup.sha256"
    build_source = (ROOT / "tools" / "build_consumer_home_agent_installer.ps1").read_text(
        encoding="utf-8"
    )

    assert setup.read_bytes()[:2] == b"MZ"
    assert setup.stat().st_size < 1_000_000

    expected = {
        line.split()[1]: line.split()[0]
        for line in checksums.read_text(encoding="utf-8").splitlines()
    }
    assert expected == {setup.name: hashlib.sha256(setup.read_bytes()).hexdigest().upper()}
    assert not list(DOWNLOADS.glob("PetCare-Home-Agent-Bundle*.zip"))
    assert "[Convert]::ToBase64String" in build_source
    assert "Convert.FromBase64String(BundleBase64)" in build_source
    assert "WebClient" not in build_source
    assert "BundleUrl" not in build_source
