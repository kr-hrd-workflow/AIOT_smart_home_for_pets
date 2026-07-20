# PetCare Home Agent And Event Clips Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Package the existing loopback-only backend as an always-on Windows or Raspberry Pi home agent that enrolls securely, captures one bounded H.264 event clip for eligible committed events, uploads it with an agent signature, reports health, and cleans every local clip artifact after verified upload or bounded expiry.

**Architecture:** Keep the current FastAPI, camera, PostgreSQL, MQTT, and rule semantics intact. `CameraService` only updates the latest annotated JPEG; one `ClipRecorder` sampler emits the latest JPEG, repeated when unchanged, at exactly 10 Hz into a ten-second `FrameRing` and into FFmpeg only while a clip is active. Eligible rule rows insert a transactional outbox row in the same commit, and a separate dispatcher marks it processed only after `ClipRecorder.on_trigger` returns normally. A bounded disk queue owns the H.264 MP4 until `SignedClipUploadClient` verifies the server receipt; the supervisor binds Uvicorn only to `127.0.0.1`, starts `cloudflared` with an ACL-only token file, and is hosted by pywin32 on Windows or systemd on Raspberry Pi.

**Tech Stack:** Python 3.12.13, FastAPI 0.139.0, Pydantic 2.13.4, httpx 0.28.1, cryptography 49.0.0 Ed25519, pywin32 312 on Windows, BtbN FFmpeg 8.1.2-22-g94138f6973, cloudflared 2026.7.2, pytest 9.1.1, PowerShell, bash, systemd.

## Global Constraints

- Do not change the current eating, resting, `no_meal_12h`, or `bed_sensor_mismatch` rule thresholds, ordering, deduplication, persistence, restart, or rollback behavior.
- Eligible clip triggers are only newly committed `eating`, `resting`, and `bed_sensor_mismatch` rows. `no_meal_12h` must never call the clip recorder.
- `ClipTrigger.occurred_at` is the persisted domain timestamp. The clip window starts when the committed outbox row is accepted by `ClipRecorder.on_trigger`, because a behavior may be committed after its domain start time.
- One sampler owns all 100 ms buckets. Before a trigger it writes the latest annotated JPEG, repeating it when unchanged, into the in-memory buckets `[trigger_bucket - 100, trigger_bucket)`; after acceptance it writes buckets `[trigger_bucket, trigger_bucket + 200)`. This yields exactly 300 frames and 30.000 seconds at 10 Hz.
- A trigger received while the current post-roll is open coalesces only when its full twenty-second post-roll keeps total clip duration at or below 120 seconds. Otherwise `on_trigger` rejects it without mutation; the outbox retries it after the current clip closes, starting a new bounded clip.
- The ring stores no file and retains exactly the latest 100 sampled buckets, for a hard maximum of 100 annotated JPEGs and ten wall-clock seconds. FFmpeg is never started merely to maintain pre-roll.
- A second trigger exactly fifteen seconds after the first extends the single output to 450 frames and 45.000 seconds; ffprobe duration is the acceptance test for both the default and overlap cases.
- FFmpeg starts only after an eligible trigger. It reads JPEG bytes from stdin and writes H.264/YUV420P MP4 to a random `.partial.mp4`, which is atomically renamed only after exit code zero.
- The persistent retry queue holds at most eight completed MP4s, retries after 5, 30, 120, then 600 seconds, expires an entry exactly one hour after creation, and deletes MP4 plus sidecar after verified upload or expiry.
- Every runtime, connector-token, queue-sidecar, and partial-MP4 file is created with mode `0600` on POSIX or a restricted owner/SYSTEM DACL on Windows before the first secret or video byte is written.
- Writer-queue insertion is non-blocking. Saturation aborts FFmpeg, removes the partial file, records `writer_queue_full`, and degrades agent health without blocking the camera or rule worker.
- Eligible rows and `clip_trigger_outbox` rows commit atomically. The dispatcher retries an unprocessed row after recorder rejection/crash and sets `processed_at` only after normal `ClipRecorder.on_trigger` return.
- Upload success requires HTTP `201` and a strictly validated JSON receipt with exactly `id`, `createdAt`, and `expiresAt`. The BFF verifies the signed content digest; any other status or response shape remains retryable failure.
- Runtime configuration, Ed25519 private key, connector token, and token file are ACL-restricted: current service identity plus SYSTEM on Windows, mode `0600` and `petcare:petcare` ownership on Raspberry Pi.
- The enrollment code is read with `getpass` and must be canonical 22-character unpadded base64url decoding to exactly 16 bytes; private key and connector token never appear in Git, command arguments, URLs, browser storage, logs, exception representations, or health responses.
- Uvicorn binds exactly `127.0.0.1:8000`. PostgreSQL remains loopback `:55432`; no FastAPI, webcam, PostgreSQL, or MQTT public listener is added.
- Do not add a health alias or any other FastAPI route. The existing API route set remains exact; remote online checks use the authoritative `/api/dashboard/summary`, while agent diagnostics remain in-process, CLI, and service status only.
- The remote provisioning endpoint is implemented by a later plan. This plan fixes its agent-side contract at `POST /api/petcare/agent/enroll` (strict HTTP 201 response) and `POST /api/petcare/agent/clips`.
- The production Windows service install, Raspberry Pi systemd install, Cloudflare enrollment, or external resource creation remains an approval-gated operation; this plan only implements and tests the local code and installers.

## Frozen Interfaces For Later Plans

These names, fields, and call shapes are the cross-plan contract. Later BFF and dashboard plans consume them without renaming:

```python
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Literal


@dataclass(frozen=True, slots=True)
class ClipTrigger:
    event_type: Literal["eating", "resting", "bed_sensor_mismatch"]
    event_id: int
    occurred_at: datetime


class FrameRing:
    def push(self, jpeg: bytes, observed_at: datetime) -> None:
        """Retain an annotated JPEG in the exact bounded ten-second memory window."""


class ClipRecorder:
    def on_trigger(self, trigger: ClipTrigger) -> None:
        """Start or extend the active post-commit clip window without changing rule state."""


class SignedClipUploadClient:
    def upload(self, path: Path, metadata: ClipMetadata) -> UploadReceipt:
        """Upload one signed MP4 and return only after the strict 201 receipt is validated."""
```

`ClipMetadata` is the local queue-sidecar value object with these exact keys; it is not sent in an upload header:

```json
{"camera_id":"pc-webcam-01","ended_at":"2026-07-20T04:00:20.000000Z","events":[{"event_id":41,"event_type":"eating","occurred_at":"2026-07-20T03:59:30.000000Z"}],"started_at":"2026-07-20T03:59:50.000000Z"}
```

The upload body is raw `video/mp4`. Enrollment returns the server camera UUID used in `X-PetCare-Camera-Id`. `X-PetCare-Nonce` is exactly 16 random bytes encoded as 22-character base64url without padding. `X-PetCare-Content-SHA256` is the case-preserving, 43-character unpadded base64url encoding of the MP4 SHA-256 bytes; never lowercase or otherwise normalize it. `X-PetCare-Events` is the comma-separated sequence `event_type:event_id`, sorted by event type and then decimal event ID with no duplicates. The Ed25519 signature covers this exact UTF-8 string, including the final newline:

```text
PETCARE-CLIP-V1
POST
/api/petcare/agent/clips
<agent-id>
<camera-id>
<unix-seconds>
<nonce>
<content-sha256>
<started-at>
<ended-at>
<comma-separated events sorted by event-type then event-id>

```

Headers are exactly `Content-Type: video/mp4`, `Content-Length`, `X-PetCare-Agent-Id`, `X-PetCare-Camera-Id`, `X-PetCare-Timestamp`, `X-PetCare-Nonce`, `X-PetCare-Content-SHA256`, `X-PetCare-Started-At`, `X-PetCare-Ended-At`, `X-PetCare-Events`, and `X-PetCare-Signature`. The BFF returns HTTP `201` with `{"id","createdAt","expiresAt"}` and rejects timestamp skew over 300 seconds, nonce reuse, malformed base64url, digest mismatch, unsorted or duplicate events, invalid durations, unknown/revoked agents, and invalid signatures.

## File Map

- Create `backend/app/clip_contracts.py`: frozen trigger, frame, metadata, and upload receipt value objects.
- Create `backend/app/frame_ring.py`: exact ten-second, 10 Hz, 100-frame sampled annotated JPEG memory ring.
- Create `backend/app/clip_outbox.py`: transactional outbox dispatcher and recorder-acceptance retry.
- Create `backend/app/agent_config.py`: enrollment response parsing, Ed25519 key generation, atomic runtime-file persistence, and OS ACL enforcement.
- Create `backend/app/agent_client.py`: canonical request signing, verified streaming upload, and enrollment HTTP call.
- Consume `contracts/petcare-agent-wire-v1.json`: the integration plan owns the single shared Python/TypeScript enrollment and clip golden vector; this plan never creates or rewrites it.
- Create `backend/app/clip_recorder.py`: overlap coalescing, FFmpeg stdin writer, bounded persistent retry queue, expiry, and cleanup.
- Create `backend/app/agent_runtime.py`: CLI, child-process supervisor, loopback-only Uvicorn, cloudflared token-file launch, and health snapshot.
- Create `backend/app/agent_lifecycle.py`: component-owned construction/start/stop hooks consumed by the integration-owned FastAPI lifespan.
- Create `backend/app/windows_service.py`: minimal pywin32 service host for the supervisor.
- Create `backend/app/agent_health.py`: in-process `AgentHealthSnapshot` and secret-free restricted status-file serializer.
- Modify `backend/app/camera_service.py`: send successfully persisted annotated JPEGs to the recorder.
- Modify `backend/app/rules.py`: insert one eligible outbox row in the same transaction as the event row; do not call the recorder from the commit path.
- Modify `backend/app/models.py`: add the minimal eligible-event outbox model.
- Create `backend/migrations/versions/0002_clip_trigger_outbox.py`: outbox table, constraints, and unique event identity.
- Consume the integration-owned `backend/pyproject.toml`, `backend/uv.lock`, `tools/platform-manifest.json`, validator, and validator tests as read-only runtime prerequisites.
- Create `tools/bootstrap_agent_runtime.ps1` and `tools/bootstrap_agent_runtime.sh`: verify and extract only the manifest-backed agent artifacts.
- Create `tools/tests/test_bootstrap_agent_runtime.ps1` and `tools/tests/test_bootstrap_agent_runtime.py`: fixture, hash-mutation, path, version, and architecture tests.
- Create `packaging/windows/install-home-agent.ps1`: install/remove/query the Windows service without putting a secret on argv.
- Create `packaging/linux/petcare-agent.service` and `packaging/linux/install-home-agent.sh`: hardened Raspberry Pi systemd package.
- Create `packaging/tests/test_windows_home_agent_packaging.py`: Windows-only service/ACL/static checks.
- Create `packaging/tests/test_linux_home_agent_packaging.py`: Raspberry Pi systemd/fixture checks.
- Create `backend/tests/test_frame_ring.py`, `backend/tests/test_agent_config.py`, `backend/tests/test_agent_client.py`, `backend/tests/test_clip_recorder.py`, `backend/tests/test_agent_runtime.py`, `backend/tests/test_agent_health.py`, `backend/tests/test_agent_lifecycle.py`, and `backend/tests/test_windows_service.py`.
- Modify `backend/tests/test_camera_service.py`, `backend/tests/test_rules.py`, and `backend/tests/test_rule_worker.py`: integration and lifecycle regression evidence.
- Create `backend/tests/test_clip_outbox.py` and modify `backend/tests/test_migrations.py`: atomic commit, retry, acceptance, and migration evidence.

