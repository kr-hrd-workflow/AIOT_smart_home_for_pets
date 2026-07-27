from __future__ import annotations

import base64
import hashlib
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

import httpx
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from app.agent_client import SignedLiveUploadClient, b64url
from app.live_delivery import LiveDeliveryWorker


NOW = datetime(2026, 7, 27, 1, 2, 3, 456789, tzinfo=UTC)


class FakeStdin:
    def __init__(self) -> None:
        self.content = bytearray()
        self.closed = False

    def write(self, value: bytes) -> int:
        self.content.extend(value)
        return len(value)

    def flush(self) -> None:
        pass

    def close(self) -> None:
        self.closed = True


class FakeProcess:
    def __init__(self) -> None:
        self.stdin = FakeStdin()
        self.returncode: int | None = None

    def poll(self) -> int | None:
        return self.returncode

    def terminate(self) -> None:
        self.returncode = 0

    def kill(self) -> None:
        self.returncode = -9

    def wait(self, timeout: float | None = None) -> int:
        del timeout
        self.returncode = 0 if self.returncode is None else self.returncode
        return self.returncode


def test_ffmpeg_command_uses_pinned_binary_and_required_fmp4_contract(tmp_path: Path) -> None:
    ffmpeg = tmp_path / "runtime" / "ffmpeg.exe"
    ffmpeg.parent.mkdir()
    ffmpeg.touch()
    worker = LiveDeliveryWorker(
        SimpleNamespace(upload=lambda *args, **kwargs: None, close=lambda: None),
        lambda: iter(()),
        ffmpeg_path=ffmpeg.resolve(),
        work_dir=tmp_path / "live",
    )

    command = worker.ffmpeg_command(tmp_path / "boot")

    assert command[0] == str(ffmpeg.resolve())
    assert command[command.index("-framerate") + 1] == "30"
    assert command.index("-framerate") < command.index("-i")
    required = [
        "-an", "-c:v", "libx264", "-profile:v", "baseline",
        "-pix_fmt", "yuv420p", "-r", "30", "-g", "30",
        "-f", "hls", "-hls_time", "3", "-hls_segment_type", "fmp4",
        "-hls_list_size", "8",
        "-hls_flags", "delete_segments+independent_segments+temp_file",
    ]
    start = command.index("-an")
    assert command[start:start + len(required)] == required
    assert "-start_number" in command
    assert command[command.index("-start_number") + 1] == "1"
    assert command[command.index("-hls_fmp4_init_filename") + 1] == str(tmp_path / "boot" / "init.mp4")


def test_completed_files_publish_init_then_ordered_segments_and_ignore_temp_files(
    tmp_path: Path,
) -> None:
    uploaded: list[tuple[str, int, bytes]] = []

    class Client:
        def upload(self, path: Path, *, kind: str, sequence: int, **_kwargs: object) -> None:
            uploaded.append((kind, sequence, path.read_bytes()))

        def close(self) -> None:
            pass

    boot_dir = tmp_path / "boot"
    boot_dir.mkdir()
    (boot_dir / "init.mp4.tmp").write_bytes(b"partial-init")
    (boot_dir / "1.m4s.tmp").write_bytes(b"partial-one")
    worker = LiveDeliveryWorker(
        Client(),
        lambda: iter(()),
        ffmpeg_path=(tmp_path / "ffmpeg.exe"),
        work_dir=tmp_path / "live",
        now=lambda: NOW,
    )
    state = worker.new_boot_state("boot-a", boot_dir)

    assert worker.publish_completed(state) is True
    assert uploaded == []

    (boot_dir / "init.mp4").write_bytes(b"init")
    (boot_dir / "2.m4s").write_bytes(b"two")
    assert worker.publish_completed(state) is True
    assert uploaded == []

    (boot_dir / "1.m4s").write_bytes(b"one")
    assert worker.publish_completed(state) is True
    assert uploaded == [
        ("init", 0, b"init"),
        ("segment", 1, b"one"),
        ("segment", 2, b"two"),
    ]
    assert (boot_dir / "init.mp4").exists()
    assert (boot_dir / "1.m4s").exists()
    assert (boot_dir / "2.m4s").exists()


