import hashlib
import json
import subprocess
import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "tools" / "evidence_manifest.py"


def make_repo(tmp_path: Path, *, artifact_status="PASS", record_status="PASS"):
    evidence_dir = tmp_path / ".omo" / "evidence"
    evidence_dir.mkdir(parents=True)
    artifact = evidence_dir / "task-1.txt"
    artifact_data = {
        "task_or_gate_id": "1",
        "kind": "contract-mock",
        "status": artifact_status,
        "red": [{"command": "pytest focused", "exit_code": 1, "result": "EXPECTED_RED"}],
        "checks": [{"command": "pytest focused", "exit_code": 0, "result": "PASS"}],
        "hardware": {"status": "NOT_RUN", "reason": "hardware unavailable"},
    }
    artifact.write_text(json.dumps(artifact_data), encoding="utf-8")
    manifest = evidence_dir / "manifest.json"
    record = {
        "task_or_gate_id": "1",
        "kind": "contract-mock",
        "status": record_status,
        "artifact": ".omo/evidence/task-1.txt",
        "sha256": hashlib.sha256(artifact.read_bytes()).hexdigest().upper(),
    }
    manifest.write_text(json.dumps({"records": [record]}), encoding="utf-8")
    return manifest, artifact, record


def run_validator(manifest: Path, repo_root: Path):
    return subprocess.run(
        [sys.executable, str(SCRIPT), "validate", str(manifest), "--repo-root", str(repo_root)],
        capture_output=True,
        text=True,
    )


def test_evidence_validator_accepts_real_hashed_pass_artifact(tmp_path):
    manifest, _, _ = make_repo(tmp_path)
    result = run_validator(manifest, tmp_path)
    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize(
    "payload",
    [[], {"records": {}}, {"records": []}, {"records": ["not-an-object"]}, {"records": [{}]}],
)
def test_malformed_json_shapes_are_rejected_without_traceback(tmp_path, payload):
    evidence_dir = tmp_path / ".omo" / "evidence"
    evidence_dir.mkdir(parents=True)
    manifest = evidence_dir / "manifest.json"
    manifest.write_text(json.dumps(payload), encoding="utf-8")
    result = run_validator(manifest, tmp_path)
    assert result.returncode == 1
    assert "Traceback" not in result.stderr


@pytest.mark.parametrize("field,value", [
    ("kind", "mock"), ("status", "SUCCESS"), ("sha256", "abc"),
    ("artifact", ""), ("artifact", "C:/outside.txt"), ("artifact", "../outside.txt"),
])
def test_invalid_record_fields_and_escaping_paths_are_rejected(tmp_path, field, value):
    manifest, _, record = make_repo(tmp_path)
    record[field] = value
    manifest.write_text(json.dumps({"records": [record]}), encoding="utf-8")
    result = run_validator(manifest, tmp_path)
    assert result.returncode == 1
    assert "Traceback" not in result.stderr


def test_existing_outside_artifact_cannot_escape_repository(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    manifest, _, record = make_repo(repo)
    outside = tmp_path / "outside.txt"
    outside.write_bytes(b"outside")
    record["artifact"] = "../outside.txt"
    record["sha256"] = hashlib.sha256(outside.read_bytes()).hexdigest().upper()
    manifest.write_text(json.dumps({"records": [record]}), encoding="utf-8")
    assert run_validator(manifest, repo).returncode == 1


def test_missing_artifact_and_byte_hash_mismatch_are_rejected(tmp_path):
    manifest, artifact, record = make_repo(tmp_path)
    artifact.unlink()
    assert run_validator(manifest, tmp_path).returncode == 1
    artifact.write_bytes(b"different bytes")
    manifest.write_text(json.dumps({"records": [record]}), encoding="utf-8")
    assert run_validator(manifest, tmp_path).returncode == 1


@pytest.mark.parametrize("artifact_status,record_status", [("FAIL", "PASS"), ("PASS", "FAIL")])
def test_record_and_artifact_status_must_match(tmp_path, artifact_status, record_status):
    manifest, _, _ = make_repo(tmp_path, artifact_status=artifact_status, record_status=record_status)
    assert run_validator(manifest, tmp_path).returncode == 1


def test_false_pass_command_is_rejected(tmp_path):
    manifest, artifact, record = make_repo(tmp_path)
    data = json.loads(artifact.read_text(encoding="utf-8"))
    data["checks"][0]["exit_code"] = 7
    artifact.write_text(json.dumps(data), encoding="utf-8")
    record["sha256"] = hashlib.sha256(artifact.read_bytes()).hexdigest().upper()
    manifest.write_text(json.dumps({"records": [record]}), encoding="utf-8")
    assert run_validator(manifest, tmp_path).returncode == 1


def test_false_fail_without_a_failed_check_is_rejected(tmp_path):
    manifest, artifact, record = make_repo(tmp_path, artifact_status="FAIL", record_status="FAIL")
    record["sha256"] = hashlib.sha256(artifact.read_bytes()).hexdigest().upper()
    manifest.write_text(json.dumps({"records": [record]}), encoding="utf-8")
    assert run_validator(manifest, tmp_path).returncode == 1
