# PetCare Cloudflare-Free Outbound Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use
> `superpowers:subagent-driven-development` to implement this plan task by
> task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace customer-managed Cloudflare Tunnel connectivity with signed
Home Agent outbound status and short-lived 30 FPS media delivery through the
existing Sites D1/R2 bindings.

**Architecture:** Jetson and Pico remain private inputs to the Home Agent.
Enrollment creates an outbound agent identity without a connector token. The
Home Agent pushes one strict summary every two seconds and one-second fMP4
parts; Sites stores only the latest summary and eight seconds of private media.

**Tech Stack:** TypeScript, Vinext/React, D1/Drizzle, R2, Python 3.12, httpx,
Ed25519, FFmpeg HLS/fMP4, native MediaSource, Vitest, pytest.

## Global Constraints

- Customers configure no Cloudflare account, domain, tunnel, router port, or
  Tailscale.
- Jetson remains private `https://<private-ip>:9443` with TLS pinning and HMAC.
- Supabase remains authentication-only; no service-role key is added.
- New enrollment returns only `agent_id` and `camera_id`.
- Status cadence is two seconds and status becomes stale after five seconds.
- Live media is 640 x 480 H.264 at 30 FPS, one-second parts, no audio.
- The browser targets two-to-three-second latency.
- R2 retains at most eight live seconds; abandoned parts have a one-minute
  cleanup ceiling.
- Reuse the existing `DB`, `CLIPS`, Ed25519, nonce, FFmpeg, and strict summary
  paths. Add no production dependency.
- Agent request bodies are length-bounded before buffering and use private,
  no-store responses.
- Use focused tests while editing, one component run per task group, one full
  run for the final candidate SHA, and no same-SHA reruns.

---

### Task 1: Outbound Schema and Atomic Enrollment

**Files:**

- Create: `dashboard/drizzle/0003_petcare_outbound.sql`
- Modify: `dashboard/drizzle/meta/_journal.json`
- Modify: `dashboard/db/schema.ts`
- Modify: `dashboard/lib/tenancy/repository.ts`
- Create: `dashboard/lib/petcare/outbound-enrollment.ts`
- Modify: `dashboard/lib/petcare/agent-enroll.ts`
- Modify: `dashboard/app/api/petcare/enrollment/route.ts`
- Modify: `backend/app/agent_client.py`
- Modify: `backend/app/agent_config.py`
- Test: `dashboard/tests/db/migrations.test.ts`
- Test: `dashboard/tests/tenancy/repository.d1.test.ts`
- Test: `dashboard/tests/agent-enroll.test.ts`
- Test: `dashboard/tests/tenancy/enrollment-route.test.ts`
- Test: `backend/tests/test_agent_client.py`
- Test: `backend/tests/test_agent_config.py`

**Interfaces:**

- Produces:
  `TenantRepository.consumeOutboundEnrollment(input) -> EnrollmentBinding`.
- Produces:
  `OutboundEnrollmentService.enroll(input) -> { agentId, cameraId }`.
- Produces an enrollment wire response with exactly `agent_id` and `camera_id`.
- Produces `AgentRuntimeConfig.connector_token: SecretStr | None`.

- [ ] **Step 1: Write failing migration and repository tests**

Assert that migrated legacy agents have `connection_mode = 'tunnel'`, that
`tunnel_origin` accepts `NULL`, and that one D1 batch inserts an outbound
agent/camera and consumes the matching unexpired code. Prove expired, reused,
foreign, and second-agent attempts leave all four tables unchanged.

The repository input is:

```ts
type ConsumeOutboundEnrollmentInput = {
  codeHash: string;
  consumedAt: string;
  agent: { id: string; publicKey: string };
  camera: { id: string; localCameraId: "pc-webcam-01" };
};
```

- [ ] **Step 2: Run RED**

Run the managed Node command equivalent of:

```powershell
npm --prefix dashboard exec vitest run -- `
  tests/db/migrations.test.ts `
  tests/tenancy/repository.d1.test.ts
```

Expected: failure because migration `0003` and
`consumeOutboundEnrollment` do not exist.