## Cross-Plan Ownership And Serialization DAG

- The remote-integration contract-fixture step owns `contracts/petcare-agent-wire-v1.json` and must finish before Home Task 3 or the BFF signature tests consume it.
- Integration Task 1 exclusively owns `tools/platform-manifest.json`, `tools/validate_platform_manifest.py`, `tools/tests/test_validate_platform_manifest.py`, `backend/pyproject.toml`, `backend/uv.lock`, shared dashboard locks, and `contracts/petcare-agent-wire-v1.json`. Home workers never edit or stage them.
- Integration Task 2 exclusively owns `backend/app/main.py`. Home exports `backend/app/agent_lifecycle.py`; Integration Task 2 alone attaches that hook to the completed Task 12 lifespan, proves the final shutdown order, and runs the no-extra-route regression.
- The required order is `Integration Task 1 shared-authority commit -> Home Task 1 read-only verification -> Home Tasks 2-10`. Any prerequisite drift returns to the single integration owner for correction.
- Home owns only its component modules, migrations, bootstrap scripts/tests, and Windows/Linux packaging listed below. Those may run in parallel only after the shared-authority gate passes and when their file sets are disjoint.

---

### Task 1: Verify The Integration-Owned Runtime Prerequisite

**Files:**
- Test/consume only: `tools/platform-manifest.json`
- Test/consume only: `tools/validate_platform_manifest.py`
- Test/consume only: `tools/tests/test_validate_platform_manifest.py`
- Test/consume only: `backend/pyproject.toml`
- Test/consume only: `backend/uv.lock`

**Interfaces:**
- Consumes: integration-owned exact Python/uv, FFmpeg, cloudflared, cryptography, httpx, and Windows pywin32 pins.
- Produces: read-only prerequisite evidence for Home Tasks 2-10; no install, edit, stage, or commit.

- [ ] **Step 1: Confirm the single-integrator gate is present**

Do not proceed until Integration Task 1 has committed all five shared files. Inspect the integration commit and require these exact values through the existing validator/tests rather than duplicating another authority:

- Python `3.12.13+20260623` and uv `0.11.28` for Linux arm64;
- FFmpeg `8.1.2-22-g94138f6973` from `autobuild-2026-07-19-13-12` for Windows x64, Linux x64, and Linux arm64;
- cloudflared `2026.7.2` for the same three platforms;
- `cryptography==49.0.0`, production `httpx==0.28.1`, and Windows-only `pywin32==312`;
- validator canonical manifest SHA `837698DE02BB63C5A056A09EB702DFDC5B753842BF514304E55D86A14D253052`.

If a value is missing or differs, stop with `integration runtime prerequisite missing`; do not patch, install, regenerate, or stage a shared file from this plan.

- [ ] **Step 2: Run read-only manifest and lock verification**

```powershell
Set-Location backend
uv run pytest ../tools/tests/test_validate_platform_manifest.py -q
uv lock --check
```

Expected: both commands PASS against the integration-owned files. Any failure blocks Home Task 2 and is returned to Integration Task 1 for correction.

- [ ] **Step 3: Prove Home Task 1 did not mutate shared authority**

```powershell
git diff --exit-code -- tools/platform-manifest.json tools/validate_platform_manifest.py tools/tests/test_validate_platform_manifest.py backend/pyproject.toml backend/uv.lock
git status --short -- tools/platform-manifest.json tools/validate_platform_manifest.py tools/tests/test_validate_platform_manifest.py backend/pyproject.toml backend/uv.lock
```

Expected: no diff and no status entries attributable to Home Task 1. There is intentionally no Task 1 commit.

---

### Task 2: Freeze Clip Value Objects And Exact Frame Ring

**Files:**
- Create: `backend/app/clip_contracts.py`
- Create: `backend/app/frame_ring.py`
- Create: `backend/tests/test_frame_ring.py`

**Interfaces:**
- Produces: `ClipTrigger(event_type, event_id, occurred_at)`.
- Produces: `FrameRing.push(jpeg, observed_at)` and `FrameRing.snapshot(through)`.
- Produces: `ClipMetadata`, `ClipEventMetadata`, and `UploadReceipt` for recorder and uploader tasks.

- [ ] **Step 1: Write boundary, capacity, and validation tests**

Create `backend/tests/test_frame_ring.py`:

```python
from datetime import UTC, datetime, timedelta

import pytest

from app.clip_contracts import ClipEventMetadata, ClipMetadata, ClipTrigger, UploadReceipt
from app.frame_ring import FrameRing


NOW = datetime(2026, 7, 20, 4, 0, tzinfo=UTC)


def test_ring_keeps_exact_ten_second_boundary_and_is_hard_bounded() -> None:
    ring = FrameRing()
    for index in range(110):
        ring.push(f"jpeg-{index}".encode(), NOW + timedelta(milliseconds=index * 100))
    frames = ring.snapshot(NOW + timedelta(seconds=11))
    assert len(frames) == 100
    assert frames[0].observed_at == NOW + timedelta(seconds=1)
    assert frames[-1].observed_at == NOW + timedelta(seconds=10, milliseconds=900)
    assert ring.frame_count == 100


def test_ring_replaces_same_bucket_and_never_writes_to_disk() -> None:
    ring = FrameRing()
    ring.push(b"first", NOW)
    ring.push(b"latest", NOW)
    assert [(frame.jpeg, frame.observed_at) for frame in ring.snapshot(NOW + timedelta(milliseconds=100))] == [
        (b"latest", NOW)
    ]


def test_ring_rejects_invalid_or_reversed_input() -> None:
    ring = FrameRing()
    with pytest.raises(ValueError, match="annotated JPEG"):
        ring.push(b"", NOW)
    with pytest.raises(ValueError, match="timezone-aware"):
        ring.push(b"jpeg", NOW.replace(tzinfo=None))
    ring.push(b"jpeg", NOW)
    with pytest.raises(ValueError, match="monotonic observed_at"):
        ring.push(b"older", NOW - timedelta(milliseconds=100))
    with pytest.raises(ValueError, match="100 ms bucket"):
        ring.push(b"unaligned", NOW + timedelta(milliseconds=101))
    with pytest.raises(ValueError, match="contiguous sampler bucket"):
        ring.push(b"gap", NOW + timedelta(milliseconds=300))


def test_clip_contracts_are_strict_and_canonical() -> None:
    trigger = ClipTrigger("eating", 41, NOW)
    event = ClipEventMetadata.from_trigger(trigger)
    metadata = ClipMetadata("pc-webcam-01", NOW - timedelta(seconds=10), NOW + timedelta(seconds=20), (event,))
    assert metadata.canonical_json() == (
        b'{"camera_id":"pc-webcam-01","ended_at":"2026-07-20T04:00:20.000000Z",'
        b'"events":[{"event_id":41,"event_type":"eating","occurred_at":"2026-07-20T04:00:00.000000Z"}],'
        b'"started_at":"2026-07-20T03:59:50.000000Z"}'
    )
    with pytest.raises(ValueError, match="eligible event type"):
        ClipTrigger("no_meal_12h", 42, NOW)  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="eligible event type"):
        ClipEventMetadata("no_meal_12h", 1, "2026-07-20T04:00:00.000000Z")  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="canonical UTC"):
        ClipEventMetadata("eating", 1, "2026-07-20T04:00:00+00:00")
    with pytest.raises(ValueError, match="opaque clip id"):
        UploadReceipt("", "2026-07-20T04:00:00.000Z", "2026-07-20T05:00:00.000Z")
    with pytest.raises(ValueError, match="BFF UTC timestamp"):
        UploadReceipt("clip_01", "2026-07-20T04:00:00+00:00", "2026-07-20T05:00:00.000Z")
    with pytest.raises(ValueError, match="expiresAt"):
        UploadReceipt("clip_01", "2026-07-20T05:00:00.000Z", "2026-07-20T04:00:00.000Z")
```

- [ ] **Step 2: Run the new tests and verify imports fail**

Run:

```powershell
Set-Location backend
uv run pytest tests/test_frame_ring.py -q
```

Expected: FAIL during collection with `ModuleNotFoundError: No module named 'app.clip_contracts'`.

- [ ] **Step 3: Implement the strict frozen contracts**

Create `backend/app/clip_contracts.py` with dataclasses that validate `event_id > 0`, timezone-aware UTC timestamps, the exact BFF receipt shape, fixed local camera ID, sorted unique event identities, and `ended_at > started_at`. The JSON serializer is only for the persistent local sidecar; upload signing uses the separate wire fields in Task 3:

```python
from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from typing import Literal


EligibleEventType = Literal["eating", "resting", "bed_sensor_mismatch"]
ELIGIBLE_EVENT_TYPES = frozenset(("eating", "resting", "bed_sensor_mismatch"))


def utc_text(value: datetime) -> str:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("timestamp must be timezone-aware")
    return value.astimezone(UTC).isoformat(timespec="microseconds").replace("+00:00", "Z")


def canonical_utc_text(value: str) -> str:
    if not isinstance(value, str) or not value.endswith("Z"):
        raise ValueError("canonical UTC timestamp required")
    try:
        parsed = datetime.fromisoformat(value[:-1] + "+00:00")
    except ValueError as error:
        raise ValueError("canonical UTC timestamp required") from error
    if utc_text(parsed) != value:
        raise ValueError("canonical UTC timestamp required")
    return value


def bff_utc_datetime(value: str) -> datetime:
    if not isinstance(value, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3}Z", value):
        raise ValueError("BFF UTC timestamp required")
    try:
        return datetime.fromisoformat(value[:-1] + "+00:00")
    except ValueError as error:
        raise ValueError("BFF UTC timestamp required") from error


@dataclass(frozen=True, slots=True)
class ClipTrigger:
    event_type: EligibleEventType
    event_id: int
    occurred_at: datetime

    def __post_init__(self) -> None:
        if not isinstance(self.event_type, str) or self.event_type not in ELIGIBLE_EVENT_TYPES:
            raise ValueError("eligible event type required")
        if type(self.event_id) is not int or self.event_id <= 0:
            raise ValueError("event_id must be positive")
        if not isinstance(self.occurred_at, datetime):
            raise ValueError("occurred_at must be a datetime")
        utc_text(self.occurred_at)


@dataclass(frozen=True, slots=True)
class ClipEventMetadata:
    event_type: EligibleEventType
    event_id: int
    occurred_at: str

    def __post_init__(self) -> None:
        if not isinstance(self.event_type, str) or self.event_type not in ELIGIBLE_EVENT_TYPES:
            raise ValueError("eligible event type required")
        if type(self.event_id) is not int or self.event_id <= 0:
            raise ValueError("event_id must be positive")
        canonical_utc_text(self.occurred_at)

    @classmethod
    def from_trigger(cls, trigger: ClipTrigger) -> "ClipEventMetadata":
        return cls(trigger.event_type, trigger.event_id, utc_text(trigger.occurred_at))


@dataclass(frozen=True, slots=True)
class ClipMetadata:
    camera_id: Literal["pc-webcam-01"]
    started_at: datetime
    ended_at: datetime
    events: tuple[ClipEventMetadata, ...]

    def __post_init__(self) -> None:
        if (
            self.camera_id != "pc-webcam-01"
            or not isinstance(self.started_at, datetime)
            or not isinstance(self.ended_at, datetime)
            or type(self.events) is not tuple
            or not self.events
            or any(not isinstance(event, ClipEventMetadata) for event in self.events)
        ):
            raise ValueError("invalid clip metadata")
        utc_text(self.started_at)
        utc_text(self.ended_at)
        if self.ended_at <= self.started_at:
            raise ValueError("invalid clip metadata")
        if len({(event.event_type, event.event_id) for event in self.events}) != len(self.events):
            raise ValueError("clip events must be unique")
        if self.events != tuple(sorted(self.events, key=lambda event: (event.event_type, event.event_id))):
            raise ValueError("clip events must use canonical order")

    def canonical_json(self) -> bytes:
        value = {
            "camera_id": self.camera_id,
            "ended_at": utc_text(self.ended_at),
            "events": [asdict(event) for event in self.events],
            "started_at": utc_text(self.started_at),
        }
        return json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")


@dataclass(frozen=True, slots=True)
class UploadReceipt:
    id: str
    createdAt: str
    expiresAt: str

    def __post_init__(self) -> None:
        if not isinstance(self.id, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", self.id):
            raise ValueError("opaque clip id required")
        if not isinstance(self.createdAt, str) or not isinstance(self.expiresAt, str):
            raise ValueError("BFF UTC timestamp required")
        created = bff_utc_datetime(self.createdAt)
        expires = bff_utc_datetime(self.expiresAt)
        if expires <= created:
            raise ValueError("expiresAt must be after createdAt")
```

- [ ] **Step 4: Implement the exact time-bucketed ring**

Create `backend/app/frame_ring.py`:

```python
from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from datetime import UTC, datetime
from threading import Lock

from .clip_contracts import utc_text


BUCKET_MICROSECONDS = 100_000
MAX_FRAMES = 100
UTC_EPOCH = datetime(1970, 1, 1, tzinfo=UTC)


def bucket_number(value: datetime) -> int:
    utc_text(value)
    delta = value.astimezone(UTC) - UTC_EPOCH
    microseconds = ((delta.days * 86_400 + delta.seconds) * 1_000_000) + delta.microseconds
    if microseconds % BUCKET_MICROSECONDS:
        raise ValueError("observed_at must align to a 100 ms bucket")
    return microseconds // BUCKET_MICROSECONDS


@dataclass(frozen=True, slots=True)
class AnnotatedFrame:
    jpeg: bytes
    observed_at: datetime


class FrameRing:
    def __init__(self) -> None:
        self._frames: deque[AnnotatedFrame] = deque(maxlen=MAX_FRAMES)
        self._lock = Lock()

    @property
    def frame_count(self) -> int:
        with self._lock:
            return len(self._frames)

    def push(self, jpeg: bytes, observed_at: datetime) -> None:
        if not jpeg:
            raise ValueError("annotated JPEG must not be empty")
        bucket = bucket_number(observed_at)
        with self._lock:
            if self._frames and observed_at < self._frames[-1].observed_at:
                raise ValueError("monotonic observed_at required")
            if self._frames:
                previous_bucket = bucket_number(self._frames[-1].observed_at)
                if bucket == previous_bucket:
                    self._frames[-1] = AnnotatedFrame(bytes(jpeg), observed_at)
                elif bucket == previous_bucket + 1:
                    self._frames.append(AnnotatedFrame(bytes(jpeg), observed_at))
                else:
                    raise ValueError("contiguous sampler bucket required")
            else:
                self._frames.append(AnnotatedFrame(bytes(jpeg), observed_at))

    def snapshot(self, through: datetime) -> tuple[AnnotatedFrame, ...]:
        through_bucket = bucket_number(through)
        with self._lock:
            return tuple(
                frame for frame in self._frames
                if through_bucket - MAX_FRAMES <= bucket_number(frame.observed_at) < through_bucket
            )
```

- [ ] **Step 5: Run tests and commit the frozen contract**

Run:

```powershell
Set-Location backend
uv run pytest tests/test_frame_ring.py -q
```

Expected: 4 tests PASS.

```powershell
git add backend/app/clip_contracts.py backend/app/frame_ring.py backend/tests/test_frame_ring.py
git commit -m "feat(agent): add bounded annotated frame ring"
```

---

### Task 3: Enrollment, Ed25519 Identity, And ACL Runtime File

**Files:**
- Create: `backend/app/agent_config.py`
- Create: `backend/app/agent_client.py`
- Create: `backend/tests/test_agent_config.py`
- Create: `backend/tests/test_agent_client.py`
- Test/consume: `contracts/petcare-agent-wire-v1.json` (owned by the remote-integration plan)

**Interfaces:**
- Consumes: fixed enrollment route `POST /api/petcare/agent/enroll`.
- Consumes: integration-owned `contracts/petcare-agent-wire-v1.json`; execute that contract-fixture task first if the file is not present.
- Produces: `AgentRuntimeConfig`, `enroll(origin, code, local_settings, path)`, and `SignedClipUploadClient.upload(path, metadata)`.

- [ ] **Step 1: Write config, ACL, enrollment, and signature tests**

Create tests that use a temporary runtime file and `httpx.MockTransport`. The key assertions are exact:

```python
def test_enrollment_generates_ed25519_identity_and_writes_no_secret_to_argv_or_repr(tmp_path, monkeypatch):
    captured = {}

    def handler(request):
        captured["json"] = json.loads(request.content)
        return httpx.Response(201, json={
            "agent_id": "agent_01",
            "camera_id": "camera_01",
            "connector_token": "connector-secret",
        })

    output = tmp_path / "agent.json"
    config = enroll(
        origin="https://petcare.example",
        code="AQEBAQEBAQEBAQEBAQEBAQ",
        local_settings=LocalSettings(
            database_url="postgresql+psycopg://petcare:db-secret@127.0.0.1:55432/petcare",
            mqtt_profile="local_live",
            mqtt_username="petcare",
            mqtt_password="mqtt-secret",
        ),
        path=output,
        transport=httpx.MockTransport(handler),
    )
    assert captured["json"]["algorithm"] == "Ed25519"
    assert captured["json"]["enrollment_code"] == "AQEBAQEBAQEBAQEBAQEBAQ"
    assert "private_key" not in captured["json"]
    assert config.connector_token.get_secret_value() == "connector-secret"
    assert "connector-secret" not in repr(config)
    assert "mqtt-secret" not in repr(config)
    assert json.loads(output.read_text(encoding="utf-8"))["connector_token"] == "connector-secret"
    if os.name == "posix":
        assert stat.S_IMODE(output.stat().st_mode) == 0o600
```

```python
def test_signed_upload_matches_petcare_clip_v1_and_validates_receipt(tmp_path):
    private_key = Ed25519PrivateKey.generate()
    public_key = private_key.public_key()
    video = tmp_path / "clip.mp4"
    video.write_bytes(b"mp4-bytes")
    metadata = ClipMetadata(
        "pc-webcam-01",
        NOW - timedelta(seconds=10),
        NOW + timedelta(seconds=20),
        (ClipEventMetadata.from_trigger(ClipTrigger("eating", 41, NOW)),),
    )

    def handler(request):
        assert request.url.path == "/api/petcare/agent/clips"
        body = request.read()
        assert body == b"mp4-bytes"
        assert request.headers["X-PetCare-Nonce"] == "AAAAAAAAAAAAAAAAAAAAAA"
        assert request.headers["X-PetCare-Content-SHA256"] == b64url(hashlib.sha256(body).digest())
        assert request.headers["X-PetCare-Started-At"] == "2026-07-20T03:59:50.000000Z"
        assert request.headers["X-PetCare-Ended-At"] == "2026-07-20T04:00:20.000000Z"
        assert request.headers["X-PetCare-Events"] == "eating:41"
        canonical = "\n".join((
            "PETCARE-CLIP-V1", "POST", request.url.path,
            request.headers["X-PetCare-Agent-Id"],
            request.headers["X-PetCare-Camera-Id"],
            request.headers["X-PetCare-Timestamp"],
            request.headers["X-PetCare-Nonce"],
            request.headers["X-PetCare-Content-SHA256"],
            request.headers["X-PetCare-Started-At"],
            request.headers["X-PetCare-Ended-At"],
            request.headers["X-PetCare-Events"], "",
        )).encode()
        public_key.verify(
            base64.urlsafe_b64decode(request.headers["X-PetCare-Signature"] + "=="),
            canonical,
        )
        return httpx.Response(201, json={
            "id": "clip_01",
            "createdAt": "2026-07-20T04:00:00.000Z",
            "expiresAt": "2026-07-20T05:00:00.000Z",
        })

    client = SignedClipUploadClient(
        origin="https://petcare.example",
        agent_id="agent_01",
        camera_id="camera_01",
        private_key=private_key,
        transport=httpx.MockTransport(handler),
        now=lambda: NOW,
        nonce=lambda: "AAAAAAAAAAAAAAAAAAAAAA",
    )
    assert client.upload(video, metadata).id == "clip_01"
```

