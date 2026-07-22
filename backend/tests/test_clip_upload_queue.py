from __future__ import annotations

import json
import hashlib
import inspect
import os
import threading
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from app.clip_contracts import ClipEventMetadata, ClipMetadata
from app.clip_upload_queue import ClipUploadQueue, ClipUploadQueueFull
from app.agent_config import protect_runtime_file


COMMANDS = (
    "11111111111111111111111111111111",
    "22222222222222222222222222222222",
)


class Clock:
    def __init__(self) -> None:
        self.value = datetime(2026, 7, 21, 0, 0, tzinfo=UTC)

    def __call__(self) -> datetime:
        return self.value

    def advance(self, seconds: int) -> None:
        self.value += timedelta(seconds=seconds)


class UploadClient:
    def __init__(self, failures: int = 0) -> None:
        self.failures = failures
        self.calls: list[tuple[bytes, ClipMetadata]] = []
        self.handles: list[object] = []
        self.close_calls = 0

    def close(self) -> None:
        self.close_calls += 1

    def upload(self, path: Path, metadata: ClipMetadata) -> object:
        raise AssertionError("queue must not reopen a verified MP4 by path")

    def upload_open_file(
        self,
        source: object,
        *,
        size: int,
        content_digest: str,
        metadata: ClipMetadata,
    ) -> object:
        assert source.tell() == 0  # type: ignore[attr-defined]
        body = source.read()  # type: ignore[attr-defined]
        assert len(body) == size
        self.handles.append(source)
        self.calls.append((body, metadata))
        if len(self.calls) <= self.failures:
            raise RuntimeError("offline")
        return object()


def metadata(commands: tuple[str, ...] = COMMANDS) -> ClipMetadata:
    return ClipMetadata(
        camera_id="pc-webcam-01",
        started_at=datetime(2026, 7, 20, 23, 59, 50, tzinfo=UTC),
        ended_at=datetime(2026, 7, 21, 0, 0, 20, tzinfo=UTC),
        events=(
            ClipEventMetadata(
                event_type="eating",
                event_id=41,
                occurred_at="2026-07-20T23:59:59.000000Z",
            ),
        ),
        remote_command_ids=commands,
    )


def enqueue(
    queue: ClipUploadQueue,
    source: Path,
    number: int = 1,
    commands: tuple[str, ...] = COMMANDS,
) -> str:
    import app.clip_upload_queue as module

    source.write_bytes(f"mp4-{number}".encode())
    module._protect_file(source)
    queue_id = f"{number:064x}"
    return queue.enqueue_verified(
        queue_id,
        source,
        metadata(commands),
    )


def test_queue_exposes_the_frozen_task6_public_api() -> None:
    assert tuple(inspect.signature(ClipUploadQueue.enqueue_verified).parameters) == (
        "self",
        "queue_id",
        "source_mp4",
        "metadata",
    )
    assert not hasattr(ClipUploadQueue, "unreleased_command_ids")
    assert not hasattr(ClipUploadQueue, "unreleased_queue_ids")