- [ ] **Step 3: Implement the migration and atomic repository operation**

Rebuild `agents` with nullable `tunnel_origin` and:

```sql
`connection_mode` text NOT NULL
  CHECK (`connection_mode` IN ('outbound','tunnel'))
```

Copy existing rows with `connection_mode = 'tunnel'` and recreate the indexes.
Add these exact ownership and retention columns so there is only one forward
migration:

```text
agent_snapshots(home_id PK, agent_id UNIQUE, body, generated_at, received_at)
live_streams(home_id PK, agent_id UNIQUE, camera_id UNIQUE, boot_id,
  init_object_key UNIQUE, newest_sequence INTEGER, updated_at, expires_at)
live_parts(home_id, boot_id, sequence INTEGER, object_key UNIQUE, sha256,
  size_bytes INTEGER, started_at, duration_ms INTEGER, created_at, expires_at,
  PK(home_id, boot_id, sequence))
```

Foreign keys bind every home, agent, and camera identifier to the existing
tenant tables. `duration_ms` is constrained to `1000`; byte sizes and sequences
are nonnegative.

Use one `D1Database.batch()` for the outbound-mode agent insert, camera insert,
and token consumption. Validate all three affected-row counts before returning.

- [ ] **Step 4: Write failing enrollment contract tests**

Prove:

```json
{"agent_id":"agent_test","camera_id":"camera_test"}
```

is the complete response, `CF_*` is absent, code issuance calls
`issueEnrollment`, and the Python parser rejects a response containing
`connector_token`.

- [ ] **Step 5: Run enrollment RED**

```powershell
npm --prefix dashboard exec vitest run -- `
  tests/agent-enroll.test.ts `
  tests/tenancy/enrollment-route.test.ts
backend\.venv\Scripts\python.exe -m pytest `
  backend/tests/test_agent_client.py backend/tests/test_agent_config.py -q
```

Expected: existing code still requires Cloudflare provisioning and a connector
token.

- [ ] **Step 6: Implement minimal outbound enrollment**

`OutboundEnrollmentService` validates the existing public-key and camera
contracts, applies existing IP/code rate limits, generates IDs, and delegates
the only write to `consumeOutboundEnrollment`. Remove `readPetCareConfig()` and
`CloudflareClient` from code issuance and new agent enrollment.

New runtime JSON omits `connector_token`. Loading a legacy config must accept it
as optional for the compatibility release, but saving a new outbound config
must not write it.

- [ ] **Step 7: Run GREEN and commit**

Run the Task 1 tests once, inspect `git diff --check`, and commit:

```text
feat(agent): enroll without customer tunnel
```

---

### Task 2: Signed Snapshot Push and Dashboard Read

**Files:**

- Create: `dashboard/lib/petcare/agent-signature.ts`
- Create: `dashboard/lib/petcare/snapshot.ts`
- Modify: `dashboard/lib/petcare/repository.ts`
- Modify: `dashboard/lib/petcare/router.ts`
- Modify: `dashboard/lib/petcare/live-proxy.ts`
- Create: `backend/app/snapshot_delivery.py`
- Modify: `backend/app/agent_client.py`
- Modify: `backend/app/agent_lifecycle.py`
- Test: `dashboard/tests/agent-signature.test.ts`
- Test: `dashboard/tests/snapshot.test.ts`
- Test: `dashboard/tests/live-proxy.test.ts`
- Test: `backend/tests/test_snapshot_delivery.py`
- Test: `backend/tests/test_agent_lifecycle.py`

**Interfaces:**

- Produces:
  `verifySignedAgentRequest(request, env, contract) -> VerifiedAgentRequest`.
- Produces:
  `handleSnapshotUpload(request, env, now) -> Promise<Response>`.
- Produces:
  `SnapshotDeliveryWorker.start()/stop(timeout_seconds)`.
- Changes `loadRemoteStatus(ownerSub, env)` to read D1 snapshot state.

- [ ] **Step 1: Write agent-signature and snapshot RED tests**

The canonical bytes are:

```text
PETCARE-SNAPSHOT-V1
POST
/api/petcare/agent/snapshot
<agent-id>
<timestamp>
<nonce>
<digest>

