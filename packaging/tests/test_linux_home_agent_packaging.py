from __future__ import annotations

import os
import shlex
import subprocess
import tempfile
import uuid
from pathlib import Path

import pytest


REPO_ROOT = Path(".")
SERVICE = REPO_ROOT / "packaging" / "linux" / "petcare-agent.service"
INSTALLER = REPO_ROOT / "packaging" / "linux" / "install-home-agent.sh"


def _run_bash(command: str) -> subprocess.CompletedProcess[bytes]:
    shell = ["wsl.exe", "bash"] if os.name == "nt" else ["bash"]
    return subprocess.run(
        [*shell, "-c", command],
        check=False,
        capture_output=True,
    )


def _run_root(*command: str) -> subprocess.CompletedProcess[bytes]:
    if os.name == "nt":
        prefix = ["wsl.exe", "-u", "root", "--"]
    else:
        if os.geteuid() != 0:
            pytest.skip("root is required for the Linux ownership regression")
        prefix = []
    return subprocess.run(
        [*prefix, *command],
        check=False,
        capture_output=True,
    )


def _pairing_helper_source() -> str:
    script = INSTALLER.read_text(encoding="utf-8")
    marker = "<<'PY_PAIRING_BUNDLE'\n"
    assert marker in script
    return script.split(marker, 1)[1].split("\nPY_PAIRING_BUNDLE", 1)[0]


def test_service_fixes_jetson_environment_and_runtime_user() -> None:
    unit = SERVICE.read_text(encoding="utf-8")

    assert "User=petcare" in unit
    assert "Group=petcare" in unit
    assert "Environment=PETCARE_CAMERA_SOURCE=jetson" in unit
    assert (
        "Environment=PETCARE_JETSON_CONFIG=/var/lib/petcare/jetson.json" in unit
    )
    assert (
        "ExecStart=/opt/petcare-agent/.venv/bin/python -m app.agent_runtime run "
        "--config /var/lib/petcare/agent.json" in unit
    )


def test_real_install_pairs_validates_status_then_deletes_and_enables() -> None:
    script = " ".join(INSTALLER.read_text(encoding="utf-8").split())
    pair = (
        'runuser -u petcare -- /opt/petcare-agent/.venv/bin/python -m '
        'app.agent_runtime pair-jetson --config /var/lib/petcare/agent.json '
        '--bundle "$STAGED_PAIRING_BUNDLE" --jetson-config '
        "/var/lib/petcare/jetson.json"
    )
    validations = (
        "verify_private_file /var/lib/petcare/jetson.crt petcare petcare",
        "verify_private_file /var/lib/petcare/jetson.psk petcare petcare",
        "verify_private_file /var/lib/petcare/jetson.json petcare petcare",
    )
    status = (
        "runuser -u petcare -- env -i PETCARE_CAMERA_SOURCE=jetson "
        "PETCARE_JETSON_CONFIG=/var/lib/petcare/jetson.json "
        "/opt/petcare-agent/.venv/bin/python -m app.agent_runtime status "
        "--config /var/lib/petcare/agent.json >/dev/null"
    )
    deletion = (
        'pairing_bundle_file delete "$PAIRING_BUNDLE" '
        '"$PAIRING_CANONICAL_PATH" "$PAIRING_DEVICE" "$PAIRING_INODE"'
    )
    enable = "systemctl enable --now petcare-agent.service"

    positions = [script.index(pair)]
    positions.extend(script.index(validation) for validation in validations)
    positions.extend((script.index(status), script.index(deletion), script.index(enable)))
    assert positions == sorted(positions)
    assert "stat -c %U:%G" in script
    assert "stat -c %a" in script
    assert "set -x" not in script
    assert 'cat "$PAIRING_BUNDLE"' not in script


