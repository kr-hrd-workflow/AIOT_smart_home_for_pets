from __future__ import annotations

import hashlib
import subprocess
import threading
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.clip_contracts import ClipDeliveryIdentity, ClipEventMetadata, ClipIntent, utc_text
from app.clip_delivery import ClipAdmissionWorker, ClipDeliveryWorker
from app.jetson_client import JetsonClientError
from app.jetson_contracts import JetsonClipHeaders, JetsonClipReceipt, JetsonPutResult


NOW = datetime(2026, 7, 21, 3, 0, tzinfo=UTC)
COMMAND = "1" * 32
BOOT = "2" * 32
BODY = b"validated mp4 bytes"
DIGEST = hashlib.sha256(BODY).hexdigest()


def intent(**changes: object) -> ClipIntent:
    value = ClipIntent(
        outbox_id=7,
        event_type="eating",
        event_id=41,
        occurred_at=NOW - timedelta(seconds=1),
        created_at=NOW,
        deadline_at=NOW + timedelta(seconds=3),
        attempts=0,
        remote_boot_id=None,
        remote_command_id=None,
        accepted_at=None,
    )
    return replace(value, **changes)


class Repository:
    def __init__(self, row: ClipIntent | None) -> None:
        self.row = row
        self.calls: list[tuple[object, ...]] = []
        self.processed = False
        self.put_started = bool(row and row.remote_command_id is not None and row.attempts > 0)
        self.renew_results: list[bool] = []
        self.identity = ClipDeliveryIdentity(
            events=(ClipEventMetadata("eating", 41, "2026-07-21T02:59:59.000000Z"),),
            remote_command_ids=(COMMAND,),
            accepted_at=(NOW + timedelta(seconds=1),),
        )

    def claim_unaccepted(self, now: datetime, lease_until: datetime) -> ClipIntent | None:
        self.calls.append(("claim_unaccepted", now, lease_until))
        return self.row if self.row and self.row.accepted_at is None else None

    def persist_command(self, outbox_id: int, command_id: str) -> ClipIntent:
        self.calls.append(("persist_command", outbox_id, command_id))
        assert self.row is not None
        self.row = replace(self.row, remote_command_id=command_id)
        return self.row

    def record_acceptance(
        self, outbox_id: int, boot_id: str, command_id: str, accepted_at: datetime
    ) -> None:
        self.calls.append(("record_acceptance", outbox_id, boot_id, command_id, accepted_at))

    def mark_put_started(self, outbox_id: int, started_at: datetime) -> bool:
        self.calls.append(("mark_put_started", outbox_id, started_at))
        first = not self.put_started
        self.put_started = True
        return first

    def defer_admission(self, outbox_id: int, next_attempt_at: datetime, error: str) -> None:
        self.calls.append(("defer_admission", outbox_id, next_attempt_at, error))

    def mark_terminal(self, outbox_id: int, reason: str, processed_at: datetime, error: str) -> None:
        self.calls.append(("mark_terminal", outbox_id, reason, processed_at, error))

    def claim_accepted(self, now: datetime, lease_until: datetime) -> ClipIntent | None:
        self.calls.append(("claim_accepted", now, lease_until))
        return self.row if self.row and self.row.accepted_at is not None else None

    def defer_delivery(self, outbox_id: int, next_attempt_at: datetime, error: str) -> None:
        self.calls.append(("defer_delivery", outbox_id, next_attempt_at, error))

    def renew_delivery(
        self, outbox_id: int, expected_lease_until: datetime, lease_until: datetime
    ) -> datetime | None:
        self.calls.append(("renew_delivery", outbox_id, expected_lease_until, lease_until))
        return lease_until if not self.renew_results or self.renew_results.pop(0) else None

    def resolve_accepted_clip(
        self, command_id: str, boot_id: str, canonical_events: str
    ) -> ClipDeliveryIdentity | None:
        self.calls.append(("resolve_accepted_clip", command_id, boot_id, canonical_events))
        return self.identity

    def command_processed(self, command_id: str) -> bool:
        self.calls.append(("command_processed", command_id))
        return self.processed

    def mark_commands_processed(self, command_ids: tuple[str, ...], processed_at: datetime) -> None:
        self.calls.append(("mark_commands_processed", command_ids, processed_at))
        self.processed = True

    def reset_command_for_readmission(
        self, outbox_id: int, expected_command_id: str, next_attempt_at: datetime
    ) -> None:
        self.calls.append(("reset_command_for_readmission", outbox_id, expected_command_id, next_attempt_at))


