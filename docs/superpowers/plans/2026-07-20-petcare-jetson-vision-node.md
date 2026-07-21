# PetCare Jetson Nano Vision Node Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Move the single MVP USB webcam, TensorRT inference, preview, exact event pre/post-roll, and temporary H.264 assembly to one private-LAN Jetson Nano P3450 B01 while preserving the Home Agent's current camera/rule contracts and all authenticated cloud behavior.

**Architecture:** A stock JetPack 4.6.6 / TensorRT 8.2.1 Jetson service exposes six pinned-TLS, HMAC-authenticated operations across four LAN resource paths using only Python 3.6-compatible stdlib plus JetPack system packages. The Home Agent long-polls strict observations, derives subject/center/zone, persists the existing `pc-webcam-01` contract, sends committed event intents through idempotent PUT resources backed by Jetson stdlib SQLite, verifies and queues completed MP4s, and alone performs the existing Ed25519 `PETCARE-CLIP-V1` cloud upload.

**Tech Stack:** Jetson Nano P3450 B01, JetPack 4.6.6 / L4T 32.7.6, TensorRT 8.2.1, CUDA 10.2 generation, Python 3.6 system runtime, Python stdlib HTTPS/HMAC/JSON/SQLite/unittest, JetPack OpenCV/NumPy/GStreamer, `nvv4l2h264enc`, systemd; Home Python 3.12.13, Pydantic 2.13.4, httpx 0.28.1, PostgreSQL 17.10, validation-only ffprobe 8.1.2-22-g94138f6973, pytest 9.1.1.

## Global Constraints

- Use one Jetson and one USB webcam. Do not implement second-node discovery, replication, election, or failover.
- Jetson Nano starts from NVIDIA's official JetPack 4.6.1 Nano SD-card image and is upgraded through the NVIDIA R32 APT repository to JetPack 4.6.6 / L4T 32.7.6 and TensorRT 8.2.1. Do not install the Home Python 3.12/Ultralytics/FastAPI stack on it.
- Jetson application and tests must import and run under stock Python 3.6. Use stdlib `unittest`; do not add pip/runtime/test dependencies to the Nano.
- Keep the logical camera ID `pc-webcam-01`, output geometry 640x480, current class/subject ordering, current zone ownership, three-second camera TTL, and current `CameraFrameCommitted` ordering.
- Eligible clips remain exactly `eating`, `resting`, and `bed_sensor_mismatch`; `no_meal_12h` never creates a clip intent or Jetson request.
- Clip timing remains exactly 100 pre-roll buckets plus 200 post-roll buckets at 10 Hz; a +15-second eligible overlap is exactly 450 frames/45 seconds; total duration never exceeds 120 seconds.
- The Jetson writes no continuous video and no pre-roll file. It holds at most 100 annotated JPEGs in RAM and at most two ready MP4s / 256 MiB / one hour on private temporary storage.
- The Home Agent continues to own Pico MQTT, fusion/rules, PostgreSQL, tenant/device ownership, enrollment, cloud identity, signed `PETCARE-CLIP-V1` upload, R2, tunnel, and all Sites-facing routes.
- The browser and Sites Worker never receive a Jetson URL, certificate, PSK, boot ID, LAN IP, or clip path.
- Jetson HTTPS binds to one configured RFC1918 Ethernet IPv4 address on port 9443. Wildcard, loopback, link-local, multicast, public, Wi-Fi, router-forwarded, and Cloudflare-exposed Jetson listeners are forbidden.
- TLS verification, HMAC input validation, replay protection, media validation, ACLs, and camera-offline sensor isolation may not be simplified.
- Home network calls are bounded: connect 1 second, ordinary read 2 seconds, observation long-poll 2 seconds, MP4 download 45 seconds, and at most one request of each class in flight.
- A new event must be accepted by the Jetson within a real three seconds of the Home outbox `created_at` or it becomes terminal `clip_missed`; the Home admission worker is isolated from slow media/cloud work, every first PUT has a fresh signed calibration plus wall/monotonic discontinuity guards and a 200 ms total error budget, and the exact media window is anchored to the first PUT's Jetson monotonic receipt rather than either machine's adjustable wall clock. `occurred_at` remains only a domain label.
- No production installation, firewall mutation, service enablement, certificate/secret transfer, model conversion on hardware, or public deployment occurs without the user's approval at that gate.

## Frozen `PETCARE-JETSON-V1` Wire Contract

Create one shared fixture at `contracts/petcare-jetson-wire-v1.json`. It is separate from and must not modify `contracts/petcare-agent-wire-v1.json` or `PETCARE-CLIP-V1`.

The deterministic commit vector is:

```text
secret_base64url = AAECAwQFBgcICQoLDA0ODxAREhMUFRYXGBkaGxwdHh8
boot_id = 0123456789abcdef0123456789abcdef
timestamp = 1784520000
nonce = AAECAwQFBgcICQoLDA0ODw
command_id = fedcba9876543210fedcba9876543210
body = {"committed_at":"2026-07-20T04:00:00.000000Z","event_id":41,"event_type":"eating","occurred_at":"2026-07-20T03:59:30.000000Z"}
body_sha256 = bb8973c4c644e9c7e1f2182aed7fc3f62913fbc0b9f626481320bcd1f756e656
signature = jRDgkQ3q6mrGL7rQxGtI1QANRKamx9ieVseiURXrnzE
```

The exact signed bytes include the final newline:

```text
PETCARE-JETSON-V1
PUT
/v1/clips/fedcba9876543210fedcba9876543210
0123456789abcdef0123456789abcdef
1784520000
AAECAwQFBgcICQoLDA0ODw
bb8973c4c644e9c7e1f2182aed7fc3f62913fbc0b9f626481320bcd1f756e656

```

Canonical query rules are: percent-encode UTF-8 with RFC 3986 unreserved characters only, sort by encoded key then value, preserve duplicate pairs, and join with `&`. Request JSON uses UTF-8, sorted keys, separators `(',', ':')`, `ensure_ascii=False`, and no trailing newline. Response JSON is strict but not signed separately because verified TLS identifies the Jetson; every media response carries a body SHA-256 that the Home Agent verifies.

## File Ownership And Overlap Map

| Workstream | Exclusive files | Shared/serialized files |
|---|---|---|
| Wire contract | `contracts/petcare-jetson-wire-v1.json`, `backend/tests/test_jetson_wire_contract.py`, `jetson/tests/test_wire_contract.py` | none |
| Jetson protocol/core | `jetson/protocol.py`, `jetson/clip_writer.py`, `jetson/tensorrt_yolo.py`, their unit tests | consumes fixture read-only |
| Jetson service/package | `jetson/vision_node.py`, `jetson/petcare-vision.service`, `jetson/install.sh`, service/package tests | consumes Jetson core |
| Home camera adapter | `backend/app/jetson_contracts.py`, `backend/app/jetson_client.py`, their tests | serial owner of `backend/app/config.py`, `backend/app/camera_service.py`, `backend/tests/test_camera_service.py` |
| Home event delivery | `backend/app/clip_outbox.py`, `backend/app/clip_delivery.py`, `backend/app/clip_upload_queue.py`, their tests and migration | serial owner modifies existing `backend/app/clip_contracts.py`, `backend/tests/test_clip_contracts.py`, `backend/app/models.py`, `backend/app/rules.py`, `backend/tests/test_rules.py`, and `backend/tests/test_migrations.py` |
| Home lifecycle/runtime/package | `backend/app/agent_health.py`, `backend/app/agent_lifecycle.py`, `backend/app/agent_runtime.py`, `backend/app/windows_service.py`, their tests, Windows/Linux installer files and packaging tests | integration owner alone edits `backend/app/main.py`; consumes existing bootstrap scripts/manifest read-only |
| Candidate verification source | `backend/tests/integration/test_jetson_vision_stack.py`, `tools/run_jetson_integration.ps1`, `tools/tests/test_run_jetson_integration.ps1`, `tools/jetson_vision_soak.py`, `tools/tests/test_jetson_vision_soak.py` | Task 8 alone owns all five and commits them before candidate SHA freeze |
| Hardware evidence | `.runtime/evidence/jetson-vision-node.json` (untracked runtime output) | no source owner |

Do not run Home camera adapter and Home event delivery workers concurrently with another worker editing their serialized files. Required order is Task 0, then approval-gated Task 9 Steps 1-3 so stock Python 3.6 and the actual JetPack bindings/plugins are proven, then Task 1. After that, Jetson Tasks 2-4 serialize on their files and each passes the real Python 3.6 gate before commit, while Home Task 5 may run in parallel against the frozen fixture. Task 6 follows Task 5; Task 7 follows Tasks 4-6. Task 8 then creates and commits every integration/soak runner and collector, the tree is frozen to one clean candidate SHA, Task 9 completes pairing and the real 60-minute soak without source writes, and Task 10 runs regression and parallel reviews against exactly that same SHA. Reviews are read-only and may run in parallel only against the named unchanged candidate.

Before each pre-candidate on-device gate in Tasks 1-4, the owning worker records SHA-256 for only that task's non-secret source/tests, copies them over approved SSH to a disposable `/opt/petcare-vision-dev/taskN` tree, reruns the same hashes on Jetson, and executes there. The tree has no service, PSK, certificate, cloud secret, or production config and is deleted after the gate. This development sync is distinct from Task 9's clean immutable candidate staging; `/opt/petcare-vision-source` is used only after Task 8 freezes a SHA.

## Existing Plan Replacement

Task 0 marks these prior Home-plan steps as superseded before any component worker starts, so an older worker cannot implement both paths:

- Home Task 2: do not create Home `frame_ring.py`; retain only clip value objects used by Home delivery.
- Home Task 4: do not create a Home sampler/FFmpeg `clip_recorder.py`; create Jetson clip writer and Home `clip_delivery.py` instead.
- Home Task 5: do not add a camera-to-recorder JPEG callback or dispatcher; retain only transactional event/outbox insertion, while this plan's Task 6 owns Jetson admission/delivery.
- Home Task 6: remove `latest_frame_sink` from the planned lifecycle interface; compose Jetson client, fast admission worker, slow media-delivery worker, and the concrete persistent `ClipUploadQueue`.
- Home Task 7: do not require Home FFmpeg encoding. Keep the existing manifest authority untouched and retain Python/cloudflared plus ffprobe for validation of untrusted Jetson MP4s.
- Home Task 10: replace Home FFmpeg evidence with Tasks 8-10 in this plan.
- Remote Integration Task 2: delete its old recorder/`latest_frame_sink` interface and tests and consume only the revised lifecycle/start/stop API frozen below.
- Remote Integration Task 4: validate both wire fixtures independently and prove `PETCARE-CLIP-V1` remains unchanged.

