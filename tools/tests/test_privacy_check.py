import json
import shutil
from pathlib import Path

import pytest

from tools.privacy_check import (
    PASS_MARKER,
    main,
    scan_remote_artifacts,
    scan_tracked_files,
)
from tools.secret_sentinel import encoded_forms


ROOT = Path(__file__).resolve().parents[2]


def remote_sentinels() -> tuple[str, str, str, str]:
    return (
        "access-" + "secret-A",
        "tunnel-" + "token-A",
        "refresh-" + "token-A",
        "current-" + "password-A",
    )


def test_remote_artifacts_reject_every_encoded_sentinel_form(tmp_path: Path) -> None:
    values = remote_sentinels()
    artifact = tmp_path / "artifact.log"
    for form in encoded_forms(values[0]):
        artifact.write_text(form, encoding="utf-8")
        with pytest.raises(ValueError, match="sentinel #1"):
            scan_remote_artifacts(tmp_path, values)


@pytest.mark.parametrize("suffix", [".mp4", ".mjpeg", ".jpg", ".jpeg", ".png", ".webp"])
def test_captured_media_residue_fails(tmp_path: Path, suffix: str) -> None:
    (tmp_path / f"capture{suffix}").write_bytes(b"captured")
    with pytest.raises(ValueError, match="media residue"):
        scan_remote_artifacts(tmp_path, ())


def test_only_approved_public_media_assets_are_whitelisted(tmp_path: Path) -> None:
    public = tmp_path / "dashboard" / "public"
    public.mkdir(parents=True)
    (public / "demo-camera.webp").write_bytes(b"approved demo")
    (public / "landing-apartment-photoreal-mobile-v2.webp").write_bytes(
        b"approved mobile landing"
    )
    (public / "landing-apartment-photoreal-mobile-v2-blue.webp").write_bytes(
        b"approved mobile landing blue hour"
    )
    (public / "landing-apartment-photoreal-v3.webp").write_bytes(
        b"approved desktop landing"
    )
    (public / "landing-apartment-photoreal-v3-blue.webp").write_bytes(
        b"approved desktop landing blue hour"
    )
    (public / "landing-apartment-cinematic-loop.mp4").write_bytes(
        b"approved desktop cinematic loop"
    )
    (public / "landing-apartment-cinematic-loop-mobile.mp4").write_bytes(
        b"approved mobile cinematic loop"
    )
    scroll_world = public / "landing" / "scroll-world"
    (scroll_world / "desktop").mkdir(parents=True)
    (scroll_world / "source").mkdir(parents=True)
    (scroll_world / "desktop" / "scene-01-arrival.mp4").write_bytes(
        b"approved Seedance arrival"
    )
    (scroll_world / "source" / "scene-01-arrival.png").write_bytes(
        b"approved Seedance poster"
    )
    (public / "og.png").write_bytes(b"approved social card")
    scan_remote_artifacts(tmp_path, ())


@pytest.mark.parametrize(
    "leak",
    [
        "-----BEGIN " + "PRIVATE KEY-----",
        "https://" + "home-a.example.test/api/health",
        "https://user:" + "credential@example.test/path",
        "Cookie" + ": session=value",
        "current_" + "password=value",
        "https://example.test/reset?" + "token=value",
        "video_" + "bytes=AAAA",
        "https://objects.example.test/clip?" + "signature=value",
        "r2_object_key=" + "owner_sub/eating/home-name",
    ],
)
def test_remote_structural_leaks_fail(tmp_path: Path, leak: str) -> None:
    (tmp_path / "artifact.log").write_text(leak, encoding="utf-8")
    with pytest.raises(ValueError):
        scan_remote_artifacts(tmp_path, ())


@pytest.mark.parametrize(
    "leak",
    [
        r"C:\Users\profile-dir\workspace\artifact.log",
        r"C:\\Users\\profile-dir\\workspace\\artifact.log",
        "private-user",
    ],
)
def test_remote_artifacts_reject_local_identity(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    leak: str,
) -> None:
    monkeypatch.setenv("USERPROFILE", r"C:\Users\profile-dir")
    monkeypatch.setenv("USERNAME", "private-user")
    (tmp_path / "artifact.log").write_text(leak, encoding="utf-8")

    with pytest.raises(ValueError, match="local identity leak"):
        scan_remote_artifacts(tmp_path, ())