class Jetson:
    def __init__(self) -> None:
        self.put_results: list[object] = []
        self.download_result: object = headers()
        self.delete_result: object = 204
        self.puts: list[tuple[str, object, bool]] = []
        self.downloads: list[tuple[str, Path]] = []
        self.deletes: list[str] = []

    def put_clip(self, command_id: str, command: object, *, first: bool = True) -> JetsonPutResult:
        self.puts.append((command_id, command, first))
        result = self.put_results.pop(0) if self.put_results else JetsonPutResult(
            status_code=201 if first else 200,
            receipt=JetsonClipReceipt(
                accepted_boot_id=BOOT,
                command_id=command_id,
                state="recording",
                accepted_at=utc_text(NOW + timedelta(seconds=1)),
            ),
        )
        if isinstance(result, BaseException):
            raise result
        return result  # type: ignore[return-value]

    def download_clip(self, command_id: str, destination: Path) -> JetsonClipHeaders:
        self.downloads.append((command_id, destination))
        if isinstance(self.download_result, BaseException):
            raise self.download_result
        destination.write_bytes(BODY)
        return self.download_result  # type: ignore[return-value]

    def delete_clip(self, command_id: str) -> int:
        self.deletes.append(command_id)
        if isinstance(self.delete_result, BaseException):
            raise self.delete_result
        return self.delete_result  # type: ignore[return-value]


class Queue:
    def __init__(self) -> None:
        self.found: str | None = None
        self.command_ids = (COMMAND,)
        self.queue_ids: tuple[str, ...] = ()
        self.error: Exception | None = None
        self.release_error: Exception | None = None
        self.calls: list[tuple[object, ...]] = []

    def find_unreleased_by_command(self, command_id: str) -> str | None:
        self.calls.append(("find", command_id))
        return self.found

    def _unreleased_command_ids(self, queue_id: str) -> tuple[str, ...]:
        self.calls.append(("command_ids", queue_id))
        return self.command_ids

    def _unreleased_queue_ids(self) -> tuple[str, ...]:
        self.calls.append(("queue_ids",))
        return self.queue_ids

    def enqueue_verified(
        self,
        queue_id: str,
        source_mp4: Path,
        metadata: object,
    ) -> str:
        self.calls.append(("enqueue", queue_id, source_mp4, metadata))
        if self.error:
            raise self.error
        source_mp4.unlink()
        return queue_id

    def release(self, queue_id: str) -> None:
        self.calls.append(("release", queue_id))
        if self.release_error:
            raise self.release_error


def headers(**changes: object) -> JetsonClipHeaders:
    values = {
        "boot_id": BOOT,
        "command_id": COMMAND,
        "content_sha256": DIGEST,
        "started_at": utc_text(NOW - timedelta(seconds=10)),
        "ended_at": utc_text(NOW + timedelta(seconds=20)),
        "events": "eating:41",
        "frame_count": 300,
        "video_codec": "h264",
        "pixel_format": "yuv420p",
    }
    values.update(changes)
    return JetsonClipHeaders(**values)