---

### Task 0: Mark The Old Plans Superseded Before Parallel Work

**Files:**
- Modify: `docs/superpowers/plans/2026-07-20-petcare-home-agent-clips.md`
- Modify: `docs/superpowers/plans/2026-07-20-petcare-remote-integration-deployment.md`

**Interfaces:**
- Produces: one executable plan authority; no runtime/code/package change.

- [ ] **Step 1: Add an exact override banner to the Home plan**

Immediately after its title add:

```markdown
> **Jetson vision override (2026-07-20):** For the approved one-Jetson USB-camera deployment, do not execute this plan's Home `FrameRing`, local sampler/FFmpeg encoder, `ClipRecorder`, camera `latest_frame_sink`, or related lifecycle/bootstrap/smoke steps. Execute `2026-07-20-petcare-jetson-vision-node.md`; retain Home clip value objects, transactional event outbox, validation-only ffprobe, signed `PETCARE-CLIP-V1` upload queue, enrollment, services, and loopback/tunnel duties.
```

Add `**Status:** Superseded by the Jetson vision plan for the approved hardware path.` under Home Task 2's frame-ring portion, Task 4, the camera callback/recorder-dispatch portions of Task 5, the `latest_frame_sink` portions of Task 6, Home FFmpeg encoding in Task 7, and local FFmpeg smoke in Task 10. Do not delete historical detail.

Freeze ownership in those markers: old Home Task 5 retains only transactional eligible-event plus outbox insertion in `rules.py`/models/migration tests; this plan's Task 6 alone owns `ClipAdmissionWorker`, `ClipDeliveryWorker`, `ClipUploadQueue`, and their tests. Old Home Task 6 retains no recorder, dispatcher, `latest_frame_sink`, or stop export; this plan's Task 7 alone owns the revised lifecycle/runtime exports and runtime/package tests. Remote Integration Task 2 alone owns final `main.py` lifespan composition/tests and must not edit `agent_lifecycle.py`, any clip worker, or upload queue.

- [ ] **Step 2: Add an exact integration override banner**

Immediately after the remote integration plan title state that Integration Task 2 supersedes its old `AgentLifecycleComponents(recorder, dispatcher, started_at)`, `.latest_frame_sink`, `stop_agent_dispatcher`, `stop_agent_recorder`, and `test_clip_recorder.py` expectations. Replace them with exactly:

```python
AgentLifecycleComponents(jetson_client, clip_admission, clip_delivery, upload_queue, started_at)
build_agent_components(config_path, tools_path, session_factory, *, now=utc_now)
start_agent_components(components)
stop_agent_components(components, *, timeout_seconds=105.0)
```

`stop_agent_components` uses one 105-second global monotonic deadline and always attempts this exact component-owned sequence with per-step caps: `clip_admission.stop(5)`; `clip_delivery.stop(45)`; `jetson_client.close(2)`; `upload_queue.stop(45)`. Each receives `min(cap, remaining_global_time)`; exhaustion is recorded but later nonblocking cleanup is still attempted. It preserves the first failure only after attempting later cleanup, and repeated calls are safe. The Integration Task 2 lifespan owns the surrounding sequence `rule_ingress.stop_accepting -> mqtt.stop -> rule_worker.shutdown -> camera.shutdown -> stop_agent_components -> dispose_database`, again attempting later cleanup after a failure. Integration tests must assert these exact exports/order and must not import, fake, or mention `ClipRecorder`, `latest_frame_sink`, `stop_agent_dispatcher`, or `stop_agent_recorder`. Integration Task 4 validates `petcare-jetson-wire-v1.json` separately while preserving `petcare-agent-wire-v1.json` byte-for-byte.

- [ ] **Step 3: Verify no worker can select both authorities**

```powershell
rg -n "Jetson vision override|Superseded by the Jetson vision plan|latest_frame_sink|petcare-jetson-wire-v1" docs/superpowers/plans/2026-07-20-petcare-home-agent-clips.md docs/superpowers/plans/2026-07-20-petcare-remote-integration-deployment.md
```

Expected: both banners and every named superseded task marker appear. A reviewer confirms retained ffprobe is validation-only and retained cloud upload uses `PETCARE-CLIP-V1`.

- [ ] **Step 4: Commit only the two plan authority edits**

```powershell
git add docs/superpowers/plans/2026-07-20-petcare-home-agent-clips.md docs/superpowers/plans/2026-07-20-petcare-remote-integration-deployment.md
git commit -m "docs(plan): supersede local camera recorder with Jetson"
```

---

### Task 1: Freeze The Separate Jetson Wire Contract

**Files:**
- Create: `contracts/petcare-jetson-wire-v1.json`
- Create: `backend/tests/test_jetson_wire_contract.py`
- Create: `jetson/tests/test_wire_contract.py`

**Interfaces:**
- Produces: exact HMAC vector, status/observation/command/clip schemas, error codes, and media headers consumed by every later task.
- Preserves: `contracts/petcare-agent-wire-v1.json` byte-for-byte.

- [ ] **Step 1: Write the strict fixture**

Store top-level keys in this order: `auth`, `status`, `observation`, `command`, `clip`, `errors`. Include the deterministic values above and these exact success schemas:

```json
{
  "status": {
    "boot_id": "0123456789abcdef0123456789abcdef",
    "server_time": "2026-07-20T04:00:00.050000Z",
    "camera_state": "online",
    "clip_state": "idle",
    "jetpack": "4.6.6",
    "l4t": "32.7.6",
    "tensorrt": "8.2.1",
    "temperature_c": 54.5,
    "throttled": false
  },
  "command_response": {
    "accepted_boot_id": "0123456789abcdef0123456789abcdef",
    "command_id": "fedcba9876543210fedcba9876543210",
    "state": "recording",
    "accepted_at": "2026-07-20T04:00:00.000000Z"
  }
}
```

The observation fixture uses sequence 42 and the exact observation body in the design. Add a preview fixture whose JPEG bytes decode to exactly 640x480x3 and whose response headers are exactly `Content-Type`, `Content-Length`, `Cache-Control: private, no-store, no-transform`, `X-PetCare-Jetson-Boot-Id`, `X-PetCare-Jetson-Sequence`, `X-PetCare-Jetson-Observed-At`, and `X-PetCare-Jetson-Content-SHA256`; cap `Content-Length` at 1,048,576 bytes and forbid every other `X-PetCare-Jetson-*` header.

The wire-only clip vector declares 300-frame H.264/YUV420P metadata, 640x480 resolution, start `2026-07-20T03:59:50.000000Z`, end `2026-07-20T04:00:20.000000Z`, event `eating:41`, and Home outbox `created_at=2026-07-20T04:00:00.000000Z`; the earlier `occurred_at` is the eating domain start, not the clip anchor. Its response headers are exactly:

```json
{
  "Content-Type": "video/mp4",
  "Content-Length": "9",
  "X-PetCare-Jetson-Boot-Id": "0123456789abcdef0123456789abcdef",
  "X-PetCare-Jetson-Command-Id": "fedcba9876543210fedcba9876543210",
  "X-PetCare-Jetson-Content-SHA256": "225e2e71f6963695684cf5c2aef7d582fff76acb8c028ed8b79c9c52bc93495d",
  "X-PetCare-Jetson-Started-At": "2026-07-20T03:59:50.000000Z",
  "X-PetCare-Jetson-Ended-At": "2026-07-20T04:00:20.000000Z",
  "X-PetCare-Jetson-Events": "eating:41",
  "X-PetCare-Jetson-Frame-Count": "300",
  "X-PetCare-Jetson-Video-Codec": "h264",
  "X-PetCare-Jetson-Pixel-Format": "yuv420p"
}
```

Use body bytes `b"mp4-bytes"`, whose lowercase SHA-256 is the frozen value above, only for wire digest/header parity. This nine-byte vector is deliberately not a valid MP4 and media-validation tests must reject it; Tasks 3, 6, and 10 generate/use a real 300-frame H.264/YUV420P fixture. External error codes are exactly `invalid_request`, `unauthorized`, `command_conflict`, `command_expired`, `camera_unavailable`, `clip_busy`, `clip_not_ready`, `clip_gone`, and `internal_error`. Internal authentication reasons `stale_request`, `replayed_request`, and `wrong_boot` are never serialized; they all produce the same external `401 unauthorized` body.

- [ ] **Step 2: Add independent Python 3.12 and Python 3.6-compatible fixture tests**

Both tests recompute body bytes, digest, canonical bytes, HMAC, and MP4 fixture digest using only `json`, `hashlib`, `hmac`, and `base64`. The Jetson-side test uses stdlib `unittest.TestCase`, not pytest. Assert exact top-level keys, no extra fields in each schema, the exact application-header names/grammars after ignoring only standard `Date` and `Connection: close`, canonical query behavior, and the known signature. Test signed `committed_at` is required; immediately before every first PUT require a sample no older than one second with `abs(midpoint_offset)+half_rtt+50 ms drift budget <=200 ms` and healthy 100 ms wall/monotonic guards, then admit only Jetson wall ages from -0.200 through 2.800 seconds. Prove `accepted_at` is actual first socket receipt, internal `accepted_monotonic_ns` selects the trigger bucket, a wall-clock discontinuity disables admission for 60 seconds, and replay returns the immutable stored receipt without a second age test. Test identical PUT during recording/finalizing/ready returns 200, while delivered/expired/restart-gone returns 410 and a changed digest returns 409. Assert every internal authentication failure maps to the same external 401 body. Assert `petcare-agent-wire-v1.json` still has version `PETCARE-CLIP-V1` and contains no `PETCARE-JETSON-V1` field.

- [ ] **Step 3: Run RED/GREEN contract checks**

Run before the fixture exists:

```powershell
backend\.venv\Scripts\python.exe -m pytest backend/tests/test_jetson_wire_contract.py -q
backend\.venv\Scripts\python.exe -m unittest discover -s jetson/tests -p 'test_wire_contract.py'
```

Expected RED: fixture missing. After adding the fixture, expected GREEN: both tests PASS on the development host.

- [ ] **Step 4: Run the fixture test on stock JetPack Python 3.6**

After Task 9 Steps 1-3 provide the approved staging board:

```bash
cd /opt/petcare-vision-dev/task1
/usr/bin/python3 -m unittest discover -s jetson/tests -p 'test_wire_contract.py'
```

