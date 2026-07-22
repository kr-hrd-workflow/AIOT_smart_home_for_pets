from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import secrets
import stat
import threading
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import BinaryIO, Callable

from .agent_client import SignedClipUploadClient
from .agent_config import protect_runtime_file
from .clip_contracts import ClipEventMetadata, ClipMetadata, canonical_utc_text, utc_text
from .config import _owner_only_descriptor, _secure_read


_QUEUE_ID = re.compile(r"[0-9a-f]{64}")
_COMMAND_ID = re.compile(r"[0-9a-f]{32}")
_RETRY_SECONDS = (5, 30, 120, 600)
_CAPACITY = 8
_EXPIRY = timedelta(hours=1)


class ClipUploadQueueFull(RuntimeError):
    pass


def _protect_directory(path: Path) -> None:
    status = path.lstat()
    reparse = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
    if (
        path.is_symlink()
        or not stat.S_ISDIR(status.st_mode)
        or getattr(status, "st_file_attributes", 0) & reparse
    ):
        raise ValueError("queue root must be a directory")
    if os.name == "nt":
        if not _windows_owner_sid_allowed(path):
            raise PermissionError("queue root must be owner-only")
        protect_runtime_file(path)
        _set_windows_owner_acl(path)
    else:
        if status.st_uid != os.getuid():
            raise PermissionError("queue root must be owner-only")
        os.chmod(path, 0o700)


def _protect_file(path: Path) -> None:
    protect_runtime_file(path)
    if os.name == "nt":
        _set_windows_owner_acl(path)


def _set_windows_owner_acl(path: Path) -> None:
    import win32api
    import win32con
    import win32security

    token = win32security.OpenProcessToken(win32api.GetCurrentProcess(), win32con.TOKEN_QUERY)
    current = win32security.GetTokenInformation(token, win32security.TokenUser)[0]
    system = win32security.ConvertStringSidToSid("S-1-5-18")
    dacl = win32security.ACL()
    dacl.AddAccessAllowedAce(win32security.ACL_REVISION, win32con.GENERIC_ALL, current)
    dacl.AddAccessAllowedAce(win32security.ACL_REVISION, win32con.GENERIC_ALL, system)
    security = win32security.SECURITY_DESCRIPTOR()
    security.SetSecurityDescriptorDacl(True, dacl, False)
    security.SetSecurityDescriptorControl(win32security.SE_DACL_PROTECTED, win32security.SE_DACL_PROTECTED)
    win32security.SetFileSecurity(str(path), win32security.DACL_SECURITY_INFORMATION, security)


def _windows_owner_allowed(path: Path) -> bool:
    import win32api
    import win32con
    import win32security

    current = win32security.GetTokenInformation(
        win32security.OpenProcessToken(win32api.GetCurrentProcess(), win32con.TOKEN_QUERY),
        win32security.TokenUser,
    )[0]
    system = win32security.ConvertStringSidToSid("S-1-5-18")
    security = win32security.GetNamedSecurityInfo(
        str(path),
        win32security.SE_FILE_OBJECT,
        win32security.OWNER_SECURITY_INFORMATION | win32security.DACL_SECURITY_INFORMATION,
    )
    owner = security.GetSecurityDescriptorOwner()
    allowed = {
        win32security.ConvertSidToStringSid(current),
        win32security.ConvertSidToStringSid(system),
    }
    if win32security.ConvertSidToStringSid(owner) not in allowed:
        return False
    dacl = security.GetSecurityDescriptorDacl()
    if dacl is None:
        return False
    for index in range(dacl.GetAceCount()):
        ace = dacl.GetAce(index)
        if (
            ace[0][0] != win32security.ACCESS_ALLOWED_ACE_TYPE
            or win32security.ConvertSidToStringSid(ace[-1]) not in allowed
        ):
            return False
    return True