def ffprobe_result(**stream_changes: object) -> subprocess.CompletedProcess[str]:
    stream = {
        "codec_name": "h264",
        "pix_fmt": "yuv420p",
        "width": 640,
        "height": 480,
        "r_frame_rate": "10/1",
        "nb_frames": "300",
    }
    stream.update(stream_changes)
    return subprocess.CompletedProcess(
        [], 0, stdout=__import__("json").dumps({"streams": [stream], "format": {"duration": "30.000000"}}), stderr=""
    )


def delivery(
    tmp_path: Path,
    repo: Repository,
    jetson: Jetson,
    queue: Queue,
    *,
    run=lambda *args, **kwargs: ffprobe_result(),
    now=lambda: NOW + timedelta(seconds=2),
    wait=None,
) -> ClipDeliveryWorker:
    probe = (tmp_path / "ffprobe.exe").resolve()
    probe.write_bytes(b"")
    return ClipDeliveryWorker(
        repo, jetson, queue, work_dir=tmp_path, ffprobe_path=probe, now=now, run=run, wait=wait
    )


def test_admission_persists_command_and_distinguishes_first_201_from_replay_200() -> None:
    repo = Repository(intent())
    jetson = Jetson()
    worker = ClipAdmissionWorker(repo, jetson, now=lambda: NOW, command_id=lambda: COMMAND)

    assert worker.dispatch_once() is True
    assert repo.calls[0] == ("claim_unaccepted", NOW, NOW + timedelta(seconds=1))
    assert jetson.puts[0][2] is True
    assert repo.calls[1] == ("persist_command", 7, COMMAND)
    assert repo.calls[2] == ("mark_put_started", 7, NOW)
    assert repo.calls[-1][0] == "record_acceptance"

    replay_repo = Repository(intent(remote_command_id=COMMAND, attempts=1))
    replay_jetson = Jetson()
    replay_jetson.put_results = [
        JetsonPutResult(
            status_code=200,
            receipt=JetsonClipReceipt(
                accepted_boot_id=BOOT,
                command_id=COMMAND,
                state="recording",
                accepted_at=utc_text(NOW + timedelta(seconds=1)),
            ),
        )
    ]
    assert ClipAdmissionWorker(replay_repo, replay_jetson, now=lambda: NOW).dispatch_once() is True
    assert replay_jetson.puts[0][2] is False
    assert replay_repo.calls[-1][0] == "record_acceptance"

    lost_repo = Repository(intent(remote_command_id=COMMAND))
    lost_repo.put_started = True
    lost_jetson = Jetson()
    lost_jetson.put_results = [
        JetsonPutResult(
            status_code=200,
            receipt=JetsonClipReceipt(
                accepted_boot_id=BOOT,
                command_id=COMMAND,
                state="recording",
                accepted_at=utc_text(NOW + timedelta(seconds=1)),
            ),
        )
    ]
    assert ClipAdmissionWorker(lost_repo, lost_jetson, now=lambda: NOW).dispatch_once() is True
    assert len(lost_jetson.puts) == 1
    assert lost_jetson.puts[0][2] is True
    assert lost_repo.calls[-1][0] == "record_acceptance"

    pre_wire_crash_repo = Repository(intent(remote_command_id=COMMAND))
    pre_wire_crash_repo.put_started = True
    pre_wire_crash_jetson = Jetson()
    assert ClipAdmissionWorker(
        pre_wire_crash_repo,
        pre_wire_crash_jetson,
        now=lambda: NOW,
    ).dispatch_once() is True
    assert pre_wire_crash_jetson.puts[0][2] is True
    assert pre_wire_crash_repo.calls[-1][0] == "record_acceptance"