Expected: PASS under the real system Python. Do not approve the shared fixture on the development runtime alone.

- [ ] **Step 5: Review and commit only Task 1 files**

```powershell
git diff --check -- contracts/petcare-jetson-wire-v1.json backend/tests/test_jetson_wire_contract.py jetson/tests/test_wire_contract.py
git add contracts/petcare-jetson-wire-v1.json backend/tests/test_jetson_wire_contract.py jetson/tests/test_wire_contract.py
git commit -m "test(jetson): freeze LAN vision wire contract"
```

---

### Task 2: Implement Jetson Authentication, Ring, And Clip State

**Files:**
- Create: `jetson/protocol.py`
- Create: `jetson/clip_writer.py`
- Create: `jetson/tests/test_protocol.py`
- Create: `jetson/tests/test_clip_writer.py`

**Interfaces:**
- Consumes: Task 1 fixture.
- Produces: `verify_request`, `ReplayGuard`, `FrameRing`, and `ClipWriter` for the Jetson service.

The public Python 3.6-compatible signatures are:

```python
def verify_request(method, target, headers, body, secret, boot_id, now_unix, replay_guard):
    """Return None or raise ProtocolError with one frozen error code."""

class FrameRing(object):
    def push(self, bucket, jpeg):
        """Keep exactly the newest 100 strictly increasing 100 ms buckets."""

class ClipWriter(object):
    def put(self, command_id, canonical_body_sha256, committed_at, event_type, event_id,
            occurred_at, received_wall_at, received_monotonic_ns):
        """Persist and return the idempotent command; raise CommandConflict or ClipBusy without mutation."""
```

- [ ] **Step 1: Write RED authentication and replay tests**

Cover the golden vector, malformed/missing/duplicate headers, body mismatch, noncanonical query, timestamp at exactly +/-30 seconds, rejection beyond the boundary, nonce reuse, constant-time HMAC comparison, wrong boot ID, bootstrap allowed only for status, boot restart invalidation, and a 120-second bounded nonce cache. Assert exception strings contain only frozen error codes and no signature, PSK, body, or certificate material.

- [ ] **Step 2: Write RED ring and state-machine tests**

Use synthetic JPEG bytes and integer 100 ms buckets. Prove:

- out-of-order/duplicate buckets are rejected;
- 101 pushes retain buckets 2 through 101 and no file exists;
- a commit requires exactly 100 preceding buckets;
- first admission stores `accepted_at=received_wall_at`, derives the trigger bucket from `received_monotonic_ns`, requires the fresh 200 ms calibration/discontinuity guard, accepts measured wall age -0.200 through 2.800 seconds only, and is unchanged by a later permitted wall-clock slew;
- default output owns 100 pre plus 200 post buckets;
- duplicate `command_id` plus identical body digest during recording/finalizing/ready returns the immutable admission receipt and does not extend it;
- duplicate `command_id` plus a different body digest returns `command_conflict` without mutation;
- delivered/expired/restart-gone commands return `clip_gone` on repeated PUT;
- a distinct event at +15 seconds yields one 450-frame clip and sorted unique events;
- GET of a ready command is side-effect free; DELETE through either coalesced command removes the shared media and marks every associated command delivered;
- extending past 1200 total buckets raises `clip_busy` without mutation;
- only one recording/finalizing clip exists;
- partial/failure/shutdown removes `.partial.mp4`;
- ready capacity is two files and 256 MiB; one-hour expiry removes content while retaining a bounded gone tombstone;
- stdlib SQLite persists command IDs/body digests/states across restart, marks interrupted recording/finalizing commands gone, retains valid ready commands, prunes tombstones after 24 hours, and never exceeds 1024 rows.

- [ ] **Step 3: Run RED**

```powershell
backend\.venv\Scripts\python.exe -m unittest discover -s jetson/tests -p 'test_protocol.py'
backend\.venv\Scripts\python.exe -m unittest discover -s jetson/tests -p 'test_clip_writer.py'
```

Expected: FAIL because the modules do not exist.

- [ ] **Step 4: Implement protocol with stdlib only**

Use `urlparse`, `parse_qsl`, `quote`, `json`, `hashlib`, `hmac.compare_digest`, `base64`, `time`, `collections.OrderedDict`, and `threading.Lock`. Do not import requests, cryptography, FastAPI, Pydantic, or Home code. Reject request bodies larger than 4096 bytes before JSON parsing. Use strict key-set equality and `type(value) is ...` checks so booleans cannot pass integer fields.

- [ ] **Step 5: Implement the exact RAM and clip state**

Use `collections.deque(maxlen=100)` for the ring and one lock around state transitions. `ClipWriter` accepts only `state_dir`, an encoder callable, and injected wall/monotonic clocks; tests use `tempfile.TemporaryDirectory` and fake clocks/encoder. It opens its own stdlib SQLite connection at `state_dir/commands.sqlite3`, creates/protects the database before the first command, uses transactions for idempotent command admission/state changes, stores the first receipt wall time plus monotonic trigger bucket, creates an owner-only partial path only on PUT, writes pre-roll first, accepts post-roll frames from the one 10 Hz sampler, finalizes atomically, hashes the result, and records media metadata in SQLite. Temperature and clock-sync policy stays in the service admission layer. A service restart removes partials, marks interrupted recording/finalizing commands gone, and keeps verified ready clips downloadable; it never resumes a partially timed clip.

- [ ] **Step 6: Run GREEN and a Python 3.6 syntax check**

```powershell
backend\.venv\Scripts\python.exe -m unittest discover -s jetson/tests -p 'test_protocol.py'
backend\.venv\Scripts\python.exe -m unittest discover -s jetson/tests -p 'test_clip_writer.py'
backend\.venv\Scripts\python.exe -m py_compile jetson/protocol.py jetson/clip_writer.py
@'
import ast
from pathlib import Path
for name in ('jetson/protocol.py', 'jetson/clip_writer.py'):
    ast.parse(Path(name).read_text(encoding='utf-8'), filename=name, feature_version=(3, 6))
'@ | backend\.venv\Scripts\python.exe -
```

Expected: all tests and the Python 3.6 grammar parse PASS. Do not use dataclass slots, `X | Y`, built-in generic annotations, `zoneinfo`, `match`, or other post-3.6 syntax.

- [ ] **Step 7: Run the same tests immediately on stock JetPack Python 3.6**

After approval to copy these non-secret source/tests to the flashed Jetson staging directory, run:

```bash
cd /opt/petcare-vision-dev/task2
/usr/bin/python3 -m unittest discover -s jetson/tests -p 'test_protocol.py'
/usr/bin/python3 -m unittest discover -s jetson/tests -p 'test_clip_writer.py'
/usr/bin/python3 -m py_compile jetson/protocol.py jetson/clip_writer.py
```

Expected: PASS under the real `/usr/bin/python3`. Task 2 is not approved or committed if only Python 3.12 emulation passed.

- [ ] **Step 8: Review and commit Task 2**

```powershell
git add jetson/protocol.py jetson/clip_writer.py jetson/tests/test_protocol.py jetson/tests/test_clip_writer.py
git commit -m "feat(jetson): add authenticated bounded clip core"
```

---

### Task 3: Implement TensorRT Capture, Inference, And Hardware H.264

**Files:**
- Create: `jetson/tensorrt_yolo.py`
- Create: `jetson/tests/test_tensorrt_yolo.py`
- Create: `jetson/tests/test_gstreamer_pipeline.py`
- Create: `jetson/build_engine.sh`
- Create after the compatibility gate: `jetson/model-manifest.json`

**Interfaces:**
- Consumes: pinned ONNX export and the real JetPack TensorRT 8.2.1 import/plugin inventory from Task 9 Step 3.
- Produces: `TensorRtYolo.infer(frame)` and an injected `GstreamerEncoder` consumed by Task 2 `ClipWriter`.

- [ ] **Step 1: Run and freeze the ONNX compatibility spike before adapter implementation**

Using the pinned Home `yolo11n.pt` and existing locked Ultralytics runtime, export fixed 640x640 ONNX candidates in deterministic order opset 13, 12, then 11. For each candidate, record export argv/tool version/SHA, run TensorRT 8.2.1 parser/build on the actual Nano, then compare one repository golden image's classes/confidences/half-open boxes to the source model within the test's declared tolerances. Select the first passing candidate only. Write `jetson/model-manifest.json` with the selected numeric `onnx_opset`, exact export argv/tool version, source/ONNX SHA-256, input/output tensor names and shapes, precision, and TensorRT version. Commit no engine bytes. No downstream Task 3 implementation begins until exactly one candidate passes and the manifest has no placeholder or `TBD`.

- [ ] **Step 2: Write host-side RED adapter tests**

Inject fake TensorRT bindings and buffers. Assert 640x480 BGR input, deterministic letterbox mapping, finite confidence in `[0,1]`, clipped half-open boxes, class filter exactly person/dog/cat, at most one result per class using current `normalize_detections` ordering, and rejection of wrong engine metadata/opset. Test the GStreamer command/caps builder as a pure function and require exactly `appsrc` BGR 640x480@10/1 -> `videoconvert` -> BGRx caps -> `nvvidconv` -> `video/x-raw(memory:NVMM),format=NV12,width=640,height=480,framerate=10/1` -> `nvv4l2h264enc` -> `h264parse` -> `qtmux` -> private partial file.

- [ ] **Step 3: Run RED**

```powershell
backend\.venv\Scripts\python.exe -m unittest discover -s jetson/tests -p 'test_tensorrt_yolo.py'
backend\.venv\Scripts\python.exe -m unittest discover -s jetson/tests -p 'test_gstreamer_pipeline.py'
```

Expected: FAIL because the TensorRT/GStreamer adapter does not exist.

- [ ] **Step 4: Implement lazy hardware imports and strict engine metadata**

Import `tensorrt`, `pycuda.driver`, `pycuda.autoinit`, `cv2`, `numpy`, and `gi.repository.Gst` only inside hardware constructors. Keep module importable on the host. Beside the engine require owner-readable JSON with exactly the committed model-manifest fields plus Jetson module model and generated engine SHA. Refuse mismatches before deserialization.

Use the existing model's three recognized COCO classes only. Postprocessing must yield the raw bbox/confidence schema from the design; do not duplicate PetCare subject or zone logic on the Jetson.

- [ ] **Step 5: Implement on-device engine build script**