def test_cloudflare_secret_names_fail_in_dashboard_bundle(tmp_path: Path) -> None:
    bundle = tmp_path / "dashboard" / "dist" / "entry.js"
    bundle.parent.mkdir(parents=True)
    bundle.write_text("CF_" + "TUNNEL_API_TOKEN", encoding="utf-8")
    with pytest.raises(ValueError, match="remote structural leak"):
        scan_remote_artifacts(tmp_path, ())


def test_repo_literal_gate_excludes_plans_omo_and_test_sources(tmp_path: Path) -> None:
    value = remote_sentinels()[0]
    paths = [
        Path("docs/superpowers/plans/design.md"),
        Path(".omo/evidence/record.txt"),
        Path("backend/tests/test_fixture.py"),
        Path("backend/app.py"),
    ]
    for relative in paths[:-1]:
        path = tmp_path / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(value, encoding="utf-8")
    (tmp_path / paths[-1]).parent.mkdir(parents=True, exist_ok=True)
    (tmp_path / paths[-1]).write_text("safe", encoding="utf-8")

    scan_tracked_files(tmp_path, remote_sentinels(), tracked_paths=paths)
    with pytest.raises(ValueError, match="sentinel #1"):
        scan_remote_artifacts(tmp_path / ".omo", remote_sentinels())


def test_exact_agent_and_jetson_contract_vectors_allow_only_the_frozen_seed(
    tmp_path: Path,
) -> None:
    contracts = tmp_path / "contracts"
    contracts.mkdir()
    agent = contracts / "petcare-agent-wire-v1.json"
    jetson = contracts / "petcare-jetson-wire-v1.json"
    shutil.copyfile(ROOT / "contracts" / agent.name, agent)
    shutil.copyfile(ROOT / "contracts" / jetson.name, jetson)

    scan_tracked_files(
        tmp_path,
        (),
        tracked_paths=[agent.relative_to(tmp_path), jetson.relative_to(tmp_path)],
    )

    mutated = json.loads(agent.read_text(encoding="utf-8"))
    mutated["enrollment"]["response"]["status"] = 202
    agent.write_text(json.dumps(mutated), encoding="utf-8")
    with pytest.raises(ValueError, match="frozen contract"):
        scan_tracked_files(tmp_path, (), tracked_paths=[agent.relative_to(tmp_path)])


def test_frozen_seed_and_connector_token_fail_outside_exact_contracts(tmp_path: Path) -> None:
    agent = json.loads(
        (ROOT / "contracts" / "petcare-agent-wire-v1.json").read_text(encoding="utf-8")
    )
    values = (
        agent["clip"]["seed"],
        agent["enrollment"]["response"]["body"]["connector_token"],
    )
    source = tmp_path / "backend" / "app.py"
    source.parent.mkdir()
    for value in values:
        source.write_text(value, encoding="utf-8")
        with pytest.raises(ValueError, match="frozen contract"):
            scan_tracked_files(tmp_path, (), tracked_paths=[source.relative_to(tmp_path)])


def test_privacy_pass_marker_remains_stable() -> None:
    assert PASS_MARKER == "PRIVACY_CHECK=PASS"


def test_cli_reads_sentinels_from_environment_without_secret_arguments(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    values = remote_sentinels()[:3]
    for index, value in enumerate(values, start=1):
        monkeypatch.setenv(f"PETCARE_SENTINEL_{index}", value)
    toolchain = json.loads((ROOT / ".runtime" / "toolchain.json").read_text(encoding="utf-8"))
    monkeypatch.setenv("PETCARE_TEST_GIT", toolchain["paths"]["git_path"])
    arguments = [
        "--repo",
        str(ROOT),
        "--sentinel-environment",
        "PETCARE_SENTINEL_1",
        "--sentinel-environment",
        "PETCARE_SENTINEL_2",
        "--sentinel-environment",
        "PETCARE_SENTINEL_3",
    ]

    assert all(value not in arguments for value in values)
    assert main(arguments) == 0