def test_real_install_stages_root_bundle_as_petcare_owner_only() -> None:
    script = " ".join(INSTALLER.read_text(encoding="utf-8").split())
    inspection = (
        'mapfile -t PAIRING_IDENTITY < <(pairing_bundle_file inspect '
        '"$PAIRING_BUNDLE")'
    )
    canonical = 'PAIRING_CANONICAL_PATH="${PAIRING_IDENTITY[0]}"'
    device = 'PAIRING_DEVICE="${PAIRING_IDENTITY[1]}"'
    inode = 'PAIRING_INODE="${PAIRING_IDENTITY[2]}"'
    staging = 'STAGING_DIR="$(mktemp -d /run/petcare-pairing.XXXXXX)"'
    copy = (
        'pairing_bundle_file stage "$PAIRING_BUNDLE" "$STAGED_PAIRING_BUNDLE" '
        '"$PAIRING_CANONICAL_PATH" "$PAIRING_DEVICE" "$PAIRING_INODE"'
    )
    staged_validation = (
        'verify_private_file "$STAGED_PAIRING_BUNDLE" petcare petcare'
    )
    pair = '--bundle "$STAGED_PAIRING_BUNDLE"'
    original_deletion = (
        'pairing_bundle_file delete "$PAIRING_BUNDLE" '
        '"$PAIRING_CANONICAL_PATH" "$PAIRING_DEVICE" "$PAIRING_INODE"'
    )

    positions = [
        script.index(inspection),
        script.index(canonical),
        script.index(device),
        script.index(inode),
        script.index(staging),
        script.index(copy),
        script.index(staged_validation),
        script.index(pair),
        script.index(original_deletion),
    ]
    assert positions == sorted(positions)


def test_bundle_helper_uses_nofollow_fd_copy_and_parent_contract() -> None:
    helper = _pairing_helper_source()

    for required in (
        "os.O_NOFOLLOW",
        "source_stat = os.fstat(source_fd)",
        "validate_parent_chain(canonical_path)",
        "source_stat.st_dev",
        "source_stat.st_ino",
        "source_digest = hashlib.sha256()",
        "staged_digest = hashlib.sha256()",
        "source_digest.digest() == staged_digest.digest()",
        "os.unlink(bundle_name, dir_fd=parent_fd)",
    ):
        assert required in helper


def test_bundle_delete_rejects_writable_parent_and_inode_swap() -> None:
    helper = _pairing_helper_source()
    test_root = f"/root/petcare-package-{uuid.uuid4().hex}"
    bundle = f"{test_root}/pairing.json"
    retired = f"{test_root}/retired.json"

    try:
        created = _run_root(
            "install", "-d", "-o", "root", "-g", "root", "-m", "0700", test_root
        )
        assert created.returncode == 0, created.stderr.decode(errors="replace")
        installed = _run_root(
            "install", "-o", "root", "-g", "root", "-m", "0600", "/dev/null", bundle
        )
        assert installed.returncode == 0, installed.stderr.decode(errors="replace")
        identity = _run_root("stat", "-c", "%d %i", bundle)
        assert identity.returncode == 0, identity.stderr.decode(errors="replace")
        device, inode = identity.stdout.decode().strip().split()

        assert _run_root("chmod", "0777", test_root).returncode == 0
        writable_parent = _run_root(
            "python3", "-c", helper, "delete", bundle, bundle, device, inode
        )
        assert writable_parent.returncode != 0
        assert _run_root("test", "-f", bundle).returncode == 0

        assert _run_root("chmod", "0700", test_root).returncode == 0
        assert _run_root("mv", "--", bundle, retired).returncode == 0
        assert _run_root(
            "install", "-o", "root", "-g", "root", "-m", "0600", "/dev/null", bundle
        ).returncode == 0
        swapped = _run_root(
            "python3", "-c", helper, "delete", bundle, bundle, device, inode
        )
        assert swapped.returncode != 0
        assert _run_root("test", "-f", bundle).returncode == 0

        current = _run_root("stat", "-c", "%d %i", bundle)
        current_device, current_inode = current.stdout.decode().strip().split()
        legitimate = _run_root(
            "python3",
            "-c",
            helper,
            "delete",
            bundle,
            bundle,
            current_device,
            current_inode,
        )
        assert legitimate.returncode == 0, legitimate.stderr.decode(errors="replace")
        assert _run_root("test", "-e", bundle).returncode != 0
    finally:
        _run_root("rm", "-f", "--", bundle, retired)
        _run_root("rmdir", "--", test_root)


