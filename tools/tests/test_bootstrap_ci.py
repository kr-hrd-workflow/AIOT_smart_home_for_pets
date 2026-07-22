import hashlib
import json
import os
import subprocess
from pathlib import Path
from pathlib import PurePosixPath


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "tools" / "bootstrap_ci.sh"
BUILD = ROOT / "tools" / "build_pico_host.sh"

LINUX_PATH_KEYS = {
    "git_path", "bash_path", "uv_path", "python_path", "node_path", "npm_cli_path",
    "cmake_path", "ctest_path", "ninja_path", "host_cc_path", "host_cxx_path",
    "host_as_path", "host_ar_path", "host_ranlib_path", "host_ld_path",
    "host_objcopy_path", "host_size_path", "arm_toolchain_root", "arm_gcc_path",
    "arm_gxx_path", "arm_asm_path", "arm_as_path", "arm_ar_path",
    "arm_ranlib_path", "arm_ld_path", "arm_objcopy_path", "arm_size_path",
    "docker_path", "compose_plugin_path",
}


def msys(path):
    value = str(Path(path).resolve()).replace("\\", "/")
    return f"/{value[0].lower()}{value[2:]}" if value[1:3] == ":/" else value


def test_ubuntu_2404_fixture_records_complete_absolute_versioned_closure(tmp_path):
    bash = os.environ["PETCARE_TEST_BASH"]
    fixture = tmp_path / "fixture"
    output = tmp_path / "platform-linux.json"
    result = subprocess.run(
        [bash, msys(SCRIPT), "--fixture-root", msys(fixture), "--output", msys(output)],
        cwd=ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    assert result.returncode == 0, result.stderr
    data = json.loads(output.read_text(encoding="utf-8"))
    assert data["platform"] == "ubuntu-24.04"
    assert data["manifest_sha256"] == hashlib.sha256((ROOT / "tools" / "platform-manifest.json").read_bytes()).hexdigest().upper()
    assert set(data["paths"]) == LINUX_PATH_KEYS
    assert set(data["versions"]) == LINUX_PATH_KEYS
    assert all(Path(path).is_absolute() or PurePosixPath(path).is_absolute() for path in data["paths"].values())
    assert all(data["versions"].values())
    assert data["capabilities"] == {
        "git": "2.43", "bash": "5.2", "gnu_c_cpp": "13.3",
        "gnu_binutils": "2.42", "docker": "26.1", "compose": "2.27",
    }


def test_ubuntu_fixture_rejects_wrong_managed_bytes_and_capability_floor(tmp_path):
    bash = os.environ["PETCARE_TEST_BASH"]
    for mutation in ("wrong-byte", "low-capability"):
        result = subprocess.run(
            [bash, msys(SCRIPT), "--fixture-root", msys(tmp_path / mutation),
             "--output", msys(tmp_path / f"{mutation}.json"), "--mutation", mutation],
            cwd=ROOT,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        assert result.returncode != 0, mutation


def test_linux_bootstrap_reads_artifact_and_pico_identities_from_manifest():
    manifest = json.loads((ROOT / "tools" / "platform-manifest.json").read_text(encoding="utf-8"))
    script = SCRIPT.read_text(encoding="utf-8")
    managed = manifest["managed_exact"]
    duplicated = []
    for name in ("uv", "python", "node", "cmake", "ninja", "arm_gnu"):
        duplicated.extend((managed[name]["linux"]["url"], managed[name]["linux"]["sha256"]))
    pico = managed["pico_sdk"]
    duplicated.extend((pico["url"], pico["tag"], pico["commit"]))
    assert not [value for value in duplicated if value in script]


def test_linux_bootstrap_accepts_the_managed_python_symlink():
    script = SCRIPT.read_text(encoding="utf-8")
    assert "find \"$MANAGED/python\" -path '*/bin/python3' -print -quit" in script
    assert "find \"$MANAGED/python\" -type f -path '*/bin/python3'" not in script


def test_fixture_runtime_uses_a_real_python_interpreter():
    script = SCRIPT.read_text(encoding="utf-8")
    assert 'paths[python_path]="$BASE_PYTHON"' in script


def test_linux_host_build_uses_only_runtime_manifest_paths(tmp_path):
    bash = os.environ["PETCARE_TEST_BASH"]
    output = tmp_path / "platform-linux.json"
    subprocess.run(
        [bash, msys(SCRIPT), "--fixture-root", msys(tmp_path / "fixture"), "--output", msys(output)],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    env = os.environ.copy()
    env["PATH"] = ""
    result = subprocess.run(
        [bash, msys(BUILD), "--runtime", msys(output), "--dry-run"],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    assert result.returncode == 0, result.stderr
    assert "manifest-backed host build PASS" in result.stdout


def test_fixture_runtime_cannot_claim_real_linux_host_build(tmp_path):
    bash = os.environ["PETCARE_TEST_BASH"]
    output = tmp_path / "platform-linux.json"
    subprocess.run(
        [bash, msys(SCRIPT), "--fixture-root", msys(tmp_path / "fixture"), "--output", msys(output)],
        cwd=ROOT, check=True, capture_output=True, text=True, encoding="utf-8", errors="replace",
    )
    result = subprocess.run(
        [bash, msys(BUILD), "--runtime", msys(output), "--build-dir", msys(tmp_path / "build")],
        cwd=ROOT, capture_output=True, text=True, encoding="utf-8", errors="replace",
    )
    assert result.returncode != 0
    assert "fixture runtime cannot prove a real host build" in result.stderr


def test_linux_child_rejects_runtime_hash_and_executable_mutations(tmp_path):
    bash = os.environ["PETCARE_TEST_BASH"]
    output = tmp_path / "platform-linux.json"
    subprocess.run(
        [bash, msys(SCRIPT), "--fixture-root", msys(tmp_path / "fixture"), "--output", msys(output)],
        cwd=ROOT, check=True, capture_output=True, text=True, encoding="utf-8", errors="replace",
    )
    baseline = json.loads(output.read_text(encoding="utf-8"))
    mutations = [
        ("manifest_sha256", None),
        ("ctest_path", "paths"), ("host_cxx_path", "paths"), ("host_ar_path", "paths"),
        ("host_ld_path", "paths"), ("host_objcopy_path", "paths"), ("host_size_path", "paths"),
    ]
    for key, section in mutations:
        changed = json.loads(json.dumps(baseline))
        if section:
            changed[section][key] = "relative-or-missing"
        else:
            changed[key] = "0" * 64
        altered = tmp_path / f"altered-{key}.json"
        altered.write_text(json.dumps(changed), encoding="utf-8")
        env = os.environ.copy()
        env["PATH"] = ""
        result = subprocess.run(
            [bash, msys(BUILD), "--runtime", msys(altered), "--dry-run"], cwd=ROOT, env=env,
            capture_output=True, text=True, encoding="utf-8", errors="replace",
        )
        assert result.returncode != 0, key


def test_ubuntu_child_executes_complete_closure_with_empty_path(tmp_path):
    bash = os.environ["PETCARE_TEST_BASH"]
    output = tmp_path / "platform-linux.json"
    subprocess.run(
        [bash, msys(SCRIPT), "--fixture-root", msys(tmp_path / "fixture"), "--output", msys(output)],
        cwd=ROOT, check=True, capture_output=True, text=True, encoding="utf-8", errors="replace",
    )
    data = json.loads(output.read_text(encoding="utf-8"))
    executable_keys = sorted(LINUX_PATH_KEYS - {"arm_toolchain_root"})
    env = os.environ.copy()
    env["PATH"] = ""
    result = subprocess.run(
        [bash, "-c", 'for tool in "$@"; do "$tool" --version >/dev/null; done', "closure",
         *(data["paths"][key] for key in executable_keys)],
        cwd=ROOT, env=env, capture_output=True, text=True, encoding="utf-8", errors="replace",
    )
    assert result.returncode == 0, result.stderr


def test_ignore_rules_are_root_scoped_and_preserve_source_assets():
    git_path = os.environ["PETCARE_TEST_GIT"]

    def ignored(path):
        return subprocess.run([git_path, "check-ignore", "--no-index", "-q", path], cwd=ROOT).returncode == 0

    assert ignored("progress-monitor/file")
    assert not ignored("nested/progress-monitor/file")
    assert not ignored("dashboard/build/sites-vite-plugin.ts")
    assert not ignored("assets/demo.png")
    assert not ignored("src/icon.jpeg")
    assert ignored(".runtime/frames/frame.png")
    assert ignored(".runtime/video/home.mp4")
    assert ignored(".runtime/models/yolo11n.pt")
    assert ignored(".runtime/ms-playwright/chromium/chrome.exe")
    assert ignored(".runtime/sites-credentials/token.json")
