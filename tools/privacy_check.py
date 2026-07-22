"""Scan tracked and runtime artifacts for PetCare privacy leaks."""

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
from collections.abc import Iterable, Sequence
from pathlib import Path

if __package__:
    from tools.secret_sentinel import encoded_forms, require_independent_sentinels
else:
    from secret_sentinel import encoded_forms, require_independent_sentinels


PASS_MARKER = "PRIVACY_CHECK=PASS"

_AGENT_VECTOR_SHA256 = "D9849424F38A2F99B844C4705EB0652BF245B74ECE6173C62E0271D1DB7E2E4B"
_JETSON_VECTOR_SHA256 = "7938D32349407405775075BC3B4F9212C3F5BFC2DDBE6C85F1D2E5AAD8B4D596"
_FROZEN_SEED = "AAECAwQFBgcICQoL" + "DA0ODxAREhMUFRYXGBkaGxwdHh8"
_FIXTURE_CONNECTOR = "fixture-only-" + "connector-token"
_MEDIA_SUFFIXES = {".mp4", ".mjpeg", ".jpg", ".jpeg", ".png", ".webp"}
_MEDIA_ALLOWLIST = {
    "dashboard/public/demo-camera.webp",
    "dashboard/public/og.png",
}
_PRIVATE_KEY = re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----")
_CREDENTIAL_URL = re.compile(r"(?i)\b[a-z][a-z0-9+.-]*://[^/\s:@]+:[^/\s@]+@")
_TUNNEL_OR_LOCAL_URL = re.compile(
    r"(?i)\b(?:https?|wss?)://(?:[^/\s@]+@)?(?:"
    r"localhost|127(?:\.\d{1,3}){3}|0\.0\.0\.0|\[::1\]|"
    r"(?:home|agent|tunnel)-[a-z0-9.-]+|[a-z0-9.-]+\.trycloudflare\.com)"
)
_REMOTE_SECRET_NAME = re.compile(
    r"\b(?:CF-Access-Client-Secret|CF_TUNNEL_API_TOKEN|CLOUDFLARE_API_TOKEN|"
    r"TUNNEL_TOKEN)\b"
)
_R2_SENSITIVE_KEY = re.compile(
    r"(?im)^.*(?:r2[_ .-]*(?:object[_ .-]*)?key|object[_ .-]*key).*"
    r"(?:@|owner[_-]?sub|[\w.+-]+@[\w.-]+|bed_sensor_mismatch|eating|resting|"
    r"home[_ .-]*(?:name|id))"
)
_LOG_LEAK = re.compile(
    r"(?i)(?:\b(?:set-)?cookie\s*[:=]|\bcurrent[_-]?password\s*[:=]|"
    r"https?://[^\s]+/reset[^\s]*(?:token|code)=|\bvideo[_ -]?bytes\s*[:=]|"
    r"https?://[^\s]+[?&](?:x-amz-signature|signature|signed|token|expires)=)"
)


def _local_identity_values() -> tuple[str, ...]:
    values: list[str] = []
    for name in ("USERPROFILE", "USERNAME"):
        value = os.environ.get(name, "").strip()
        if not value:
            continue
        values.extend(
            (value, value.replace("\\", "/"), value.replace("\\", "\\\\"))
        )
    return tuple(dict.fromkeys(values))


def _relative(root: Path, path: Path) -> Path:
    candidate = path if path.is_absolute() else root / path
    resolved = candidate.resolve()
    try:
        return resolved.relative_to(root.resolve())
    except ValueError as exc:
        raise ValueError("scan path escapes root") from exc


def _is_excluded_tracked(relative: Path) -> bool:
    parts = tuple(part.lower() for part in relative.parts)
    return (
        bool(parts)
        and (
            parts[0] == ".omo"
            or parts[:3] == ("docs", "superpowers", "plans")
            or "tests" in parts
        )
    )


def _fixed_allowlist(relative: str, data: bytes) -> set[str]:
    digest = hashlib.sha256(data).hexdigest().upper()
    if relative == "contracts/petcare-agent-wire-v1.json" and digest == _AGENT_VECTOR_SHA256:
        return {_FROZEN_SEED, _FIXTURE_CONNECTOR}
    if relative == "contracts/petcare-jetson-wire-v1.json" and digest == _JETSON_VECTOR_SHA256:
        return {_FROZEN_SEED}
    return set()


def _contains(data: bytes, value: str) -> bool:
    return any(form.encode("utf-8") in data for form in encoded_forms(value))


