from __future__ import annotations

import hashlib
import json
import math
import secrets
import subprocess
import threading
import time
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Protocol

from .clip_contracts import ClipDeliveryIdentity, ClipIntent, ClipMetadata
from .jetson_client import JetsonClientError
from .jetson_contracts import MAX_CLIP_BYTES, JetsonClipHeaders, JetsonPutResult


ADMISSION_LEASE = timedelta(seconds=1)
DELIVERY_LEASE = timedelta(seconds=90)
DELIVERY_BACKOFF = (1, 2, 4, 8, 16, 30)
SAFE_ERRORS = frozenset(
    (
        "clip_gone",
        "clip_not_ready",
        "clip_timeout",
        "clock_uncertain",
        "command_conflict",
        "command_expired",
        "invalid_clip_digest",
        "invalid_clip_headers",
        "invalid_clip_identity",
        "invalid_clip_media",
        "invalid_clip_receipt",
        "jetson_unavailable",
        "queue_full",
    )
)


def utc_now() -> datetime:
    return datetime.now(UTC)


class ClipRepository(Protocol):
    def claim_unaccepted(self, now: datetime, lease_until: datetime) -> ClipIntent | None: ...

    def persist_command(self, outbox_id: int, command_id: str) -> ClipIntent: ...

    def record_acceptance(
        self, outbox_id: int, boot_id: str, command_id: str, accepted_at: datetime
    ) -> None: ...

    def mark_put_started(self, outbox_id: int, started_at: datetime) -> bool: ...

    def defer_admission(self, outbox_id: int, next_attempt_at: datetime, error: str) -> None: ...

    def mark_terminal(
        self, outbox_id: int, reason: str, processed_at: datetime, error: str
    ) -> None: ...

    def claim_accepted(self, now: datetime, lease_until: datetime) -> ClipIntent | None: ...

    def defer_delivery(self, outbox_id: int, next_attempt_at: datetime, error: str) -> None: ...

    def renew_delivery(
        self, outbox_id: int, expected_lease_until: datetime, lease_until: datetime
    ) -> datetime | None: ...

    def resolve_accepted_clip(
        self, command_id: str, boot_id: str, canonical_events: str
    ) -> ClipDeliveryIdentity | None: ...

    def command_processed(self, command_id: str) -> bool: ...

    def mark_commands_processed(
        self, command_ids: tuple[str, ...], processed_at: datetime
    ) -> None: ...

    def reset_command_for_readmission(
        self, outbox_id: int, expected_command_id: str, next_attempt_at: datetime
    ) -> None: ...


class JetsonClipClient(Protocol):
    def put_clip(self, command_id: str, command: object, *, first: bool = True) -> JetsonPutResult: ...

    def download_clip(self, command_id: str, destination: Path) -> JetsonClipHeaders: ...

    def delete_clip(self, command_id: str) -> int: ...


class UploadQueue(Protocol):
    def enqueue_verified(
        self,
        queue_id: str,
        source_mp4: Path,
        metadata: ClipMetadata,
    ) -> str: ...

    def find_unreleased_by_command(self, command_id: str) -> str | None: ...

    def _unreleased_command_ids(self, queue_id: str) -> tuple[str, ...]: ...

    def _unreleased_queue_ids(self) -> tuple[str, ...]: ...

    def release(self, queue_id: str) -> None: ...


class _LeaseLost(RuntimeError):
    pass


