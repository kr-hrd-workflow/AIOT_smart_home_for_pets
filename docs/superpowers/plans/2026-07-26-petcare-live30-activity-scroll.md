# PetCare Live 30 FPS, Activity, and Smooth Scroll Implementation Plan

> Execute with subagent-driven development. Every implementation task uses
> `gpt-5.6-terra` at `xhigh`; every task review uses `gpt-5.6-sol` at `xhigh`.
> Apply TDD, Karpathy Guidelines, and Ponytail full.

**Goal:** Ship a unique-frame 30 FPS Jetson live stream, camera-observed
activity and repeated-movement warning, and an interruptible 420 ms landing
video scrub, then validate, push, and publicly deploy the exact Sites
candidate.

**Architecture:** Keep the released HTTPS/HMAC MJPEG path and strict dashboard
contracts. Split Jetson capture from inference, proxy one private stream through
the Home Agent and Sites BFF, aggregate existing pet bounding-box centers into
one-second activity rows, and update the landing through its existing
requestAnimationFrame slot.

**Tech Stack:** Jetson Python 3.6 stdlib, OpenCV, TensorRT 8.2.1, Home Agent
Python 3.12.13, FastAPI 0.139.0, httpx 0.28.1, SQLAlchemy 2.0.51, Alembic
1.18.5, React 19, Next.js 16, TypeScript 5.9, Vitest 4.1, Playwright 1.61,
Cloudflare Vinext, OpenAI Sites.

## Global Constraints

- Landing scrub duration is exactly 420 ms.
- Landing media is paused while interpolating and while idle.
- Native scroll is not intercepted. Do not add `wheel`, `preventDefault`,
  `scrollTo`, autoplay, chapter snap, idle drift, or a motion dependency.
- Live stream target is 640 x 480 at 30 unique FPS.
- Observation `fps` continues to mean inference FPS.
- Inference consumes only the latest unprocessed frame and does not block
  capture.
- Event clips remain 10 FPS with current pre-roll and post-roll behavior.
- Jetson live transport uses existing TLS and HMAC and admits one stream.
- Home Agent uses a dedicated fourth Jetson HTTP connection for live video.
- Activity uses dog and cat bounding-box centers only, one row per UTC second.
- Movement is at least 24 pixels with a gap of at most 3 seconds.
- Repeated movement uses 120 seconds, 30 observed seconds, 12 moving
  transitions, 640 pixels travel, 6 reversals, and dot product at most -0.6.
- Repeated-movement warnings deduplicate for 15 minutes and do not trigger a
  clip.
- UI says the activity and warning are camera observations, not health or
  medical conclusions.
- No new production dependency.
- Same-SHA policy: focused tests during edits, one component test per feature
  bundle, one full suite at the final candidate, no duplicate reviewer test
  runs.
- Run pytest, Node, browser, build, and soak processes one heavy process at a
  time, BelowNormal or detached with redirected stdio, polling every 30
  seconds.
- Preserve `.codex/`, `.omo/drafts/petcare-sites-completion.md`, and root
  `node_modules/`.

## Task 1: Smooth the accepted landing video scrub

**Files:**

- Modify: `dashboard/components/landing/scene-director.ts`
- Modify: `dashboard/tests/landing/scene-director.test.ts`
- Modify: `dashboard/tests/landing/scene-runtime-contract.test.ts`

### TDD

Add fake-time and fake-requestAnimationFrame tests that fail against the direct
seek implementation:

- after one scroll update, current time advances through intermediate values
  before 420 ms;
- an input received during interpolation retargets from the visible current
  time without jumping backward or restarting at the old origin;
- reverse scroll interpolates backward;
- the final frame is the exact target and no frame remains scheduled;
- no time changes after settlement;
- teardown cancels the scheduled playback frame;
- reduced-motion and data-saver modes remain poster-only.

Run focused RED and GREEN:

```powershell
Set-Location dashboard
npm test -- tests/landing/scene-director.test.ts tests/landing/scene-runtime-contract.test.ts
```