def _windows_owner_sid_allowed(path: Path) -> bool:
    import win32api
    import win32con
    import win32security

    current = win32security.GetTokenInformation(
        win32security.OpenProcessToken(win32api.GetCurrentProcess(), win32con.TOKEN_QUERY),
        win32security.TokenUser,
    )[0]
    system = win32security.ConvertStringSidToSid("S-1-5-18")
    security = win32security.GetNamedSecurityInfo(
        str(path), win32security.SE_FILE_OBJECT, win32security.OWNER_SECURITY_INFORMATION
    )
    owner = win32security.ConvertSidToStringSid(security.GetSecurityDescriptorOwner())
    return owner in {
        win32security.ConvertSidToStringSid(current),
        win32security.ConvertSidToStringSid(system),
    }


def _assert_root(path: Path, opened: os.stat_result, label: str = "queue root") -> None:
    current = path.lstat()
    reparse = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
    if (
        not os.path.samestat(opened, current)
        or not stat.S_ISDIR(current.st_mode)
        or getattr(current, "st_file_attributes", 0) & reparse
    ):
        raise ValueError(f"{label} changed")
    if os.name == "nt":
        if not _windows_owner_allowed(path):
            raise ValueError(f"{label} ACL changed")
    elif current.st_uid != os.getuid() or stat.S_IMODE(current.st_mode) != 0o700:
        raise ValueError(f"{label} ACL changed")


def _assert_private_directory(path: Path, label: str) -> None:
    status = path.lstat()
    _assert_root(path, status, label)


def _open_regular_file(path: Path, *, owner_only: bool) -> tuple[int, os.stat_result]:
    flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0)
    before = path.lstat()
    reparse = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
    if not stat.S_ISREG(before.st_mode) or getattr(before, "st_file_attributes", 0) & reparse:
        raise ValueError("invalid queue file")
    descriptor = os.open(path, flags)
    try:
        opened = os.fstat(descriptor)
        after = path.lstat()
        if (
            not stat.S_ISREG(opened.st_mode)
            or getattr(opened, "st_file_attributes", 0) & reparse
            or not os.path.samestat(before, opened)
            or not os.path.samestat(opened, after)
            or (owner_only and not _owner_only_descriptor(descriptor, opened))
        ):
            raise ValueError("invalid queue file")
        return descriptor, opened
    except BaseException:
        os.close(descriptor)
        raise


def _descriptor_digest(descriptor: int) -> tuple[int, str]:
    digest = hashlib.sha256()
    size = 0
    os.lseek(descriptor, 0, os.SEEK_SET)
    while True:
        chunk = os.read(descriptor, 1024 * 1024)
        if not chunk:
            return size, digest.hexdigest()
        size += len(chunk)
        digest.update(chunk)


def _copy_descriptor(descriptor: int, output: BinaryIO) -> tuple[int, str]:
    digest = hashlib.sha256()
    size = 0
    os.lseek(descriptor, 0, os.SEEK_SET)
    while True:
        chunk = os.read(descriptor, 1024 * 1024)
        if not chunk:
            return size, digest.hexdigest()
        output.write(chunk)
        size += len(chunk)
        digest.update(chunk)


def _assert_opened_path(path: Path, opened: os.stat_result) -> None:
    current = path.lstat()
    if not os.path.samestat(opened, current):
        raise ValueError("source MP4 changed before ownership transfer")


def _fsync_directory(path: Path) -> None:
    if os.name != "posix":
        return
    descriptor = os.open(path, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _private_temp(path: Path) -> tuple[Path, int]:
    temporary = path.with_name(f".{path.name}.{secrets.token_hex(8)}.new")
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        _protect_file(temporary)
    except BaseException:
        os.close(descriptor)
        temporary.unlink(missing_ok=True)
        raise
    return temporary, descriptor


def _atomic_bytes(path: Path, content: bytes) -> None:
    temporary, descriptor = _private_temp(path)
    try:
        with os.fdopen(descriptor, "wb") as output:
            descriptor = -1
            output.write(content)
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, path)
        _fsync_directory(path.parent)
    except BaseException:
        if descriptor >= 0:
            os.close(descriptor)
        temporary.unlink(missing_ok=True)
        raise


def _unique_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate queue sidecar key")
        result[key] = value
    return result


