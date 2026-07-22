from __future__ import annotations

import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "packaging" / "windows" / "install-home-agent.ps1"


def test_windows_installer_has_exact_service_registry_and_no_secret_or_firewall_surface() -> None:
    source = SCRIPT.read_text(encoding="utf-8")
    assert "PetCareHomeAgent" in source
    assert r"HKLM:\Software\PetCare\HomeAgent" in source
    for name in ("ConfigPath", "ToolsPath", "JetsonConfigPath"):
        assert name in source
    assert "New-NetFirewallRule" not in source
    for secret in ("ConnectorToken", "EnrollmentCode", "PrivateKey"):
        assert f"New-ItemProperty -Path $RegistryPath -Name {secret}" not in source
    assert "ValidateSet('Install', 'Uninstall', 'Status', 'Fixture')" in source
    assert "WindowsPrincipal" in source
    assert "WindowsBuiltInRole]::Administrator" in source


def test_windows_installer_preserves_exact_service_commands_and_status_cli() -> None:
    source = SCRIPT.read_text(encoding="utf-8")
    install = "& $Python -m app.windows_service --startup auto install"
    failure = 'sc.exe" failure PetCareHomeAgent reset= 86400 actions= restart/5000/restart/30000/restart/120000'
    failure_flag = 'sc.exe" failureflag PetCareHomeAgent 1'
    start = "& $Python -m app.windows_service start"
    assert install in source
    assert failure in source
    assert failure_flag in source
    assert start in source
    assert source.index(install) < source.index(failure) < source.index(failure_flag) < source.index(start)
    assert "app.agent_runtime status --config $storedConfig --tools $storedTools --jetson-config $storedJetson" in source
    assert "http://" not in source and "https://" not in source


def test_pairing_precedes_registry_service_and_bundle_removal_follows_verified_import() -> None:
    source = SCRIPT.read_text(encoding="utf-8")
    pair = "app.agent_runtime pair-jetson"
    registry = "New-ItemProperty"
    remove_bundle = "Remove-Item -LiteralPath $PairingBundle"
    assert source.index(pair) < source.index(remove_bundle) < source.index(registry)
    assert "Get-FileIdentity" in source
    assert "pairing bundle identity changed" in source
    assert "Assert-OwnerOnlyAcl $PairingBundle" in source


def test_install_removes_known_stale_secrets_and_rejects_unknown_registry_values() -> None:
    source = SCRIPT.read_text(encoding="utf-8")
    assert "Remove-ItemProperty" in source
    assert "ConnectorToken" in source
    assert "EnrollmentCode" in source
    assert "PrivateKey" in source
    assert "unexpected HomeAgent registry value" in source


def test_powershell_script_parses_and_fixture_has_no_external_mutation(tmp_path: Path) -> None:
    parse = subprocess.run(
        ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", f"[scriptblock]::Create((Get-Content -Raw -LiteralPath '{SCRIPT}')) | Out-Null"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert parse.returncode == 0, parse.stderr

    config = tmp_path / "agent.json"
    tools = tmp_path / "agent-tools.json"
    jetson = tmp_path / "jetson.json"
    bundle = tmp_path / "pairing.json"
    fixture = tmp_path / "fixture.json"
    for path in (config, tools, jetson, bundle):
        path.write_text("{}", encoding="utf-8")
    result = subprocess.run(
        [
            "powershell.exe", "-NoProfile", "-NonInteractive", "-File", str(SCRIPT),
            "-Action", "Fixture", "-ConfigPath", str(config), "-ToolsPath", str(tools),
            "-JetsonConfigPath", str(jetson), "-PairingBundle", str(bundle), "-FixturePath", str(fixture),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert fixture.exists() and bundle.exists()