Load the repository-wide `contracts/petcare-agent-wire-v1.json` owned by the remote-integration plan; do not create a clip-only fixture or restate its bytes in a Python test. Assert the real enrollment serializer and clip signer reproduce every enrollment request, digest, canonical final-newline text, signature, header, and strict 201 receipt field in that file. The integration and BFF TypeScript tests consume the identical path.

Also add tests for HTTPS-only origins, rejecting enrollment codes that are not canonical 22-character unpadded base64url for exactly 16 bytes, malformed enrollment responses, enrollment camera UUID propagation, atomic replace failure preserving the previous config, POSIX `0600` and Windows owner/SYSTEM DACL being applied to the empty exclusive temporary file before the first secret byte is written, Windows `icacls` arguments using service/user SIDs, non-201 upload, missing/extra/wrong-type/duplicate/invalid receipt fields, malformed JSON/UTF-8, malformed 22-character nonce, multi-event canonical type/decimal-ID sorting, case-preserving digest equality with the golden vector, and 30-second HTTP timeout. Assert the retired metadata headers are absent.

- [ ] **Step 2: Run the tests and verify both modules are missing**

Run:

```powershell
Set-Location backend
uv run pytest tests/test_agent_config.py tests/test_agent_client.py -q
```

Expected: FAIL during collection for missing `app.agent_config` and `app.agent_client`.

- [ ] **Step 3: Implement key generation, strict config, and atomic ACL writes**

In `backend/app/agent_config.py`, define `LocalSettings` and `AgentRuntimeConfig` with `ConfigDict(extra="forbid", strict=True, hide_input_in_errors=True)`. Store database URL, MQTT password, private key, and connector token as `SecretStr`; the atomic serializer explicitly calls `get_secret_value()` while normal `repr` remains redacted. Validate HTTPS origin, loopback PostgreSQL `:55432`, fixed local camera ID `pc-webcam-01`, nonempty MQTT values, and base64url raw 32-byte Ed25519 keys. Generate keys with:

```python
private_key = Ed25519PrivateKey.generate()
private_raw = private_key.private_bytes(Encoding.Raw, PrivateFormat.Raw, NoEncryption())
public_raw = private_key.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)
```

Create a random sibling `.new` path exclusively. On POSIX, use `os.open(..., os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)` so mode `0600` exists at creation. On Windows, create the empty exclusive file and run `protect_runtime_file` before opening it for the first secret write. Only then serialize, flush, `os.fsync`, and `os.replace`; fsync the parent directory on POSIX. If protection or writing fails, unlink only that temporary file and preserve the previous config. `protect_runtime_file(path, windows_identity_sid)` must execute this exact Windows command as an argument list:

```python
[
    "icacls.exe", str(path),
    "/inheritance:r",
    "/grant:r", f"*{windows_identity_sid}:(F)", "*S-1-5-18:(F)",
    "/remove:g", "*S-1-5-32-545",
]
```

On POSIX call `os.chmod(path, 0o600)`. Never include secret model values in exception messages or `repr`.

- [ ] **Step 4: Implement enrollment and the exact signed streaming upload**

In `backend/app/agent_client.py`, implement the frozen canonical request. The core upload method is:

```python
def upload(self, path: Path, metadata: ClipMetadata) -> UploadReceipt:
    content_digest = content_sha256(path)
    timestamp = str(int(self._now().timestamp()))
    nonce = self._nonce()
    validate_nonce(nonce)
    started_at = utc_text(metadata.started_at)
    ended_at = utc_text(metadata.ended_at)
    events = ",".join(
        f"{event.event_type}:{event.event_id}"
        for event in sorted(metadata.events, key=lambda event: (event.event_type, event.event_id))
    )
    canonical = "\n".join((
        "PETCARE-CLIP-V1", "POST", UPLOAD_PATH, self.agent_id, self.camera_id,
        timestamp, nonce, content_digest, started_at, ended_at, events, "",
    )).encode("utf-8")
    signature = b64url(self.private_key.sign(canonical))
    headers = {
        "Content-Type": "video/mp4",
        "Content-Length": str(path.stat().st_size),
        "X-PetCare-Agent-Id": self.agent_id,
        "X-PetCare-Camera-Id": self.camera_id,
        "X-PetCare-Timestamp": timestamp,
        "X-PetCare-Nonce": nonce,
        "X-PetCare-Content-SHA256": content_digest,
        "X-PetCare-Started-At": started_at,
        "X-PetCare-Ended-At": ended_at,
        "X-PetCare-Events": events,
        "X-PetCare-Signature": signature,
    }
    with path.open("rb") as source:
        response = self._client.post(UPLOAD_PATH, headers=headers, content=source)
    if response.status_code != 201:
        raise UploadVerificationError(f"unexpected upload status: {response.status_code}")
    return parse_upload_receipt(response.content)
```

`parse_upload_receipt` decodes strict UTF-8 and uses `json.loads(..., object_pairs_hook=...)` to reject duplicate keys before requiring a dict with exactly `id`, `createdAt`, and `expiresAt`; wrap all decode/schema/value failures as `UploadVerificationError` with no response body in the message. Construct the internal `httpx.Client(base_url=origin, timeout=httpx.Timeout(30.0), transport=transport)`. `content_sha256` reads 1 MiB chunks and returns `b64url(hash.digest())` exactly, preserving base64url case; `b64url` strips only `=` padding. The default nonce source is `b64url(secrets.token_bytes(16))`, and the shared validator enforces the 22-character canonical unpadded base64url form for both nonce and enrollment code, decoding to exactly 16 bytes. `enroll` sends only `enrollment_code`, `algorithm`, `public_key`, and fixed `local_camera_id`; it requires HTTP 201, duplicate-safe strict JSON with exactly `agent_id`, `camera_id`, and `connector_token`, then combines those validated server values with local secrets and writes the ACL file.

- [ ] **Step 5: Verify and commit identity plus transport**

Run:

```powershell
Set-Location backend
uv run pytest tests/test_agent_config.py tests/test_agent_client.py -q
```

Expected: all config and client tests PASS.

```powershell
git add backend/app/agent_config.py backend/app/agent_client.py backend/tests/test_agent_config.py backend/tests/test_agent_client.py
git commit -m "feat(agent): add secure enrollment and signed uploads"
```

---

### Task 4: Exact 10 Hz Sampler, Bounded FFmpeg Recorder, Retry, And Cleanup

**Files:**
- Create: `backend/app/clip_recorder.py`
- Create: `backend/tests/test_clip_recorder.py`

**Interfaces:**
- Consumes: irregular annotated JPEG updates, `FrameRing`, `ClipTrigger`, `ClipMetadata`, and `SignedClipUploadClient.upload(path, metadata)`.
- Produces: stable `ClipRecorder.on_frame(jpeg, observed_at)` (latest-frame update only), stable `ClipRecorder.on_trigger(trigger)`, deterministic `ClipRecorder.sample_bucket(at)`, `ClipRecorder.health()`, and `ClipRecorder.shutdown()`.

- [ ] **Step 1: Write RED tests for the wall-clock sampler and recorder**

Use an injected UTC clock, fake process, and explicit `sample_bucket` calls. Camera updates may arrive at any rate; only `sample_bucket` writes `FrameRing` and FFmpeg. Test the exact default window like this:

```python
def test_sampler_produces_exact_100_pre_and_200_post_buckets(tmp_path):
    process_factory = FakeFfmpegFactory()
    recorder = make_recorder(tmp_path, process_factory=process_factory)
    recorder.on_frame(b"old", NOW - timedelta(seconds=11))
    for index in range(-100, 0):
        if index == -37:
            recorder.on_frame(b"latest", NOW + timedelta(milliseconds=index * 100 - 1))
        recorder.sample_bucket(NOW + timedelta(milliseconds=index * 100))
    assert process_factory.calls == []  # no FFmpeg before a trigger

    recorder.on_trigger(ClipTrigger("eating", 41, NOW - timedelta(seconds=30)))
    for index in range(0, 200):
        recorder.sample_bucket(NOW + timedelta(milliseconds=index * 100))
    recorder.finish_due(NOW + timedelta(seconds=20))
    assert recorder.wait_idle(timeout=1.0)

    call = process_factory.calls[0]
    assert call.frame_count == 300
    assert call.frames[:63] == [b"old"] * 63
    assert call.frames[63:100] == [b"latest"] * 37
    assert call.frames[100:] == [b"latest"] * 200
    assert recorder.completed_metadata[0].started_at == NOW - timedelta(seconds=10)
    assert recorder.completed_metadata[0].ended_at == NOW + timedelta(seconds=20)
```

The fake stdin decodes each length-delimited test JPEG and records frame boundaries instead of concatenating ambiguous byte strings. Assert the exact `ffmpeg_command` argument vector. Add focused tests for:

- an irregular camera burst updating only the latest slot and a camera gap repeating the unchanged latest JPEG in every 100 ms bucket;
- ring buckets exactly `[trigger_bucket - 100, trigger_bucket)` and active buckets exactly `[trigger_bucket, trigger_bucket + 200)`, with no frame at the exclusive end;
- an overlapping `resting` trigger exactly 15 seconds later producing one MP4, 450 frames, a 45-second metadata window, and two events sorted by `(event_type, decimal event_id)`;
- repeated overlaps reaching exactly 120 seconds, followed by a trigger whose full post-roll would exceed 120 seconds being rejected without mutation and later accepted as a new 30-second clip after dispatcher retry;
- duplicate `(event_type, event_id)` not duplicating metadata; a trigger at the exact open end coalescing, while a trigger after finalization starts a new clip;
- no latest JPEG at startup causing `on_trigger` to reject without starting FFmpeg so the outbox remains retryable;
- queue capacity eight rejecting/deleting the ninth completed MP4 and reporting `queue_full`;
- retry times 5, 30, 120, and 600 seconds; one-hour expiry; startup cleanup of orphan partials, malformed sidecars, expired entries, and excess entries;
- strict invalid sidecar metadata/receipt handling, HTTP/upload failure, FFmpeg nonzero exit, broken pipe, shutdown during recording, and unlink retry paths leaving no unowned file;
- partial MP4 and sidecar permissions being restricted before the first video or metadata byte is written on POSIX and Windows;
- a stalled writer filling `WRITER_QUEUE_LIMIT`: `put_nowait` never blocks, FFmpeg is terminated, stdin is closed once, the partial is removed, `last_error == "writer_queue_full"`, health is degraded, and subsequent camera updates plus rule/outbox work continue;
- ring, disk queue, and writer queue sizes never exceeding their constants.

- [ ] **Step 2: Run RED**

```powershell
Set-Location backend
uv run pytest tests/test_clip_recorder.py -m "not ffmpeg_smoke" -q
```

Expected: FAIL during collection with `ModuleNotFoundError: No module named 'app.clip_recorder'`.

- [ ] **Step 3: Implement one sampler and the bounded writer state machine**

Use these fixed limits:

```python
PRE_ROLL_BUCKETS = 100
POST_ROLL_BUCKETS = 200
MAX_CLIP_DURATION = timedelta(seconds=120)
BUCKET_INTERVAL = timedelta(milliseconds=100)
QUEUE_EXPIRY = timedelta(hours=1)
QUEUE_LIMIT = 8
WRITER_QUEUE_LIMIT = 128
RETRY_DELAYS = (5, 30, 120, 600)
FFMPEG_FPS = 10
```

`on_frame` validates and atomically replaces `_latest_jpeg`; it never pushes the ring and never touches FFmpeg. One monotonic scheduler calls `sample_bucket` at UTC-aligned 100 ms boundaries. For every bucket, copy the latest JPEG once, repeat it unchanged when no newer JPEG exists, push that exact sample into `FrameRing`, and, if the bucket lies in the active half-open window, enqueue the same bytes with `put_nowait`.

On the first accepted trigger, floor `now()` to `trigger_bucket`, require an available latest sample and an exact 100-bucket ring snapshot, create and protect an empty random `.partial.mp4` before FFmpeg can write it, start FFmpeg, and enqueue the 100 pre-roll samples. Set `started_at = trigger_bucket - 10 seconds` and `ended_at = trigger_bucket + 20 seconds`. Never start FFmpeg merely to populate pre-roll. A trigger while `trigger_bucket <= active_end_bucket` coalesces only if `new_trigger_bucket + 20 seconds <= started_at + MAX_CLIP_DURATION`; then it extends the exclusive end and inserts a unique event in canonical type/decimal-ID order. Otherwise raise `RecorderRejected("clip_window_full")` without mutating events/end time, so the transactional outbox retries after the current clip closes and the next clip still gets a full 10+20-second window.

Build the process arguments in one function:

```python
def ffmpeg_command(ffmpeg: Path, output: Path) -> list[str]:
    return [
        str(ffmpeg), "-hide_banner", "-loglevel", "error", "-nostdin",
        "-f", "image2pipe", "-framerate", "10", "-vcodec", "mjpeg", "-i", "pipe:0",
        "-an", "-c:v", "libx264", "-preset", "veryfast", "-pix_fmt", "yuv420p",
        "-movflags", "+faststart", "-y", str(output),
    ]
```

The writer thread alone owns stdin. Every producer uses `put_nowait`; `queue.Full` atomically transitions the recording to failed, prevents further enqueue, terminates FFmpeg, closes stdin once, removes the partial, stores `writer_queue_full`, and wakes the supervisor without propagating into the camera or rule worker. At the exclusive end bucket, enqueue a sentinel, close stdin, wait, and atomically rename only on exit zero. Create the completed-file sidecar using the same secure-before-write helper from Task 3. `finish_due(at)` drives the same transition deterministically in tests and when the camera has stopped.

- [ ] **Step 4: Implement the persistent bounded upload queue**

Each restricted sidecar has this exact local-only schema and no key material:

```json
{"attempt":0,"created_at":"2026-07-20T04:00:20.000000Z","metadata":{"camera_id":"pc-webcam-01","ended_at":"2026-07-20T04:00:20.000000Z","events":[{"event_id":41,"event_type":"eating","occurred_at":"2026-07-20T03:59:30.000000Z"}],"started_at":"2026-07-20T03:59:50.000000Z"},"next_attempt_at":"2026-07-20T04:00:20.000000Z","video":"b35f.mp4"}
```

Create every MP4 and sidecar as owner-only before content is written: POSIX exclusive `0600`; Windows empty exclusive file followed by the owner/SYSTEM DACL before the writer opens it. On startup, strictly parse metadata, sort valid entries by `(created_at, video)`, keep at most eight, and delete invalid, expired, excess, and orphan partial files. On failure, increment `attempt`, choose `RETRY_DELAYS[min(attempt - 1, 3)]`, and securely replace the sidecar. On strict 201 receipt success or expiry, close handles before unlinking sidecar and MP4. A failed unlink becomes cleanup-only and retries every five seconds; it is never uploaded twice.

- [ ] **Step 5: Add real ffprobe duration acceptance tests**

Mark two tests `ffmpeg_smoke`. Load only manifest-backed `ffmpeg_path` and `ffprobe_path` from `.runtime/agent-tools.json`. Generate one valid annotated JPEG, feed it through the sampler for the default 300 buckets and overlap 450 buckets, and run:

```powershell
& $tools.paths.ffprobe_path -v error -select_streams v:0 -show_entries stream=codec_name,pix_fmt -show_entries format=duration -of json $clip
```

Assert `codec_name == "h264"`, `pix_fmt == "yuv420p"`, and `abs(duration - 30.000) <= 0.050` for the default clip. Assert the same codec/pixel format and `abs(duration - 45.000) <= 0.050` for the trigger at +15 seconds. These are the wall-clock acceptance tests, not frame-count-only proxies.

- [ ] **Step 6: Run GREEN and commit**

```powershell
Set-Location backend
uv run pytest tests/test_clip_recorder.py -m "not ffmpeg_smoke" -q
```

Expected: all non-smoke recorder tests PASS, including queue saturation and secure-before-write tests.

```powershell
git add backend/app/clip_recorder.py backend/tests/test_clip_recorder.py
git commit -m "feat(agent): record exact bounded event clips"
```

---

### Task 5: Wire Latest Annotated Frames And A Transactional Trigger Outbox

**Files:**
- Modify: `backend/app/camera_service.py`
- Modify: `backend/app/rules.py`
- Modify: `backend/app/models.py`
- Create: `backend/app/clip_outbox.py`
- Create: `backend/migrations/versions/0002_clip_trigger_outbox.py`
- Modify: `backend/tests/test_camera_service.py`
- Modify: `backend/tests/test_rules.py`
- Create: `backend/tests/test_clip_outbox.py`
- Modify: `backend/tests/test_migrations.py`

**Interfaces:**
- Consumes: `ClipRecorder.on_frame(jpeg, observed_at)` as a latest-frame update and `ClipRecorder.on_trigger(trigger)` as an acceptance boundary.
- Produces: `ClipTriggerOutbox`, `enqueue_clip_trigger(session, row)`, and `ClipOutboxDispatcher.dispatch_once()`.
- Preserves: existing `CameraFrameCommitted`, rule thresholds/state, and rollback semantics.

- [ ] **Step 1: Write RED camera tests for latest-frame handoff**

Pass a recorder fake through the existing `service_for` helper. Prove a successfully persisted annotated JPEG calls `on_frame` once after `commit` and before ingress `resolve`; a failed camera commit never calls it. Add a burst test proving three irregular camera callbacks only replace the recorder's latest slot; Task 4's sampler, not `CameraService`, determines 10 Hz buckets. Keep every existing helper caller valid with `clip_recorder=None`, and catch recorder update errors without stopping future camera processing.

- [ ] **Step 2: Write RED atomic-outbox and dispatcher tests**

Extend the existing eating/rest/mismatch transaction tests and add `backend/tests/test_clip_outbox.py`. Assert:

- a successful eligible event transaction contains exactly one matching unprocessed outbox row with persisted `event_type`, decimal `event_id`, and domain `occurred_at`;
- an injected commit failure leaves neither event nor outbox row, and retry creates one of each;
- `no_meal_12h` creates its existing anomaly/publication but no outbox row;
- the unique `(event_type, event_id)` constraint prevents duplicate enqueue without changing rule deduplication;
- `dispatch_once` locks the oldest due row, reconstructs strict `ClipTrigger`, calls the recorder once, and sets `processed_at` only after normal return;
- recorder rejection/exception increments `attempts`, stores a bounded non-secret `last_error`, advances `next_attempt_at`, and leaves `processed_at IS NULL`;
- a simulated crash/rollback after recorder return leaves the row unprocessed so delivery is at-least-once rather than lost;
- two dispatchers using `FOR UPDATE SKIP LOCKED` do not deliver the same locked row concurrently;
- duplicate redelivery while an event is active/queued is an idempotent normal recorder return, allowing the replayed row to be marked processed;
- malformed database values are quarantined as `invalid_trigger` without invoking the recorder or crashing the worker.

Add migration assertions for table name, check constraints, unique event identity, due-row index, and downgrade. The minimal model is:

```python
class ClipTriggerOutbox(Base):
    __tablename__ = "clip_trigger_outbox"
    __table_args__ = (
        CheckConstraint("event_type IN ('eating','resting','bed_sensor_mismatch')"),
        CheckConstraint("event_id > 0"),
        UniqueConstraint("event_type", "event_id"),
    )

    id: Mapped[int]
    event_type: Mapped[str]
    event_id: Mapped[int]
    occurred_at: Mapped[datetime]
    created_at: Mapped[datetime]
    next_attempt_at: Mapped[datetime]
    attempts: Mapped[int]
    last_error: Mapped[str | None]
    processed_at: Mapped[datetime | None]
```