```

Prove exact headers, 128 KiB limit, timestamp window, Ed25519 verification,
nonce replay rejection, strict UTF-8 JSON, exact summary schema, atomic latest
replacement, and no cross-agent mutation.

- [ ] **Step 2: Run dashboard RED**

```powershell
npm --prefix dashboard exec vitest run -- `
  tests/agent-signature.test.ts tests/snapshot.test.ts tests/live-proxy.test.ts
```

- [ ] **Step 3: Implement shared verification and snapshot storage**

Extract only the common Ed25519/timestamp/nonce/body-digest checks from the clip
path. Keep contract-specific canonical lines and body limits explicit.

Repository methods:

```ts
putAgentSnapshot(agentId: string, body: string, generatedAt: string,
  receivedAt: string): Promise<void>
getOwnerSnapshot(ownerSub: string): Promise<{
  agentId: string;
  cameraId: string;
  body: string;
  receivedAt: string;
} | null>
```

`loadRemoteStatus` parses the stored strict summary, derives freshness from
server `receivedAt`, and returns `agent_offline` after five seconds without
fetching any tunnel origin.

- [ ] **Step 4: Write Python delivery RED tests**

Use a fake local summary supplier and `httpx.MockTransport`. Prove exact
canonical signature, two-second cadence, immediate retry on the next cadence,
bounded stop, no secret logging, and no unbounded queue.

- [ ] **Step 5: Run Python RED**

```powershell
backend\.venv\Scripts\python.exe -m pytest `
  backend/tests/test_snapshot_delivery.py `
  backend/tests/test_agent_lifecycle.py -q
```

- [ ] **Step 6: Implement the snapshot client and worker**

`SignedSnapshotClient.upload(summary_bytes)` performs one bounded request.
`SnapshotDeliveryWorker` keeps only the newest summary and never blocks local
rules. Wire it into lifecycle start/stop beside cleanup and clip delivery.

- [ ] **Step 7: Run GREEN and commit**

Run Task 2 tests once, inspect the diff, and commit:

```text
feat(agent): push signed dashboard snapshots
```

---

### Task 3: Private Rolling Live-Part API

**Files:**

- Create: `dashboard/lib/petcare/live-upload.ts`
- Create: `dashboard/lib/petcare/live-manifest.ts`
- Modify: `dashboard/lib/petcare/repository.ts`
- Modify: `dashboard/lib/petcare/router.ts`
- Modify: `dashboard/lib/petcare/reconcile.ts`
- Test: `dashboard/tests/live-upload.test.ts`
- Test: `dashboard/tests/live-manifest.test.ts`
- Test: `dashboard/tests/petcare-repository.test.ts`
- Test: `dashboard/tests/reconcile.test.ts`

**Interfaces:**

- Produces:
  `handleLiveUpload(request, env, now) -> Promise<Response>`.
- Produces:
  `getLiveManifest(user, env, cameraId) -> Promise<Response>`.
- Produces:
  `getLivePart(user, env, cameraId, bootId, sequence, kind)`.

- [ ] **Step 1: Write live upload RED tests**

Use `PETCARE-LIVE-V1` with canonical fields:

```text
<agent-id>
<camera-id>
<boot-id>
<init|segment>
<sequence>
<started-at>
<duration-ms>
<content-length>
<digest>
```

Prove one init per boot, strictly increasing segment sequence, exact one-second
duration, init size at most 256 KiB, segment size at most 1 MiB, digest and
signature verification, nonce replay rejection, R2 key derivation, and
rollback when D1 metadata write fails.

- [ ] **Step 2: Run upload RED**

```powershell
npm --prefix dashboard exec vitest run -- `
  tests/live-upload.test.ts tests/petcare-repository.test.ts