def test_fixture_stages_unit_without_external_mutation_or_secret_output() -> None:
    with tempfile.TemporaryDirectory(prefix="linux-package-", dir=".") as temp_name:
        temp_root = Path(Path(temp_name).name)
        fixture_root = temp_root / "root"
        bundle = temp_root / "pairing.json"
        mutation_log = temp_root / "mutations.log"
        secret = "fixture-secret-must-not-appear"
        bundle.write_text('{"psk":"' + secret + '"}', encoding="utf-8")

        script_path = INSTALLER.as_posix()
        root_path = fixture_root.as_posix()
        bundle_path = bundle.as_posix()
        log_path = mutation_log.as_posix()
        command = f"""
set -euo pipefail
export MUTATION_LOG={shlex.quote(log_path)}
record_mutation() {{ printf '%s\\n' "$1" >> "$MUTATION_LOG"; return 97; }}
useradd() {{ record_mutation useradd; }}
ufw() {{ record_mutation ufw; }}
systemctl() {{ record_mutation systemctl; }}
runuser() {{ record_mutation runuser; }}
export -f record_mutation useradd ufw systemctl runuser
bash {shlex.quote(script_path)} --root {shlex.quote(root_path)} --pairing-bundle {shlex.quote(bundle_path)}
"""

        completed = _run_bash(command)

        assert completed.returncode == 0, completed.stderr.decode(errors="replace")
        assert bundle.exists()
        assert not mutation_log.exists()
        assert secret.encode() not in completed.stdout
        assert secret.encode() not in completed.stderr
        assert (fixture_root / "etc/systemd/system/petcare-agent.service").read_text(
            encoding="utf-8"
        ) == SERVICE.read_text(encoding="utf-8")


def test_fixture_rejects_symlink_to_root_and_parent_escape_without_mutation() -> None:
    script = " ".join(INSTALLER.read_text(encoding="utf-8").split())
    assert 'FIXTURE_BASE="$(realpath -e -- "$PWD")"' in script
    assert 'ROOT="$(realpath -m -- "$ROOT")"' in script
    assert 'case "$ROOT/" in "$FIXTURE_BASE"/*)' in script

    with tempfile.TemporaryDirectory(prefix="linux-package-", dir=".") as temp_name:
        temp_root = Path(Path(temp_name).name)
        symlink_root = temp_root / "root-link"
        mutation_log = temp_root / "mutations.log"
        link_result = _run_bash(
            f"ln -s / {shlex.quote(symlink_root.as_posix())}"
        )
        assert link_result.returncode == 0, link_result.stderr.decode(errors="replace")

        unsafe_roots = (symlink_root, temp_root / ".." / ".." / "escape")
        for unsafe_root in unsafe_roots:
            mutation_log.unlink(missing_ok=True)
            command = f"""
set -euo pipefail
export MUTATION_LOG={shlex.quote(mutation_log.as_posix())}
record_mutation() {{ printf '%s\\n' "$1" >> "$MUTATION_LOG"; return 97; }}
install() {{ record_mutation install; }}
useradd() {{ record_mutation useradd; }}
ufw() {{ record_mutation ufw; }}
systemctl() {{ record_mutation systemctl; }}
runuser() {{ record_mutation runuser; }}
export -f record_mutation install useradd ufw systemctl runuser
bash {shlex.quote(INSTALLER.as_posix())} --root {shlex.quote(unsafe_root.as_posix())}
"""
            completed = _run_bash(command)

            assert completed.returncode != 0
            assert not mutation_log.exists()
