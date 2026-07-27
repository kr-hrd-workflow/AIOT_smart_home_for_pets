from __future__ import annotations

import os
import re
import secrets
import shutil
import subprocess
import threading
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Protocol

from .agent_client import b64url


_SEGMENT = re.compile(r"([1-9]\d*)\.m4s\Z")
_BOOT_ID = re.compile(r"[A-Za-z0-9_-]{1,64}\Z")


class LiveUploadClient(Protocol):
    def upload(
        self,
        path: Path,
        *,
        boot_id: str,
        kind: str,
        sequence: int,
        started_at: datetime,
        duration_ms: int,
    ) -> None: ...

    def close(self) -> None: ...


@dataclass(slots=True)
class LiveBootState:
    boot_id: str
    directory: Path
    started_at: datetime
    init_uploaded: bool = False
    next_sequence: int = 1


class LiveDeliveryWorker:
    def __init__(
        self,
        client: LiveUploadClient,
        mjpeg_stream: Callable[[], Iterator[bytes]],
        *,
        ffmpeg_path: Path,
        work_dir: Path,
        close_source: Callable[[], None] | None = None,
        popen: Callable[..., Any] = subprocess.Popen,
        now: Callable[[], datetime] | None = None,
        boot_id: Callable[[], str] | None = None,
        poll_seconds: float = 0.05,
    ) -> None:
        ffmpeg_path = Path(ffmpeg_path)
        if not ffmpeg_path.is_absolute():
            raise ValueError("ffmpeg_path must be absolute")
        if poll_seconds <= 0:
            raise ValueError("poll_seconds must be positive")
        self._client = client
        self._mjpeg_stream = mjpeg_stream
        self._close_source = close_source
        self._ffmpeg_path = ffmpeg_path
        self._work_dir = Path(work_dir)
        self._popen = popen
        self._now = now or (lambda: datetime.now(UTC))
        self._boot_id = boot_id or (lambda: b64url(secrets.token_bytes(16)))
        self._poll_seconds = poll_seconds
        self._stop = threading.Event()
        self._lock = threading.Lock()
        self._thread: threading.Thread | None = None
        self._process: Any | None = None
        self._source_closed = False
        self._client_closed = False
        self.last_error: str | None = None

    def ffmpeg_command(self, boot_dir: Path) -> list[str]:
        return [
            str(self._ffmpeg_path),
            "-hide_banner", "-loglevel", "error",
            "-f", "mpjpeg", "-i", "pipe:0",
            "-an", "-c:v", "libx264", "-profile:v", "baseline",
            "-pix_fmt", "yuv420p", "-r", "30", "-g", "30",
            "-f", "hls", "-hls_time", "3", "-hls_segment_type", "fmp4",
            "-hls_list_size", "8",
            "-hls_flags", "delete_segments+independent_segments+temp_file",
            "-start_number", "1",
            "-hls_fmp4_init_filename", str(boot_dir / "init.mp4"),
            "-hls_segment_filename", str(boot_dir / "%d.m4s"),
            str(boot_dir / "live.m3u8"),
        ]

    def new_boot_state(self, boot_id: str, directory: Path) -> LiveBootState:
        started_at = self._now()
        if started_at.tzinfo is None or started_at.utcoffset() is None:
            raise ValueError("live clock must be timezone-aware")
        return LiveBootState(boot_id, Path(directory), started_at.astimezone(UTC))

    @staticmethod
    def _segments(directory: Path) -> dict[int, Path]:
        result: dict[int, Path] = {}
        for path in directory.iterdir():
            match = _SEGMENT.fullmatch(path.name)
            if match is not None and path.is_file():
                result[int(match.group(1))] = path
        return result

    def _cap_local_parts(self, state: LiveBootState) -> bool:
        segments = self._segments(state.directory)
        return (
            not segments
            or state.next_sequence in segments
            or max(segments) - state.next_sequence < 8
        )

    def publish_completed(self, state: LiveBootState) -> bool:
        try:
            if not self._cap_local_parts(state):
                raise RuntimeError("live sequence gap")
            init_path = state.directory / "init.mp4"
            if not state.init_uploaded:
                if not init_path.is_file() or state.next_sequence not in self._segments(state.directory):
                    return True
                self._client.upload(
                    init_path,
                    boot_id=state.boot_id,
                    kind="init",
                    sequence=0,
                    started_at=state.started_at,
                    duration_ms=0,
                )
                state.init_uploaded = True
            segments = self._segments(state.directory)
            while path := segments.get(state.next_sequence):
                self._client.upload(
                    path,
                    boot_id=state.boot_id,
                    kind="segment",
                    sequence=state.next_sequence,
                    started_at=state.started_at,
                    duration_ms=3000,
                )
                state.next_sequence += 1
            self.last_error = None
            return True
        except Exception:
            self._cap_local_parts(state)
            self.last_error = "live_upload_unavailable"
            return False

    def _prepare_boot(self, boot_id: str) -> LiveBootState:
        if _BOOT_ID.fullmatch(boot_id) is None:
            raise ValueError("invalid live boot id")
        self._work_dir.mkdir(parents=True, exist_ok=True)
        for stale in self._work_dir.iterdir():
            if stale.is_dir() and not stale.is_symlink():
                shutil.rmtree(stale)
            else:
                stale.unlink(missing_ok=True)
        directory = self._work_dir / boot_id
        directory.mkdir()
        return self.new_boot_state(boot_id, directory)

    @staticmethod
    def _stop_process(process: Any) -> None:
        if process.poll() is not None:
            return
        process.terminate()
        try:
            process.wait(timeout=1.0)
        except Exception:
            process.kill()
            process.wait(timeout=1.0)

    def _feed(self, process: Any, finished: threading.Event) -> None:
        try:
            for chunk in self._mjpeg_stream():
                if self._stop.is_set():
                    break
                if type(chunk) is not bytes or not chunk:
                    raise ValueError("invalid live frame stream")
                process.stdin.write(chunk)
                process.stdin.flush()
        except Exception:
            self.last_error = "live_source_unavailable"
        finally:
            try:
                process.stdin.close()
            except Exception:
                pass
            finished.set()

    def _run_boot(self) -> None:
        state = self._prepare_boot(self._boot_id())
        launch: dict[str, object] = {
            "stdin": subprocess.PIPE,
            "stdout": subprocess.DEVNULL,
            "stderr": subprocess.DEVNULL,
        }
        if os.name == "nt":
            launch["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        process = self._popen(self.ffmpeg_command(state.directory), **launch)
        with self._lock:
            self._process = process
        finished = threading.Event()
        feeder = threading.Thread(
            target=self._feed,
            args=(process, finished),
            name="petcare-live-source",
            daemon=True,
        )
        feeder.start()
        try:
            while not self._stop.is_set():
                if not self.publish_completed(state):
                    break
                if process.poll() is not None or finished.is_set():
                    self.publish_completed(state)
                    break
                self._stop.wait(self._poll_seconds)
        finally:
            self._stop_process(process)
            feeder.join(2.0)
            with self._lock:
                if self._process is process:
                    self._process = None
            shutil.rmtree(state.directory, ignore_errors=True)
        if feeder.is_alive():
            raise RuntimeError("live source did not stop")

    def _close_client(self) -> None:
        with self._lock:
            if self._client_closed:
                return
            self._client_closed = True
        self._client.close()

    def _run(self) -> None:
        try:
            while not self._stop.is_set():
                try:
                    self._run_boot()
                except Exception:
                    self.last_error = "live_delivery_unavailable"
                if not self._stop.is_set():
                    self._stop.wait(1.0)
        finally:
            self._close_client()

    def start(self) -> None:
        with self._lock:
            if self._thread is not None or self._client_closed:
                return
            self._thread = threading.Thread(
                target=self._run,
                name="petcare-live-delivery",
                daemon=True,
            )
            self._thread.start()

    def stop(self, *, timeout_seconds: float) -> None:
        if timeout_seconds < 0:
            raise ValueError("timeout_seconds must be nonnegative")
        with self._lock:
            if self._client_closed:
                return
            self._stop.set()
            process = self._process
            thread = self._thread
            close_source = self._close_source if not self._source_closed else None
            self._source_closed = True
        first_error: BaseException | None = None
        if process is not None:
            try:
                self._stop_process(process)
            except BaseException as error:
                first_error = error
        if close_source is not None:
            try:
                close_source()
            except BaseException as error:
                first_error = first_error or error
        if thread is not None:
            thread.join(timeout_seconds)
            if thread.is_alive():
                raise TimeoutError("live delivery shutdown timed out")
        try:
            self._close_client()
        except BaseException as error:
            first_error = first_error or error
        if first_error is not None:
            raise first_error