def test_admission_retries_once_then_marks_expiry_or_conflict_terminal() -> None:
    times = iter((NOW, NOW, NOW + timedelta(seconds=1)))
    repo = Repository(intent())
    jetson = Jetson()
    jetson.put_results = [JetsonClientError("jetson_unavailable"), JetsonClientError("jetson_unavailable")]
    worker = ClipAdmissionWorker(
        repo,
        jetson,
        now=lambda: next(times),
        command_id=lambda: COMMAND,
        wait=lambda _seconds: False,
    )
    assert worker.dispatch_once() is True
    assert len(jetson.puts) == 2
    assert [put[2] for put in jetson.puts] == [True, False]
    assert repo.calls[-1] == ("defer_admission", 7, NOW + timedelta(seconds=3), "jetson_unavailable")

    expired = Repository(intent())
    assert ClipAdmissionWorker(expired, Jetson(), now=lambda: NOW + timedelta(seconds=3)).dispatch_once() is True
    assert expired.calls[-1][0:2] == ("mark_terminal", 7)

    conflict = Repository(intent(remote_command_id=COMMAND, attempts=1))
    conflict_jetson = Jetson()
    conflict_jetson.put_results = [JetsonClientError("command_conflict")]
    assert ClipAdmissionWorker(conflict, conflict_jetson, now=lambda: NOW).dispatch_once() is True
    assert conflict.calls[-1][-1] == "command_conflict"


def test_admission_replay_requires_idempotent_200_receipt() -> None:
    repo = Repository(intent(remote_command_id=COMMAND, attempts=1))
    jetson = Jetson()
    jetson.put_results = [
        JetsonPutResult(
            status_code=201,
            receipt=JetsonClipReceipt(
                accepted_boot_id=BOOT,
                command_id=COMMAND,
                state="recording",
                accepted_at=utc_text(NOW + timedelta(seconds=1)),
            ),
        ),
        JetsonClientError("jetson_unavailable"),
    ]

    times = iter((NOW, NOW, NOW + timedelta(seconds=1)))
    assert ClipAdmissionWorker(
        repo,
        jetson,
        now=lambda: next(times),
        wait=lambda _seconds: False,
    ).dispatch_once() is True

    assert [put[2] for put in jetson.puts] == [False, False]
    assert all(call[0] != "record_acceptance" for call in repo.calls)

    retry_repo = Repository(intent(remote_command_id=COMMAND, attempts=1))
    retry_jetson = Jetson()
    retry_jetson.put_results = [
        JetsonClientError("jetson_unavailable"),
        JetsonPutResult(
            status_code=201,
            receipt=JetsonClipReceipt(
                accepted_boot_id=BOOT,
                command_id=COMMAND,
                state="recording",
                accepted_at=utc_text(NOW + timedelta(seconds=1)),
            ),
        ),
    ]
    retry_times = iter((NOW, NOW, NOW + timedelta(seconds=1)))
    assert ClipAdmissionWorker(
        retry_repo,
        retry_jetson,
        now=lambda: next(retry_times),
        wait=lambda _seconds: False,
    ).dispatch_once() is True

    assert [put[2] for put in retry_jetson.puts] == [False, False]
    assert all(call[0] != "record_acceptance" for call in retry_repo.calls)


def test_admission_rejects_mismatched_or_late_receipts() -> None:
    for receipt in (
        JetsonClipReceipt(
            accepted_boot_id=BOOT,
            command_id="4" * 32,
            state="recording",
            accepted_at=utc_text(NOW + timedelta(seconds=1)),
        ),
        JetsonClipReceipt(
            accepted_boot_id=BOOT,
            command_id=COMMAND,
            state="recording",
            accepted_at=utc_text(NOW + timedelta(seconds=3, milliseconds=1)),
        ),
    ):
        repo = Repository(intent())
        jetson = Jetson()
        jetson.put_results = [
            JetsonPutResult(status_code=201, receipt=receipt),
            JetsonPutResult(status_code=200, receipt=receipt),
        ]
        times = iter((NOW, NOW, NOW + timedelta(seconds=1)))
        assert ClipAdmissionWorker(
            repo,
            jetson,
            now=lambda: next(times),
            command_id=lambda: COMMAND,
            wait=lambda _seconds: False,
        ).dispatch_once()
        assert all(call[0] != "record_acceptance" for call in repo.calls)
        assert repo.calls[-1][-1] == "invalid_clip_receipt"