- [ ] **Step 3: Run RED**

```powershell
Set-Location backend
uv run pytest tests/test_camera_service.py tests/test_rules.py tests/test_clip_outbox.py tests/test_migrations.py -q
```

Expected: FAIL because the outbox model/dispatcher and recorder handoff do not exist.

- [ ] **Step 4: Add the post-persistence latest-frame callback**

Add an optional recorder to `build_camera_service` and `CameraService.__init__`. In `process_once`, call `on_frame(processed.jpeg, processed.observed_at)` only after `_persist_frame` returns and before `resolve_committed`. This call only replaces the latest JPEG; it does not push a ring bucket. Catch failures into a bounded health error and continue. Do not move database commit, latest-frame assignment, or ingress resolution.

- [ ] **Step 5: Insert outbox rows inside the existing rule transactions**

Change `_open_eating` and `_open_rest` to return the newly flushed `BehaviorEvent | None`; obtain the mismatch row the same way. For each eligible row, call `enqueue_clip_trigger(session, row)` before the existing `session.commit()`. The helper adds, but never commits, one `ClipTriggerOutbox` using the same session and maps behavior `started_at` or anomaly `occurred_at`. Do not call the recorder from `RuleEngine`, do not catch a post-commit callback, and do not add an outbox row in `_emit_no_meal`.

This makes event and trigger intent one atomic unit: rollback removes both; successful commit exposes both. Keep current pending MQTT publications/schedules in their existing after-commit flow.

- [ ] **Step 6: Implement the minimal retrying dispatcher**

`dispatch_once` opens a short transaction, selects the oldest unprocessed due row with `FOR UPDATE SKIP LOCKED`, validates it into `ClipTrigger`, and calls `recorder.on_trigger` while the row lock is held. Normal return means the recorder accepted or idempotently already owns that event; set `processed_at = now()` and clear `last_error` in the same transaction. On `RecorderRejected` or other exception, leave `processed_at` null, increment attempts, set `next_attempt_at = now() + min(2 ** min(attempts, 5), 30) seconds`, and store only exception class plus a capped safe reason. If the process dies before commit, the database rolls back the mark and retries the row.

The dispatcher guarantees no committed trigger is silently lost; delivery is at-least-once. `ClipRecorder` therefore checks event identities across active and queued sidecars and returns normally for already-owned duplicates. Do not introduce a message broker or a second database.

- [ ] **Step 7: Run GREEN and commit**

```powershell
Set-Location backend
uv run pytest tests/test_camera_service.py tests/test_rules.py tests/test_rule_worker.py tests/test_clip_outbox.py tests/test_migrations.py -q
```

Expected: all tests PASS, including rollback, retry, concurrency, no-meal exclusion, and unchanged rule thresholds.

```powershell
git add backend/app/camera_service.py backend/app/rules.py backend/app/models.py backend/app/clip_outbox.py backend/migrations/versions/0002_clip_trigger_outbox.py backend/tests/test_camera_service.py backend/tests/test_rules.py backend/tests/test_clip_outbox.py backend/tests/test_migrations.py
git commit -m "feat(agent): persist clip trigger outbox"
```

---

### Task 6: Agent Status, Loopback Runtime, And Lifecycle Hook

**Files:**
- Create: `backend/app/agent_health.py`
- Create: `backend/app/agent_runtime.py`
- Create: `backend/app/agent_lifecycle.py`
- Create: `backend/tests/test_agent_health.py`
- Create: `backend/tests/test_agent_runtime.py`
- Create: `backend/tests/test_agent_lifecycle.py`

**Interfaces:**
- Produces: in-process `AgentHealthSnapshot` plus restricted CLI/service status; no HTTP route.
- Produces: `python -m app.agent_runtime enroll`, `run`, and `status`.
- Produces: the exact `backend/app/agent_lifecycle.py` interface below for Integration Task 2.
- Consumes: `.runtime/agent-tools.json` absolute `ffmpeg_path`, `ffprobe_path`, and `cloudflared_path`.

```python
@dataclass(frozen=True, slots=True)
class AgentLifecycleComponents:
    recorder: ClipRecorder
    dispatcher: ClipOutboxDispatcher
    started_at: datetime

    @property
    def latest_frame_sink(self) -> ClipRecorder: ...


def build_agent_components(
    config_path: Path,
    tools_path: Path,
    session_factory: sessionmaker[Session],
    *,
    now: Callable[[], datetime] = utc_now,
) -> AgentLifecycleComponents: ...


def start_agent_components(components: AgentLifecycleComponents) -> None: ...


def stop_agent_dispatcher(
    components: AgentLifecycleComponents, *, timeout: float = 10.0
) -> None: ...


def stop_agent_recorder(
    components: AgentLifecycleComponents, *, timeout: float = 45.0
) -> None: ...
```

- [ ] **Step 1: Write health and supervisor tests**

Status tests instantiate the in-process snapshot and assert exact safe keys without a TestClient request:

```python
def test_status_has_operational_state_and_no_secret_fields(snapshot):
    payload = snapshot.to_dict()
    assert set(payload) == {
        "status", "started_at", "camera", "rule_worker", "clip_recorder", "last_successful_upload_at"
    }
    serialized = json.dumps(payload).lower()
    assert "token" not in serialized
    assert "private_key" not in serialized
    assert "database_url" not in serialized
    assert "mqtt_password" not in serialized
```

Supervisor tests inject a process factory and assert exact argv:

```python
assert supervisor.commands(config) == [
    [
        sys.executable, "-m", "uvicorn", "app.main:app",
        "--host", "127.0.0.1", "--port", "8000", "--no-access-log",
    ],
    [
        str(tools.cloudflared_path), "tunnel", "--metrics", "127.0.0.1:20241",
        "run", "--token-file", str(config.connector_token_path),
    ],
]
```

Add tests that one child exit terminates the sibling and returns nonzero, stop terminates both, the token is absent from argv/environment dumps, child environment contains the existing backend variables, missing/fixture/tampered tool manifests refuse startup, and no command contains `0.0.0.0`, `::`, or a public hostname. Snapshot/status-file tests prove secure-before-write, stale/crashed PID detection, and no secret fields. `agent_lifecycle.py` tests assert it imports no web-routing primitive and cannot register a route; Integration Task 2 owns the final FastAPI route-inventory regression.

- [ ] **Step 2: Run tests and verify runtime modules are missing**

Run:

```powershell
Set-Location backend
uv run pytest tests/test_agent_health.py tests/test_agent_runtime.py tests/test_agent_lifecycle.py -q
```

Expected: FAIL during collection for missing agent modules and lifecycle hook.

- [ ] **Step 3: Implement safe in-process and CLI/service status**

`backend/app/agent_health.py` defines only `AgentHealthSnapshot`; it imports no web-routing primitive and registers nothing on FastAPI. Return `healthy` only when camera is online, `rule_worker.last_error is None`, the outbox dispatcher is running without a fatal error, recorder `last_error is None`, and its disk queue has fewer than eight entries; otherwise return `degraded`. `writer_queue_full` immediately degrades status.

The running supervisor atomically writes the secret-free snapshot to a config-sibling status file created with POSIX `0600` or owner/SYSTEM DACL before content. `agent_runtime status --config ...` validates file ownership, process liveness, and freshness, prints only this schema, and exits nonzero for stopped/stale/unavailable state. Windows installer `Status` combines SCM state with this snapshot; Pi operators combine `systemctl status` with the same CLI. The BFF does not consume this file or a new route; its online probe stays `/api/dashboard/summary`.

Use fixed nested fields:

```json
{"camera":{"last_frame_at":null,"reason":"not_started","state":"offline"},"clip_recorder":{"active":false,"last_error":null,"queue_depth":0,"trigger_dispatcher_running":true,"writer_queue_depth":0},"last_successful_upload_at":null,"rule_worker":{"last_error":null,"running":true},"started_at":"2026-07-20T04:00:00.000000Z","status":"degraded"}
```

- [ ] **Step 4: Implement the three-command CLI and supervisor**

`enroll` accepts only `--origin` and `--config`; read the code with `getpass.getpass("Enrollment code: ")` and local database/MQTT secrets from environment. `run` accepts only `--config` and `--tools`; secrets come from the ACL file. `status` accepts only `--config`, reads the derived restricted status file, and never contacts or creates an HTTP endpoint.

Before starting children, validate the agent-tools manifest SHA against `tools/platform-manifest.json`, require absolute executable paths, reject `fixture: true`, verify each executable SHA recorded by the bootstrap, and create/protect the empty connector-token sibling file before writing the token. Populate child environment with `DATABASE_URL`, `PETCARE_MQTT_PROFILE`, `PETCARE_MQTT_USERNAME`, `PETCARE_MQTT_PASSWORD`, `PETCARE_AGENT_CONFIG`, and `PETCARE_AGENT_TOOLS`; remove the connector token value from the environment. Start both children hidden on Windows and in the same process group on POSIX. If either exits, stop the other and return the failed exit code or `1`.

- [ ] **Step 5: Export the component-owned lifecycle composition hook**

Create `backend/app/agent_lifecycle.py` without importing `app.main`, FastAPI, or any router. `build_agent_components(config_path, tools_path, session_factory)` loads strict config/tools and returns a frozen `AgentLifecycleComponents` containing `FrameRing`, `SignedClipUploadClient` with the enrolled server camera ID, `ClipRecorder`, `ClipOutboxDispatcher`, and `started_at`. It performs no startup side effect.

Export the exact typed operations above. `start_agent_components` starts only the recorder sampler then dispatcher and rolls the recorder back if dispatcher startup fails. `stop_agent_dispatcher` stops/joins only the dispatcher without marking a rejected in-flight row; `stop_agent_recorder` shuts down sampler/writer/uploader and preserves pending outbox rows. Both stops are idempotent and raise a bounded explicit timeout error rather than hanging. Integration Task 2 passes `components.latest_frame_sink` to `build_camera_service`. `RuleEngine` receives no callback because Task 5 writes outbox rows transactionally.