def test_upload_failure_keeps_boot_without_queuing_or_mutating_ffmpeg_files(
    tmp_path: Path,
) -> None:
    class Client:
        def upload(self, *_args: object, **_kwargs: object) -> None:
            raise RuntimeError("secret=/do/not/log")

        def close(self) -> None:
            pass

    boot_dir = tmp_path / "boot"
    boot_dir.mkdir()
    (boot_dir / "init.mp4").write_bytes(b"init")
    for sequence in range(1, 16):
        (boot_dir / f"{sequence}.m4s").write_bytes(bytes([sequence]))
    worker = LiveDeliveryWorker(
        Client(),
        lambda: iter(()),
        ffmpeg_path=tmp_path / "ffmpeg.exe",
        work_dir=tmp_path / "live",
        now=lambda: NOW,
    )
    state = worker.new_boot_state("boot-a", boot_dir)

    assert worker.publish_completed(state) is True
    assert worker.last_error == "live_upload_unavailable"
    assert len(list(boot_dir.glob("*.m4s"))) == 15
    assert "secret" not in worker.last_error


def test_transient_upload_failure_retries_same_boot_and_segment(tmp_path: Path) -> None:
    attempts = 0
    uploaded: list[tuple[str, int]] = []

    class Client:
        def upload(self, _path: Path, *, kind: str, sequence: int, **_kwargs: object) -> None:
            nonlocal attempts
            attempts += 1
            if attempts == 1:
                raise TimeoutError("temporary upload delay")
            uploaded.append((kind, sequence))

        def close(self) -> None:
            pass

    boot_dir = tmp_path / "boot"
    boot_dir.mkdir()
    (boot_dir / "init.mp4").write_bytes(b"init")
    (boot_dir / "1.m4s").write_bytes(b"one")
    worker = LiveDeliveryWorker(
        Client(),
        lambda: iter(()),
        ffmpeg_path=tmp_path / "ffmpeg.exe",
        work_dir=tmp_path / "live",
        now=lambda: NOW,
    )
    state = worker.new_boot_state("boot-a", boot_dir)

    assert worker.publish_completed(state) is True
    assert state.init_uploaded is False
    assert worker.publish_completed(state) is True
    assert uploaded == [("init", 0), ("segment", 1)]
    assert state.boot_id == "boot-a"


def test_signed_live_upload_matches_server_canonical_contract(tmp_path: Path) -> None:
    private_key = Ed25519PrivateKey.generate()
    media = tmp_path / "1.m4s"
    media.write_bytes(b"fragment")
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(204, headers={"Cache-Control": "private, no-store"})

    client = SignedLiveUploadClient(
        origin="https://petcare.example",
        agent_id="agent-a",
        camera_id="camera-a",
        private_key=private_key,
        transport=httpx.MockTransport(handler),
        now=lambda: NOW,
        nonce=lambda: b64url(bytes(range(16))),
    )
    client.upload(
        media,
        boot_id="boot-a",
        kind="segment",
        sequence=1,
        started_at=NOW,
        duration_ms=3000,
    )
    request = requests[0]
    body = request.read()
    digest = b64url(hashlib.sha256(body).digest())
    canonical = "\n".join((
        "PETCARE-LIVE-V1", "POST", "/api/petcare/agent/live",
        "agent-a", "camera-a", "boot-a", "segment", "1",
        "2026-07-27T01:02:03.456789Z", "3000", str(len(body)), digest, "",
    )).encode()
    private_key.public_key().verify(
        base64.urlsafe_b64decode(request.headers["X-PetCare-Signature"] + "=="),
        canonical,
    )
    assert request.headers["Content-Type"] == "video/mp4"


def test_stop_terminates_process_closes_source_and_client_once(tmp_path: Path) -> None:
    process = FakeProcess()
    calls: list[str] = []

    class Client:
        def upload(self, *_args: object, **_kwargs: object) -> None:
            pass

        def close(self) -> None:
            calls.append("client:close")

    worker = LiveDeliveryWorker(
        Client(),
        lambda: iter((b"--frame\r\n",)),
        close_source=lambda: calls.append("source:close"),
        ffmpeg_path=tmp_path / "ffmpeg.exe",
        work_dir=tmp_path / "live",
        popen=lambda *_args, **_kwargs: process,
        poll_seconds=0.001,
    )
    worker.start()
    worker.stop(timeout_seconds=1)
    worker.stop(timeout_seconds=1)

    assert process.returncode is not None
    assert calls.count("source:close") == 1
    assert calls.count("client:close") == 1