class ClipAdmissionWorker:
    def __init__(
        self,
        repository: ClipRepository,
        jetson_client: JetsonClipClient,
        *,
        now: Callable[[], datetime] = utc_now,
        command_id: Callable[[], str] = lambda: secrets.token_hex(16),
        wait: Callable[[float], bool] | None = None,
    ) -> None:
        self._repository = repository
        self._client = jetson_client
        self._now = now
        self._command_id = command_id
        self._stop_event = threading.Event()
        self._wait = wait or self._stop_event.wait
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._run, name="petcare-clip-admission", daemon=True)
        self._thread.start()

    def _run(self) -> None:
        while not self._stop_event.is_set():
            try:
                dispatched = self.dispatch_once()
            except Exception:
                self._wait(1.0)
                continue
            if not dispatched:
                self._wait(0.1)

    def dispatch_once(self) -> bool:
        current = self._now()
        row = self._repository.claim_unaccepted(current, current + ADMISSION_LEASE)
        if row is None:
            return False
        if current >= row.deadline_at:
            self._repository.mark_terminal(row.outbox_id, "clip_missed", current, "command_expired")
            return True

        created_here = row.remote_command_id is None
        if created_here:
            row = self._repository.persist_command(row.outbox_id, self._command_id())
        assert row.remote_command_id is not None
        current = self._now()
        if current >= row.deadline_at:
            self._repository.mark_terminal(row.outbox_id, "clip_missed", current, "command_expired")
            return True
        first_wire_attempt = self._repository.mark_put_started(row.outbox_id, current)
        ambiguous_recovery = not first_wire_attempt and row.attempts == 0
        expected_statuses = (
            (201,) if first_wire_attempt else ((200, 201) if ambiguous_recovery else (200,))
        )
        try:
            result = self._client.put_clip(
                row.remote_command_id,
                row.command_body(),
                first=first_wire_attempt or ambiguous_recovery,
            )
            self._accept(
                row,
                result,
                expected_statuses=expected_statuses,
            )
            return True
        except JetsonClientError as error:
            code = _safe_error(error)

        if code in ("command_conflict", "command_expired"):
            self._repository.mark_terminal(row.outbox_id, "clip_missed", current, code)
            return True

        retry_at = current + timedelta(seconds=1)
        if retry_at < row.deadline_at and not self._wait(1.0):
            current = self._now()
            if current < row.deadline_at:
                try:
                    result = self._client.put_clip(
                        row.remote_command_id,
                        row.command_body(),
                        first=False,
                    )
                    self._accept(
                        row,
                        result,
                        expected_statuses=(200, 201) if row.attempts == 0 else (200,),
                    )
                    return True
                except JetsonClientError as error:
                    code = _safe_error(error)
        if current >= row.deadline_at or code in ("command_conflict", "command_expired"):
            self._repository.mark_terminal(row.outbox_id, "clip_missed", current, code)
        else:
            self._repository.defer_admission(row.outbox_id, row.deadline_at, code)
        return True

    def _accept(
        self,
        row: ClipIntent,
        result: JetsonPutResult,
        *,
        expected_statuses: tuple[int, ...],
    ) -> None:
        if (
            result.status_code not in expected_statuses
            or result.receipt.command_id != row.remote_command_id
            or not (
                row.created_at - timedelta(milliseconds=200)
                <= result.receipt.accepted_at
                <= row.deadline_at
            )
        ):
            raise JetsonClientError("invalid_clip_receipt")
        self._repository.record_acceptance(
            row.outbox_id,
            result.receipt.accepted_boot_id,
            result.receipt.command_id,
            result.receipt.accepted_at,
        )

    def stop(self, *, timeout_seconds: float = 5.0) -> None:
        self._stop_event.set()
        thread = self._thread
        if thread is None:
            return
        thread.join(max(0.0, timeout_seconds))
        if thread.is_alive():
            raise TimeoutError("clip admission worker did not stop")