def _parse_utc(value: object) -> datetime:
    if not isinstance(value, str):
        raise ValueError("invalid queue timestamp")
    canonical_utc_text(value)
    return datetime.fromisoformat(value[:-1] + "+00:00")


def _metadata_from(value: object) -> ClipMetadata:
    if not isinstance(value, dict) or set(value) != {"camera_id", "started_at", "ended_at", "events"}:
        raise ValueError("invalid queue metadata")
    events_value = value["events"]
    if not isinstance(events_value, list):
        raise ValueError("invalid queue metadata")
    events: list[ClipEventMetadata] = []
    for event in events_value:
        if not isinstance(event, dict) or set(event) != {"event_type", "event_id", "occurred_at"}:
            raise ValueError("invalid queue metadata")
        events.append(ClipEventMetadata(**event))
    return ClipMetadata(
        camera_id=value["camera_id"],
        started_at=_parse_utc(value["started_at"]),
        ended_at=_parse_utc(value["ended_at"]),
        events=tuple(events),
    )


class _Item:
    def __init__(self, payload: dict[str, object], metadata: ClipMetadata) -> None:
        self.payload = payload
        self.metadata = metadata

    @property
    def released(self) -> bool:
        return self.payload["released"] is True


class ClipUploadQueue:
    def __init__(
        self,
        root: Path,
        client: SignedClipUploadClient,
        now: Callable[[], datetime],
        items: dict[str, _Item],
        root_status: os.stat_result,
        parent_status: os.stat_result,
    ) -> None:
        self._root = root
        self._client = client
        self._now = now
        self._items = items
        self._root_status = root_status
        self._parent_status = parent_status
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._in_flight: str | None = None
        self._closing = False
        self._closed = False

    @classmethod
    def open(
        cls,
        root: Path,
        client: SignedClipUploadClient,
        *,
        now: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> "ClipUploadQueue":
        root = Path(root)
        _protect_directory(root.parent)
        parent_status = root.parent.lstat()
        _assert_root(root.parent, parent_status, "queue parent")
        root.mkdir(parents=True, exist_ok=True)
        _protect_directory(root)
        root_status = root.lstat()
        _assert_root(root, root_status)
        for path in root.iterdir():
            if path.name.endswith(".new"):
                _assert_root(root, root_status)
                path.unlink(missing_ok=True)

        json_ids = {path.stem for path in root.glob("*.json") if _QUEUE_ID.fullmatch(path.stem)}
        mp4_ids = {path.stem for path in root.glob("*.mp4") if _QUEUE_ID.fullmatch(path.stem)}
        for queue_id in json_ids ^ mp4_ids:
            _assert_root(root, root_status)
            (root / f"{queue_id}.json").unlink(missing_ok=True)
            (root / f"{queue_id}.mp4").unlink(missing_ok=True)

        items: dict[str, _Item] = {}
        for queue_id in sorted(json_ids & mp4_ids):
            _assert_root(root, root_status)
            item = cls._read_item(root, queue_id)
            cls._verify_pair(root, queue_id, item)
            items[queue_id] = item
        if len(items) > _CAPACITY:
            raise ValueError("queue capacity exceeded on disk")
        _assert_root(root, root_status)
        return cls(root, client, now, items, root_status, parent_status)

    @staticmethod
    def _verify_pair(root: Path, queue_id: str, item: _Item) -> None:
        descriptor = ClipUploadQueue._open_verified_mp4(root, queue_id, item)
        os.close(descriptor)

    @staticmethod
    def _open_verified_mp4(root: Path, queue_id: str, item: _Item) -> int:
        descriptor, _ = _open_regular_file(root / f"{queue_id}.mp4", owner_only=True)
        try:
            size, digest = _descriptor_digest(descriptor)
            if size != item.payload["content_size"] or digest != item.payload["content_sha256"]:
                raise ValueError("queue MP4 integrity check failed")
            os.lseek(descriptor, 0, os.SEEK_SET)
            return descriptor
        except BaseException:
            os.close(descriptor)
            raise

    @staticmethod
    def _read_item(root: Path, queue_id: str) -> _Item:
        sidecar_path = root / f"{queue_id}.json"
        raw = _secure_read(sidecar_path, owner_only=True)
        try:
            payload = json.loads(raw, object_pairs_hook=_unique_object)
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise ValueError("invalid queue sidecar") from error
        expected = {
            "version", "queue_id", "remote_command_ids", "metadata", "created_at",
            "released", "released_at", "attempts", "next_attempt_at", "content_size", "content_sha256",
        }
        if not isinstance(payload, dict) or set(payload) != expected:
            raise ValueError("invalid queue sidecar")
        commands = payload["remote_command_ids"]
        if (
            payload["version"] != "PETCARE-HOME-QUEUE-V1"
            or payload["queue_id"] != queue_id
            or not isinstance(commands, list)
            or not commands
            or any(not isinstance(value, str) or _COMMAND_ID.fullmatch(value) is None for value in commands)
            or commands != sorted(set(commands))
            or type(payload["released"]) is not bool
            or type(payload["attempts"]) is not int
            or payload["attempts"] < 0
            or type(payload["content_size"]) is not int
            or payload["content_size"] <= 0
            or not isinstance(payload["content_sha256"], str)
            or _QUEUE_ID.fullmatch(payload["content_sha256"]) is None
        ):
            raise ValueError("invalid queue sidecar")
        _parse_utc(payload["created_at"])
        if payload["released"]:
            _parse_utc(payload["released_at"])
        elif payload["released_at"] is not None:
            raise ValueError("invalid queue sidecar")
        if payload["next_attempt_at"] is not None:
            _parse_utc(payload["next_attempt_at"])
        metadata = _metadata_from(payload["metadata"])
        canonical = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
        if raw != canonical:
            raise ValueError("queue sidecar must be canonical JSON")
        return _Item(payload, metadata)

    @property
    def depth(self) -> int:
        with self._lock:
            self._assert_root()
            return len(self._items)

    def enqueue_verified(
        self,
        queue_id: str,
        source_mp4: Path,
        metadata: ClipMetadata,
    ) -> str:
        if not isinstance(queue_id, str) or _QUEUE_ID.fullmatch(queue_id) is None:
            raise ValueError("invalid queue id")
        if not isinstance(metadata, ClipMetadata):
            raise TypeError("metadata must be ClipMetadata")
        remote_command_ids = metadata.remote_command_ids
        if (
            type(remote_command_ids) is not tuple
            or not remote_command_ids
            or any(not isinstance(value, str) or _COMMAND_ID.fullmatch(value) is None for value in remote_command_ids)
            or remote_command_ids != tuple(sorted(set(remote_command_ids)))
        ):
            raise ValueError("remote command ids must be canonical")
        source_mp4 = Path(source_mp4)
        try:
            _assert_private_directory(source_mp4.parent, "source parent")
            source_descriptor, source_status = _open_regular_file(source_mp4, owner_only=True)
        except (OSError, ValueError) as error:
            raise ValueError("source MP4 and parent must be owner-only regular paths") from error

        try:
            with self._lock:
                self._assert_accepting()
                self._assert_root()
                source_size, source_digest = _descriptor_digest(source_descriptor)
                if source_size <= 0:
                    raise ValueError("source MP4 must not be empty")
                if queue_id in self._items:
                    item = self._items[queue_id]
                    if (
                        item.metadata != metadata
                        or item.payload["remote_command_ids"] != list(remote_command_ids)
                        or item.payload["content_size"] != source_size
                        or item.payload["content_sha256"] != source_digest
                    ):
                        raise ValueError("queue id conflict")
                    _assert_opened_path(source_mp4, source_status)
                    if os.name == "nt":
                        os.close(source_descriptor)
                        source_descriptor = -1
                    source_mp4.unlink()
                    return queue_id
                if len(self._items) >= _CAPACITY:
                    raise ClipUploadQueueFull("clip upload queue is full")

                now = self._utc_now()
                payload: dict[str, object] = {
                    "attempts": 0,
                    "content_sha256": source_digest,
                    "content_size": source_size,
                    "created_at": utc_text(now),
                    "metadata": json.loads(metadata.canonical_json()),
                    "next_attempt_at": None,
                    "queue_id": queue_id,
                    "released": False,
                    "released_at": None,
                    "remote_command_ids": list(remote_command_ids),
                    "version": "PETCARE-HOME-QUEUE-V1",
                }
                sidecar = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
                mp4_path = self._root / f"{queue_id}.mp4"
                json_path = self._root / f"{queue_id}.json"
                mp4_temp, descriptor = _private_temp(mp4_path)
                json_temp: Path | None = None
                mp4_published = False
                published = False
                try:
                    os.lseek(source_descriptor, 0, os.SEEK_SET)
                    with os.fdopen(descriptor, "wb") as output:
                        descriptor = -1
                        copied_size, copied_digest = _copy_descriptor(source_descriptor, output)
                        output.flush()
                        os.fsync(output.fileno())
                    if (copied_size, copied_digest) != (source_size, source_digest):
                        raise ValueError("source MP4 changed during copy")
                    json_temp, json_descriptor = _private_temp(json_path)
                    with os.fdopen(json_descriptor, "wb") as output:
                        output.write(sidecar)
                        output.flush()
                        os.fsync(output.fileno())
                    _assert_opened_path(source_mp4, source_status)
                    os.replace(mp4_temp, mp4_path)
                    mp4_published = True
                    os.replace(json_temp, json_path)
                    published = True
                    _fsync_directory(self._root)
                    _assert_opened_path(source_mp4, source_status)
                    if os.name == "nt":
                        os.close(source_descriptor)
                        source_descriptor = -1
                    source_mp4.unlink()
                    self._items[queue_id] = _Item(payload, metadata)
                    return queue_id
                except BaseException:
                    if descriptor >= 0:
                        os.close(descriptor)
                    mp4_temp.unlink(missing_ok=True)
                    if json_temp is not None:
                        json_temp.unlink(missing_ok=True)
                    if mp4_published or published:
                        mp4_path.unlink(missing_ok=True)
                        json_path.unlink(missing_ok=True)
                        _fsync_directory(self._root)
                    raise
        finally:
            if source_descriptor >= 0:
                os.close(source_descriptor)

    def find_unreleased_by_command(self, command_id: str) -> str | None:
        if not isinstance(command_id, str) or _COMMAND_ID.fullmatch(command_id) is None:
            raise ValueError("invalid command id")
        with self._lock:
            self._assert_root()
            matches = [
                queue_id
                for queue_id, item in self._items.items()
                if not item.released and command_id in item.payload["remote_command_ids"]
            ]
        if len(matches) > 1:
            raise ValueError("command id belongs to multiple queue items")
        return matches[0] if matches else None

    def _unreleased_command_ids(self, queue_id: str) -> tuple[str, ...]:
        if not isinstance(queue_id, str) or _QUEUE_ID.fullmatch(queue_id) is None:
            raise ValueError("invalid queue id")
        with self._lock:
            self._assert_root()
            item = self._items.get(queue_id)
            if item is None or item.released:
                return ()
            return tuple(item.payload["remote_command_ids"])

    def _unreleased_queue_ids(self) -> tuple[str, ...]:
        with self._lock:
            self._assert_root()
            return tuple(sorted(queue_id for queue_id, item in self._items.items() if not item.released))

    def release(self, queue_id: str) -> None:
        if not isinstance(queue_id, str) or _QUEUE_ID.fullmatch(queue_id) is None:
            raise ValueError("invalid queue id")
        with self._lock:
            self._assert_accepting()
            self._assert_root()
            item = self._items.get(queue_id)
            if item is None:
                raise KeyError(queue_id)
            if item.released:
                return
            now = utc_text(self._utc_now())
            payload = dict(item.payload)
            payload["released"] = True
            payload["released_at"] = now
            payload["next_attempt_at"] = now
            self._write_payload(queue_id, payload)
            item.payload = payload

    def start(self) -> None:
        with self._lock:
            self._assert_accepting()
            if self._thread is not None and self._thread.is_alive():
                return
            self._stop.clear()
            self._thread = threading.Thread(target=self._run, name="clip-upload-queue", daemon=True)
            self._thread.start()

    def stop(self, *, timeout_seconds: float = 45.0) -> None:
        if timeout_seconds < 0:
            raise ValueError("timeout_seconds must be nonnegative")
        with self._lock:
            if self._closed:
                return
            self._closing = True
            self._stop.set()
            thread = self._thread
        first_error: BaseException | None = None
        if thread is not None:
            try:
                thread.join(timeout_seconds)
            except BaseException as error:
                first_error = error
            try:
                worker_alive = thread.is_alive()
            except BaseException as error:
                first_error = first_error or error
                worker_alive = True
            if worker_alive:
                if first_error is not None:
                    raise first_error
                raise RuntimeError("clip upload queue shutdown timed out")
        with self._lock:
            if not self._closed:
                self._closed = True
                try:
                    self._client.close()
                except BaseException as error:
                    first_error = first_error or error
        if first_error is not None:
            raise first_error

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                worked = self._expire_once() or self._process_once()
            except Exception:
                self._stop.wait(0.25)
                continue
            if not worked:
                self._stop.wait(0.25)

    def _expire_once(self) -> bool:
        now = self._utc_now()
        with self._lock:
            self._assert_root()
            for queue_id, item in sorted(self._items.items()):
                if item.released and now - _parse_utc(item.payload["released_at"]) >= _EXPIRY:
                    self._delete_item(queue_id)
                    return True
        return False

    def _process_once(self) -> bool:
        if self._expire_once():
            return True
        now = self._utc_now()
        with self._lock:
            self._assert_root()
            candidates = []
            for queue_id, item in self._items.items():
                due = item.payload["next_attempt_at"]
                if item.released and (due is None or _parse_utc(due) <= now):
                    candidates.append((item.payload["created_at"], queue_id, item))
            if not candidates:
                return False
            _, queue_id, item = min(candidates, key=lambda value: (value[0], value[1]))
            if self._in_flight is not None:
                return False
            metadata = item.metadata
            descriptor = self._open_verified_mp4(self._root, queue_id, item)
            self._in_flight = queue_id
        try:
            with os.fdopen(descriptor, "rb") as source:
                self._client.upload_open_file(
                    source,
                    size=int(item.payload["content_size"]),
                    content_digest=base64.urlsafe_b64encode(
                        bytes.fromhex(str(item.payload["content_sha256"]))
                    ).decode("ascii").rstrip("="),
                    metadata=metadata,
                )
        except Exception:
            with self._lock:
                current = self._items.get(queue_id)
                if current is not None:
                    attempts = int(current.payload["attempts"]) + 1
                    payload = dict(current.payload)
                    payload["attempts"] = attempts
                    delay = _RETRY_SECONDS[min(attempts - 1, len(_RETRY_SECONDS) - 1)]
                    payload["next_attempt_at"] = utc_text(self._utc_now() + timedelta(seconds=delay))
                    self._write_payload(queue_id, payload)
                    current.payload = payload
            return True
        else:
            with self._lock:
                if queue_id in self._items:
                    self._delete_item(queue_id)
            return True
        finally:
            with self._lock:
                self._in_flight = None

    def _write_payload(self, queue_id: str, payload: dict[str, object]) -> None:
        self._assert_root()
        content = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
        _atomic_bytes(self._root / f"{queue_id}.json", content)

    def _delete_item(self, queue_id: str) -> None:
        self._assert_root()
        (self._root / f"{queue_id}.mp4").unlink(missing_ok=True)
        (self._root / f"{queue_id}.json").unlink(missing_ok=True)
        _fsync_directory(self._root)
        del self._items[queue_id]

    def _utc_now(self) -> datetime:
        value = self._now()
        if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("queue clock must be timezone-aware")
        return value.astimezone(UTC)

    def _assert_root(self) -> None:
        _assert_root(self._root.parent, self._parent_status, "queue parent")
        _assert_root(self._root, self._root_status)

    def _assert_accepting(self) -> None:
        if self._closing or self._closed:
            raise RuntimeError("clip upload queue is closed")