def test_enqueue_takes_ownership_only_after_private_durable_pair_and_requires_release(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import app.clip_upload_queue as module

    protected_empty_files: list[Path] = []
    source = tmp_path / "source.mp4"
    protect = module._protect_file

    def protect_before_write(path: Path) -> None:
        if path == source:
            protect(path)
            return
        assert path.stat().st_size == 0
        protected_empty_files.append(path)

    monkeypatch.setattr(module, "_protect_file", protect_before_write)
    client = UploadClient()
    clock = Clock()
    queue = ClipUploadQueue.open(tmp_path / "queue", client, now=clock)

    queue_id = enqueue(queue, source)

    assert not source.exists()
    assert queue.depth == 1
    assert len(protected_empty_files) == 2
    sidecar = json.loads((tmp_path / "queue" / f"{queue_id}.json").read_text(encoding="utf-8"))
    assert sidecar == {
        "attempts": 0,
        "created_at": "2026-07-21T00:00:00.000000Z",
        "content_sha256": hashlib.sha256(b"mp4-1").hexdigest(),
        "content_size": len(b"mp4-1"),
        "metadata": json.loads(metadata().canonical_json()),
        "next_attempt_at": None,
        "queue_id": queue_id,
        "released": False,
        "released_at": None,
        "remote_command_ids": list(COMMANDS),
        "version": "PETCARE-HOME-QUEUE-V1",
    }
    assert queue.find_unreleased_by_command(COMMANDS[0]) == queue_id
    assert queue._unreleased_command_ids(queue_id) == COMMANDS
    assert queue._unreleased_queue_ids() == (queue_id,)

    clock.advance(3601)
    assert queue._process_once() is False
    assert client.calls == []
    assert queue.depth == 1


def test_queue_counts_all_entries_and_saturation_leaves_source_owned_by_caller(tmp_path: Path) -> None:
    queue = ClipUploadQueue.open(tmp_path / "queue", UploadClient())
    for number in range(1, 9):
        enqueue(queue, tmp_path / f"source-{number}.mp4", number, (f"{number:032x}",))

    source = tmp_path / "ninth.mp4"
    source.write_bytes(b"caller-owned")
    import app.clip_upload_queue as module
    module._protect_file(source)
    with pytest.raises(ClipUploadQueueFull):
        queue.enqueue_verified(f"{9:064x}", source, metadata((f"{9:032x}",)))

    assert source.read_bytes() == b"caller-owned"
    assert queue.depth == 8


def test_release_is_idempotent_and_successful_upload_deletes_pair(tmp_path: Path) -> None:
    client = UploadClient()
    queue = ClipUploadQueue.open(tmp_path / "queue", client)
    queue_id = enqueue(queue, tmp_path / "source.mp4")

    queue.release(queue_id)
    queue.release(queue_id)

    assert queue.find_unreleased_by_command(COMMANDS[0]) is None
    assert queue._unreleased_command_ids(queue_id) == ()
    assert queue._unreleased_command_ids("f" * 64) == ()
    assert queue._unreleased_queue_ids() == ()
    assert queue._process_once() is True
    assert len(client.calls) == 1
    assert client.calls[0][1] == metadata()
    assert client.handles[0].closed  # type: ignore[attr-defined]
    assert queue.depth == 0
    assert list((tmp_path / "queue").glob(f"{queue_id}.*")) == []


def test_upload_success_delete_crash_retries_identical_logical_clip(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    client = UploadClient()
    queue = ClipUploadQueue.open(tmp_path / "queue", client)
    queue_id = enqueue(queue, tmp_path / "source.mp4")
    queue.release(queue_id)
    original_delete = ClipUploadQueue._delete_item
    delete_calls = 0

    def crash_once(worker: ClipUploadQueue, current_queue_id: str) -> None:
        nonlocal delete_calls
        delete_calls += 1
        if delete_calls == 1:
            raise OSError("crash after upload success")
        original_delete(worker, current_queue_id)

    monkeypatch.setattr(ClipUploadQueue, "_delete_item", crash_once)

    with pytest.raises(OSError, match="crash after upload success"):
        queue._process_once()

    assert queue.depth == 1
    assert queue._process_once() is True
    assert client.calls == [(b"mp4-1", metadata()), (b"mp4-1", metadata())]
    assert queue.depth == 0


def test_failed_release_persistence_keeps_item_unreleased(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import app.clip_upload_queue as module

    client = UploadClient()
    queue = ClipUploadQueue.open(tmp_path / "queue", client)
    queue_id = enqueue(queue, tmp_path / "source.mp4")
    monkeypatch.setattr(module, "_atomic_bytes", lambda *_args: (_ for _ in ()).throw(OSError("disk")))

    with pytest.raises(OSError, match="disk"):
        queue.release(queue_id)

    assert queue.find_unreleased_by_command(COMMANDS[0]) == queue_id
    assert queue._unreleased_command_ids(queue_id) == COMMANDS
    assert queue._process_once() is False
    assert client.calls == []


def test_worker_survives_transient_queue_exception(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    queue = ClipUploadQueue.open(tmp_path / "queue", UploadClient())
    calls = 0

    def flaky_expire(worker: ClipUploadQueue) -> bool:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise OSError("transient disk failure")
        worker._stop.set()
        return True

    monkeypatch.setattr(ClipUploadQueue, "_expire_once", flaky_expire)

    queue._run()

    assert calls == 2


def test_stop_closes_owned_client_once_after_worker_stops(tmp_path: Path) -> None:
    client = UploadClient()
    queue = ClipUploadQueue.open(tmp_path / "queue", client)
    queue_id = enqueue(queue, tmp_path / "queued.mp4")
    queue.start()
    thread = queue._thread
    assert thread is not None
    original_close = client.close

    def close_after_worker() -> None:
        assert not thread.is_alive()
        original_close()

    client.close = close_after_worker  # type: ignore[method-assign]
    queue.stop(timeout_seconds=1)
    queue.stop(timeout_seconds=1)

    assert client.close_calls == 1
    with pytest.raises(RuntimeError, match="queue is closed"):
        queue.start()
    with pytest.raises(RuntimeError, match="queue is closed"):
        enqueue(queue, tmp_path / "after-stop.mp4")
    with pytest.raises(RuntimeError, match="queue is closed"):
        queue.release(queue_id)


def test_stop_timeout_leaves_client_open_until_worker_has_stopped(tmp_path: Path) -> None:
    class Thread:
        alive = True

        def join(self, timeout: float) -> None:
            pass

        def is_alive(self) -> bool:
            return self.alive

    client = UploadClient()
    queue = ClipUploadQueue.open(tmp_path / "queue", client)
    thread = Thread()
    queue._thread = thread  # type: ignore[assignment]

    with pytest.raises(RuntimeError, match="shutdown timed out"):
        queue.stop(timeout_seconds=0)
    assert client.close_calls == 0
    with pytest.raises(RuntimeError, match="queue is closed"):
        queue.start()
    with pytest.raises(RuntimeError, match="queue is closed"):
        enqueue(queue, tmp_path / "while-closing.mp4")

    thread.alive = False
    queue.stop(timeout_seconds=0)
    assert client.close_calls == 1


def test_stop_preserves_worker_failure_after_attempting_client_cleanup(tmp_path: Path) -> None:
    class Thread:
        def join(self, timeout: float) -> None:
            raise OSError("join failed")

        def is_alive(self) -> bool:
            return False

    class Client(UploadClient):
        def close(self) -> None:
            super().close()
            raise RuntimeError("close failed")

    client = Client()
    queue = ClipUploadQueue.open(tmp_path / "queue", client)
    queue._thread = Thread()  # type: ignore[assignment]

    with pytest.raises(OSError, match="join failed"):
        queue.stop(timeout_seconds=0)
    queue.stop(timeout_seconds=0)

    assert client.close_calls == 1


def test_stop_blocks_concurrent_start_and_enqueue_before_join_returns(tmp_path: Path) -> None:
    class BlockingWorker:
        def __init__(self) -> None:
            self.join_started = threading.Event()
            self.release_join = threading.Event()
            self.alive = True

        def join(self, timeout: float) -> None:
            self.join_started.set()
            assert self.release_join.wait(timeout)
            self.alive = False

        def is_alive(self) -> bool:
            return self.alive

    client = UploadClient()
    queue = ClipUploadQueue.open(tmp_path / "queue", client)
    worker = BlockingWorker()
    queue._thread = worker  # type: ignore[assignment]
    stop_error: list[BaseException] = []

    def stop() -> None:
        try:
            queue.stop(timeout_seconds=1)
        except BaseException as error:
            stop_error.append(error)

    stopper = threading.Thread(target=stop)
    stopper.start()
    assert worker.join_started.wait(1)
    with pytest.raises(RuntimeError, match="queue is closed"):
        queue.start()
    with pytest.raises(RuntimeError, match="queue is closed"):
        enqueue(queue, tmp_path / "during-stop.mp4")
    worker.release_join.set()
    stopper.join(1)

    assert not stopper.is_alive()
    assert stop_error == []
    assert client.close_calls == 1


def test_retry_schedule_is_exact_and_capped_at_ten_minutes(tmp_path: Path) -> None:
    clock = Clock()
    client = UploadClient(failures=5)
    queue = ClipUploadQueue.open(tmp_path / "queue", client, now=clock)
    queue_id = enqueue(queue, tmp_path / "source.mp4")
    queue.release(queue_id)

    for delay, expected_calls in ((0, 1), (4, 1), (1, 2), (29, 2), (1, 3), (119, 3), (1, 4), (599, 4), (1, 5)):
        clock.advance(delay)
        queue._process_once()
        assert len(client.calls) == expected_calls

    sidecar = json.loads((tmp_path / "queue" / f"{queue_id}.json").read_text(encoding="utf-8"))
    assert sidecar["attempts"] == 5
    assert sidecar["next_attempt_at"] == "2026-07-21T00:22:35.000000Z"


def test_released_item_expires_at_one_hour_without_upload(tmp_path: Path) -> None:
    clock = Clock()
    client = UploadClient()
    queue = ClipUploadQueue.open(tmp_path / "queue", client, now=clock)
    queue_id = enqueue(queue, tmp_path / "source.mp4")
    queue.release(queue_id)

    clock.advance(3599)
    assert queue.depth == 1
    clock.advance(1)
    assert queue._expire_once() is True

    assert client.calls == []
    assert queue.depth == 0


def test_restart_recovers_exact_pairs_and_removes_temp_or_orphan_files(tmp_path: Path) -> None:
    root = tmp_path / "queue"
    first = ClipUploadQueue.open(root, UploadClient())
    queue_id = enqueue(first, tmp_path / "source.mp4")
    (root / ".interrupted.new").write_bytes(b"temp")
    (root / f"{2:064x}.mp4").write_bytes(b"orphan")
    (root / f"{3:064x}.json").write_text("{}", encoding="utf-8")

    recovered = ClipUploadQueue.open(root, UploadClient())

    assert recovered.depth == 1
    assert recovered.find_unreleased_by_command(COMMANDS[1]) == queue_id
    assert sorted(path.name for path in root.iterdir()) == [f"{queue_id}.json", f"{queue_id}.mp4"]


def test_restart_verifies_both_recovered_files_are_private_regular_files(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import app.clip_upload_queue as module

    root = tmp_path / "queue"
    first = ClipUploadQueue.open(root, UploadClient())
    queue_id = enqueue(first, tmp_path / "source.mp4")
    checked: list[Path] = []
    original = module._open_regular_file
    secure_read = module._secure_read

    def checked_open(path: Path, *, owner_only: bool):
        if owner_only:
            checked.append(path)
        return original(path, owner_only=owner_only)

    monkeypatch.setattr(module, "_open_regular_file", checked_open)
    monkeypatch.setattr(
        module,
        "_secure_read",
        lambda path, *, owner_only: checked.append(path) or secure_read(path, owner_only=owner_only),
    )

    ClipUploadQueue.open(root, UploadClient())

    assert checked == [root / f"{queue_id}.json", root / f"{queue_id}.mp4"]


def test_restart_rejects_mp4_bytes_changed_after_verified_enqueue(tmp_path: Path) -> None:
    root = tmp_path / "queue"
    first = ClipUploadQueue.open(root, UploadClient())
    queue_id = enqueue(first, tmp_path / "source.mp4")
    (root / f"{queue_id}.mp4").write_bytes(b"changed")

    with pytest.raises(ValueError, match="queue MP4 integrity"):
        ClipUploadQueue.open(root, UploadClient())


def test_source_path_swap_does_not_delete_replacement_or_publish_pair(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import app.clip_upload_queue as module

    queue = ClipUploadQueue.open(tmp_path / "queue", UploadClient())
    source = tmp_path / "source.mp4"
    source.write_bytes(b"verified")
    module._protect_file(source)
    moved = tmp_path / "moved.mp4"
    replacement = tmp_path / "replacement.mp4"
    replacement.write_bytes(b"do-not-delete")
    module._protect_file(replacement)
    protect = module._protect_file
    swapped = False

    def swap_after_source_open(path: Path) -> None:
        nonlocal swapped
        protect(path)
        if not swapped:
            source.replace(moved)
            replacement.replace(source)
            swapped = True

    monkeypatch.setattr(module, "_protect_file", swap_after_source_open)

    with pytest.raises((ValueError, PermissionError)):
        queue.enqueue_verified("1" * 64, source, metadata())

    if moved.exists():
        assert source.read_bytes() == b"do-not-delete"
        assert moved.read_bytes() == b"verified"
    else:
        assert source.read_bytes() == b"verified"
        assert replacement.read_bytes() == b"do-not-delete"
    assert queue.depth == 0
    assert list((tmp_path / "queue").iterdir()) == []


def test_root_identity_swap_fails_closed_before_public_mutation(tmp_path: Path) -> None:
    root = tmp_path / "queue"
    queue = ClipUploadQueue.open(root, UploadClient())
    moved = tmp_path / "original-queue"
    root.replace(moved)
    root.mkdir()
    source = tmp_path / "source.mp4"
    source.write_bytes(b"caller-owned")
    import app.clip_upload_queue as module
    module._protect_file(source)

    with pytest.raises(ValueError, match="queue root changed"):
        queue.enqueue_verified("1" * 64, source, metadata())

    assert source.read_bytes() == b"caller-owned"
    assert list(root.iterdir()) == []
    assert list(moved.iterdir()) == []


def test_root_identity_swap_fails_closed_for_recovery_lookups(tmp_path: Path) -> None:
    root = tmp_path / "queue"
    queue = ClipUploadQueue.open(root, UploadClient())
    enqueue(queue, tmp_path / "source.mp4")
    root.replace(tmp_path / "original-queue")
    root.mkdir()

    with pytest.raises(ValueError, match="queue root changed"):
        queue.find_unreleased_by_command(COMMANDS[0])
    with pytest.raises(ValueError, match="queue root changed"):
        queue._unreleased_queue_ids()


def test_enqueue_disk_failure_cleans_queue_and_preserves_caller_source(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import app.clip_upload_queue as module

    queue = ClipUploadQueue.open(tmp_path / "queue", UploadClient())
    source = tmp_path / "source.mp4"
    source.write_bytes(b"caller-owned")
    module._protect_file(source)
    monkeypatch.setattr(module.os, "replace", lambda *_args: (_ for _ in ()).throw(OSError("disk")))

    with pytest.raises(OSError, match="disk"):
        queue.enqueue_verified("1" * 64, source, metadata())

    assert source.read_bytes() == b"caller-owned"
    assert queue.depth == 0
    assert list((tmp_path / "queue").iterdir()) == []


def test_second_pair_rename_failure_removes_first_published_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import app.clip_upload_queue as module

    queue = ClipUploadQueue.open(tmp_path / "queue", UploadClient())
    source = tmp_path / "source.mp4"
    source.write_bytes(b"caller-owned")
    module._protect_file(source)
    replace = module.os.replace
    calls = 0

    def fail_second(source_path: Path, destination_path: Path) -> None:
        nonlocal calls
        calls += 1
        if calls == 2:
            raise OSError("sidecar rename")
        replace(source_path, destination_path)

    monkeypatch.setattr(module.os, "replace", fail_second)

    with pytest.raises(OSError, match="sidecar rename"):
        queue.enqueue_verified("1" * 64, source, metadata())

    assert source.read_bytes() == b"caller-owned"
    assert queue.depth == 0
    assert list((tmp_path / "queue").iterdir()) == []


def test_enqueue_rejects_unprotected_source_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import app.clip_upload_queue as module

    queue = ClipUploadQueue.open(tmp_path / "queue", UploadClient())
    source = tmp_path / "source.mp4"
    source.write_bytes(b"unprotected")
    if os.name == "nt":
        monkeypatch.setattr(module, "_owner_only_descriptor", lambda *_args: False)
    else:
        source.chmod(0o644)

    with pytest.raises(ValueError, match="owner-only"):
        queue.enqueue_verified("1" * 64, source, metadata())

    assert source.read_bytes() == b"unprotected"


def test_existing_runtime_file_protection_is_accepted_as_queue_source(tmp_path: Path) -> None:
    queue = ClipUploadQueue.open(tmp_path / "queue", UploadClient())
    source = tmp_path / "source.mp4"
    source.write_bytes(b"protected")
    protect_runtime_file(source)

    queue.enqueue_verified("1" * 64, source, metadata())

    assert not source.exists()
    assert queue.depth == 1


def test_enqueue_rejects_source_under_unprotected_parent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import app.clip_upload_queue as module

    queue = ClipUploadQueue.open(tmp_path / "queue", UploadClient())
    source_dir = tmp_path / "source-private"
    source_dir.mkdir()
    module._protect_directory(source_dir)
    source = source_dir / "source.mp4"
    source.write_bytes(b"protected")
    module._protect_file(source)
    allowed = module._windows_owner_allowed
    if os.name == "nt":
        monkeypatch.setattr(
            module,
            "_windows_owner_allowed",
            lambda path: False if path == source.parent else allowed(path),
        )
    else:
        source.parent.chmod(0o755)

    with pytest.raises(ValueError, match="owner-only"):
        queue.enqueue_verified("1" * 64, source, metadata())

    assert source.read_bytes() == b"protected"


def test_enqueue_rejects_reparse_source_parent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import app.clip_upload_queue as module

    queue = ClipUploadQueue.open(tmp_path / "queue", UploadClient())
    source_dir = tmp_path / "source-private"
    source_dir.mkdir()
    module._protect_directory(source_dir)
    source = source_dir / "source.mp4"
    source.write_bytes(b"protected")
    module._protect_file(source)
    lstat = Path.lstat

    class ReparseStatus:
        def __init__(self, status: os.stat_result) -> None:
            self._status = status
            self.st_file_attributes = getattr(status, "st_file_attributes", 0) | 0x400

        def __getattr__(self, name: str) -> object:
            return getattr(self._status, name)

    monkeypatch.setattr(
        Path,
        "lstat",
        lambda path: ReparseStatus(lstat(path)) if path == source_dir else lstat(path),
    )

    with pytest.raises(ValueError, match="owner-only"):
        queue.enqueue_verified("1" * 64, source, metadata())

    assert source.read_bytes() == b"protected"


@pytest.mark.parametrize(
    ("queue_id", "commands"),
    [
        ("A" * 64, COMMANDS),
        ("1" * 63, COMMANDS),
        ("1" * 64, ()),
        ("1" * 64, (COMMANDS[0], COMMANDS[0])),
        ("1" * 64, tuple(reversed(COMMANDS))),
        ("1" * 64, ("A" * 32,)),
    ],
)
def test_enqueue_rejects_noncanonical_identity_without_touching_source(
    tmp_path: Path, queue_id: str, commands: tuple[str, ...]
) -> None:
    queue = ClipUploadQueue.open(tmp_path / "queue", UploadClient())
    source = tmp_path / "source.mp4"
    source.write_bytes(b"caller-owned")

    with pytest.raises(ValueError):
        queue.enqueue_verified(queue_id, source, metadata(commands))

    assert source.read_bytes() == b"caller-owned"
    assert queue.depth == 0


def test_private_posix_modes_are_enforced_when_supported(tmp_path: Path) -> None:
    if os.name != "posix":
        pytest.skip("POSIX mode assertion")
    root = tmp_path / "queue"
    queue = ClipUploadQueue.open(root, UploadClient())
    queue_id = enqueue(queue, tmp_path / "source.mp4")

    assert root.stat().st_mode & 0o777 == 0o700
    assert (root / f"{queue_id}.mp4").stat().st_mode & 0o777 == 0o600
    assert (root / f"{queue_id}.json").stat().st_mode & 0o777 == 0o600