def test_delivery_425_uses_capped_poll_backoff(tmp_path: Path) -> None:
    repo = Repository(intent(remote_command_id=COMMAND, remote_boot_id=BOOT, accepted_at=NOW, attempts=8))
    jetson = Jetson()
    jetson.download_result = JetsonClientError("clip_not_ready")
    assert delivery(tmp_path, repo, jetson, Queue()).deliver_once() is True
    assert repo.calls[0] == ("claim_accepted", NOW + timedelta(seconds=2), NOW + timedelta(seconds=92))
    assert repo.calls[-1] == ("defer_delivery", 7, NOW + timedelta(seconds=32), "clip_not_ready")
    assert list(tmp_path.glob("*.partial.mp4")) == []


def test_delivery_validates_ffprobe_then_deletes_commits_and_releases(tmp_path: Path) -> None:
    repo = Repository(intent(remote_command_id=COMMAND, remote_boot_id=BOOT, accepted_at=NOW))
    jetson = Jetson()
    queue = Queue()
    worker = delivery(tmp_path, repo, jetson, queue)

    assert worker.deliver_once() is True
    expected_id = hashlib.sha256(
        f"PETCARE-HOME-QUEUE-V1\n{BOOT}\neating:41\n{DIGEST}\n".encode()
    ).hexdigest()
    enqueue = next(call for call in queue.calls if call[0] == "enqueue")
    assert enqueue[1] == expected_id
    assert enqueue[3].remote_command_ids == (COMMAND,)
    assert jetson.deletes == [COMMAND]
    assert [call[0] for call in repo.calls if call[0] in {"resolve_accepted_clip", "renew_delivery", "mark_commands_processed"}] == [
        "renew_delivery", "resolve_accepted_clip", "renew_delivery", "renew_delivery", "mark_commands_processed"
    ]
    assert queue.calls[-1] == ("release", expected_id)


@pytest.mark.parametrize(
    ("identity", "run"),
    (
        (None, lambda *args, **kwargs: ffprobe_result()),
        ("valid", lambda *args, **kwargs: ffprobe_result(codec_name="hevc")),
        ("valid", lambda *args, **kwargs: ffprobe_result(nb_frames=300)),
    ),
)
def test_delivery_rejects_db_identity_or_ffprobe_mismatch_and_keeps_retryable(
    tmp_path: Path, identity: object, run: object
) -> None:
    repo = Repository(intent(remote_command_id=COMMAND, remote_boot_id=BOOT, accepted_at=NOW))
    if identity is None:
        repo.identity = None  # type: ignore[assignment]
    jetson = Jetson()
    queue = Queue()
    assert delivery(tmp_path, repo, jetson, queue, run=run).deliver_once() is True  # type: ignore[arg-type]
    assert repo.calls[-1][0] == "defer_delivery"
    assert jetson.deletes == []
    assert list(tmp_path.glob("*.partial.mp4")) == []


def test_delivery_queue_failure_cleans_partial_and_leaves_row_retryable(tmp_path: Path) -> None:
    repo = Repository(intent(remote_command_id=COMMAND, remote_boot_id=BOOT, accepted_at=NOW))
    queue = Queue()
    queue.error = RuntimeError("queue_full")
    jetson = Jetson()
    assert delivery(tmp_path, repo, jetson, queue).deliver_once() is True
    assert repo.calls[-1][0] == "defer_delivery"
    assert jetson.deletes == []
    assert list(tmp_path.glob("*.partial.mp4")) == []