`jetson/build_engine.sh` accepts `--onnx`, `--model-manifest`, `--output`, and `--precision fp16`. It verifies `uname -m` is `aarch64`, `/etc/nv_tegra_release` is R32 revision 7.6, `dpkg-query` reports TensorRT 8.2.1, and the manifest's ONNX SHA/opset/tensor metadata match before invoking `/usr/src/tensorrt/bin/trtexec`. It writes engine metadata atomically only after one deterministic image smoke returns valid finite output. It never downloads a model or executes an engine from another runtime.

- [ ] **Step 6: Implement GStreamer encoder adapter**

Feed decoded 640x480 BGR frames to one `appsrc` at 10/1 through this frozen path: `video/x-raw,format=BGR,width=640,height=480,framerate=10/1 ! videoconvert ! video/x-raw,format=BGRx ! nvvidconv ! video/x-raw(memory:NVMM),format=NV12,width=640,height=480,framerate=10/1 ! nvv4l2h264enc insert-sps-pps=true ! h264parse ! qtmux ! filesink`. EOS must complete within five seconds; nonzero bus errors, timeout, wrong codec/pixel format/resolution, or duration mismatch fail and remove the partial. Task 2 owns state/cleanup; this adapter owns only one encoder invocation.

- [ ] **Step 7: Run host GREEN**

```powershell
backend\.venv\Scripts\python.exe -m unittest discover -s jetson/tests -p 'test_tensorrt_yolo.py'
backend\.venv\Scripts\python.exe -m unittest discover -s jetson/tests -p 'test_gstreamer_pipeline.py'
backend\.venv\Scripts\python.exe -m py_compile jetson/tensorrt_yolo.py
@'
import ast
from pathlib import Path
ast.parse(Path('jetson/tensorrt_yolo.py').read_text(encoding='utf-8'), filename='jetson/tensorrt_yolo.py', feature_version=(3, 6))
'@ | backend\.venv\Scripts\python.exe -
```

Expected: all injected tests and Python 3.6 grammar parse PASS.

- [ ] **Step 8: Run the adapter immediately on stock JetPack Python 3.6**

On the approved Jetson staging tree, build the engine and run:

```bash
cd /opt/petcare-vision-dev/task3
/usr/bin/python3 -m unittest discover -s jetson/tests -p 'test_tensorrt_yolo.py'
/usr/bin/python3 -m unittest discover -s jetson/tests -p 'test_gstreamer_pipeline.py'
/usr/bin/python3 -m py_compile jetson/tensorrt_yolo.py
```

Expected: tests PASS with the real TensorRT 8.2.1 bindings and GStreamer plugins. Task 3 is not approved or committed on host fakes alone.

- [ ] **Step 9: Review and commit Task 3**

```powershell
git add jetson/tensorrt_yolo.py jetson/tests/test_tensorrt_yolo.py jetson/tests/test_gstreamer_pipeline.py jetson/build_engine.sh jetson/model-manifest.json
git commit -m "feat(jetson): add TensorRT and H264 adapters"
```

---

### Task 4: Build The Six-Operation Jetson Service And Package

**Files:**
- Create: `jetson/vision_node.py`
- Create: `jetson/tests/test_vision_node.py`
- Create: `jetson/petcare-vision.service`
- Create: `jetson/install.sh`
- Create: `jetson/tests/test_package.py`

**Interfaces:**
- Consumes: Tasks 1-3.
- Produces: exact HTTPS service with six authenticated operations across four paths at one configured private Ethernet address:9443 and an approval-gated systemd installer.

- [ ] **Step 1: Write RED endpoint tests with injected camera/inference/encoder**

Use `http.client.HTTPSConnection` against an ephemeral TLS test server. Assert exact operations `GET /v1/status`, `GET /v1/observations`, `GET /v1/preview.jpg`, and `PUT`/`GET`/`DELETE /v1/clips/<command_id>`; method handling; strict schemas; HMAC/replay behavior; signed status `server_time`; long-poll 200/204; global 2 FPS preview limit; exact application preview headers after ignoring only `Date`/`Connection`, 1 MiB bound, digest, and a decodable 640x480x3 JPEG; first PUT requires signed `committed_at`, the fresh calibration/discontinuity guard, measured wall age -0.200 through 2.800 seconds, actual receipt wall/monotonic capture, and returns 201; identical replay during recording/finalizing/ready returns 200 with the immutable admission receipt; conflicting replay returns 409; replay after delete/expiry/restart-lost returns 410; GET while recording returns 425; ready GET returns 200 with the exact frozen application media headers and no state change; DELETE returns 204 and tombstones all coalesced commands; camera/capacity/unsynchronized clock returns 503; and all authentication failures expose only the same 401 body. Assert `Server`, transfer/content encoding, and extra application headers are absent and no handler imports Home/database/cloud modules.

- [ ] **Step 2: Write RED package and network-policy tests**

Assert the unit contains:

```ini
[Unit]
Description=PetCare Jetson Vision Node
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=petcare-vision
Group=petcare-vision
SupplementaryGroups=video
WorkingDirectory=/opt/petcare-vision
ExecStart=/usr/bin/python3 /opt/petcare-vision/vision_node.py --config /var/lib/petcare-vision/config.json
Restart=on-failure
RestartSec=5
NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=strict
ProtectHome=true
ReadWritePaths=/var/lib/petcare-vision
UMask=0077
TimeoutStopSec=15

[Install]
WantedBy=multi-user.target
```

The installer must reject non-aarch64, wrong L4T/TensorRT, wildcard/public/link-local/multicast/loopback bind IP, identical Home/Jetson IP, missing Ethernet interface, Wi-Fi bind, missing webcam, missing temperature probe, or absent 10 W power mode. Fixture mode writes a root tree without calling `useradd`, `ufw`, `systemctl`, or `openssl`. Install mode generates a self-signed IP-SAN certificate, 32-byte PSK, owner-only config, and one pairing bundle; adds exactly one source-IP firewall rule; enables the service only after verification. Fan presence/spin is a manual Task 8 check and the thermal gate is authoritative.

- [ ] **Step 3: Run RED**

```powershell
backend\.venv\Scripts\python.exe -m unittest discover -s jetson/tests -p 'test_vision_node.py'
backend\.venv\Scripts\python.exe -m unittest discover -s jetson/tests -p 'test_package.py'
```

Expected: FAIL because service/package files do not exist.

- [ ] **Step 4: Implement the minimal stdlib HTTPS service**

Use `HTTPServer` plus `ThreadingMixIn`, `SSLContext.wrap_socket`, stdlib `sqlite3`, one capture/inference thread, one 10 Hz sampler, and the Task 2 lock-protected state. Set `daemon_threads = False`, bound request/header/body sizes, disable directory serving, and close connections after each response. Handlers use `send_response_only`, explicitly add only RFC-compliant `Date` and `Connection: close` plus the frozen application headers, and never call `send_response`, so `Server` cannot appear. Shutdown order is: stop accepting, stop PUT admission, finish or abort bounded encoder, transactionally mark interrupted commands, stop sampler, stop camera, clean partials, close SQLite/socket.

Status reads `tegrastats`/sysfs through an injected probe, stamps `server_time` at response creation, and returns only the exact safe schema. Capture timestamp uses UTC synchronized by systemd-timesyncd; service status is degraded and PUT admission is rejected when clock sync is absent. The handler captures receipt wall and monotonic clocks before JSON parsing, passes both to `ClipWriter`, and never derives the trigger bucket from `accepted_at`.

- [ ] **Step 5: Implement the approval-gated installer**

`jetson/install.sh --fixture-root "$PWD/.runtime/jetson-package-fixture"` is non-mutating outside the fixture. `--install --bind-ip 192.168.50.20 --home-ip 192.168.50.10 --interface eth0 --webcam /dev/video0` performs real mutation only after approval. It installs only tracked Jetson files, stock system packages already supplied by JetPack, the engine/metadata, certificate, config, and service. It never installs Docker, pip packages, Home backend, cloudflared, MQTT, PostgreSQL, Supabase, or an internet-facing service.

The one-time pairing bundle contains exactly Jetson URL, certificate PEM, and PSK, mode `0600`; no private TLS key or model is exported. After successful Home import, the operator deletes this bundle.

- [ ] **Step 6: Run GREEN and fixture install**

```powershell
backend\.venv\Scripts\python.exe -m unittest discover -s jetson/tests -p 'test_vision_node.py'
backend\.venv\Scripts\python.exe -m unittest discover -s jetson/tests -p 'test_package.py'
wsl bash -n jetson/install.sh
wsl bash jetson/install.sh --fixture-root .runtime/jetson-package-fixture
@'
import ast
from pathlib import Path
ast.parse(Path('jetson/vision_node.py').read_text(encoding='utf-8'), filename='jetson/vision_node.py', feature_version=(3, 6))
'@ | backend\.venv\Scripts\python.exe -
```

Expected: tests and Python 3.6 grammar parse PASS, shell syntax PASS, fixture prints `Jetson vision package fixture PASS`, and performs no host/network/service mutation.

- [ ] **Step 7: Run service/package tests immediately on stock JetPack Python 3.6**

```bash
cd /opt/petcare-vision-dev/task4
/usr/bin/python3 -m unittest discover -s jetson/tests -p 'test_vision_node.py'
/usr/bin/python3 -m unittest discover -s jetson/tests -p 'test_package.py'
/usr/bin/python3 -m py_compile jetson/vision_node.py
bash -n jetson/install.sh
```

Expected: PASS on the real JetPack runtime before any service/firewall install. Task 4 is not approved or committed on host fakes alone.

- [ ] **Step 8: Review and commit Task 4**

```powershell
git add jetson/vision_node.py jetson/tests/test_vision_node.py jetson/petcare-vision.service jetson/install.sh jetson/tests/test_package.py
git commit -m "feat(jetson): package private vision service"
```

---

### Task 5: Implement The Home Jetson Client And Camera Adapter

**Files:**
- Create: `backend/app/jetson_contracts.py`
- Create: `backend/app/jetson_client.py`
- Create: `backend/tests/test_jetson_contracts.py`
- Create: `backend/tests/test_jetson_client.py`
- Modify: `backend/app/config.py`
- Modify: `backend/app/camera_service.py`
- Modify: `backend/tests/test_camera_service.py`

**Interfaces:**
- Consumes: Task 1 fixture; exact Jetson service from Task 4 may be mocked.
- Produces: `JetsonVisionClient.next_frame(zones) -> ProcessedFrame`, signed clock calibration, `put_clip`, `download_clip`, and `delete_clip`.
- Preserves: current `CameraService` persistence, ingress ordering, availability slots, status shape, and MJPEG route.

- [ ] **Step 1: Write RED strict-contract and signing tests**