### Implementation

Replace direct `currentTime = targetTime(runtime)` behavior in
`driveToTarget()` with a single interruptible RAF loop:

- capture actual `video.currentTime` and the latest target at each retarget;
- use monotonic RAF timestamps and a 420 ms ease-out interpolation;
- clamp every candidate to `[0, duration]`;
- pause before and throughout the loop;
- skip sub-frame redundant assignments;
- assign the exact target once at completion;
- reuse and clear `runtime.playbackFrame`;
- preserve existing scroll batching, media readiness, poster, and cleanup
  behavior.

Do not change the video, poster, journey config, CSS, or visible copy.

### Review gate

Review only this task diff. Verify no scroll interception, autoplay, idle loop,
new dependency, or test-only production branch was added.

## Task 2: Add the Jetson 30 FPS live media plane

**Files:**

- Modify: `jetson/vision_node.py`
- Modify: `jetson/tests/test_vision_node.py`
- Modify: `jetson/tests/test_wire_contract.py`
- Modify: `jetson/tests/test_protocol.py`
- Modify: `contracts/petcare-jetson-vision-v1.json`
- Modify: `backend/tests/test_jetson_wire_contract.py`

### TDD

Add failing stdlib tests for:

- `OpenCvCamera` requests 640 x 480 MJPEG at 30 FPS;
- capture publishes unique live sequences while a blocked detector runs;
- inference drops stale raw frames and preserves strict observation ordering;
- `/v1/live.mjpeg` requires a valid existing HMAC signature;
- multipart frames are unique and bounded and include deterministic metadata;
- only one live stream is admitted;
- camera loss, client disconnect, and server shutdown release stream admission;
- `/v1/preview.jpg` remains observation-sequence matched and 2 FPS limited;
- clip sampler remains one 100 ms bucket and uses current live imagery.

Run focused RED and GREEN:

```powershell
backend\.venv\Scripts\python.exe -m unittest jetson.tests.test_vision_node jetson.tests.test_protocol
backend\.venv\Scripts\python.exe -m pytest backend/tests/test_jetson_wire_contract.py -q
```

### Implementation

- Add `LIVE_PATH = "/v1/live.mjpeg"`.
- Make `OpenCvCamera` request and verify the target FPS property without
  changing the current resolution and FourCC.
- Replace the single capture-and-infer loop with bounded latest-frame capture
  and inference loops.
- Keep a separate live sequence and observation sequence.
- Render live frames with the latest completed detections. Never wait for
  inference before publishing the next capture.
- Keep `/v1/preview.jpg` tied to the matching inference observation.
- Add a condition-based `live_frame(after, wait)` API that returns only a
  newer sequence.
- Add one-stream admission and release it in all exit paths.
- Stream strict multipart MJPEG with private no-store headers.
- Preserve Python 3.6 compatibility and current shutdown ordering.
- Extend the strict wire fixture without weakening unknown-route or
  unknown-method handling.

### Component gate

Run the Jetson vision component once:

```powershell
backend\.venv\Scripts\python.exe -m unittest discover -s jetson/tests -p "test_*.py"
```

### Review gate

Verify capture cannot build an unbounded queue, inference FPS semantics are
unchanged, clip timing is unchanged, broken-pipe cleanup is safe, and no remote
dependency or public bind is added.

## Task 3: Proxy the Jetson stream through the Home Agent

**Files:**

- Modify: `backend/app/jetson_client.py`
- Modify: `backend/app/camera_service.py`
- Modify: `backend/app/api.py`
- Modify: `backend/tests/test_jetson_client.py`
- Modify: `backend/tests/test_camera_service.py`
- Modify: `backend/tests/test_api.py`
- Modify: `backend/tests/integration/test_jetson_vision_stack.py`

### TDD

Add failing tests for:

- a fourth client is required and isolated from control, admission, and clip
  media;
- `/v1/live.mjpeg` is signed against the current boot ID;
- upstream status, content type, cache policy, and boot ID are validated before
  bytes are exposed;