def test_delivery_rechecks_download_digest_before_queue_handoff(tmp_path: Path) -> None:
    repo = Repository(intent(remote_command_id=COMMAND, remote_boot_id=BOOT, accepted_at=NOW))
    jetson = Jetson()
    jetson.download_result = headers(content_sha256="f" * 64)
    queue = Queue()
    assert delivery(tmp_path, repo, jetson, queue).deliver_once() is True
    assert repo.calls[-1][-1] == "invalid_clip_digest"
    assert all(call[0] != "enqueue" for call in queue.calls)


def test_delivery_losing_lease_stops_without_stale_db_or_queue_writes(tmp_path: Path) -> None:
    repo = Repository(intent(remote_command_id=COMMAND, remote_boot_id=BOOT, accepted_at=NOW))
    repo.renew_results = [False]
    jetson = Jetson()
    queue = Queue()
    assert delivery(tmp_path, repo, jetson, queue).deliver_once() is True
    assert repo.calls[-1][0] == "renew_delivery"
    assert all(call[0] not in {"defer_delivery", "mark_commands_processed"} for call in repo.calls)
    assert all(call[0] != "enqueue" for call in queue.calls)
    assert jetson.deletes == []


def test_delivery_rechecks_lease_after_durable_queue_copy_before_delete(tmp_path: Path) -> None:
    repo = Repository(intent(remote_command_id=COMMAND, remote_boot_id=BOOT, accepted_at=NOW))
    repo.renew_results = [True, True, False]
    jetson = Jetson()
    queue = Queue()
    assert delivery(tmp_path, repo, jetson, queue).deliver_once() is True
    assert any(call[0] == "enqueue" for call in queue.calls)
    assert all(call[0] != "mark_commands_processed" for call in repo.calls)
    assert all(call[0] != "release" for call in queue.calls)
    assert jetson.deletes == []


def test_delivery_recovers_410_without_duplicate_queue_entry(tmp_path: Path) -> None:
    row = intent(remote_command_id=COMMAND, remote_boot_id=BOOT, accepted_at=NOW)
    repo = Repository(row)
    jetson = Jetson()
    jetson.download_result = JetsonClientError("clip_gone")
    queue = Queue()
    queue.found = "a" * 64
    queue.command_ids = (COMMAND, "3" * 32)

    assert delivery(tmp_path, repo, jetson, queue).deliver_once() is True
    assert jetson.deletes == [COMMAND]
    assert repo.calls[-1] == ("mark_commands_processed", (COMMAND, "3" * 32), NOW + timedelta(seconds=2))
    assert queue.calls[-1] == ("release", "a" * 64)


def test_recovery_transport_failure_is_deferred_without_killing_worker(tmp_path: Path) -> None:
    repo = Repository(intent(remote_command_id=COMMAND, remote_boot_id=BOOT, accepted_at=NOW))
    jetson = Jetson()
    jetson.delete_result = JetsonClientError("jetson_unavailable")
    queue = Queue()
    queue.found = "a" * 64

    assert delivery(tmp_path, repo, jetson, queue).deliver_once() is True
    assert repo.calls[-1][0] == "defer_delivery"
    assert queue.calls[-2:] == [("find", COMMAND), ("command_ids", "a" * 64)]


def test_restart_releases_queue_after_processed_commit_without_network(tmp_path: Path) -> None:
    repo = Repository(None)
    repo.processed = True
    jetson = Jetson()
    queue = Queue()
    queue.queue_ids = ("a" * 64,)

    assert delivery(tmp_path, repo, jetson, queue).deliver_once() is True
    assert queue.calls == [("queue_ids",), ("command_ids", "a" * 64), ("release", "a" * 64)]
    assert jetson.downloads == []
    assert jetson.deletes == []
    assert all(call[0] != "claim_accepted" for call in repo.calls)


def test_failed_recovery_release_does_not_starve_new_delivery(tmp_path: Path) -> None:
    repo = Repository(intent(remote_command_id=COMMAND, remote_boot_id=BOOT, accepted_at=NOW))
    repo.processed = True
    jetson = Jetson()
    queue = Queue()
    queue.queue_ids = ("a" * 64,)
    queue.release_error = OSError("disk unavailable")
    worker = delivery(tmp_path, repo, jetson, queue, wait=lambda _delay: False)

    assert worker.deliver_once() is True
    assert jetson.downloads != []


