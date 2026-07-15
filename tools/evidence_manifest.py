"""Validate contained, byte-hashed PetCare evidence and its declared result."""

import argparse
import hashlib
import json
import re
import sys
from pathlib import Path, PureWindowsPath


KINDS = {"contract-mock", "local-live", "sites-production", "hardware"}
STATUSES = {"PASS", "FAIL", "NOT_RUN"}
SHA256 = re.compile(r"^[0-9A-Fa-f]{64}$")
RECORD_KEYS = {"task_or_gate_id", "kind", "status", "artifact", "sha256"}
ARTIFACT_KEYS = {"task_or_gate_id", "kind", "status", "red", "checks", "hardware"}


def _validate_artifact(data: object, record: dict, index: int) -> list[str]:
    prefix = f"record {index}"
    if not isinstance(data, dict):
        return [f"{prefix}: artifact must be a JSON object"]
    errors = []
    if set(data) != ARTIFACT_KEYS:
        errors.append(f"{prefix}: malformed artifact fields")
    for key in ("task_or_gate_id", "kind", "status"):
        if data.get(key) != record[key]:
            errors.append(f"{prefix}: artifact {key} mismatch")
    red = data.get("red")
    if not isinstance(red, list) or not red:
        errors.append(f"{prefix}: red results must be non-empty")
    elif any(
        not isinstance(item, dict)
        or set(item) != {"command", "exit_code", "result"}
        or not isinstance(item.get("command"), str)
        or not item["command"]
        or not isinstance(item.get("exit_code"), int)
        or item["exit_code"] <= 0
        or item.get("result") != "EXPECTED_RED"
        for item in red
    ):
        errors.append(f"{prefix}: malformed expected RED result")
    checks = data.get("checks")
    if not isinstance(checks, list):
        errors.append(f"{prefix}: checks must be a list")
    else:
        malformed_check = any(
            not isinstance(item, dict)
            or set(item) != {"command", "exit_code", "result"}
            or not isinstance(item.get("command"), str)
            or not item["command"]
            or not isinstance(item.get("exit_code"), int)
            or item.get("result") not in {"PASS", "FAIL"}
            or (item.get("exit_code") == 0) != (item.get("result") == "PASS")
            for item in checks
        )
        if malformed_check:
            errors.append(f"{prefix}: malformed check result")
        elif record["status"] == "PASS" and (not checks or any(item["result"] != "PASS" for item in checks)):
            errors.append(f"{prefix}: PASS artifact contains a failed check")
        elif record["status"] == "FAIL" and not any(item["result"] == "FAIL" for item in checks):
            errors.append(f"{prefix}: FAIL artifact contains no failed check")
    hardware = data.get("hardware")
    if not isinstance(hardware, dict) or set(hardware) != {"status", "reason"}:
        errors.append(f"{prefix}: malformed hardware result")
    elif hardware.get("status") not in STATUSES or not hardware.get("reason"):
        errors.append(f"{prefix}: invalid hardware result")
    return errors


def validate(path: Path, repo_root: Path | None = None) -> list[str]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        return [f"invalid JSON: {exc}"]
    if not isinstance(data, dict) or set(data) != {"records"}:
        return ["evidence manifest must be an object containing only records"]
    records = data["records"]
    if not isinstance(records, list) or not records:
        return ["records must be a non-empty list"]

    root = (repo_root or Path(__file__).resolve().parents[1]).resolve()
    evidence_root = (root / ".omo" / "evidence").resolve()
    errors = []
    for index, record in enumerate(records):
        prefix = f"record {index}"
        if not isinstance(record, dict):
            errors.append(f"{prefix}: record must be an object")
            continue
        if set(record) != RECORD_KEYS:
            errors.append(f"{prefix}: malformed record fields")
            continue
        if not isinstance(record["task_or_gate_id"], str) or not record["task_or_gate_id"]:
            errors.append(f"{prefix}: invalid task_or_gate_id")
        if record["kind"] not in KINDS:
            errors.append(f"{prefix}: invalid kind")
        if record["status"] not in STATUSES:
            errors.append(f"{prefix}: invalid status")
        if not isinstance(record["sha256"], str) or not SHA256.fullmatch(record["sha256"]):
            errors.append(f"{prefix}: invalid sha256")

        artifact_text = record["artifact"]
        if (
            not isinstance(artifact_text, str)
            or not artifact_text
            or Path(artifact_text).is_absolute()
            or PureWindowsPath(artifact_text).is_absolute()
            or ".." in Path(artifact_text).parts
        ):
            errors.append(f"{prefix}: artifact must be a contained relative path")
            continue
        artifact = (root / artifact_text).resolve()
        if not artifact.is_relative_to(evidence_root):
            errors.append(f"{prefix}: artifact must be under .omo/evidence")
            continue
        if not artifact.is_file():
            errors.append(f"{prefix}: artifact does not exist")
            continue
        actual_hash = hashlib.sha256(artifact.read_bytes()).hexdigest().upper()
        if actual_hash != record["sha256"].upper():
            errors.append(f"{prefix}: artifact hash mismatch")
            continue
        try:
            artifact_data = json.loads(artifact.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            errors.append(f"{prefix}: invalid artifact JSON: {exc}")
            continue
        errors.extend(_validate_artifact(artifact_data, record, index))
    return errors


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=["validate"])
    parser.add_argument("path", type=Path)
    parser.add_argument("--repo-root", type=Path)
    args = parser.parse_args()
    errors = validate(args.path, args.repo_root)
    if errors:
        print("\n".join(errors), file=sys.stderr)
        return 1
    print(f"valid evidence manifest: {args.path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