Pydantic models reject extra keys, wrong boot/sequence, naive or stale time, future time, wrong width/height, nonfinite numbers, unknown/duplicate classes, invalid boxes, noncanonical event sets, oversized media, and unsafe URL/certificate/secret paths. Recompute the Task 1 HMAC exactly. Test TLS with the fixture certificate and prove wrong CA, wrong IP SAN, `http://`, public IP, and `verify=False` are impossible through config. Clock tests use signed status response timestamps plus Home monotonic send/receive times to calculate midpoint offset and half-RTT uncertainty; every first PUT performs a new calibration, and admission is disabled when the sample is older than one second, `abs(offset)+half_rtt+50 ms >200 ms`, or either 100 ms wall/monotonic guard detects a >25 ms discontinuity.

- [ ] **Step 2: Write RED camera integration tests**

Extend existing helpers without breaking local `usb`, `file`, or `disabled` cases. In `jetson` mode prove:

- Home long-polls one observation, fetches preview at most twice per second, derives `subject_id`, center, and `zone_name` with existing helpers, then persists and resolves ingress in current order;
- source `observed_at`, not receipt time, reaches the database;
- duplicate/out-of-order sequence and timestamps older than three seconds become tombstones, not events;
- preview is bounded to 1,048,576 bytes before body read, the exact application headers/digest/boot/sequence/time are checked after ignoring only standard `Date`/`Connection`, `cv2.imdecode` yields exactly `(480, 640, 3)`, and only then it becomes the current MJPEG chunk without exposing its source URL;
- after three seconds of no valid observation, status is offline; reconnect with a new boot resets sequence safely;
- shutdown closes bounded requests and joins without stopping MQTT/rule workers;
- local camera modes still pass unchanged.

- [ ] **Step 3: Run RED**

```powershell
backend\.venv\Scripts\python.exe -m pytest backend/tests/test_jetson_contracts.py backend/tests/test_jetson_client.py backend/tests/test_camera_service.py backend/tests/test_rules.py -q
```

Expected: FAIL because Jetson Home modules/config mode do not exist.

- [ ] **Step 4: Extend config with the smallest strict surface**

Add `camera_source="jetson"` plus required values loaded from an ACL JSON runtime file rather than individual secret environment variables:

```json
{
  "url": "https://192.168.50.20:9443",
  "home_ip": "192.168.50.10",
  "ca_cert_path": "C:\\ProgramData\\PetCare\\jetson.crt",
  "psk_path": "C:\\ProgramData\\PetCare\\jetson.psk"
}
```

Only `PETCARE_CAMERA_SOURCE=jetson` and `PETCARE_JETSON_CONFIG=<absolute path>` enter the environment; certificate and PSK bytes remain in owner-only files. Validate absolute paths, owner-only ACLs, exactly 32 PSK bytes, HTTPS, port 9443, and a private literal IPv4 host. Never serialize secret content in config errors or model repr.

- [ ] **Step 5: Implement the client with existing httpx**

`JetsonVisionClient` is one facade over three separately constructed `httpx.Client` instances, each with its own one-connection pool and the same pinned-certificate `SSLContext`: camera/control for status/observation/preview, admission for PUT only, and media for GET/DELETE only. No pool, semaphore, or connection slot is shared between admission and media. Sign each request with stdlib HMAC, cache current boot/sequence and the fresh clock-calibration bound, limit response bytes before parsing, and allow one in-flight operation per class. `next_frame(zones)` maps strict raw detections through existing `SUBJECTS` and `zone_for_center`, fetches/reuses the validated 2 FPS JPEG, and returns the current `ProcessedFrame` shape without invoking local YOLO.

- [ ] **Step 6: Add the minimal `CameraService` branch**

`build_camera_service` creates the current local pipeline for `usb`/`file`, disabled service for `disabled`, or a `JetsonVisionClient` for `jetson`. Reuse `_persist_frame`, status, availability, MJPEG, and ingress methods. Do not create a parallel database path or change API routes. The remote branch calls `next_frame(zones)` and then follows the same persist/latest/resolve order.

- [ ] **Step 7: Run GREEN and commit**

```powershell
backend\.venv\Scripts\python.exe -m pytest backend/tests/test_jetson_contracts.py backend/tests/test_jetson_client.py backend/tests/test_camera_service.py backend/tests/test_rules.py backend/tests/test_api.py -q
```

Expected: all tests PASS, including unchanged local camera and exact Task 12 routes.

```powershell
git add backend/app/jetson_contracts.py backend/app/jetson_client.py backend/app/config.py backend/app/camera_service.py backend/tests/test_jetson_contracts.py backend/tests/test_jetson_client.py backend/tests/test_camera_service.py
git commit -m "feat(agent): consume Jetson vision observations"
```

---

### Task 6: Route Committed Event Intents Through Jetson Clip Delivery

**Files:**
- Modify: `backend/app/clip_contracts.py`
- Modify: `backend/tests/test_clip_contracts.py`
- Create: `backend/app/clip_outbox.py`
- Create: `backend/app/clip_delivery.py`
- Create: `backend/app/clip_upload_queue.py`
- Create: `backend/migrations/versions/0002_clip_trigger_outbox.py`
- Create: `backend/tests/test_clip_outbox.py`
- Create: `backend/tests/test_clip_delivery.py`
- Create: `backend/tests/test_clip_upload_queue.py`
- Modify: `backend/app/models.py`
- Modify: `backend/app/rules.py`
- Modify: `backend/tests/test_rules.py`
- Modify: `backend/tests/test_migrations.py`

**Interfaces:**
- Consumes: Task 5 `put_clip`, `download_clip`, `delete_clip`; existing `SignedClipUploadClient` contract from the Home plan.
- Produces: one crash-recoverable event-to-Jetson-to-Home-queue flow with fast admission isolated from slow media/cloud work.
- Preserves: atomic event/outbox commits and unchanged rule semantics.

- [ ] **Step 1: Write RED transaction and migration tests**

The table is the prior planned `clip_trigger_outbox` with these additional nullable fields and constraints:

```text
deadline_at = created_at + 3 seconds
remote_boot_id = 32 lowercase hex or null
remote_command_id = 32 lowercase hex or null
accepted_at = UTC or null
processed_at = UTC or null
terminal_reason = clip_missed or null
```

Assert eligible event and intent commit atomically, rollback removes both, unique `(event_type,event_id)` holds, `no_meal_12h` creates none, and existing behavior/anomaly rows and publication ordering are unchanged. `created_at` is set explicitly from the Home process's guarded UTC clock immediately before the shared transaction commit, never from a PostgreSQL server default; transaction/dispatch latency consumes rather than extends the three-second window. A detected Home wall/monotonic discontinuity suppresses clip intent admission until the 60-second guard clears.

- [ ] **Step 2: Freeze the concrete Home worker APIs**

`backend/app/clip_delivery.py` owns exactly the two Jetson-side workers:

```python
class ClipAdmissionWorker:
    def start(self) -> None: ...
    def dispatch_once(self) -> bool: ...
    def stop(self, *, timeout_seconds: float = 5.0) -> None: ...

class ClipDeliveryWorker:
    def start(self) -> None: ...
    def deliver_once(self) -> bool: ...
    def stop(self, *, timeout_seconds: float = 45.0) -> None: ...
```

`ClipAdmissionWorker` owns only selection of unaccepted outbox rows, command-ID persistence, clock-bound verification, and PUT. It has one dedicated thread and the Jetson client's dedicated admission HTTP pool/slot; it never performs GET, file I/O, ffprobe, DELETE, or cloud upload. `ClipDeliveryWorker` owns only already-accepted rows, GET/download/header and database identity validation, validation-only ffprobe, durable queue handoff, DELETE, and processed-row transaction. Its 45-second download and ffprobe process have a different thread and HTTP pool/slot, so they cannot delay first PUT.

`backend/app/clip_upload_queue.py` alone owns the bounded cloud queue:

```python
class ClipUploadQueueFull(RuntimeError):
    pass

class ClipUploadQueue:
    @classmethod
    def open(cls, root: Path, client: SignedClipUploadClient, *, now=utc_now) -> "ClipUploadQueue": ...
    def enqueue_verified(self, queue_id: str, source_mp4: Path, metadata: ClipMetadata) -> str: ...
    def find_unreleased_by_command(self, command_id: str) -> str | None: ...
    def release(self, queue_id: str) -> None: ...
    def start(self) -> None: ...
    def stop(self, *, timeout_seconds: float = 45.0) -> None: ...
    @property
    def depth(self) -> int: ...
```

`ClipMetadata` remains in the existing `backend/app/clip_contracts.py`; `ClipUploadQueueFull` and every other queue type are owned by `clip_upload_queue.py`. The delivery worker calculates lowercase `queue_id = sha256("PETCARE-HOME-QUEUE-V1\n" + accepted_boot_id + "\n" + canonical_sorted_events + "\n" + content_sha256 + "\n")`. The queue recovers from its owner-only directory, counts released, unreleased, and in-flight entries against the hard capacity of eight, and atomically creates canonical MP4/JSON sidecar pairs including all remote command IDs plus `released=false`. It takes ownership of `source_mp4` only after both are durable; on `ClipUploadQueueFull` or any pre-rename error the caller retains the source. Unreleased items cannot upload or expire. `release` atomically flips the sidecar only after Jetson acknowledgement, is idempotent, and makes the item eligible for upload/one-hour expiry. The queue alone invokes existing `SignedClipUploadClient`, uses retry delays 5/30/120/600 seconds capped at 600, and deletes an item only after signed upload success. This is the retained queue behavior from the superseded recorder plan; no `ClipRecorder`, generic queue abstraction, phantom type, or second uploader remains.

- [ ] **Step 3: Write RED admission, delivery, and queue recovery tests**

Cover this exact sequence:

1. the admission worker selects the oldest due unaccepted row with `FOR UPDATE SKIP LOCKED`;
2. if no remote command, generate and persist a random command ID, perform an immediate signed calibration with sample age <=1 second, `abs(offset)+half_rtt+50 ms <=200 ms`, and healthy discontinuity guards, sign the row's immutable `created_at` as `committed_at`, call idempotent PUT immediately and once after one second while before the real three-second deadline, and persist the Jetson's actual receipt `accepted_at`/boot without marking processed; first acceptance must be 201 and a lost-response replay while recording/finalizing/ready must be 200 with the immutable receipt;
3. after acceptance, GET returning 425 reschedules with the capped 1/2/4/8/16/30-second poll backoff; an unaccepted 503/transport failure or Jetson `409 command_expired` becomes terminal `clip_missed` at the three-second deadline;
4. GET 200 streams to an owner-only `.partial.mp4`, enforces 256 MiB ceiling, checks the exact application-header set after ignoring only `Date`/`Connection`, length/SHA, validates command/events against accepted PostgreSQL rows, validates canonical ordered time headers and `started_at <= accepted_at <= ended_at`, and requires header duration/frame count plus ffprobe H.264/YUV420P 640x480 10/1 duration/frame count to agree within 100 ms;
5. derive the stable queue ID, durably `enqueue_verified(..., released=false)`, then DELETE; after DELETE 204 or idempotent 410, mark every included event row processed in one transaction and only then call `release(queue_id)`;
6. before interpreting any restart/retry 410, call `find_unreleased_by_command`; if found and its sidecar rows are already processed, release it only; otherwise retry/idempotently accept DELETE, commit those rows processed, then release the same queue ID without GET or a second queue entry;
7. wrong digest, truncation, wrong codec/duration/event, queue full, or disk error cleans partial and leaves rows retryable;
8. a Jetson boot change refreshes request authentication and retries GET for the same persistent command; only a `410 clip_gone` with no matching unreleased queue entry assigns a new random command ID, and only before the original three-second admission deadline; `409 command_conflict` is terminal corruption and never overwrites the existing Jetson command;
9. deadline expiry sets `terminal_reason=clip_missed`, `processed_at`, and never sends a later commit;
10. hold media GET open for 45 seconds and block ffprobe, then create a new eligible row; the separate admission worker still issues its first PUT within the three-second gate;
11. queue restart recovers exact MP4/sidecar pairs, never exceeds eight including unreleased/in-flight, preserves caller ownership on saturation, blocks upload/expiry until release, reconciles crashes after enqueue, DELETE, and processed-row commit through the stable queue ID, retries 5/30/120/600, and expires released items at one hour without duplicate upload.

Simulate crashes after each boundary and prove restart resumes without lost committed intent, duplicate cloud queue entry, or duplicate processed event.

- [ ] **Step 4: Run RED**

```powershell
backend\.venv\Scripts\python.exe -m pytest backend/tests/test_clip_contracts.py backend/tests/test_rules.py backend/tests/test_clip_outbox.py backend/tests/test_clip_delivery.py backend/tests/test_clip_upload_queue.py backend/tests/test_migrations.py -q
```

Expected: FAIL because outbox/delivery/migration do not exist.

- [ ] **Step 5: Implement the atomic intent only once**

Reuse the prior Home plan's event insertion points. Add the outbox row in the same SQLAlchemy session before the existing commit. Do not call the Jetson from `RuleEngine`, do not change thresholds/deduplication, and do not add a second event bus.

- [ ] **Step 6: Implement the isolated admission/delivery workers and concrete queue**

Implement the exact three APIs above. Use the database row as durable state, the existing manifest-backed absolute ffprobe executable for media validation only, and separate PUT/media HTTP pools. Event identity comes only from exact response headers matched to accepted database rows, never from ffprobe tags. Do not add Redis, MQTT clip topics, another Home database, a Home FFmpeg encoding process, or another cloud uploader. Store only bounded safe error codes.

- [ ] **Step 7: Run GREEN and regression tests**

```powershell
backend\.venv\Scripts\python.exe -m pytest backend/tests/test_clip_contracts.py backend/tests/test_rules.py backend/tests/test_rule_worker.py backend/tests/test_clip_outbox.py backend/tests/test_clip_delivery.py backend/tests/test_clip_upload_queue.py backend/tests/test_migrations.py backend/tests/test_camera_service.py -q
```

Expected: all tests PASS; no-meal exclusion, rollback, restart, queue saturation, and camera/sensor independence are covered.

- [ ] **Step 8: Review and commit Task 6**

```powershell
git add backend/app/clip_contracts.py backend/app/clip_outbox.py backend/app/clip_delivery.py backend/app/clip_upload_queue.py backend/app/models.py backend/app/rules.py backend/migrations/versions/0002_clip_trigger_outbox.py backend/tests/test_clip_contracts.py backend/tests/test_clip_outbox.py backend/tests/test_clip_delivery.py backend/tests/test_clip_upload_queue.py backend/tests/test_rules.py backend/tests/test_migrations.py
git commit -m "feat(agent): deliver event clips through Jetson"
```

---

### Task 7: Integrate Lifecycle, Runtime, Pairing, Packages, And Exact Task 12 API

**Files:**
- Create: `backend/app/agent_health.py`
- Create: `backend/app/agent_lifecycle.py`
- Create: `backend/app/agent_runtime.py`
- Create: `backend/app/windows_service.py`
- Create: `backend/tests/test_agent_health.py`
- Create: `backend/tests/test_agent_lifecycle.py`
- Create: `backend/tests/test_agent_runtime.py`
- Create: `backend/tests/test_windows_service.py`
- Create: `packaging/windows/install-home-agent.ps1`
- Create: `packaging/tests/test_windows_home_agent_packaging.py`
- Create: `packaging/linux/petcare-agent.service`
- Create: `packaging/linux/install-home-agent.sh`
- Create: `packaging/tests/test_linux_home_agent_packaging.py`
- Modify by integration owner only: `backend/app/main.py`
- Modify by integration owner only: `backend/tests/test_api.py`

**Interfaces:**
- Consumes: Tasks 5-6, existing Home enrollment/cloud upload components, manifest-backed runtime tools, and the Jetson one-time pairing bundle.
- Produces: revised lifecycle without `FrameRing`, local `ClipRecorder`, or `latest_frame_sink`, plus the deployed Windows/Linux environment and end-to-end pairing import.

The revised lifecycle shape is:

```python
@dataclass(frozen=True, slots=True)
class AgentLifecycleComponents:
    jetson_client: JetsonVisionClient
    clip_admission: ClipAdmissionWorker
    clip_delivery: ClipDeliveryWorker
    upload_queue: ClipUploadQueue
    started_at: datetime
```

The only exported lifecycle operations are:

```python
def build_agent_components(config_path, tools_path, session_factory, *, now=utc_now) -> AgentLifecycleComponents: ...
def start_agent_components(components: AgentLifecycleComponents) -> None: ...
def stop_agent_components(components: AgentLifecycleComponents, *, timeout_seconds: float = 105.0) -> None: ...
```

- [ ] **Step 1: Write RED lifecycle and isolation tests**

Assert construction has no side effect; start order is upload queue, Jetson client, fast clip admission, then slow clip delivery. `stop_agent_components` owns only the exact Task 0 component order: clip admission (5 seconds), clip delivery (45 seconds), Jetson client (2 seconds), upload queue (45 seconds), all under the one 105-second monotonic deadline with remaining-time allocation. The integration lifespan wraps it with `rule_ingress.stop_accepting -> mqtt.stop -> rule_worker.shutdown -> camera.shutdown -> stop_agent_components -> dispose_database`. Every stop is idempotent/bounded and cleanup continues after an earlier failure. A Jetson timeout or client crash degrades only camera/clip health. MQTT ingestion, sensor API, PostgreSQL, rule worker, dashboard polling, and cloudflared remain running.

Assert agent health contains Jetson camera/boot/temperature/throttle, delivery queue state, upload queue depth, and safe last error, but no URL, IP, PSK, certificate path/content, token, database URL, MQTT credential, or clip path.

- [ ] **Step 2: Write RED runtime, pairing, and package tests**

`agent_runtime` must expose existing `enroll|run|status` plus:

```text
python -m app.agent_runtime pair-jetson --config <agent.json> --bundle <pairing.json> --jetson-config <absolute jetson.json>
```

Fixture tests prove `pair-jetson` validates the private literal URL/IP-SAN certificate/32-byte PSK, creates certificate and PSK owner-only before atomically writing strict `jetson.json`, refuses overwrite or permissive ACLs, prints no secret, and leaves the source bundle for the operator to delete after verified import. `run` refuses to launch any child unless the file and its referenced cert/PSK pass the same validation, then passes exactly `PETCARE_CAMERA_SOURCE=jetson` and `PETCARE_JETSON_CONFIG=<absolute imported file>` in the backend child's scrubbed environment; neither value is reconstructed by `main.py`.

Windows tests require `packaging/windows/install-home-agent.ps1 -Action Install -ConfigPath ... -JetsonConfigPath ...` to store only the non-secret absolute `JetsonConfigPath` beside existing registry paths. `windows_service.py` reads it, validates it, and passes both exact environment variables to `agent_runtime`; status output remains secret-free. Linux tests require the unit to contain `Environment=PETCARE_CAMERA_SOURCE=jetson` and `Environment=PETCARE_JETSON_CONFIG=/var/lib/petcare/jetson.json`. Before enablement, `install-home-agent.sh --install --pairing-bundle <owner-only path>` runs the installed venv as user `petcare`: `python -m app.agent_runtime pair-jetson --config /var/lib/petcare/agent.json --bundle <path> --jetson-config /var/lib/petcare/jetson.json`. The import writes `/var/lib/petcare/jetson.crt`, `jetson.psk`, then `jetson.json`, all owner `petcare:petcare`, mode `0600`; installer validates content/ACLs and a signed status call, deletes the bundle only after success, then enables the service. Fixture modes perform no registry, SCM, user, firewall, or systemd mutation and never delete their input fixture.

- [ ] **Step 3: Run RED**

```powershell
backend\.venv\Scripts\python.exe -m pytest backend/tests/test_agent_health.py backend/tests/test_agent_lifecycle.py backend/tests/test_agent_runtime.py backend/tests/test_windows_service.py backend/tests/test_api.py packaging/tests/test_windows_home_agent_packaging.py packaging/tests/test_linux_home_agent_packaging.py -q
```

Expected: FAIL because revised lifecycle/integration does not exist.

- [ ] **Step 4: Implement component-owned lifecycle**

Keep `agent_lifecycle.py` free of FastAPI/router imports. It constructs the strict Jetson client, separate admission/delivery workers, and concrete `ClipUploadQueue` from ACL config. No frame ring or encoder is constructed on Home. Health translates Jetson faults into bounded reasons and does not make hardware details public.

- [ ] **Step 5: Implement pairing and the deployed environment end to end**

Implement the exact Windows and Linux CLI/import/install order tested in Step 2. The imported `jetson.json` contains only URL, Home IP, absolute CA path, and absolute PSK path; it never embeds PSK/certificate bytes. Child launch uses an explicit allowlisted environment, with both Jetson variables inserted by the supervisor on Windows and Linux. `PETCARE_JETSON_CONFIG` is the single configuration authority from service install through `agent_runtime` to Task 5; there is no development-only fallback path.