- multipart bytes pass through unchanged and are not decoded or re-encoded;
- browser cancellation closes the upstream response;
- upstream error before the first byte returns `camera_unavailable`;
- local USB and fixture sources retain their current MJPEG behavior;
- API response remains private and no-store.

Run focused RED and GREEN:

```powershell
backend\.venv\Scripts\python.exe -m pytest backend/tests/test_jetson_client.py backend/tests/test_camera_service.py backend/tests/test_api.py -q
```

### Implementation

- Create four pinned `httpx.Client` instances and four locks.
- Add a streaming method that signs and opens `GET /v1/live.mjpeg` on the
  dedicated connection, validates headers, yields raw chunks, and always closes
  the response.
- Add a `CameraService.mjpeg_stream()` generator. Select passthrough for a
  Jetson source and retain the current chunk generator for local sources.
- Prime exactly one chunk before constructing the FastAPI
  `StreamingResponse`, so pre-stream errors remain JSON errors.
- Remove the fixed 50 ms duplicate-frame loop for Jetson sources.
- Keep the public API route and Sites BFF contract unchanged.

### Component gate

Run the Home Agent camera component once:

```powershell
backend\.venv\Scripts\python.exe -m pytest backend/tests/test_jetson_client.py backend/tests/test_camera_service.py backend/tests/test_api.py backend/tests/integration/test_jetson_vision_stack.py -q
```

### Review gate

Verify all streaming resources close, the fourth connection cannot starve clip
delivery, and no Jetson URL or credential is returned to the browser.

## Task 4: Persist activity estimates and repeated-movement warnings

**Files:**

- Create: `backend/migrations/versions/0003_activity_observations.py`
- Modify: `backend/app/models.py`
- Create: `backend/app/activity.py`
- Modify: `backend/app/camera_service.py`
- Modify: `backend/app/contracts.py`
- Modify: `backend/app/api.py`
- Modify: `backend/tests/test_migrations.py`
- Create: `backend/tests/test_activity.py`
- Modify: `backend/tests/test_camera_service.py`
- Modify: `backend/tests/test_contracts.py`
- Modify: `backend/tests/test_api.py`
- Modify: `backend/tests/test_account_deletion.py`

### TDD

Add failing tests for:

- migration upgrade and downgrade and all table constraints;
- deterministic dog-then-cat summary ordering;
- first observation is still, 23 pixels is still, 24 pixels is moving;
- gaps over 3 seconds are neither moving nor still time;
- same-second updates keep one row, latest center, maximum distance, and OR the
  moving flag;
- active seconds never exceed observed seconds;
- Seoul local-day boundaries;
- current state becomes unknown after 3 seconds;
- the repeated-movement rule stays silent below every individual threshold;
- the exact threshold emits one non-medical warning;
- the same pet is deduplicated for 15 minutes;
- dog and cat deduplicate independently;
- no repeated-movement clip outbox row is created;
- account deletion removes activity rows.

Run focused RED and GREEN:

```powershell
backend\.venv\Scripts\python.exe -m pytest backend/tests/test_activity.py backend/tests/test_migrations.py backend/tests/test_camera_service.py backend/tests/test_contracts.py backend/tests/test_api.py backend/tests/test_account_deletion.py -q
```

### Implementation

- Add `ActivityObservation` with strict geometry, movement, subject, unique, and
  time indexes.
- Implement one module that:
  - floors observation times to UTC seconds;
  - computes valid center distance;
  - upserts the per-second bucket inside the existing frame transaction;
  - evaluates the 120-second reversal window;
  - persists a deduplicated `repetitive_motion` anomaly;
  - returns dog-then-cat daily summaries.
- Extend `AnomalyEvent` and `AnomalyEventOut` for
  `repetitive_motion` with a pet subject, null mismatch kind, and no source
  behavior.
- Keep clip outbox eligibility unchanged.
- Add the fixed activity list to `DashboardSummary`.
- Include activity data in the existing dashboard summary query.

### Component gate

Run the activity and rules component once:

```powershell
backend\.venv\Scripts\python.exe -m pytest backend/tests/test_activity.py backend/tests/test_camera_service.py backend/tests/test_rules.py backend/tests/test_api.py backend/tests/test_contracts.py backend/tests/test_migrations.py backend/tests/test_account_deletion.py -q
```

### Review gate

Verify no medical claim, no camera gap counted as inactivity, no partial
transaction, bounded query size, no new clip trigger, and no mismatch with
strict database constraints.

## Task 5: Surface activity and expanded detection contracts in Sites

**Files:**

- Modify: `dashboard/lib/types.ts`
- Modify: `dashboard/lib/api-client.ts`
- Modify: `dashboard/lib/petcare-remote.ts`
- Modify: `dashboard/lib/petcare/live-proxy.ts`
- Modify: `dashboard/lib/demo-data.ts`
- Modify: `dashboard/components/dashboard.tsx`
- Modify: `dashboard/components/anomaly-list.tsx`
- Modify: `dashboard/tests/api-client.test.ts`
- Modify: `dashboard/tests/petcare-remote.test.ts`
- Modify: `dashboard/tests/live-proxy.test.ts`
- Modify: `dashboard/tests/dashboard.test.tsx`
- Modify: `dashboard/tests/anomaly-list.test.tsx`
- Modify: `dashboard/tests/remote-dashboard.test.tsx`

### TDD

Add failing tests for:

- strict rejection of missing, reordered, malformed, or extra activity fields;
- dog-then-cat ordering;
- `today_active_seconds <= today_observed_seconds`;
- unknown coverage renders `관측 없음`, not zero activity;
- observed coverage is visible beside the activity estimate;
- rest remains visible beside activity;
- repeated movement has the exact non-medical label and message;
- remote and demo dashboards share the same strict data shape;
- live proxy preserves multipart streaming headers.

Run focused RED and GREEN:

```powershell
Set-Location dashboard
npm test -- tests/api-client.test.ts tests/petcare-remote.test.ts tests/live-proxy.test.ts tests/dashboard.test.tsx tests/anomaly-list.test.tsx tests/remote-dashboard.test.tsx
```

### Implementation

- Mirror the backend `ActivityStatus` and new anomaly literal exactly.
- Extend every strict parser used by local, remote, and Sites BFF paths.
- Add realistic demo activity data.
- Replace one low-value summary cell with `오늘 활동 추정`; keep
  `오늘 휴식 추정`.
- Show `카메라 관측 N분 기준` under the activity value.
- If both subjects have coverage, show the per-pet values in the existing
  activity/rest content area without adding a new generic card grid.
- Add `repetitive_motion` copy to the warning list.
- Keep the current page theme, accent, radius system, navigation, route IDs,
  auth actions, and responsive grid.
- Use short CSS transitions only for state changes already present. Add no
  perpetual dashboard animation.

### Component gate

Run the dashboard data component once:

```powershell
Set-Location dashboard
npm test -- tests/api-client.test.ts tests/petcare-remote.test.ts tests/live-proxy.test.ts tests/dashboard.test.tsx tests/anomaly-list.test.tsx tests/remote-dashboard.test.tsx
```

### Review gate

Use the Emil Before/After table for UI findings. Verify Korean copy, empty and
offline states, keyboard access, mobile layout, strict parser parity, and no
generic card or decorative animation expansion.

## Task 6: Update operations, soak, and release evidence

**Files:**

- Modify: `tools/jetson_vision_soak.py`
- Modify: `tools/tests/test_jetson_vision_soak.py`
- Modify: `docs/design/petcare-jetson-vision-node.md`
- Modify: `docs/design/petcare-remote-dashboard.md`
- Modify: `docs/runbook.md`
- Modify: `README.md`
- Modify: `.github/workflows/ci.yml`

### TDD

Add failing tests and fixture evidence for:

- unique MJPEG frame counting rather than response-iteration counting;
- live FPS at least 30;
- inference FPS at least 3;
- observation age no more than 3 seconds;
- clip buckets remain 10 FPS;
- temperature below 80 C and no throttling;
- stream reconnect and shutdown;
- CI contains the focused contract gates and exact runtime versions;
- docs state activity is camera-observed and non-medical.

