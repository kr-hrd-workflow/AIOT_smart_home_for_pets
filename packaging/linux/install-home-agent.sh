#!/usr/bin/env bash
set -euo pipefail

umask 077

readonly AGENT_USER=petcare
readonly AGENT_GROUP=petcare
readonly AGENT_HOME=/var/lib/petcare
readonly AGENT_PYTHON=/opt/petcare-agent/.venv/bin/python
readonly UNIT_NAME=petcare-agent.service
readonly SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"

MODE=""
ROOT=""
PAIRING_BUNDLE=""

usage() {
    printf 'Usage: %s --install --pairing-bundle PATH\n' "$0" >&2
    printf '       %s --root PATH [--pairing-bundle PATH]\n' "$0" >&2
    exit 2
}

while (($#)); do
    case "$1" in
        --install)
            [[ -z "$MODE" ]] || usage
            MODE=install
            shift
            ;;
        --root)
            [[ -z "$MODE" && $# -ge 2 ]] || usage
            MODE=fixture
            ROOT="$2"
            shift 2
            ;;
        --pairing-bundle)
            [[ $# -ge 2 ]] || usage
            PAIRING_BUNDLE="$2"
            shift 2
            ;;
        *)
            usage
            ;;
    esac
done

[[ -n "$MODE" ]] || usage

if [[ "$MODE" == fixture ]]; then
    [[ -n "$ROOT" && "$ROOT" != / ]] || usage
    FIXTURE_BASE="$(realpath -e -- "$PWD")"
    ROOT="$(realpath -m -- "$ROOT")"
    [[ "$ROOT" != "$FIXTURE_BASE" ]] || usage
    case "$ROOT/" in
        "$FIXTURE_BASE"/*) ;;
        *) usage ;;
    esac
    install -d -m 0755 "$ROOT/etc/systemd/system"
    install -m 0644 "$SCRIPT_DIR/$UNIT_NAME" "$ROOT/etc/systemd/system/$UNIT_NAME"
    printf 'PetCare Linux fixture staged.\n'
    exit 0
fi

[[ -n "$PAIRING_BUNDLE" ]] || usage

verify_private_file() {
    local path="$1"
    local expected_user="$2"
    local expected_group="$3"
    [[ -f "$path" && ! -L "$path" ]]
    [[ "$(stat -c %U:%G -- "$path")" == "$expected_user:$expected_group" ]]
    [[ "$(stat -c %a -- "$path")" == 600 ]]
}

pairing_bundle_file() {
    /usr/bin/python3 - "$@" <<'PY_PAIRING_BUNDLE'
import grp
import hashlib
import os
import pwd
import stat
import sys


class UnsafeBundle(Exception):
    pass


def require(condition):
    if not condition:
        raise UnsafeBundle()


def validate_parent_chain(path):
    parent = os.path.dirname(path)
    while True:
        parent_stat = os.lstat(parent)
        require(stat.S_ISDIR(parent_stat.st_mode))
        require(parent_stat.st_uid == 0)
        require(stat.S_IMODE(parent_stat.st_mode) & 0o022 == 0)
        if parent == "/":
            return
        next_parent = os.path.dirname(parent)
        require(next_parent != parent)
        parent = next_parent


def canonical_bundle_path(supplied_path):
    require("\n" not in supplied_path)
    absolute_path = os.path.abspath(supplied_path)
    canonical_path = os.path.realpath(absolute_path)
    require(absolute_path == canonical_path)
    path_stat = os.lstat(canonical_path)
    require(stat.S_ISREG(path_stat.st_mode))
    validate_parent_chain(canonical_path)
    return canonical_path


def validate_source_stat(source_stat):
    require(stat.S_ISREG(source_stat.st_mode))
    require(source_stat.st_uid == 0)
    require(source_stat.st_gid == 0)
    require(stat.S_IMODE(source_stat.st_mode) == 0o600)


def open_source(supplied_path):
    canonical_path = canonical_bundle_path(supplied_path)
    flags = os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW
    source_fd = os.open(canonical_path, flags)
    try:
        source_stat = os.fstat(source_fd)
        validate_source_stat(source_stat)
        validate_parent_chain(canonical_path)
        return canonical_path, source_fd, source_stat
    except Exception:
        os.close(source_fd)
        raise


def require_identity(source_stat, expected_device, expected_inode):
    require(source_stat.st_dev == int(expected_device))
    require(source_stat.st_ino == int(expected_inode))


def inspect_bundle(supplied_path):
    canonical_path, source_fd, source_stat = open_source(supplied_path)
    os.close(source_fd)
    print(canonical_path)
    print(source_stat.st_dev)
    print(source_stat.st_ino)


def stage_bundle(
    supplied_path, destination, expected_path, expected_device, expected_inode
):
    canonical_path, source_fd, source_stat = open_source(supplied_path)
    destination_fd = None
    try:
        require(canonical_path == expected_path)
        require_identity(source_stat, expected_device, expected_inode)

        destination = os.path.abspath(destination)
        require("\n" not in destination)
        destination_parent = os.path.dirname(destination)
        require(destination_parent == os.path.realpath(destination_parent))
        validate_parent_chain(destination)

        flags = os.O_RDWR | os.O_CREAT | os.O_EXCL | os.O_CLOEXEC | os.O_NOFOLLOW
        destination_fd = os.open(destination, flags, 0o600)
        source_digest = hashlib.sha256()
        while True:
            chunk = os.read(source_fd, 1024 * 1024)
            if not chunk:
                break
            source_digest.update(chunk)
            pending = memoryview(chunk)
            while pending:
                written = os.write(destination_fd, pending)
                require(written > 0)
                pending = pending[written:]

        petcare_uid = pwd.getpwnam("petcare").pw_uid
        petcare_gid = grp.getgrnam("petcare").gr_gid
        os.fchown(destination_fd, petcare_uid, petcare_gid)
        os.fchmod(destination_fd, 0o600)
        os.fsync(destination_fd)

        staged_stat = os.fstat(destination_fd)
        require(stat.S_ISREG(staged_stat.st_mode))
        require(staged_stat.st_uid == petcare_uid)
        require(staged_stat.st_gid == petcare_gid)
        require(stat.S_IMODE(staged_stat.st_mode) == 0o600)

        os.lseek(destination_fd, 0, os.SEEK_SET)
        staged_digest = hashlib.sha256()
        while True:
            chunk = os.read(destination_fd, 1024 * 1024)
            if not chunk:
                break
            staged_digest.update(chunk)
        require(source_digest.digest() == staged_digest.digest())
    finally:
        os.close(source_fd)
        if destination_fd is not None:
            os.close(destination_fd)


def delete_bundle(
    supplied_path, expected_path, expected_device, expected_inode
):
    canonical_path = canonical_bundle_path(supplied_path)
    require(canonical_path == expected_path)
    parent_path = os.path.dirname(canonical_path)
    bundle_name = os.path.basename(canonical_path)
    parent_flags = os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC | os.O_NOFOLLOW
    parent_fd = os.open(parent_path, parent_flags)
    source_fd = None
    try:
        source_flags = os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW
        source_fd = os.open(bundle_name, source_flags, dir_fd=parent_fd)
        source_stat = os.fstat(source_fd)
        validate_source_stat(source_stat)
        require_identity(source_stat, expected_device, expected_inode)
        os.unlink(bundle_name, dir_fd=parent_fd)
    finally:
        if source_fd is not None:
            os.close(source_fd)
        os.close(parent_fd)


def main():
    operation = sys.argv[1]
    arguments = sys.argv[2:]
    if operation == "inspect" and len(arguments) == 1:
        inspect_bundle(*arguments)
    elif operation == "stage" and len(arguments) == 5:
        stage_bundle(*arguments)
    elif operation == "delete" and len(arguments) == 4:
        delete_bundle(*arguments)
    else:
        raise UnsafeBundle()


try:
    main()
except (KeyError, OSError, UnsafeBundle, ValueError):
    print("Pairing bundle validation failed.", file=sys.stderr)
    raise SystemExit(1)
PY_PAIRING_BUNDLE
}

mapfile -t PAIRING_IDENTITY < <(pairing_bundle_file inspect "$PAIRING_BUNDLE")
[[ "${#PAIRING_IDENTITY[@]}" == 3 ]] || exit 1
PAIRING_CANONICAL_PATH="${PAIRING_IDENTITY[0]}"
PAIRING_DEVICE="${PAIRING_IDENTITY[1]}"
PAIRING_INODE="${PAIRING_IDENTITY[2]}"

if ! id -u "$AGENT_USER" >/dev/null 2>&1; then
    useradd --system --user-group --home-dir "$AGENT_HOME" --shell /usr/sbin/nologin "$AGENT_USER"
fi

install -d -o "$AGENT_USER" -g "$AGENT_GROUP" -m 0700 "$AGENT_HOME"
install -m 0644 "$SCRIPT_DIR/$UNIT_NAME" "/etc/systemd/system/$UNIT_NAME"
systemctl daemon-reload

STAGING_DIR="$(mktemp -d /run/petcare-pairing.XXXXXX)"
STAGED_PAIRING_BUNDLE="$STAGING_DIR/pairing.json"
cleanup_staged_bundle() {
    rm -f -- "$STAGED_PAIRING_BUNDLE"
    rmdir -- "$STAGING_DIR"
}
trap cleanup_staged_bundle EXIT
chown root:petcare "$STAGING_DIR"
chmod 0710 "$STAGING_DIR"
pairing_bundle_file stage "$PAIRING_BUNDLE" "$STAGED_PAIRING_BUNDLE" "$PAIRING_CANONICAL_PATH" "$PAIRING_DEVICE" "$PAIRING_INODE"
verify_private_file "$STAGED_PAIRING_BUNDLE" petcare petcare

runuser -u petcare -- /opt/petcare-agent/.venv/bin/python -m app.agent_runtime pair-jetson --config /var/lib/petcare/agent.json --bundle "$STAGED_PAIRING_BUNDLE" --jetson-config /var/lib/petcare/jetson.json >/dev/null
verify_private_file /var/lib/petcare/jetson.crt petcare petcare
verify_private_file /var/lib/petcare/jetson.psk petcare petcare
verify_private_file /var/lib/petcare/jetson.json petcare petcare
runuser -u petcare -- env -i PETCARE_CAMERA_SOURCE=jetson PETCARE_JETSON_CONFIG=/var/lib/petcare/jetson.json /opt/petcare-agent/.venv/bin/python -m app.agent_runtime status --config /var/lib/petcare/agent.json >/dev/null
pairing_bundle_file delete "$PAIRING_BUNDLE" "$PAIRING_CANONICAL_PATH" "$PAIRING_DEVICE" "$PAIRING_INODE"
systemctl enable --now petcare-agent.service

printf 'PetCare Home Agent installed.\n'