Unit tests prove construction has no side effect, start order is sampler then dispatcher, each stop is idempotent, dispatcher retry survives restart, and a saturated writer reports degraded without poisoning the exported hook. Do not edit or import `backend/app/main.py`. Integration Task 2 alone performs the final sequence `stop rule production -> stop_agent_dispatcher -> stop camera updates -> stop_agent_recorder -> dispose database`, stores component state on the application, preserves disabled local behavior, and verifies Task 12's exact route set remains unchanged.

- [ ] **Step 6: Verify runtime and existing lifecycle tests, then commit**

Run:

```powershell
Set-Location backend
uv run pytest tests/test_agent_health.py tests/test_agent_runtime.py tests/test_agent_lifecycle.py tests/test_rule_worker.py tests/test_camera_service.py tests/test_clip_outbox.py tests/test_mqtt_ingest.py -q
```

Expected: all tests PASS.

```powershell
git add backend/app/agent_health.py backend/app/agent_runtime.py backend/app/agent_lifecycle.py backend/tests/test_agent_health.py backend/tests/test_agent_runtime.py backend/tests/test_agent_lifecycle.py
git commit -m "feat(agent): export runtime lifecycle hook"
```

---

### Task 7: Manifest-Backed Windows And Raspberry Pi Agent Tools Bootstrap

**Files:**
- Create: `tools/bootstrap_agent_runtime.ps1`
- Create: `tools/bootstrap_agent_runtime.sh`
- Create: `tools/tests/test_bootstrap_agent_runtime.ps1`
- Create: `tools/tests/test_bootstrap_agent_runtime.py`

**Interfaces:**
- Consumes: Task 1 manifest pins only.
- Produces: `.runtime/agent-tools.json` with absolute paths, versions, executable hashes, platform, architecture, fixture flag, and source manifest SHA.

- [ ] **Step 1: Write fixture and mutation tests first**

Both scripts accept a fixture directory, output path, and `wrong-byte` mutation. Assert the resulting dynamic-path contract exactly with:

```python
assert data["platform"] == "linux"
assert data["architecture"] == "arm64"
assert data["fixture"] is True
assert re.fullmatch(r"[0-9A-F]{64}", data["manifest_sha256"])
assert set(data["paths"]) == {"cloudflared_path", "ffmpeg_path", "ffprobe_path", "python_path", "uv_path"}
assert set(data["executable_sha256"]) == set(data["paths"])
assert all(Path(path).is_absolute() for path in data["paths"].values())
assert data["versions"] == {
    "cloudflared_path": "2026.7.2",
    "ffmpeg_path": "8.1.2-22-g94138f6973",
    "ffprobe_path": "8.1.2-22-g94138f6973",
    "python_path": "3.12.13+20260623",
    "uv_path": "0.11.28",
}
```

Tests assert all paths are absolute/executable, every hash matches the fixture bytes, wrong bytes fail before extraction, Windows selects only `windows_x64`, Linux `aarch64` selects only `linux_arm64`, Linux `x86_64` fails with `agent runtime requires Raspberry Pi arm64`, and no URL/SHA literal is duplicated in either script.

- [ ] **Step 2: Run fixture tests and verify scripts are missing**

Run:

```powershell
& tools/tests/test_bootstrap_agent_runtime.ps1
Set-Location backend
uv run pytest ../tools/tests/test_bootstrap_agent_runtime.py -q
```

Expected: both fail because the bootstrap scripts do not exist.

- [ ] **Step 3: Implement hash-first platform extraction**

PowerShell downloads the Windows FFmpeg zip and cloudflared executable, extracts `ffmpeg.exe` and `ffprobe.exe`, reuses the manifest-backed Python/uv already provisioned by `bootstrap_toolchain.ps1`, and records executable hashes. Bash on `aarch64` downloads the exact uv, Python, FFmpeg, and cloudflared artifacts from `linux_arm64`, verifies archive SHA before `tar`, locates `bin/python3`, `uv`, `bin/ffmpeg`, and `bin/ffprobe`, marks binaries executable, and writes the same schema.

Both scripts must:

- derive URLs, versions, and SHA values by parsing `tools/platform-manifest.json`;
- refuse an existing cache file with a wrong hash;
- extract each artifact into `.runtime/managed/agent/ffmpeg-8.1.2-22-g94138f6973`, `.runtime/managed/agent/cloudflared-2026.7.2`, `.runtime/managed/agent/python-3.12.13+20260623`, or `.runtime/managed/agent/uv-0.11.28`;
- atomically replace `.runtime/agent-tools.json` only after every executable probe succeeds;
- verify `ffmpeg -version`, `ffprobe -version`, `cloudflared --version`, `python --version`, and `uv --version` against the manifest;
- never call an unqualified downloaded executable through `PATH`.

- [ ] **Step 4: Verify fixtures and a real Windows bootstrap**

Run:

```powershell
& tools/tests/test_bootstrap_agent_runtime.ps1
Set-Location backend
uv run pytest ../tools/tests/test_bootstrap_agent_runtime.py -q
Set-Location ..
& tools/bootstrap_agent_runtime.ps1
```

Expected: fixture tests PASS; real bootstrap prints `agent runtime PASS: windows-x64`, and `.runtime/agent-tools.json` contains non-fixture absolute paths and matching executable hashes.

- [ ] **Step 5: Run the real FFmpeg smoke and commit**

Run:

```powershell
$env:PETCARE_AGENT_TOOLS = (Resolve-Path .runtime/agent-tools.json).Path
Set-Location backend
uv run pytest tests/test_clip_recorder.py -m ffmpeg_smoke -q
```

Expected: both default and overlap smoke tests PASS; ffprobe reports H.264/YUV420P and durations near 30.000/45.000 seconds.

```powershell
git add tools/bootstrap_agent_runtime.ps1 tools/bootstrap_agent_runtime.sh tools/tests/test_bootstrap_agent_runtime.ps1 tools/tests/test_bootstrap_agent_runtime.py
git commit -m "build(agent): bootstrap pinned runtime tools"
```

---

### Task 8: Windows Service Package

**Files:**
- Create: `backend/app/windows_service.py`
- Create: `backend/tests/test_windows_service.py`
- Create: `packaging/windows/install-home-agent.ps1`
- Create: `packaging/tests/test_windows_home_agent_packaging.py`

**Interfaces:**
- Consumes: `AgentSupervisor` and ACL config path.
- Produces: Windows service name `PetCareHomeAgent` with automatic restart.

- [ ] **Step 1: Write service-host and installer tests**

Mock pywin32 and assert `SvcDoRun` delegates once to `AgentSupervisor.run(stop_event)`, `SvcStop` sets the event and reports `SERVICE_STOP_PENDING`, and a supervisor failure is logged without serializing config.

In the packaging test assert the PowerShell installer:

- has actions `Install`, `Uninstall`, and `Status`;
- implements `Status` with SCM state plus `app.agent_runtime status`, never an HTTP health URL;
- requires an elevated token for install/uninstall;
- runs the manifest-backed Python absolute path, not `python` from PATH;
- writes only non-secret `ConfigPath` and `ToolsPath` registry values and never connector/enrollment/private-key values;
- calls pywin32 install with start mode auto;
- configures `sc.exe failure PetCareHomeAgent reset= 86400 actions= restart/5000/restart/30000/restart/120000`;
- refuses a config whose ACL grants Users or Everyone;
- never opens a firewall rule.

- [ ] **Step 2: Run tests and verify package files are missing**

Run:

```powershell
Set-Location backend
uv run pytest tests/test_windows_service.py ../packaging/tests/test_windows_home_agent_packaging.py -q
```

Expected: FAIL because the Windows service module and installer do not exist.

- [ ] **Step 3: Implement the minimal pywin32 host**

Use service constants exactly:

```python
class PetCareHomeAgentService(win32serviceutil.ServiceFramework):
    _svc_name_ = "PetCareHomeAgent"
    _svc_display_name_ = "PetCare Home Agent"
    _svc_description_ = "Runs the loopback PetCare backend and outbound Cloudflare Tunnel."
```

`SvcDoRun` reads `ConfigPath` and `ToolsPath` from `HKLM\Software\PetCare\HomeAgent`, constructs one supervisor, and blocks in `run`. `SvcStop` only reports pending and sets the stop event; the supervisor owns child cleanup. The module exits immediately with a clear platform error when imported as an executable on non-Windows.

- [ ] **Step 4: Implement install/remove/status without secret argv**

The installer validates `.runtime/agent-tools.json`, `backend/.venv`, and the ACL config. It writes `ConfigPath` and `ToolsPath` as `REG_SZ` values under `HKLM\Software\PetCare\HomeAgent`, then calls:

```powershell
& $Python -m app.windows_service --startup auto install
& $env:SystemRoot\System32\sc.exe failure PetCareHomeAgent reset= 86400 actions= restart/5000/restart/30000/restart/120000
& $env:SystemRoot\System32\sc.exe failureflag PetCareHomeAgent 1
& $Python -m app.windows_service start
```

`Status` reads the non-secret registry paths, reports SCM state, and invokes the manifest-backed Python `-m app.agent_runtime status --config <ConfigPath>` using an argument list. Uninstall stops then removes only `PetCareHomeAgent`; it does not delete the runtime config, status file, or queued clips without a separate destructive confirmation.

- [ ] **Step 5: Verify tests and commit**

Run:

```powershell
Set-Location backend
uv run pytest tests/test_windows_service.py ../packaging/tests/test_windows_home_agent_packaging.py -q
```

Expected: Windows service and static package tests PASS.

```powershell
git add backend/app/windows_service.py backend/tests/test_windows_service.py packaging/windows/install-home-agent.ps1 packaging/tests/test_windows_home_agent_packaging.py
git commit -m "feat(agent): package Windows service"
```

---

### Task 9: Raspberry Pi systemd Package

**Files:**
- Create: `packaging/linux/petcare-agent.service`
- Create: `packaging/linux/install-home-agent.sh`
- Create: `packaging/tests/test_linux_home_agent_packaging.py`

**Interfaces:**
- Consumes: manifest-backed arm64 Python, uv, FFmpeg, cloudflared, agent config, `/dev/video0`, loopback PostgreSQL/MQTT.
- Produces: systemd service `petcare-agent.service` running as user/group `petcare`.

- [ ] **Step 1: Add failing systemd hardening and loopback tests**

Assert the unit contains these exact directives:

```ini
[Unit]
Description=PetCare Home Agent
After=network-online.target postgresql.service mosquitto.service
Wants=network-online.target

[Service]
Type=simple
User=petcare
Group=petcare
SupplementaryGroups=video
Environment=PETCARE_AGENT_CONFIG=/var/lib/petcare/agent.json
Environment=PETCARE_AGENT_TOOLS=/opt/petcare/.runtime/agent-tools.json
WorkingDirectory=/opt/petcare/backend
ExecStart=/opt/petcare/backend/.venv/bin/python -m app.agent_runtime run --config /var/lib/petcare/agent.json --tools /opt/petcare/.runtime/agent-tools.json
Restart=on-failure
RestartSec=5
NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=strict
ProtectHome=true
ReadWritePaths=/var/lib/petcare
UMask=0077
TimeoutStopSec=45
KillMode=mixed

[Install]
WantedBy=multi-user.target
```

Assert the installer rejects non-`aarch64`, creates `petcare` as a system user, installs source under `/opt/petcare`, creates `/var/lib/petcare/clips` mode `0700`, installs the unit mode `0644`, verifies config ownership/mode `petcare:petcare 0600`, runs `systemd-analyze verify`, and does not execute `ufw`, `iptables`, `nft`, or bind a public address.

- [ ] **Step 2: Run the packaging test and verify Linux files are missing**

Run:

```powershell
Set-Location backend
uv run pytest ../packaging/tests/test_linux_home_agent_packaging.py -q
```

Expected: FAIL because the systemd unit and installer are absent.

- [ ] **Step 3: Implement the exact unit and idempotent installer**

The installer supports `--root "$PWD/.runtime/pi-package-fixture"` for tests and `--install` for a real Pi. It verifies `uname -m` is `aarch64`, calls `tools/bootstrap_agent_runtime.sh`, sets `UV_PYTHON` to the absolute manifest-backed Python path, installs the locked backend with the manifest-backed uv using `uv sync --frozen --no-dev`, copies only tracked application/package/tool files, checks ACLs, and writes the unit. In fixture mode it never calls useradd/systemctl. In real mode it calls `systemctl daemon-reload`, `enable`, and `restart` only after all verification passes.

Do not install or reconfigure PostgreSQL/Mosquitto in this plan; their existing authenticated loopback setup is a prerequisite. The agent’s restart policy makes a missing dependency explicit instead of exposing a fallback service.

- [ ] **Step 4: Verify static packaging and a Pi fixture**

Run on any development host:

```powershell
Set-Location backend
uv run pytest ../packaging/tests/test_linux_home_agent_packaging.py -q
```

Run on Raspberry Pi or an arm64 CI runner:

```bash
./packaging/linux/install-home-agent.sh --root "$PWD/.runtime/pi-package-fixture"
systemd-analyze verify "$PWD/.runtime/pi-package-fixture/etc/systemd/system/petcare-agent.service"
```

Expected: pytest PASS; fixture install prints `Raspberry Pi agent package fixture PASS`; systemd verification exits zero.

- [ ] **Step 5: Commit the Pi package**

```powershell
git add packaging/linux/petcare-agent.service packaging/linux/install-home-agent.sh packaging/tests/test_linux_home_agent_packaging.py
git commit -m "feat(agent): package Raspberry Pi service"
```

---

### Task 10: End-To-End Local Verification And Service Evidence

**Files:**
- Modify only if a test exposes an in-scope defect: files already listed in Tasks 1-9.

**Interfaces:**
- Verifies all frozen interfaces and does not add a new public API.

- [ ] **Step 1: Run the complete backend and tool unit suites**

With the existing dedicated loopback test database configured:

```powershell
if (-not $env:TEST_DATABASE_URL) { throw 'Set the existing dedicated loopback TEST_DATABASE_URL secret before this check.' }
Set-Location backend
uv run pytest -m "not model_smoke and not ffmpeg_smoke" -q
uv lock --check
Set-Location ..
& tools/tests/test_bootstrap_agent_runtime.ps1
```

Expected: all selected pytest tests PASS, lock check PASS, and Windows bootstrap fixture PASS. Supply the password through the environment used by the existing test setup; never commit or print it.

- [ ] **Step 2: Run model and real FFmpeg smoke checks**

```powershell
& tools/bootstrap_agent_runtime.ps1
$env:PETCARE_AGENT_TOOLS = (Resolve-Path .runtime/agent-tools.json).Path
Set-Location backend
uv run pytest tests/test_model_smoke.py -m model_smoke -q
uv run pytest tests/test_clip_recorder.py -m ffmpeg_smoke -q
```

Expected: the pinned YOLO smoke test PASS; both FFmpeg tests report H.264/YUV420P with durations within 50 ms of 30 and 45 seconds.

- [ ] **Step 3: Prove loopback-only runtime without installing a service**

Using a fake enrollment transport or approved staging enrollment response, start `python -m app.agent_runtime run` with a fake cloudflared executable that remains alive. Inspect listeners:

```powershell
Get-NetTCPConnection -State Listen | Where-Object LocalPort -In 8000,55432,18883 | Select-Object LocalAddress,LocalPort
```

Expected: port 8000 is bound only to `127.0.0.1`; PostgreSQL remains `127.0.0.1:55432`; local-live MQTT remains loopback. No `0.0.0.0` or `[::]` listener exists for those ports.

- [ ] **Step 4: Run approval-gated Windows service smoke**

After explicit approval and a valid enrolled ACL config:

```powershell
& packaging/windows/install-home-agent.ps1 -Action Install -ConfigPath 'C:\ProgramData\PetCare\agent.json'
Get-Service PetCareHomeAgent
$tools = Get-Content -Raw -Encoding UTF8 .runtime/agent-tools.json | ConvertFrom-Json
& $tools.paths.python_path -m app.agent_runtime status --config 'C:\ProgramData\PetCare\agent.json'
& packaging/windows/install-home-agent.ps1 -Action Status -ConfigPath 'C:\ProgramData\PetCare\agent.json'
```

Expected: service status `Running`; health is `healthy` or an explicit `degraded` reason; stopping one supervised child causes SCM restart according to 5/30/120-second recovery; no secret appears in `Win32_Process.CommandLine`.

- [ ] **Step 5: Run approval-gated Raspberry Pi service smoke**

After explicit approval on the target Pi:

```bash
sudo ./packaging/linux/install-home-agent.sh --install
systemctl is-active --quiet petcare-agent.service
/opt/petcare/backend/.venv/bin/python -m app.agent_runtime status --config /var/lib/petcare/agent.json
ss -ltnp | grep -E ':(8000|55432|18883)[[:space:]]'
```

Expected: unit active; health JSON contains no secret fields; all listed listeners are loopback. Trigger one committed eligible event and verify its outbox row is processed only after recorder acceptance and exactly one H.264 clip is queued/uploaded; trigger `no_meal_12h` and verify outbox/queue depth does not change; after a strict 201 receipt, both MP4 and sidecar are absent.

- [ ] **Step 6: Inspect diff and run the no-placeholder contract scan**

```powershell
git diff --check
rg -n "no_meal_12h.*on_trigger|0\.0\.0\.0:8000|--token [A-Za-z0-9]" backend tools packaging
$retired = @(
  ('PETCARE-CLIP-' + 'UPLOAD-V1'), ('X-PetCare-' + 'Body-SHA256'),
  ('X-PetCare-' + 'Metadata-SHA256'), ('X-PetCare-' + 'Clip-Metadata'),
  ('body_' + 'sha256'), ('metadata_' + 'sha256'), ('127.0.0.1:' + '8765'),
  ('test_' + 'home_agent_packaging.py'), ('/health' + '/agent')
)
foreach ($term in $retired) {
  if (rg -n -F -- $term backend tools packaging contracts) { throw "retired agent contract remains: $term" }
}
if (rg -n "ffmpeg-n7\.|cloudflared-(2025|2026\.[0-6])|FFmpeg 7\." tools backend packaging contracts) { throw 'retired runtime pin remains' }
git status --short
```

Expected: `git diff --check` exits zero; the forbidden-wiring scan returns no matches; status lists only the files named by this plan.

- [ ] **Step 7: Commit only a test-driven correction if Step 1-6 required one**

If and only if verification required an in-scope correction:

```powershell
git add backend tools packaging
git commit -m "fix(agent): close service verification gap"
```

Expected: the commit contains only the correction and its failing-then-passing test. If no correction was required, do not create an empty commit.

## Completion Evidence

The implementation is complete only when current-run evidence proves all of the following:

- One sampler repeats the latest annotated JPEG into exactly 100 pre-roll buckets and 200 default post-roll buckets; `FrameRing` holds at most 100 JPEGs and writes no pre-roll file.
- FFmpeg is absent before a trigger, starts only after acceptance, and ffprobe proves pinned H.264/YUV420P outputs near 30 seconds by default and 45 seconds for an overlap at +15 seconds.
- Coalescing preserves a full twenty-second post-roll without exceeding 120 seconds; an over-limit trigger stays unprocessed and starts a new bounded clip after retry.
- Eligible event and outbox rows commit atomically; failed commits and `no_meal_12h` create no outbox row; recorder rejection/crash leaves the committed intent retryable.
- Writer insertion never blocks. Saturation aborts/cleans the partial, degrades health, and leaves camera plus rule processing alive.
- Disk queue depth never exceeds eight; retries and one-hour expiry are exact; partial, failed, successful, expired, invalid, and shutdown paths have tested cleanup.
- Every secret, partial MP4, and sidecar file is protected with POSIX `0600` or owner/SYSTEM Windows DACL before the first sensitive byte is written.
- The shared golden vector proves case-preserving `PETCARE-CLIP-V1` digest/canonical/signature parity. Headers include server camera UUID, time bounds, and canonically sorted decimal event IDs; only strict HTTP 201 `{id,createdAt,expiresAt}` succeeds.
- Enrollment and service process listings contain no private key, connector token, database password, MQTT password, or one-time code.
- Windows and Pi service packages bind Uvicorn only to loopback, restart on failure, preserve runtime ACLs, and expose secret-free CLI/service status without adding an HTTP route.
- The full existing backend rule/camera/worker suites still pass without modified semantics.

Skipped: WebRTC, continuous recording, audio, a generic event bus, multiple cameras, notification delivery, server-side enrollment/R2 implementation, and production resource creation. Add any of them only under a separately approved plan.