def test_poison_recovery_entry_does_not_block_later_processed_queue(tmp_path: Path) -> None:
    class PoisonQueue(Queue):
        def release(self, queue_id: str) -> None:
            self.calls.append(("release", queue_id))
            if queue_id == "a" * 64:
                raise OSError("disk unavailable")

    repo = Repository(None)
    repo.processed = True
    queue = PoisonQueue()
    queue.queue_ids = ("a" * 64, "b" * 64)
    jetson = Jetson()
    assert delivery(tmp_path, repo, jetson, queue).deliver_once() is True
    assert ("release", "b" * 64) in queue.calls
    assert jetson.downloads == []


def test_delivery_410_without_queue_reopens_only_before_original_deadline(tmp_path: Path) -> None:
    row = intent(remote_command_id=COMMAND, remote_boot_id=BOOT, accepted_at=NOW)
    jetson = Jetson()
    jetson.download_result = JetsonClientError("clip_gone")

    repo = Repository(row)
    assert delivery(tmp_path, repo, jetson, Queue(), now=lambda: NOW + timedelta(seconds=2)).deliver_once()
    assert repo.calls[-1][0] == "reset_command_for_readmission"

    repo = Repository(row)
    assert delivery(tmp_path, repo, jetson, Queue(), now=lambda: NOW + timedelta(seconds=3)).deliver_once()
    assert repo.calls[-1][-2:] == (NOW + timedelta(seconds=3), "clip_gone")


def test_slow_media_worker_does_not_block_fast_admission(tmp_path: Path) -> None:
    entered = threading.Event()
    release = threading.Event()

    def blocked_probe(*args: object, **kwargs: object) -> subprocess.CompletedProcess[str]:
        entered.set()
        assert release.wait(2)
        return ffprobe_result()

    delivery_repo = Repository(intent(remote_command_id=COMMAND, remote_boot_id=BOOT, accepted_at=NOW))
    media = delivery(tmp_path, delivery_repo, Jetson(), Queue(), run=blocked_probe)
    thread = threading.Thread(target=media.deliver_once)
    thread.start()
    assert entered.wait(1)

    admission_repo = Repository(intent())
    admission_jetson = Jetson()
    assert ClipAdmissionWorker(
        admission_repo, admission_jetson, now=lambda: NOW, command_id=lambda: COMMAND
    ).dispatch_once()
    assert len(admission_jetson.puts) == 1
    release.set()
    thread.join(2)
    assert not thread.is_alive()


def test_worker_threads_survive_transient_repository_and_queue_errors(tmp_path: Path) -> None:
    admission_recovered = threading.Event()

    class FlakyRepository(Repository):
        def claim_unaccepted(self, now: datetime, lease_until: datetime) -> ClipIntent | None:
            if not self.calls:
                self.calls.append(("failed",))
                raise RuntimeError("database unavailable")
            admission_recovered.set()
            return None

    admission = ClipAdmissionWorker(FlakyRepository(None), Jetson(), now=lambda: NOW, wait=lambda _delay: False)
    admission.start()
    assert admission_recovered.wait(1)
    admission.stop(timeout_seconds=1)

    delivery_recovered = threading.Event()

    class FlakyQueue(Queue):
        def _unreleased_queue_ids(self) -> tuple[str, ...]:
            if not self.calls:
                self.calls.append(("failed",))
                raise OSError("disk unavailable")
            delivery_recovered.set()
            return ()

    media = delivery(
        tmp_path,
        Repository(None),
        Jetson(),
        FlakyQueue(),
        wait=lambda _delay: False,
    )
    media.start()
    assert delivery_recovered.wait(1)
    media.stop(timeout_seconds=1)