def _scan_paths(
    root: Path,
    paths: Iterable[Path],
    sentinels: Sequence[str],
    *,
    runtime_artifacts: bool,
) -> None:
    values = require_independent_sentinels(sentinels) if sentinels else ()
    for file_index, supplied in enumerate(paths, start=1):
        candidate = supplied if supplied.is_absolute() else root / supplied
        if candidate.is_symlink() or not candidate.is_file():
            continue
        relative_path = _relative(root, supplied)
        path = root / relative_path
        relative = relative_path.as_posix()
        lowered = relative.lower()

        if path.suffix.lower() in _MEDIA_SUFFIXES and lowered not in _MEDIA_ALLOWLIST:
            raise ValueError(f"media residue (file #{file_index})")

        data = path.read_bytes()
        fixed_allowed = _fixed_allowlist(lowered, data)
        for fixed in (_FROZEN_SEED, _FIXTURE_CONNECTOR):
            if fixed not in fixed_allowed and _contains(data, fixed):
                raise ValueError(f"frozen contract secret leak (file #{file_index})")
        for sentinel_index, sentinel in enumerate(values, start=1):
            if sentinel not in fixed_allowed and _contains(data, sentinel):
                raise ValueError(
                    f"sentinel #{sentinel_index} leak (file #{file_index})"
                )

        if not runtime_artifacts and not lowered.startswith("dashboard/dist/"):
            continue
        text = data.decode("utf-8", errors="replace")
        folded = text.casefold()
        if runtime_artifacts and any(
            value.casefold() in folded for value in _local_identity_values()
        ):
            raise ValueError(f"local identity leak (file #{file_index})")
        if (
            _PRIVATE_KEY.search(text)
            or _CREDENTIAL_URL.search(text)
            or _TUNNEL_OR_LOCAL_URL.search(text)
            or _REMOTE_SECRET_NAME.search(text)
            or _R2_SENSITIVE_KEY.search(text)
            or _LOG_LEAK.search(text)
        ):
            raise ValueError(f"remote structural leak (file #{file_index})")


def _git_tracked_paths(repo: Path) -> list[Path]:
    git_text = os.environ.get("PETCARE_TEST_GIT", "")
    if not git_text:
        runtime = repo / ".runtime" / "toolchain.json"
        try:
            git_text = json.loads(runtime.read_text(encoding="utf-8"))["paths"]["git_path"]
        except (OSError, KeyError, TypeError, json.JSONDecodeError) as exc:
            raise ValueError("explicit Git path is unavailable") from exc
    git = Path(git_text)
    if not git.is_absolute() or not git.is_file():
        raise ValueError("explicit Git path is invalid")
    result = subprocess.run(
        [git, "-C", repo, "ls-files", "-z"],
        capture_output=True,
        check=False,
    )
    if result.returncode:
        raise ValueError("unable to enumerate tracked files")
    return [Path(value.decode("utf-8")) for value in result.stdout.split(b"\0") if value]


def scan_tracked_files(
    repo: Path,
    sentinels: Sequence[str],
    *,
    tracked_paths: Iterable[Path] | None = None,
) -> None:
    root = repo.resolve()
    candidates = list(tracked_paths) if tracked_paths is not None else _git_tracked_paths(root)
    included = [path for path in candidates if not _is_excluded_tracked(Path(path))]
    _scan_paths(root, included, sentinels, runtime_artifacts=False)


def scan_remote_artifacts(root: Path, sentinels: Sequence[str]) -> None:
    resolved = root.resolve()
    if not resolved.is_dir():
        raise ValueError("remote artifact root is unavailable")
    paths = sorted(
        (path for path in resolved.rglob("*") if path.is_file() and not path.is_symlink()),
        key=lambda path: path.as_posix(),
    )
    _scan_paths(resolved, paths, sentinels, runtime_artifacts=True)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo", type=Path, default=Path("."))
    parser.add_argument("--artifact", type=Path, action="append", default=[])
    parser.add_argument("--sentinel", action="append", default=[])
    parser.add_argument("--sentinel-environment", action="append", default=[])
    parser.add_argument("--remote-artifacts", type=Path, action="append", default=[])
    parser.add_argument("--remote-sentinel", action="append", default=[])
    args = parser.parse_args(argv)
    environment_sentinels = []
    for name in args.sentinel_environment:
        value = os.environ.get(name)
        if not value:
            print("sentinel environment input is missing", file=sys.stderr)
            return 1
        environment_sentinels.append(value)
    sentinels = tuple(args.sentinel + args.remote_sentinel + environment_sentinels)
    try:
        if sentinels:
            require_independent_sentinels(sentinels)
        scan_tracked_files(args.repo, sentinels)
        for artifact_root in args.artifact + args.remote_artifacts:
            scan_remote_artifacts(artifact_root, sentinels)
    except (OSError, UnicodeError, ValueError) as exc:
        print(str(exc), file=sys.stderr)
        return 1
    print(PASS_MARKER)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