class ClipDeliveryWorker:
    def __init__(
        self,
        repository: ClipRepository,
        jetson_client: JetsonClipClient,
        upload_queue: UploadQueue,
        *,
        work_dir: Path,
        ffprobe_path: Path,
        now: Callable[[], datetime] = utc_now,
        run: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
        wait: Callable[[float], bool] | None = None,
    ) -> None:
        self._repository = repository
        self._client = jetson_client
        self._queue = upload_queue
        self._work_dir = Path(work_dir)
        self._ffprobe_path = Path(ffprobe_path)
        if not self._ffprobe_path.is_absolute() or not self._ffprobe_path.is_file():
            raise ValueError("ffprobe_path must be an absolute file")
        self._now = now
        self._subprocess_run = run
        self._stop_event = threading.Event()
        self._wait = wait or self._stop_event.wait
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._run, name="petcare-clip-delivery", daemon=True)
        self._thread.start()

    def _run(self) -> None:
        while not self._stop_event.is_set():
            try:
                delivered = self.deliver_once()
            except Exception:
                self._wait(1.0)
                continue
            if not delivered:
                self._wait(0.1)

    def deliver_once(self) -> bool:
        if self._release_processed_queue():
            return True
        current = self._now()
        lease_until = current + DELIVERY_LEASE
        row = self._repository.claim_accepted(current, lease_until)
        if row is None:
            return False
        if row.remote_command_id is None or row.remote_boot_id is None or row.accepted_at is None:
            self._repository.defer_delivery(row.outbox_id, current + timedelta(seconds=1), "invalid_clip_identity")
            return True
        command_id = row.remote_command_id
        queued = self._queue.find_unreleased_by_command(command_id)
        if queued is not None:
            try:
                self._reconcile_queued(command_id, queued, current)
            except (JetsonClientError, OSError, RuntimeError, TypeError, ValueError) as error:
                self._defer(row, self._now(), _safe_error(error))
            return True

        partial = self._work_dir / f"{command_id}.{secrets.token_hex(8)}.partial.mp4"
        try:
            response = self._client.download_clip(command_id, partial)
            renewed = self._repository.renew_delivery(
                row.outbox_id, lease_until, self._next_lease(lease_until)
            )
            if renewed is None:
                raise _LeaseLost
            lease_until = renewed
            self._validate_digest(partial, response.content_sha256)
            identity = self._repository.resolve_accepted_clip(command_id, response.boot_id, response.events)
            metadata = self._validate_identity(row, response, identity)
            self._validate_media(partial, response)
            renewed = self._repository.renew_delivery(
                row.outbox_id, lease_until, self._next_lease(lease_until)
            )
            if renewed is None:
                raise _LeaseLost
            assert identity is not None
            queue_id = _queue_id(response.boot_id, identity.canonical_events, response.content_sha256)
            queued_id = self._queue.enqueue_verified(
                queue_id,
                partial,
                metadata,
            )
            if queued_id != queue_id:
                raise RuntimeError("invalid queue receipt")
            renewed = self._repository.renew_delivery(
                row.outbox_id, lease_until, self._next_lease(lease_until)
            )
            if renewed is None:
                raise _LeaseLost
            lease_until = renewed
            status = self._client.delete_clip(command_id)
            if status not in (204, 410):
                raise RuntimeError("invalid delete status")
            self._repository.mark_commands_processed(identity.remote_command_ids, self._now())
            self._queue.release(queue_id)
        except _LeaseLost:
            pass
        except JetsonClientError as error:
            code = _safe_error(error)
            if code == "clip_gone":
                try:
                    self._handle_gone(row, self._now())
                except (JetsonClientError, OSError, RuntimeError, TypeError, ValueError) as recovery_error:
                    self._defer(row, self._now(), _safe_error(recovery_error))
            else:
                self._defer(row, self._now(), code)
        except (OSError, RuntimeError, TypeError, ValueError, subprocess.SubprocessError) as error:
            self._defer(row, self._now(), _safe_error(error))
        finally:
            try:
                partial.unlink(missing_ok=True)
            except OSError:
                pass
        return True

    def _release_processed_queue(self) -> bool:
        for queue_id in self._queue._unreleased_queue_ids():
            command_ids = self._queue._unreleased_command_ids(queue_id)
            if command_ids and all(self._repository.command_processed(value) for value in command_ids):
                try:
                    self._queue.release(queue_id)
                except (OSError, RuntimeError, ValueError):
                    continue
                return True
        return False

    def _reconcile_queued(self, command_id: str, queue_id: str, current: datetime) -> None:
        command_ids = self._queue._unreleased_command_ids(queue_id)
        if not command_ids or command_id not in command_ids:
            raise ValueError("invalid_clip_identity")
        if all(self._repository.command_processed(value) for value in command_ids):
            self._queue.release(queue_id)
            return
        status = self._client.delete_clip(command_id)
        if status not in (204, 410):
            raise RuntimeError("invalid delete status")
        self._repository.mark_commands_processed(command_ids, current)
        self._queue.release(queue_id)

    def _handle_gone(self, row: ClipIntent, current: datetime) -> None:
        assert row.remote_command_id is not None
        queued = self._queue.find_unreleased_by_command(row.remote_command_id)
        if queued is not None:
            self._reconcile_queued(row.remote_command_id, queued, current)
        elif current < row.deadline_at:
            self._repository.reset_command_for_readmission(row.outbox_id, row.remote_command_id, current)
        else:
            self._repository.mark_terminal(row.outbox_id, "clip_missed", current, "clip_gone")

    def _defer(self, row: ClipIntent, current: datetime, error: str) -> None:
        delay = DELIVERY_BACKOFF[min(row.attempts, len(DELIVERY_BACKOFF) - 1)]
        self._repository.defer_delivery(row.outbox_id, current + timedelta(seconds=delay), error)

    def _next_lease(self, expected: datetime) -> datetime:
        return max(self._now() + DELIVERY_LEASE, expected + timedelta(seconds=1))

    @staticmethod
    def _validate_digest(path: Path, expected: str) -> None:
        if path.is_symlink() or not path.is_file() or not 0 < path.stat().st_size <= MAX_CLIP_BYTES:
            raise ValueError("invalid_clip_digest")
        digest = hashlib.sha256()
        with path.open("rb") as source:
            while chunk := source.read(1024 * 1024):
                digest.update(chunk)
        if digest.hexdigest() != expected:
            raise ValueError("invalid_clip_digest")

    @staticmethod
    def _validate_identity(
        row: ClipIntent,
        headers: JetsonClipHeaders,
        identity: ClipDeliveryIdentity | None,
    ) -> ClipMetadata:
        if (
            identity is None
            or headers.command_id != row.remote_command_id
            or headers.boot_id != row.remote_boot_id
            or headers.events != identity.canonical_events
            or row.remote_command_id not in identity.remote_command_ids
            or any(not headers.started_at <= accepted <= headers.ended_at for accepted in identity.accepted_at)
        ):
            raise ValueError("invalid_clip_identity")
        return ClipMetadata(
            "pc-webcam-01",
            headers.started_at,
            headers.ended_at,
            identity.events,
            identity.remote_command_ids,
        )

    def _validate_media(self, path: Path, headers: JetsonClipHeaders) -> None:
        result = self._subprocess_run(
            [
                str(self._ffprobe_path),
                "-v",
                "error",
                "-show_entries",
                "stream=codec_name,pix_fmt,width,height,r_frame_rate,nb_frames:format=duration",
                "-of",
                "json",
                str(path),
            ],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="strict",
            timeout=30.0,
            check=False,
        )
        if result.returncode != 0 or len(result.stdout) > 65_536:
            raise ValueError("invalid_clip_media")
        try:
            value = json.loads(result.stdout)
            if type(value) is not dict or set(value) != {"streams", "format"}:
                raise ValueError
            streams = value["streams"]
            media_format = value["format"]
            if (
                type(streams) is not list
                or len(streams) != 1
                or type(media_format) is not dict
                or set(media_format) != {"duration"}
            ):
                raise ValueError
            stream = streams[0]
            if type(stream) is not dict or set(stream) != {
                "codec_name", "pix_fmt", "width", "height", "r_frame_rate", "nb_frames"
            }:
                raise ValueError
            frame_text = stream["nb_frames"]
            duration_text = media_format["duration"]
            if (
                type(frame_text) is not str
                or not frame_text.isdigit()
                or str(int(frame_text)) != frame_text
                or type(duration_text) is not str
            ):
                raise ValueError
            frame_count = int(frame_text)
            duration = float(duration_text)
            if (
                stream["codec_name"] != "h264"
                or stream["pix_fmt"] != "yuv420p"
                or type(stream["width"]) is not int
                or stream["width"] != 640
                or type(stream["height"]) is not int
                or stream["height"] != 480
                or stream["r_frame_rate"] != "10/1"
                or not math.isfinite(duration)
                or frame_count != headers.frame_count
                or abs(duration - headers.frame_count / 10) > 0.1
                or abs(duration - (headers.ended_at - headers.started_at).total_seconds()) > 0.1
            ):
                raise ValueError
        except (KeyError, TypeError, ValueError, OverflowError, json.JSONDecodeError) as error:
            raise ValueError("invalid_clip_media") from error

    def stop(self, *, timeout_seconds: float = 45.0) -> None:
        self._stop_event.set()
        thread = self._thread
        if thread is None:
            return
        thread.join(max(0.0, timeout_seconds))
        if thread.is_alive():
            raise TimeoutError("clip delivery worker did not stop")


def _queue_id(boot_id: str, canonical_events: str, content_sha256: str) -> str:
    value = f"PETCARE-HOME-QUEUE-V1\n{boot_id}\n{canonical_events}\n{content_sha256}\n"
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _safe_error(error: BaseException) -> str:
    value = str(error)
    if value in SAFE_ERRORS:
        return value
    if value == "queue_full" or type(error).__name__ == "ClipUploadQueueFull":
        return "queue_full"
    return "delivery_failed"