- [ ] **Step 6: Let the single integration owner wire `main.py`**

The integration owner passes the Jetson-aware camera service and delivery components into the existing lifespan, preserves the exact 12 HTTP plus one WebSocket Task 12 route set, and proves shutdown cannot hang. No new public health, Jetson, commit, or clip-transfer route is added.

- [ ] **Step 7: Run GREEN plus package fixtures and sensor-offline integration**

```powershell
backend\.venv\Scripts\python.exe -m pytest backend/tests/test_agent_health.py backend/tests/test_agent_lifecycle.py backend/tests/test_agent_runtime.py backend/tests/test_windows_service.py backend/tests/test_api.py backend/tests/test_websocket.py backend/tests/test_mqtt_ingest.py backend/tests/test_rules.py packaging/tests/test_windows_home_agent_packaging.py packaging/tests/test_linux_home_agent_packaging.py -q
& packaging/windows/install-home-agent.ps1 -Action Fixture -ConfigPath "$PWD/.runtime/agent.json" -JetsonConfigPath "$PWD/.runtime/jetson.json"
wsl bash packaging/linux/install-home-agent.sh --root "$PWD/.runtime/linux-agent-fixture"
```

Expected: all tests PASS. Unit/lifespan tests use an injected Jetson client timeout and prove camera health degrades without stopping MQTT/rule/database components. Task 8 alone owns the full cross-stack disconnect test source; Task 10 only reruns it.

- [ ] **Step 8: Review and commit Task 7**

```powershell
git add backend/app/agent_health.py backend/app/agent_lifecycle.py backend/app/agent_runtime.py backend/app/windows_service.py backend/app/main.py backend/tests/test_agent_health.py backend/tests/test_agent_lifecycle.py backend/tests/test_agent_runtime.py backend/tests/test_windows_service.py backend/tests/test_api.py packaging/windows/install-home-agent.ps1 packaging/tests/test_windows_home_agent_packaging.py packaging/linux/petcare-agent.service packaging/linux/install-home-agent.sh packaging/tests/test_linux_home_agent_packaging.py
git commit -m "feat(agent): integrate Jetson vision lifecycle"
```

---

### Task 8: Build, Commit, And Freeze All Candidate Verification Tooling

**Files:**
- Create: `backend/tests/integration/test_jetson_vision_stack.py`
- Create: `tools/run_jetson_integration.ps1`
- Create: `tools/tests/test_run_jetson_integration.ps1`
- Create: `tools/jetson_vision_soak.py`
- Create: `tools/tests/test_jetson_vision_soak.py`
- Runtime marker only, untracked: `.runtime/evidence/jetson-candidate-sha.txt`

**Interfaces:**
- Consumes: completed Tasks 1-7.
- Produces: every source file needed by the hardware soak and final regression, committed before one immutable candidate SHA is named.

- [ ] **Step 1: Write RED soak parser/gate tests**

Feed synthetic observation, signed clock-calibration, wall/monotonic guards, `tegrastats`, locked CPU/GPU clock, boot ID, kernel-log, ffprobe, disk, and reconnect samples. Assert exact thresholds: 60 minutes; candidate SHA equality; >=3.0 inference FPS; p99 observation gap <=1.0 second; p99 Home age <=1.5 seconds; no healthy gap >3 seconds; every first PUT calibration age <=1 second and `abs(clock midpoint offset)+half RTT+50 ms <=200 ms`; no >25 ms wall/monotonic discontinuity; preview <=2 FPS and valid 640x480 JPEG; default 300 frames/30.000 seconds; overlap 450/45.000; duration tolerance 100 ms; H.264/YUV420P 640x480 10/1; ring <=100; ready files <=2; temp <=256 MiB; temperature < configurable 80 C; unchanged boot ID; no new throttle/undervoltage kernel match; no locked-clock drop; and sensor API success throughout a Jetson disconnect.

- [ ] **Step 2: Write RED integration-runner tests**

The integration test covers the exact six-operation fixture, actual-receipt/monotonic admission, the real three-second clock-bound deadline including harmless negative skew, immutable PUT replay, a 45-second media stall that does not block a new PUT, coalesced DELETE, restart/gone recovery, exact application preview/media headers after ignoring only `Date`/`Connection`, JPEG decode, validation-only ffprobe media checks, database-owned event identity, stable unreleased queue reconciliation across enqueue/DELETE crashes, and a greater-than-three-second disconnect that leaves sensors alive. Runner tests require refusal of dirty tracked state, production credentials, unexpected listeners, absent/stale hardware evidence, or evidence SHA different from candidate SHA.

- [ ] **Step 3: Run RED**

```powershell
backend\.venv\Scripts\python.exe -m pytest tools/tests/test_jetson_vision_soak.py -q
& tools/tests/test_run_jetson_integration.ps1
```

Expected: both commands FAIL because the collector/runner/integration test do not exist.

- [ ] **Step 4: Implement the bounded collector and exact runner**

The collector uses stdlib plus existing Home httpx, reads Jetson status only through the authenticated client, samples Home camera/sensor APIs locally, consumes raw `tegrastats`/clock/kernel evidence supplied by the approved harness, triggers fixture-authorized eligible events, inspects clips with manifest ffprobe, and atomically writes secret-free JSON. The runner records candidate Git SHA, starts only fixture services for local tests, waits on authoritative health, invokes the integration test, and stops all children in `finally`. Both refuse to serialize request headers, URLs, IPs, paths, certificates, PSKs, usernames, hostnames, MACs, serials, or cloud credentials.

- [ ] **Step 5: Run GREEN before hardware**

```powershell
if (-not $env:TEST_DATABASE_URL) { throw 'Set the dedicated loopback TEST_DATABASE_URL.' }
backend\.venv\Scripts\python.exe -m pytest backend/tests/integration/test_jetson_vision_stack.py tools/tests/test_jetson_vision_soak.py -q
& tools/tests/test_run_jetson_integration.ps1
& tools/run_jetson_integration.ps1 -Fixture
git diff --check
```

Expected: all fixture/parser/runner tests PASS and no hardware evidence is fabricated.

- [ ] **Step 6: Commit every candidate source file, then freeze one SHA**

```powershell
git add backend/tests/integration/test_jetson_vision_stack.py tools/run_jetson_integration.ps1 tools/tests/test_run_jetson_integration.ps1 tools/jetson_vision_soak.py tools/tests/test_jetson_vision_soak.py
git commit -m "test(jetson): add immutable vision verification gates"
$tracked = git status --short --untracked-files=no
if ($tracked) { throw "Tracked tree must be clean before candidate freeze: $tracked" }
$candidate = git rev-parse HEAD
New-Item -ItemType Directory -Force .runtime/evidence | Out-Null
Set-Content -NoNewline .runtime/evidence/jetson-candidate-sha.txt $candidate
```

Expected: the five source/runner/collector files and every implementation change are committed before `$candidate` is recorded. Tasks 9-10 may write only untracked runtime evidence. Any later source fix creates a new SHA and invalidates all prior hardware evidence.

---

### Task 9: Bring Up, Pair, And Soak The Immutable Candidate On Real Hardware

**Files:**
- Runtime evidence only, untracked: `.runtime/evidence/jetson-bringup.json`
- Runtime evidence only, untracked: `.runtime/evidence/jetson-vision-node.json`

**Interfaces:**
- Consumes: the clean Task 8 candidate SHA and its already-committed runners/collector.
- Produces: real hardware/version/import/plugin/pairing/performance evidence bound to exactly that SHA; no source write or commit.

- [ ] **Step 1: Verify physical setup before power**

Confirm one P3450 B01 only, regulated center-positive 5 V/4 A barrel supply, J48 jumper installed, 5 V PWM fan connected, one USB webcam, wired Ethernet, and a 32 GB-or-larger UHS-1 microSD. Do not connect a 12 V/19 V adapter. Keep the second Jetson unpowered.

- [ ] **Step 2: Flash official 4.6.1, then upgrade to R32.7.6 after approval**

Flash NVIDIA's official Jetson Nano JetPack 4.6.1 SD-card image, complete first boot, confirm the NVIDIA APT sources target `r32.7`, ensure sufficient free space, then follow the NVIDIA point-release/package path exactly:

```bash
cat /etc/nv_tegra_release
grep -R '^deb .*repo.download.nvidia.com/jetson/.* r32.7 ' /etc/apt/sources.list.d
df -h /
sudo apt update
sudo apt dist-upgrade
sudo apt install --reinstall nvidia-jetpack
sudo reboot
```

After reboot capture:

```bash
cat /etc/nv_tegra_release
dpkg-query -W nvidia-jetpack libnvinfer8 python3-libnvinfer
/usr/bin/python3 --version
uname -m
nvpmodel -q --verbose
v4l2-ctl --device=/dev/video0 --all
```

Expected: R32 revision 7.6, JetPack 4.6.6 package line, TensorRT 8.2.1 package line, system Python 3.6 generation, `aarch64`, 10 W mode available, and webcam 640x480. Do not proceed on a partial package upgrade.

- [ ] **Step 3: Inventory actual Python bindings and encoder plugins before implementation**

Run this before Task 1 and retain the secret-free output:

```bash
/usr/bin/python3 - <<'PY'
import tensorrt as trt
import pycuda.driver as cuda
import pycuda.autoinit
import cv2
import numpy
import gi
gi.require_version('Gst', '1.0')
from gi.repository import Gst
Gst.init(None)
assert 'GStreamer:                   YES' in cv2.getBuildInformation()
print('TensorRT', trt.__version__)
print('CUDA devices', cuda.Device.count())
print('OpenCV', cv2.__version__)
print('NumPy', numpy.__version__)
print('GStreamer', Gst.version_string())
PY
for plugin in appsrc videoconvert nvvidconv nvv4l2h264enc h264parse qtmux filesink; do
  gst-inspect-1.0 "$plugin" >/dev/null || exit 1
done
```

Expected: every import and plugin check passes. If PyCUDA or GObject bindings are absent, install only the matching Ubuntu/NVIDIA R32 repository package after recording the package choice; never use pip to replace JetPack libraries. Pause hardware work here, execute Tasks 1-8, and resume only after the candidate SHA is frozen. Task 3's opset spike uses this proven runtime.

- [ ] **Step 4: Stage exactly the candidate SHA and build the pinned engine**