```

- [ ] **Step 3: Implement bounded R2/D1 ingestion**

Use server-derived keys:

```text
live/<home-id>/<camera-id>/<boot-id>/init.mp4
live/<home-id>/<camera-id>/<boot-id>/<sequence>.m4s
```

After accepting sequence `N`, retain metadata and objects `N-7..N`. Queue a
private deletion job for any failed immediate delete. Never accept an object
key from the agent.

- [ ] **Step 4: Write manifest, part, and reconciliation RED tests**

Prove verified owner scope, camera scope, `private, no-store, no-transform`,
fresh-stream-only manifest output, ordered eight-part maximum, 404 for foreign
IDs, and deletion of live objects older than one minute without loading
Cloudflare config.

- [ ] **Step 5: Implement authenticated reads and cleanup**

Manifest JSON contains only boot ID, codec, newest sequence, target latency,
init route, and ordered same-origin part routes. R2 object keys never leave the
server.

- [ ] **Step 6: Run GREEN and commit**

Run Task 3 tests once, inspect the diff, and commit:

```text
feat(sites): relay short-lived live media
```

---

### Task 4: FFmpeg Producer and Native MediaSource Player

**Files:**

- Create: `backend/app/live_delivery.py`
- Modify: `backend/app/agent_client.py`
- Modify: `backend/app/agent_lifecycle.py`
- Modify: `backend/app/agent_runtime.py`
- Create: `dashboard/components/live-media-player.tsx`
- Modify: `dashboard/components/live-camera.tsx`
- Modify: `dashboard/components/remote-dashboard.tsx`
- Modify: `dashboard/lib/petcare/client.ts`
- Test: `backend/tests/test_live_delivery.py`
- Test: `backend/tests/test_agent_lifecycle.py`
- Test: `dashboard/tests/live-media-player.test.tsx`
- Test: `dashboard/tests/remote-dashboard.test.tsx`
- Test: `dashboard/e2e/dashboard.spec.ts`

**Interfaces:**

- Produces `LiveDeliveryWorker.start()/stop(timeout_seconds)`.
- Produces `PetCareClient.liveManifest(cameraId)` and `livePart(url)`.
- Produces `<LiveMediaPlayer cameraId={id} />`.

- [ ] **Step 1: Write FFmpeg producer RED tests**

Assert the exact manifest-pinned `ffmpeg_path` and argument vector:

```text
-an -c:v libx264 -profile:v baseline -pix_fmt yuv420p -r 30 -g 30
-f hls -hls_time 1 -hls_segment_type fmp4 -hls_list_size 8
-hls_flags delete_segments+independent_segments+temp_file
```

Use fake stdin/filesystem/upload clients to prove atomic completed-file
detection, init-before-segment, monotonic sequence, twelve-part local cap,
drop-on-upload-failure, bounded shutdown, and secret-free logs.

- [ ] **Step 2: Run Python RED**

```powershell
backend\.venv\Scripts\python.exe -m pytest `
  backend/tests/test_live_delivery.py `
  backend/tests/test_agent_lifecycle.py -q
```

- [ ] **Step 3: Implement the producer**

Feed the newest validated Jetson/Home Agent MJPEG frames to one FFmpeg child.
Upload only files whose `.tmp` rename completed. Restart with a new random boot
ID after camera loss or FFmpeg exit. Do not queue missing parts.

- [ ] **Step 4: Write MediaSource RED tests**

Fake `MediaSource`, `SourceBuffer`, and timers. Prove init-first append,
sequence ordering, two-to-three-second target, old-buffer removal, boot-change
reset, three-second offline state, fetch abort on unmount, and no autoplay or
idle animation outside the live camera.

- [ ] **Step 5: Run dashboard RED**

```powershell
npm --prefix dashboard exec vitest run -- `
  tests/live-media-player.test.tsx tests/remote-dashboard.test.tsx
```

- [ ] **Step 6: Implement the native player**

Use `MediaSource.isTypeSupported('video/mp4; codecs="avc1.42E01E"')`. Poll the
manifest once per second, append missing ordered parts, maintain a single
`SourceBuffer` operation queue, and fall back to the existing offline card when
unsupported or stale.

- [ ] **Step 7: Run GREEN and commit**

Run Task 4 focused tests once, then the backend and dashboard component groups
once. Commit:

```text
feat(camera): stream outbound 30fps parts
```

---

### Task 5: Remove Runtime Gate, Reconcile Legacy State, and Release

**Files:**

- Modify: `dashboard/lib/petcare/env.ts`
- Modify: `dashboard/lib/petcare/reconcile.ts`
- Modify: `backend/app/agent_runtime.py`
- Modify: `backend/app/windows_service.py`
- Modify: `packaging/windows/install-home-agent.ps1`
- Modify: `tools/bootstrap_agent_runtime.ps1`
- Modify: `README.md`
- Modify: `dashboard/README.md`
- Modify: `docs/demo-runbook.md`
- Modify: `docs/privacy.md`
- Modify: `docs/implementation-plan.md`
- Test: `backend/tests/test_agent_runtime.py`
- Test: `backend/tests/test_windows_service.py`
- Test: `packaging/tests/test_windows_home_agent_packaging.py`
- Test: `dashboard/tests/env.test.ts`
- Test: `dashboard/tests/reconcile.test.ts`
- Test: `tools/docs_check.py`

**Interfaces:**

- `readPetCareConfig()` is no longer required for enrollment, status, live
  upload/read, clip upload, account cleanup, or scheduled reconciliation.
- New outbound runtime starts the backend only; legacy tunnel config may start
  `cloudflared` during the compatibility release.

- [ ] **Step 1: Write RED tests for no-Cloudflare runtime**

Prove the installer/runtime is healthy with no `connector_token` and no
`cloudflared_path`, scheduled reconciliation succeeds with only DB/R2, new
Windows service copy says “outbound Sites connection,” and docs contain no
claim that customers need Cloudflare.

- [ ] **Step 2: Run RED**

Run the focused backend, packaging, env, reconcile, and docs checks. Start
`test_agent_runtime.py` detached with redirected stdio and poll it at 30-second
intervals so Codex app-server stdio is never shared.

- [ ] **Step 3: Implement compatibility-limited cleanup**

Make FFmpeg/Python required tools and Cloudflare optional legacy tools.
`AgentSupervisor` starts `cloudflared` only for a legacy `connection_mode =
'tunnel'` config. Reconciliation calls Cloudflare only for rows that contain a
legacy resource ledger; outbound cleanup never reads `CF_*`.

- [ ] **Step 4: Update user and operator documentation**

Document:

- customer setup requires only Home Agent, Pico Wi-Fi, and optional Jetson
  pairing;
- operator runtime requires Supabase URL/publishable key plus Sites DB/R2;
- 30 FPS is encoded cadence with two-to-three-second remote latency;
- live parts retain at most eight seconds and event clips retain seven days;
- legacy Cloudflare compatibility is temporary and not used by new customers.

- [ ] **Step 5: Run focused and component GREEN**

Run each changed focused group once and one dashboard/backend component pass.
Inspect `git diff --check`, generated migration packaging, secret scans, and
the exact changed-file list.

- [ ] **Step 6: Adversarial review and fix gate**

Request one task review after every task and one final Sol xhigh whole-branch
review covering tenancy, replay, R2 cleanup, process shutdown, privacy, and
browser buffer correctness. Fix all load-bearing findings and run only the
covering tests for changed SHAs.

- [ ] **Step 7: Final exact-SHA verification**

Run one final full suite and build on the candidate SHA. Run the Jetson hardware
gate only if Jetson code or the media path changed; verify unique decoded frames
through the deployed browser, not repeated multipart responses.

- [ ] **Step 8: Commit, push, CI, and public Sites deployment**

Commit docs/release evidence, push the approved branch and `main`, wait for all
CI jobs on the exact SHA, build the exact dashboard subtree, save one Sites
version, deploy it publicly, and verify:

- landing scroll remains paused while idle;
- signup/login callbacks work;
- enrollment works without `CF_*`;
- two-account status/media isolation;
- stale/offline state;
- 30 FPS decoded playback and bounded latency;
- Pico and Jetson state appear for the enrolled home.

Record the exact source SHA, Sites project/version/deployment IDs, CI URL, and
remaining hardware soak caveats in `README.md`.