Run focused RED and GREEN:

```powershell
backend\.venv\Scripts\python.exe -m pytest tools/tests/test_jetson_vision_soak.py tools/tests/test_ci_workflow.py tools/tests/test_docs_check.py -q
```

### Implementation

- Extend the existing soak client to authenticate and parse the multipart
  stream, hash each JPEG, and report unique frame cadence.
- Preserve current secret handling and do not write media evidence to the
  repository.
- Update architecture, deployment, troubleshooting, hardware capability,
  activity semantics, warning limitations, and privacy documentation.
- Update README feature and validation sections without exposing secrets or
  claiming hardware results not yet measured.
- Add focused CI coverage without duplicating the final full suites.

### Review gate

Verify docs match code and do not claim 30 FPS or calibrated abnormal behavior
until the hardware evidence exists.

## Final Verification and Release

### 1. Adversarial whole-branch review

Create one exact diff package from branch base to candidate head. Dispatch a
read-only `gpt-5.6-sol` `xhigh` reviewer. The reviewer does not rerun suites.
Address every Critical and Important finding through the original Terra
implementer and perform scoped re-review only on the fix diff.

### 2. Exact-candidate local gates

At the final unchanged SHA, run each heavy gate once with redirected output and
30-second polling:

```powershell
backend\.venv\Scripts\python.exe -m pytest backend/tests tools/tests/test_jetson_vision_soak.py -q
backend\.venv\Scripts\python.exe -m unittest discover -s jetson/tests -p "test_*.py"
Set-Location dashboard
npm test
npm run lint
npm run build
npm run test:e2e:demo:production
```

Run secret, privacy, installer, and docs gates already defined by the repository
at the same candidate SHA. Do not repeat a passing full gate on the unchanged
SHA.

### 3. Jetson hardware gate

Because Jetson code changes, deploy only the reviewed candidate through local
`ssh petcare-jetson` commands. Do not install remote Codex.

Verify:

- device identity is `workflow`;
- webcam negotiates 640 x 480 MJPEG at 30 FPS;
- service starts with the reviewed files;
- 60-minute soak meets every design threshold;
- live stream disconnect and reconnect work;
- event clip creation still has 10 FPS buckets;
- temperature stays below 80 C with no throttling;
- shutdown leaves no request or capture thread.

If the webcam cannot negotiate 30 FPS, report the measured physical ceiling and
do not manufacture duplicate frames or claim the gate passed.

### 4. Integrate and push

- Confirm the feature worktree is clean and all commits are descendants of
  `0cbf403aa343679643b242136ea698465686c845`.
- Fast-forward or merge into `codex/petcare-mvp` without touching preserved
  untracked user files.
- Confirm the merged tree equals the reviewed candidate tree.
- Push `codex/petcare-mvp`, then push the approved main update.

### 5. Save and publicly deploy Sites

- Read `.openai/hosting.json` and reuse
  current-account project `appgprj_6a6101d826ac8191a4eb06c48375f83f`.
- Push the exact dashboard source state used for the build.
- Build and archive from that exact pushed commit.
- Save one Sites version with D1 `DB` and R2 `CLIPS`.
- Deploy that saved version publicly from the current ChatGPT account.
- Inspect deployment until it reaches a terminal succeeded state.

### 6. Production smoke

Verify on the production URL:

- anonymous `/` loads the accepted landing video;
- one scroll input advances through several smooth intermediate video times and
  settles while paused;
- idle video time remains unchanged;
- `/demo` loads with activity and rest values;
- login and signup actions are visible and operational;
- unauthenticated dashboard and API access are rejected;
- an authenticated paired agent exposes summary, activity, anomalies, and the
  multipart camera stream;
- account and tenant isolation remain enforced.

Record the exact commit, Sites version, production URL, test totals, hardware
measurements, and any remaining external limitation before completing the
goal.