Stage a clean archive/export of the candidate SHA at `/opt/petcare-vision-source`, record its SHA, copy only the manifest-matched ONNX plus expected hash, and run `jetson/build_engine.sh`. Do not copy a desktop TensorRT engine. Run the exact BGR -> BGRx -> `nvvidconv` NVMM/NV12 -> `nvv4l2h264enc` pipeline test; copy its non-secret MP4 to Home and use the manifest-backed Home ffprobe to verify H.264/YUV420P 640x480 10/1. Jetson READY depends only on GStreamer EOS/hash/recorded metadata, never ffprobe. A source fix ends this task: commit it, freeze a new candidate, and restart Task 9 from Step 4.

- [ ] **Step 5: Install and import pairing end to end after separate approval**

Reserve two wired LAN addresses. Install the Jetson service with actual private Jetson/Home IPs and `eth0`, copy the one-time pairing bundle over an authenticated local transfer, and on Home run:

```powershell
backend\.venv\Scripts\python.exe -m app.agent_runtime pair-jetson --config C:\ProgramData\PetCare\agent.json --bundle .runtime\pairing\jetson.json --jetson-config C:\ProgramData\PetCare\jetson.json
& packaging/windows/install-home-agent.ps1 -Action Install -ConfigPath C:\ProgramData\PetCare\agent.json -JetsonConfigPath C:\ProgramData\PetCare\jetson.json
```

Verify imported ACLs and a successful signed status call, then delete the source pairing bundle and restart services. Inspect the Home supervisor child and prove it received exactly `PETCARE_CAMERA_SOURCE=jetson` and `PETCARE_JETSON_CONFIG=C:\ProgramData\PetCare\jetson.json`; do not print environment secrets. On Linux run `sudo packaging/linux/install-home-agent.sh --install --pairing-bundle /run/petcare-pairing/jetson.json`; it executes the exact Task 7 `pair-jetson` command, verifies `/var/lib/petcare/jetson.crt`, `.psk`, and `.json` are `petcare:petcare` mode `0600`, deletes the bundle after the signed status check, then enables the fixed unit environment.

Verify Jetson isolation:

```bash
systemctl is-active --quiet petcare-vision.service
ss -ltnp | grep ':9443'
sudo ufw status numbered
```

Expected: one listener on the exact private Ethernet IP:9443, one allow rule from the exact Home IP, no wildcard/public listener, and no Jetson cloud process.

- [ ] **Step 6: Run all Jetson tests on the staged candidate**

```bash
cd /opt/petcare-vision-source
/usr/bin/python3 -m unittest discover -s jetson/tests -p 'test_*.py'
```

Expected: Python 3.6 tests, real TensorRT deserialize/inference, exact GStreamer pipeline, preview JPEG, and hardware H.264 tests PASS.

- [ ] **Step 7: Define and start the undervoltage/throttle probe**

Record `/proc/sys/kernel/random/boot_id` before and after the soak. Before load, capture `sudo dmesg --ctime`, run `sudo jetson_clocks`, retain `sudo jetson_clocks --show`, then collect one-second `tegrastats` plus CPU/GPU current-clock samples for the whole load. After load capture only new kernel messages and fail on `soctherm|throttl|OC ALARM|under.?voltage|vdd.*fail`, any boot-ID change, or any current-clock drop below the locked values while load is active. Nano exposes no Raspberry Pi-style undervoltage bit, so these three signals plus temperature are the explicit gate.

- [ ] **Step 8: Run the real 60-minute soak and write SHA-bound evidence**

Run the already-committed collector for exactly 3600 seconds with the real webcam and candidate SHA. Create one default eligible clip; create a second eligible event exactly 15 seconds after a first; hold one media GET/ffprobe path slow while proving a new PUT is admitted within three real seconds; unplug/replug the webcam once; disconnect Jetson LAN for more than three seconds while Pico/sensor endpoints remain live; and verify no file exists before the first trigger and all acknowledged Jetson temporary files disappear. Capture a fresh signed calibration immediately before every first PUT, sample both wall/monotonic guards at 100 ms, and fail on sample age >1 second, total bound >200 ms, or a >25 ms discontinuity.

Evidence `PASS` requires all Task 8 thresholds, the exact candidate SHA, no source diff, and the explicit power/throttle probe. Bring-up evidence records only board model, software versions, executable/model hashes, camera format, private-address booleans, import/plugin checks, service/listener counts, and outcomes. It contains no PSK, certificate bytes/path, username, hostname, literal IP, MAC, serial, or cloud credential. If any source is changed, discard both evidence files and rerun the full hour on the new SHA.

---

### Task 10: Run Integrated Regression And Parallel Reviews

**Files:**
- Read-only: committed files from Tasks 1-8.
- Read-only: Task 9 untracked evidence bound to the candidate SHA.
- No source, runner, collector, evidence, commit, or deployment write is allowed in this task.

**Interfaces:**
- Verifies: Jetson local wire, Home camera/rules/outbox, existing cloud wire, exact API, sensor independence, privacy, and hardware gate against one unchanged candidate SHA.

- [ ] **Step 1: Prove the candidate and hardware evidence are immutable**

Read `.runtime/evidence/jetson-candidate-sha.txt`, require `git rev-parse HEAD` to equal it, require `git status --short --untracked-files=no` to be empty, and require both Task 9 evidence files to name the same SHA and report PASS. Hash the evidence files before reviews and require the hashes to remain unchanged. If any check fails, stop; Task 10 cannot repair or regenerate evidence.

- [ ] **Step 2: Run local unit and integration suites**

```powershell
if (-not $env:TEST_DATABASE_URL) { throw 'Set the dedicated loopback TEST_DATABASE_URL.' }
backend\.venv\Scripts\python.exe -m pytest backend/tests tools/tests/test_jetson_vision_soak.py -q
backend\.venv\Scripts\python.exe -m unittest discover -s jetson/tests -p 'test_*.py'
& tools/run_jetson_integration.ps1
git diff --check
```

Expected: all tests PASS, integration prints `Jetson vision integration PASS`, and diff check exits zero.

- [ ] **Step 3: Re-run the existing remote gates**

Run the current repository-defined full check command, exact Task 12 API/WS tests, `PETCARE-CLIP-V1` fixture parity, privacy/secret sentinel, `/demo` no-network test, and two-account remote isolation tests. Expected: all PASS without a changed route count, cloud signature, tenant mapping, clip retention, or public deployment artifact.

- [ ] **Step 4: Verify prohibited paths are absent**

```powershell
@'
import ast
from pathlib import Path
forbidden = {'paho', 'psycopg', 'sqlalchemy', 'supabase', 'boto3', 'cloudflare'}
for name in ('jetson/protocol.py', 'jetson/clip_writer.py', 'jetson/tensorrt_yolo.py', 'jetson/vision_node.py'):
    tree = ast.parse(Path(name).read_text(encoding='utf-8'), filename=name)
    imports = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imports.update(alias.name.split('.')[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imports.add(node.module.split('.')[0])
    overlap = imports & forbidden
    if overlap:
        raise SystemExit('{} imports forbidden Home/cloud modules: {}'.format(name, sorted(overlap)))
print('Jetson ownership import gate PASS')
'@ | backend\.venv\Scripts\python.exe -
```

Expected: import gate PASS. Endpoint-inventory, private-bind/firewall, TLS-verification, `no_meal_12h` exclusion, and local USB compatibility are proved by their semantic tests instead of brittle forbidden-string searches.

- [ ] **Step 5: Dispatch parallel read-only reviews against one SHA**

Run four independent reviews in parallel:

1. contract/correctness: exact camera freshness, ring/clip timing, outbox recovery, and cloud wire preservation;
2. security/privacy: TLS/HMAC/replay/ACL/network boundary, untrusted media validation, and secret scans;
3. Jetson compatibility/performance: Python 3.6 syntax, TensorRT 8.2.1 API, GStreamer hardware path, power/thermal evidence;
4. Ponytail/code quality: unnecessary dependencies, duplicate state, speculative abstractions, and Home/Jetson responsibility leakage.

Each reviewer reports Critical/Important/Minor findings with file/line evidence. Any Critical/Important finding returns to the owning Task 1-8, adds a failing test and fix, commits a new candidate SHA, and invalidates Task 9 evidence; rerun the entire 60-minute Task 9 gate and all four reviews. Task 10 itself never edits. Do not combine a reviewer result or hardware evidence from an earlier SHA.

- [ ] **Step 6: Hand back to the remote deployment plan**

After all local and hardware gates pass, resume `2026-07-20-petcare-remote-integration-deployment.md` at its next unfinished auth/Sites/CI/external-resource gate. Jetson completion does not itself authorize Supabase, SMTP, Cloudflare, R2, account, cost, push, or public Sites mutations.

## Completion Evidence

This plan is complete only when current evidence proves:

- one P3450 B01 and one USB webcam run the exact JetPack 4.6.6/TensorRT 8.2.1 vision service without the Home Python stack;
- only the configured private Ethernet IP:9443 listens and only the Home IP firewall rule can reach it;
- pinned TLS, PSK HMAC, 30-second skew, nonce replay cache, boot identity, strict schemas, and media validation pass adversarial tests;
- Home persists the existing `pc-webcam-01` detections with source timestamps, Home-derived subjects/centers/zones, and unchanged three-second freshness/rule ordering;
- exactly 100 RAM pre-roll plus 200 post-roll buckets create a 300-frame/30-second H.264/YUV420P clip, and +15-second overlap creates 450/45 seconds without exceeding 120 seconds;
- no continuous/pre-roll file exists, every partial/error/expiry/ack path cleans up, and Jetson disk limits hold;
- committed event intent survives crashes/lost responses without duplicate/lost cloud queue work; `no_meal_12h` never reaches Jetson; three-second missed events do not capture a misleading later scene;
- Jetson/camera outage leaves Pico MQTT, sensor ingestion, PostgreSQL, rules not requiring camera, exact Task 12 API, and tunnel alive;
- the existing Ed25519 `PETCARE-CLIP-V1`, tenant ownership, private R2, and seven-day retention remain unchanged;
- the 60-minute real hardware soak passes performance, latency, reconnect, power, thermal, memory, disk, and sensor-isolation thresholds;
- all four final read-only reviews approve one unchanged candidate SHA with no Critical or Important findings.

Skipped: the second Jetson, CSI, multiple cameras, continuous recording, audio, WebRTC, DeepStream, Triton, Docker/Kubernetes, gRPC, a message broker, direct browser access, cloud credentials on Jetson, and automatic PC CPU failover. Add one only when a measured requirement exceeds this single-node design.
