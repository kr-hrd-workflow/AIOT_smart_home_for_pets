# PetCare Remote Integration And Deployment Implementation Plan

> **Jetson vision override (2026-07-20):** Integration Task 2 supersedes its old `AgentLifecycleComponents(recorder, dispatcher, started_at)`, `.latest_frame_sink`, `stop_agent_dispatcher`, `stop_agent_recorder`, and `test_clip_recorder.py` expectations. The approved one-Jetson USB-camera path consumes exactly:

```python
AgentLifecycleComponents(jetson_client, clip_admission, clip_delivery, upload_queue, started_at)
build_agent_components(config_path, tools_path, session_factory, *, now=utc_now)
start_agent_components(components)
stop_agent_components(components, *, timeout_seconds=105.0)
```

`stop_agent_components` uses one 105-second global monotonic deadline and always attempts this exact component-owned sequence with per-step caps: `clip_admission.stop(5)`; `clip_delivery.stop(45)`; `jetson_client.close(2)`; `upload_queue.stop(45)`. Each receives `min(cap, remaining_global_time)`; exhaustion is recorded but later nonblocking cleanup is still attempted. It preserves the first failure only after attempting later cleanup, and repeated calls are safe. Integration Task 2 owns the surrounding lifespan sequence `rule_ingress.stop_accepting -> mqtt.stop -> rule_worker.shutdown -> camera.shutdown -> stop_agent_components -> dispose_database`, again attempting later cleanup after a failure. Its tests assert these exact exports/order without importing or faking the superseded recorder path. Integration Task 4 validates `petcare-jetson-wire-v1.json` separately while preserving `petcare-agent-wire-v1.json` byte-for-byte.

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Integrate the existing Task 11/12 loopback PetCare API, the four remote component workstreams, reproducible home-agent runtimes, hermetic multi-tenant tests, exact-SHA CI, and an explicitly approved public Sites release whose live routes remain Supabase-authenticated and tenant-scoped.

**Architecture:** Keep PostgreSQL, MQTT, rules, webcam ownership, and the exact Task 12 API on the home machine. The integrated Sites Worker verifies Supabase identity, resolves one caller-owned home in D1, and proxies same-origin requests through an Access-protected outbound Tunnel; private event clips flow from the signed home agent into R2 and back only through ownership-checked BFF routes. Local completion uses the real loopback backend and FFmpeg with fake Supabase, D1, R2, Tunnel, Access, and provisioning boundaries; external completion is a separately reported, opt-in gate for real accounts, credentials, billable resources, SMTP, DNS, and public deployment.

**Tech Stack:** CPython 3.12.13, FastAPI 0.139.0, PostgreSQL 17.10, Mosquitto 2.1.2, FFmpeg 8.1.2-22-g94138f6973, cloudflared 2026.7.2, cryptography 49.0.0, Node 22.23.1, Vinext/Next 16.2.6, Supabase SSR 0.12.3, Supabase JS 2.110.7, jose 6.2.3, D1, private R2, Cloudflare Tunnel/Access, Vitest 4.1.10, Playwright 1.61.1, axe 4.12.1, GitHub Actions `ubuntu-24.04`, Sites plugin 0.1.30.

## Global Constraints

- Start Task 1 in parallel with Todo 12 because their write sets do not overlap. Start component work after Task 1 seals shared pins/locks; keep only overlapping local dashboard, final `main.py`, Worker/hosting, integration, browser, CI, and deployment gates ordered. Do not reimplement rules, calibration, PostgreSQL models, MQTT ingestion, camera inference, or the twelve REST plus one WebSocket routes here.
- Consume, do not duplicate, the implementation and exported interfaces from:
  - `docs/superpowers/plans/2026-07-20-petcare-auth-tenancy.md`
  - `docs/superpowers/plans/2026-07-20-petcare-home-agent-clips.md`
  - `docs/superpowers/plans/2026-07-20-petcare-sites-bff-tunnel-clips.md`
  - `docs/superpowers/plans/2026-07-20-petcare-remote-dashboard.md`
- The integrator alone owns shared merge points: `tools/platform-manifest.json`, `tools/validate_platform_manifest.py`, `backend/pyproject.toml`, `backend/uv.lock`, `backend/app/main.py`, `dashboard/package.json`, `dashboard/package-lock.json`, `dashboard/.openai/hosting.json`, `dashboard/vite.config.ts`, `dashboard/worker/index.ts`, `dashboard/playwright.config.ts`, `.github/workflows/ci.yml`, `tools/bootstrap_ci.sh`, `tools/check_all.ps1`, README integration prose, and final route wiring. Auth and Home consume those files read-only after Task 1. The home-agent sibling owns `backend/app/agent_lifecycle.py`, recorder/upload/runtime modules, and their unit tests; it never imports or edits `backend/app/main.py`. A component defect returns to its sibling plan and commit; do not patch component-owned files opportunistically in an integration task.
- The Task 12 server continues to bind exactly `127.0.0.1:8000`. `cloudflared` routes to that loopback service on the same machine. PostgreSQL, MQTT, FastAPI, the webcam, tunnel origin, R2 bucket, and object URLs are never directly exposed to the browser.
- `/demo`, `/login`, `/signup`, `/forgot-password`, `/reset-password`, the auth form handlers, and the auth callback are public. `/demo` remains bundled-data-only and creates no PetCare client, Supabase session client, Tunnel request, loopback request, WebSocket, cross-origin image, or authenticated fetch.
- `/`, enrollment, status, live MJPEG, clip list/read/delete, and `DELETE /api/petcare/account` require a verified Supabase session. Authentication failure is `401`; a caller selecting another tenant's opaque ID receives `404`. Tunnel revocation remains internal; account deletion is the single canonical same-origin route below.
- Every lookup begins with the verified immutable Supabase JWT `sub`. Never authorize from email or a client-supplied `owner_sub`, `home_id`, `agent_id`, `camera_id`, object key, or tunnel origin.
- Live and clip responses set `Cache-Control: private, no-store, no-transform`. Remote operational state uses two-second REST polling. Do not add a remote WebSocket or WebRTC path.
- BFF status proxying targets Task 12 `GET /api/dashboard/summary`; MJPEG proxying targets Task 12 `GET /api/video_feed`. `/api/dashboard` and `/video_feed` are forbidden aliases.
- One active home, one active agent, and one active camera per account are hard MVP constraints. Sharing, invites, roles, multiple active cameras, continuous recording, `no_meal_12h` clips, signed public URLs, and service-role Supabase APIs are out of scope.
- `DELETE /api/petcare/account` is implemented by the BFF's exact `deletePetCareAccountData(request: Request, env: PetCareEnv, now: Date)` handler. That handler owns same-origin CSRF validation and recent reauthentication: it derives `ownerSub` and email from the verified session, reads the current password only from the request body, and performs request-scoped Supabase `signInWithPassword`. A first accepted deletion or an idempotent retry while cleanup is pending returns exact `202 {"status":"cleanup_pending"}`; an already-absent or fully completed account deletion returns an empty `204`. Both success statuses cause the dashboard to `POST` the existing `/auth/logout` route and follow its `/login` redirect. The first acceptance atomically denies PetCare access, revokes agent/enrollment state, logically removes clips, and queues idempotent Tunnel/Access/DNS/R2 cleanup. While cleanup is pending, enrollment remains blocked; after finite cleanup completes, delete the tenant registry and cleanup ledger so the retained Supabase identity can explicitly enroll a fresh home. No permanent tombstone, automatic reactivation, Supabase identity deletion, or service-role key exists.
- Secrets enter processes only through inherited environment, redirected stdin, an ACL-restricted ignored file, or connector/runtime secret APIs. Tunnel startup uses `--token-file`, never `--token`. No secret appears in a command argument, URL, Git remote/config, browser storage, bundle, evidence, screenshot, log, or D1/R2 object name.
- Do not create an account, Supabase project, SMTP provider, DNS record, Tunnel, Access application/policy/token, D1 database, R2 bucket, Sites project/version/deployment, repository secret/environment, paid plan, or public deployment without fresh explicit authorization covering that exact external action.
- A public Sites URL is not anonymous access to live data. Public deployment is acceptable only when all protected pages and `/api/petcare/**` live/clip routes pass anonymous rejection and two-account isolation on the exact deployed version.
- Use `docs/superpowers/specs/2026-07-20-petcare-multitenant-remote-design.md` as the requirement authority. If a sibling plan conflicts with it, stop integration and correct the sibling plan; do not silently widen the contract here.

## Completion Status Model

| Status | Meaning | May be claimed without external credentials? |
| --- | --- | --- |
| `REMOTE_LOCAL=PASS` | Manifest closure, Task 11/12 loopback integration, real FFmpeg, all fake providers, two-account isolation, browser/accessibility QA, CI, privacy, and operator docs pass for one exact commit. | Yes |
| `REMOTE_EXTERNAL=NOT_RUN_APPROVAL` | Local completion passed, but the user has not authorized external resource changes/public deployment. | Yes |
| `REMOTE_EXTERNAL=NOT_RUN_PREREQUISITES` | Authorization exists, but account, credential, domain, SMTP, quota, or billing prerequisites are missing. | Yes |
| `REMOTE_EXTERNAL=FAIL` | An approved real-resource or deployed multi-account gate ran and failed. | Yes, with the failure named; production completion must not be claimed. |
| `REMOTE_EXTERNAL=PASS` | Real Supabase/SMTP/Tunnel/Access/D1/R2/Sites and two-account manual evidence pass against the exact deployed source. | No; it requires approved external actions. |

`REMOTE_LOCAL=PASS` is a complete local/mockable engineering deliverable. Production completion requires both `REMOTE_LOCAL=PASS` and `REMOTE_EXTERNAL=PASS`; never collapse the two into one ambiguous “done”.

## File Structure And Ownership

### Files this integration plan creates

| Path | Responsibility |
| --- | --- |
| `tools/tests/test_remote_manifest_integration.py` | Prove the sibling agent pins, auth dependencies, Sites helper version, and `.runtime/agent-tools.json` resolve through one central authority. |
| `backend/tests/integration/test_remote_agent_stack.py` | Real Task 12 loopback API plus home-agent clip/tunnel lifecycle integration test; all cloud boundaries are fake. |
| `contracts/petcare-agent-wire-v1.json` | One deterministic cross-language enrollment and `PETCARE-CLIP-V1` golden vector consumed by Python and TypeScript. |
| `backend/tests/integration/test_agent_wire_contract.py` | Python agent request/signature side of the shared wire vector. |
| `dashboard/tests/integration/remote-stack.test.ts` | Static shared-Worker composition/binding regression first, then cross-provider route/cache/ownership assertions after the sibling fakes exist. |
| `dashboard/tests/integration/agent-wire-contract.test.ts` | TypeScript BFF parsing/verification/receipt side of the shared wire vector. |
| `tools/run_remote_integration.ps1` | One deterministic local orchestration command; `-Mode Fake` is default, `-Mode Real` is approval-gated. |
| `tools/tests/test_run_remote_integration.ps1` | Command, process, cleanup, approval, and no-secret-argument tests. |
| `dashboard/e2e/remote-multitenant.spec.ts` | Two isolated browser contexts covering auth, enrollment, live state, clips, deletion, and copied-ID denial. |
| `dashboard/e2e/remote-visual.spec.ts` | Login/enrollment/online/offline/live/clip mobile, pixel, keyboard, and axe QA. |
| `tools/tests/test_remote_ci_workflow.py` | Static workflow parser for job names, SHA pins, manifests, no-secret base CI, and aggregate dependencies. |
| `docs/remote-operations.md` | Windows/Raspberry Pi home-agent start, stop, health, upgrade, backup, revocation, and troubleshooting. |
| `docs/remote-privacy.md` | Data flow, seven-day retention, deletion, log redaction, incident response, and honest limits. |
| `docs/external-resource-checklist.md` | Human approval and account/credential/cost/resource checklist with `PASS|FAIL|NOT RUN` evidence slots. |
| `tools/docs_check.py` | The single generic structured-doc checker, absorbing completion Todo 18 and remote command/contracts without a remote-specific wrapper. |
| `tools/tests/test_docs_check.py` | Tests for the single generic docs checker. |

### Shared files this integration plan may modify

- `tools/platform-manifest.json`
- `tools/validate_platform_manifest.py`
- `tools/bootstrap_ci.sh`
- `tools/check_all.ps1`
- `tools/secret_sentinel.py`
- `tools/privacy_check.py`
- `tools/tests/test_secret_sentinel.py`
- `tools/tests/test_privacy_check.py`
- `backend/pyproject.toml`
- `backend/uv.lock`
- `backend/app/main.py`
- `tools/tests/test_bootstrap_ci.py`
- `dashboard/package.json`
- `dashboard/package-lock.json`
- `dashboard/.openai/hosting.json`
- `dashboard/vite.config.ts`
- `dashboard/worker/index.ts`
- `dashboard/playwright.config.ts`
- `.github/workflows/ci.yml`
- `README.md`

### Component-owned files consumed read-only during integration

All files named in the four sibling plans are read-only to integration except the shared merge points listed above. The integration tests call their public HTTP/TypeScript/Python contracts; they do not reproduce their D1 schema, Supabase handlers, clip ring buffer, signing, R2 retention, tunnel provisioner, or dashboard components.

## Prerequisite And Shared-File Execution Order

The older completion plan remains authoritative for its local hardware/API foundation, but its private Sites release is superseded by the approved remote design:

| Order | Owner | Required disposition |
| --- | --- | --- |
| 0A (parallel) | `.omo/plans/petcare-sites-mvp-completion.md` Todo 12 | Complete the exact loopback API while integration Task 1 runs on disjoint shared dependency/fixture files. Preserve its evidence. |
| 0B (parallel) | This plan Task 1 | Merge all exact runtime/dependency pins and lockfiles, seal the final manifest hash, and create `contracts/petcare-agent-wire-v1.json` as the sole frozen cross-language authority. Component plans consume these shared files read-only after this commit. |
| 2 | Auth/tenancy sibling | Implement auth-owned component files, D1 ownership, recent-reauth helper, and `POST /api/petcare/enrollment`. Consume Task 1's dashboard package/lock pins read-only; defer manifest, Worker, Vite, hosting, Playwright, workflow, README, and `check_all` hunks to integration. |
| 3 | Home-agent sibling | Implement component-owned transactional outbox, recorder/signing/supervisor, pre-write ACL persistence, bounded queue semantics, packaging, bootstrap scripts, and the frozen `backend/app/agent_lifecycle.py` hook. Consume Task 1's manifest/validator/backend locks and fixture read-only; never import or edit `backend/app/main.py`. |
| 4 | BFF/Tunnel/clips sibling | Implement component-owned `routePetCare`, D1/R2/Tunnel fakes, reconciliation, canonical proxies, and account deletion after auth exposes recent reauth. Consume Task 1's fixture and dashboard dependency locks read-only. Defer hosting/Vite/Worker changes; integration Task 3 alone composes shared Worker files. |
| 5 | Remote-dashboard sibling | Implement component-owned protected pages/components and CSS against canonical same-origin routes. Consume Task 1's dashboard package/lock pins read-only and defer Playwright/Worker hunks to their integration owner; do not invent auth aliases. |
| 6 | This plan Tasks 2-9 | After Todo 12 and Home's lifecycle hook both pass, Task 2 alone composes them in integration-owned `backend/app/main.py` and seals shutdown order. Then serialize the remaining shared-file merges: Task 3 last-writes hosting logical bindings/Vite/Worker; Task 4 consumes the Task 1 fixture in the hermetic gate; Tasks 6-7 extend the Todo 15 Playwright baseline; Task 8 extends the Todo 16 workflow; Task 9 absorbs Todo 18's not-yet-created generic docs checker and reconciles its operator/privacy prose in one commit. |
| 7 | This plan Tasks 10-13 | Stop after local sealing unless approved. Do not execute completion-plan Todo 17: its private deployment is replaced by Task 12's public exact-SHA Sites deployment with authenticated live routes. Task 12 alone adds `project_id` after Task 3's `DB`/`CLIPS`. |
| 8 | This plan Task 14 | Replaces the old final wave for the remote scope: F2/F3/F4 run read-only in parallel, then F1 runs last. Physical hardware remains Todo 18's honest `PASS|FAIL|NOT RUN` gate. |

The four sibling plans never write `tools/platform-manifest.json`, its validator, either dependency lock, shared package files, `backend/app/main.py`, `dashboard/worker/index.ts`, `dashboard/vite.config.ts`, `dashboard/playwright.config.ts`, `.github/workflows/ci.yml`, `dashboard/.openai/hosting.json`, README integration prose, or `tools/check_all.ps1`. They consume the exact integration-owned result after Task 1; Home exports its side-effect-free lifecycle composition hook instead of editing main. A component correction is committed only in component-owned files, then the dependent integration task is rerun; shared-file changes remain in the integration commit.

---

### Task 1: Merge and verify the sibling runtime/dependency pins in one authority

**Files:**
- Create: `tools/tests/test_remote_manifest_integration.py`
- Create: `contracts/petcare-agent-wire-v1.json`
- Modify: `tools/platform-manifest.json`
- Modify: `tools/validate_platform_manifest.py`
- Modify: `backend/pyproject.toml`
- Modify: `backend/uv.lock`
- Modify: `dashboard/package.json`
- Modify: `dashboard/package-lock.json`

**Interfaces:**
- Consumes: the exact `managed_exact.ffmpeg`, `managed_exact.cloudflared`, agent bootstrap scripts, `.runtime/agent-tools.json`, `cryptography==49.0.0`, and `pywin32==312` Windows marker from `docs/superpowers/plans/2026-07-20-petcare-home-agent-clips.md`; the exact Supabase pins from `docs/superpowers/plans/2026-07-20-petcare-auth-tenancy.md`.
- Produces: one conflict-free central manifest/lock merge, root wire fixture, and integration test; it does not create a second bootstrap or runtime loader.

- [ ] **Step 1: Write the failing manifest/runtime tests**

```python
# tools/tests/test_remote_manifest_integration.py
import hashlib
import json
from pathlib import Path


def test_remote_manifest_pins_are_immutable() -> None:
    manifest = json.loads(Path("tools/platform-manifest.json").read_text(encoding="utf-8"))
    managed = manifest["managed_exact"]
    assert managed["ffmpeg"] == {
        "version": "8.1.2-22-g94138f6973",
        "source_tag": "autobuild-2026-07-19-13-12",
        "windows_x64": {
            "url": "https://github.com/BtbN/FFmpeg-Builds/releases/download/autobuild-2026-07-19-13-12/ffmpeg-n8.1.2-22-g94138f6973-win64-gpl-8.1.zip",
            "sha256": "9DB2860AF5D1C536ED7FCB7ED84FA4EF80D188D1396D1CDF8CAD180137510F3F",
        },
        "linux_x64": {
            "url": "https://github.com/BtbN/FFmpeg-Builds/releases/download/autobuild-2026-07-19-13-12/ffmpeg-n8.1.2-22-g94138f6973-linux64-gpl-8.1.tar.xz",
            "sha256": "166375E7F8B1F6963949A61A83FFFFE858EBA742F6326180B8FF3BC58B205C72",
        },
        "linux_arm64": {
            "url": "https://github.com/BtbN/FFmpeg-Builds/releases/download/autobuild-2026-07-19-13-12/ffmpeg-n8.1.2-22-g94138f6973-linuxarm64-gpl-8.1.tar.xz",
            "sha256": "371203E43AB3AAA703C9904B40F6A6065DCC08FD3260F441F3BBE040E1AFE8BF",
        },
    }
    assert managed["cloudflared"] == {
        "version": "2026.7.2",
        "windows_x64": {
            "url": "https://github.com/cloudflare/cloudflared/releases/download/2026.7.2/cloudflared-windows-amd64.exe",
            "sha256": "CDB5D4432F6AE1595654A692A51308B69D2BF7AF961F5578D9391837CF072DF9",
        },
        "linux_x64": {
            "url": "https://github.com/cloudflare/cloudflared/releases/download/2026.7.2/cloudflared-linux-amd64",
            "sha256": "EC905EA7B7E327FF8ABDDE8CB64697A2152DE74DBCDBF6AEC9DB8364EB3886CD",
        },
        "linux_arm64": {
            "url": "https://github.com/cloudflare/cloudflared/releases/download/2026.7.2/cloudflared-linux-arm64",
            "sha256": "405DF476437E027FC6D18729A5A77155C0A33A6082AEEE60A799A688F3052E66",
        },
    }


def test_agent_tools_resolve_from_the_same_manifest() -> None:
    manifest_path = Path("tools/platform-manifest.json")
    runtime = json.loads(Path(".runtime/agent-tools.json").read_text(encoding="utf-8"))
    assert runtime["manifest_sha256"] == hashlib.sha256(manifest_path.read_bytes()).hexdigest().upper()
    assert runtime["fixture"] is False
    for key in ("ffmpeg_path", "ffprobe_path", "cloudflared_path"):
        path = Path(runtime["paths"][key])
        assert path.is_absolute() and path.is_file()
        assert hashlib.sha256(path.read_bytes()).hexdigest().upper() == runtime["sha256"][key]
    assert runtime["versions"]["ffmpeg_path"] == "8.1.2-22-g94138f6973"
    assert runtime["versions"]["cloudflared_path"] == "2026.7.2"


def test_one_shared_wire_fixture_exists() -> None:
    fixture = json.loads(Path("contracts/petcare-agent-wire-v1.json").read_text(encoding="utf-8"))
    assert fixture["enrollment"]["request"]["enrollment_code"] == "AQEBAQEBAQEBAQEBAQEBAQ"
    assert fixture["clip"]["signature"] == "fiTRBQk2p-2ny3LcFvBtHO2DdnqC0CqueJzuczGdC7xA_Idv0YAZ0nDCuGBiPVqS8SwldyHTrhDatHMBFUW5Aw"


def test_auth_and_agent_dependencies_are_exact() -> None:
    manifest = json.loads(Path("tools/platform-manifest.json").read_text(encoding="utf-8"))["managed_exact"]
    assert manifest["backend_dependencies"]["cryptography"] == "49.0.0"
    assert manifest["backend_dependencies"]["pywin32"] == "312"
    for name, version in {
        "@supabase/ssr": "0.12.3",
        "@supabase/supabase-js": "2.110.7",
        "jose": "6.2.3",
    }.items():
        assert manifest["sites_dependencies"][name] == version
    assert manifest["sites_plugin"]["version"] == "0.1.30"
```

- [ ] **Step 2: Run the focused tests and verify RED**

Run from the repository root:

```powershell
$runtime = Get-Content -Raw -Encoding UTF8 .runtime/toolchain.json | ConvertFrom-Json
& $runtime.paths.python_path -m pytest tools/tests/test_remote_manifest_integration.py -q
```

Expected: FAIL until the exact plan-declared entries and Task 4-frozen shared fixture are merged and a real non-fixture `.runtime/agent-tools.json` has been bootstrapped.

- [ ] **Step 3: Resolve the shared-file merge before component implementation**

Add the exact `ffmpeg` and `cloudflared` objects asserted above under `managed_exact`, change `sites_plugin.version` from `0.1.27` to `0.1.30` without rerunning the initializer, add `cryptography: "49.0.0"` and `pywin32: "312"` under `backend_dependencies`, and add these exact `sites_dependencies` keys:

```json
{
  "@supabase/ssr": "0.12.3",
  "@supabase/supabase-js": "2.110.7",
  "jose": "6.2.3"
}
```

Home Task 7 owns the not-yet-created `tools/bootstrap_agent_runtime.ps1`, `tools/bootstrap_agent_runtime.sh`, and their tests. Task 1 must not create, execute, or require those component files; it only seals the manifest/dependency authority they later consume. The central merge keeps every pre-existing pin, contains each exact entry once, rejects alternative FFmpeg/cloudflared entries, keeps the Windows marker on `pywin32==312` in `backend/pyproject.toml`, and stores the manifest version as exact string `"312"`.

Create `contracts/petcare-agent-wire-v1.json` byte-for-byte from the frozen enrollment, clip, headers, and receipt values specified in Task 4. Task 1 is its sole creator/owner; later component and integration tasks consume it read-only and may not create a clip-only variant.

Update both integration-owned dependency locks with the manifest executables:

```powershell
$runtime = Get-Content -Raw -Encoding UTF8 .runtime/toolchain.json | ConvertFrom-Json
& $runtime.paths.uv_path lock --project backend
Push-Location dashboard
try {
  & $runtime.paths.node_path $runtime.paths.npm_cli_path install --save-exact '@supabase/ssr@0.12.3' '@supabase/supabase-js@2.110.7' 'jose@6.2.3'
} finally { Pop-Location }
```

Expected: both commands exit 0; `backend/uv.lock` pins `cryptography==49.0.0`; `dashboard/package.json` and lock use the three exact versions and no range prefixes.

- [ ] **Step 4: Regenerate the sealed manifest hash and run GREEN checks**

Update only `EXPECTED_CANONICAL_SHA256` in `tools/validate_platform_manifest.py` to the uppercase SHA-256 of canonical sorted/minified JSON after the final manifest edit. Then run:

```powershell
& $runtime.paths.python_path tools/validate_platform_manifest.py --manifest tools/platform-manifest.json
& $runtime.paths.python_path -m pytest tools/tests/test_validate_platform_manifest.py tools/tests/test_remote_manifest_integration.py -q
& $runtime.paths.python_path -c "import cryptography; assert cryptography.__version__ == '49.0.0'"
& $runtime.paths.node_path -e "const p=require('./dashboard/package-lock.json'); for (const [n,v] of Object.entries({'node_modules/@supabase/ssr':'0.12.3','node_modules/@supabase/supabase-js':'2.110.7','node_modules/jose':'6.2.3'})) if(p.packages[n].version!==v) process.exit(1)"
```

Expected: every command exits 0; validator prints `valid platform manifest`; pytest passes; Python and lock probes print no error.

- [ ] **Step 5: Commit the single integration authority update**

```bash
git add tools/platform-manifest.json tools/validate_platform_manifest.py tools/tests/test_remote_manifest_integration.py contracts/petcare-agent-wire-v1.json backend/pyproject.toml backend/uv.lock dashboard/package.json dashboard/package-lock.json
git commit -m "chore(remote): pin agent and cloud runtimes"
```

### Task 2: Wire the home-agent lifecycle around the exact Task 12 API

**Files:**
- Create: `backend/tests/integration/test_remote_agent_stack.py`
- Modify: `backend/app/main.py`

**Interfaces:**
- Consumes: Task 11 `RuleIngress`, `RuleWorker`, `RuleEngine`; Task 12's already-implemented exact API router/hub/main; revised Jetson-aware camera service; home-agent commands `python -m app.agent_runtime enroll|run|status|pair-jetson`, restricted status file, in-process `AgentHealthSnapshot`, `.runtime/agent-tools.json`, and the imported Jetson configuration from `docs/superpowers/plans/2026-07-20-petcare-jetson-vision-node.md`.
- Consumes exact Home exports from `backend/app/agent_lifecycle.py`: `AgentLifecycleComponents(jetson_client, clip_admission, clip_delivery, upload_queue, started_at)`; `build_agent_components(config_path, tools_path, session_factory, *, now=utc_now)`; `start_agent_components(components)`; and `stop_agent_components(components, *, timeout_seconds=105.0)`.
- Consumes for uploads: the single `PETCARE-CLIP-V1` canonical string and exact BFF headers `Content-Type`, `Content-Length`, `X-PetCare-Agent-Id`, `X-PetCare-Camera-Id`, `X-PetCare-Timestamp`, `X-PetCare-Nonce`, `X-PetCare-Content-SHA256`, `X-PetCare-Started-At`, `X-PetCare-Ended-At`, `X-PetCare-Events`, and `X-PetCare-Signature`; success is only HTTP `201` with exact `{ id, createdAt, expiresAt }` JSON.
- Produces: the sole final `backend/app/main.py` production composition, application-state attachment, exact shutdown order, and cross-plan regression evidence. Jetson vision Task 7 retains ownership of `agent_lifecycle.py`, all clip workers/upload queue, `agent_runtime.py`, and their unit behavior; Integration Task 2 modifies none of them.
- Modifies no Task 12 route, response model, error, ordering, Origin, or bind address; it only composes the frozen Task 12 app with the frozen Home hook.

- [ ] **Step 1: Add a failing lifecycle integration test using sibling fakes**

```python
# backend/tests/integration/test_remote_agent_stack.py
from __future__ import annotations

from fastapi.testclient import TestClient

from app.main import app


def test_agent_mode_preserves_the_existing_api(configured_agent_app) -> None:
    with TestClient(app) as client:
        assert app.state.agent_components is configured_agent_app
        assert client.get("/api/health").status_code == 200
        assert client.get("/api/dashboard/summary").status_code == 200
        assert client.get("/api/devices").status_code == 200
        assert client.get("/api/sensors/latest").status_code == 200
        assert client.get("/api/behaviors").status_code == 200
        assert client.get("/api/anomalies").status_code == 200
        assert client.get("/api/camera/status").status_code == 200
        assert client.get("/api/bed/status").status_code == 200
        assert client.get("/api/zones").status_code == 200


def test_agent_status_is_cli_only(agent_health_snapshot, agent_status_cli) -> None:
    payload = agent_health_snapshot.to_dict()
    assert payload["status"] in {"healthy", "degraded"}
    assert set(payload["jetson"]) >= {"camera", "boot", "temperature", "throttle"}
    assert "queue_depth" in payload["clip_delivery"]
    assert "queue_depth" in payload["upload_queue"]
    assert agent_status_cli(payload).exit_code == 0
    serialized = agent_status_cli(payload).stdout.lower()
    assert not any(secret in serialized for secret in (
        '"url"', '"ip"', '"psk"', '"certificate"', '"token"',
        '"private_key"', '"database_url"', '"mqtt_password"', '"clip_path"',
    ))


def test_supervisor_uses_task12_loopback_and_token_file(agent_supervisor) -> None:
    commands = agent_supervisor.commands()
    assert commands[0][4:8] == ["--host", "127.0.0.1", "--port", "8000"]
    assert "--token-file" in commands[1]
    assert "--token" not in commands[1]
    assert all("0.0.0.0" not in part and "::" not in part for command in commands for part in command)


def test_main_owns_final_shutdown_order(configured_agent_app, lifecycle_calls) -> None:
    with TestClient(app):
        pass
    assert lifecycle_calls == [
        "rule_ingress.stop_accepting",
        "mqtt.stop",
        "rule_worker.shutdown",
        "camera.shutdown",
        "stop_agent_components",
        "dispose_database",
    ]
```

`configured_agent_app` and `lifecycle_calls` are integration fixtures that patch only the revised Home lifecycle boundary and existing Task 12 services; they do not reproduce Jetson client, admission, delivery, or upload-queue behavior. `agent_health_snapshot`, `agent_status_cli`, and `agent_supervisor` reuse the Jetson vision plan's Home test factories through their public helpers. The existing Task 12 route-set tests remain the sole HTTP authority and must stay byte-for-byte green; diagnostics are CLI/status-file only.

- [ ] **Step 2: Run the lifecycle test and verify RED**

```powershell
$runtime = Get-Content -Raw -Encoding UTF8 .runtime/toolchain.json | ConvertFrom-Json
& $runtime.paths.uv_path run --project backend pytest backend/tests/integration/test_remote_agent_stack.py -q
```

Expected: FAIL after Home is complete because Task 12's `backend/app/main.py` does not yet compose the exported agent lifecycle or final shutdown order.

- [ ] **Step 3: Compose the Home lifecycle into integration-owned main.py**

Preserve Task 12's single FastAPI app, router/hub wiring, `docs_url=None`, `redoc_url=None`, `openapi_url=None`, and `127.0.0.1:8000` supervisor contract. In its existing lifespan, consume the validated agent environment produced by Jetson vision Task 7; when agent mode is absent, retain byte-for-byte local startup behavior and set `application.state.agent_components = None`. Reject a partial agent/Jetson configuration before starting background work.

In agent mode, call `build_agent_components(Path(config_path), Path(tools_path), session_factory)` exactly once with no copied config, Jetson-client, clip-worker, or upload-queue logic. Store the components on `application.state.agent_components`, call `start_agent_components(components)` before accepting rule/MQTT/camera work, and compose the revised Jetson-aware camera service without a frame sink. `RuleEngine` receives no clip callback because the transactional outbox and fast admission worker own post-commit delivery.

The final lifespan teardown is exactly: `rule_ingress.stop_accepting` -> `mqtt.stop` -> `rule_worker.shutdown` -> `camera.shutdown` -> `stop_agent_components(components, timeout_seconds=105.0)` -> `dispose_database`. `stop_agent_components` alone owns the component sequence and global deadline frozen in the title override. Preserve cleanup attempts when an earlier stop raises and surface the first bounded failure after all later cleanup runs. If a test exposes Jetson client, clip worker, upload queue, config, ACL, or hook defects, return those to Jetson vision Task 7; Task 2 edits only `backend/app/main.py` and its integration test and adds no second runtime factory, outbox, clip worker, upload queue, or lifespan.

- [ ] **Step 4: Run Task 11/12 and lifecycle regression tests**

```powershell
& $runtime.paths.uv_path run --project backend pytest backend/tests/test_rule_worker.py backend/tests/test_api.py backend/tests/test_websocket.py backend/tests/test_clip_outbox.py backend/tests/test_clip_delivery.py backend/tests/test_clip_upload_queue.py backend/tests/test_jetson_client.py backend/tests/test_agent_config.py backend/tests/test_agent_health.py backend/tests/test_agent_lifecycle.py backend/tests/test_agent_runtime.py backend/tests/integration/test_remote_agent_stack.py -q
```

Expected: exit 0; Task 11 shutdown order, all twelve HTTP/one WebSocket contracts, transactional outbox atomicity/retry, isolated fast admission, bounded slow delivery/upload, Jetson-fault degraded health with sensors alive, ACL-before-replace persistence, and remote-agent order pass unchanged.

- [ ] **Step 5: Commit the integration-owned lifecycle composition**

```bash
git add backend/app/main.py backend/tests/integration/test_remote_agent_stack.py
git commit -m "feat(agent): compose remote lifecycle with local API"
```

### Task 3: Merge the authenticated BFF, D1/R2 bindings, and remote dashboard into the Worker

**Files:**
- Create: `dashboard/tests/integration/remote-stack.test.ts`
- Modify: `dashboard/.openai/hosting.json`
- Modify: `dashboard/vite.config.ts`
- Modify: `dashboard/worker/index.ts`
- Modify: `dashboard/package.json`
- Modify: `dashboard/package-lock.json`

**Interfaces:**
- Consumes: exact exports and route handlers from the auth/tenancy, BFF/Tunnel/clips, and remote-dashboard sibling plans.
- Produces: one Worker environment with logical `DB` and `CLIPS` bindings, server-only runtime values, existing image handling, Vinext handler fallback, and the complete `/api/petcare/**` route set.
- Consumes internally: BFF `deletePetCareAccountData(request: Request, env: PetCareEnv, now: Date)` and its idempotent cleanup jobs. `routePetCare` dispatches canonical `DELETE /api/petcare/account` to that handler; the handler itself owns verified-session lookup, same-origin validation, auth's request-scoped recent-reauth helper, and cleanup orchestration.
- Preserves: `dashboard/app/demo/page.tsx` and its static same-origin assets-only contract.

- [ ] **Step 1: Write a failing Worker composition test**

```ts
// dashboard/tests/integration/remote-stack.test.ts
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { describe, expect, it } from "vitest";

describe("integrated PetCare Worker", () => {
  const workerSource = readFileSync(resolve("worker/index.ts"), "utf8");
  const hosting = JSON.parse(readFileSync(resolve(".openai/hosting.json"), "utf8"));

  it("declares only the shared D1/R2 binding names", () => {
    expect(hosting).toEqual({ d1: "DB", r2: "CLIPS" });
  });

  it("orders image handling, the PetCare router, then Vinext", () => {
    const image = workerSource.indexOf('url.pathname === "/_vinext/image"');
    const petcare = workerSource.indexOf("routePetCare(request, env, ctx)");
    const vinext = workerSource.indexOf("handler.fetch(request, env, ctx)");
    expect(image).toBeGreaterThan(-1);
    expect(petcare).toBeGreaterThan(image);
    expect(vinext).toBeGreaterThan(petcare);
    expect(workerSource.match(/routePetCare\(request, env, ctx\)/g)).toHaveLength(1);
  });
});
```

This integration test proves composition without inventing a second auth/provider fake. Runtime authorization, tenancy, secret non-serialization, and canonical-route behavior remain in the sibling BFF/auth tests and are rerun in Step 4.

- [ ] **Step 2: Run the Worker test and verify RED**

```powershell
Push-Location dashboard
try { & $runtime.paths.node_path $runtime.paths.npm_cli_path test -- tests/integration/remote-stack.test.ts } finally { Pop-Location }
```

Expected: FAIL because the final Worker Env/bindings and sibling route composition are not merged.

- [ ] **Step 3: Declare logical bindings and one server-only Worker environment**

Set `dashboard/.openai/hosting.json` exactly to:

```json
{"d1":"DB","r2":"CLIPS"}
```

Keep `project_id` absent until an approved Sites project is resolved in Task 11. Update `dashboard/vite.config.ts` to use the existing `d1`/`r2` values without renaming them and preserve project-local Miniflare state.

Merge the sibling handler into `dashboard/worker/index.ts` before image optimization/Vinext fallback:

```ts
interface Env {
  ASSETS: Fetcher;
  DB: D1Database;
  CLIPS: R2Bucket;
  IMAGES: {
    input(stream: ReadableStream): {
      transform(options: Record<string, unknown>): {
        output(options: { format: string; quality: number }): Promise<{ response(): Response }>;
      };
    };
  };
  SUPABASE_URL: string;
  SUPABASE_PUBLISHABLE_KEY: string;
  CF_ACCOUNT_ID: string;
  CF_ZONE_ID: string;
  CF_ZONE_NAME: string;
  CF_ACCESS_TEAM_NAME: string;
  CF_TUNNEL_API_TOKEN: string;
  CF_ACCESS_SERVICE_TOKEN_ID: string;
  CF_ACCESS_CLIENT_ID: string;
  CF_ACCESS_CLIENT_SECRET: string;
}

// In fetch(), after /_vinext/image and before handler.fetch:
const petCareResponse = await routePetCare(request, env, ctx);
if (petCareResponse) return petCareResponse;
```

`routePetCare` is the single export from `dashboard/lib/petcare/router.ts` in the BFF sibling plan; do not reproduce its routing table in `worker/index.ts`. Add its sibling-owned scheduled handler exactly once. The Vinext handler continues to serve `/auth/login|signup|forgot-password|reset-password`, `/auth/callback`, `/auth/logout`, public pages, `/demo`, and protected page composition as defined by the auth/dashboard plans. The auth plan solely owns `POST /api/petcare/enrollment`; `routePetCare` must return `null` for that exact method/path so Vinext preserves the exact `201 { code: string, expiresAt: string }` response.

Before GREEN, require the sibling plans to expose this one canonical browser route set and no aliases:

```text
POST   /api/petcare/enrollment
GET    /api/petcare/status
GET    /api/petcare/cameras/:cameraId/stream.mjpeg
GET    /api/petcare/clips
GET    /api/petcare/clips/:clipId.mp4
DELETE /api/petcare/clips/:clipId
DELETE /api/petcare/account
POST   /api/petcare/agent/enroll
POST   /api/petcare/agent/clips
```

The auth plan alone owns `/auth/**` and `POST /api/petcare/enrollment`; `routePetCare` returns `null` only for that auth-owned enrollment method/path. The BFF owns `DELETE /api/petcare/account`, and its frozen `deletePetCareAccountData(request, env, now)` handler consumes auth's request-scoped recent-reauth helper without exposing a second owner-sub cleanup API. The remote-dashboard plan consumes these contracts and must not add `/api/petcare/auth/**` duplicates.

- [ ] **Step 4: Run component and Worker integration tests/build**

```powershell
Push-Location dashboard
try {
  & $runtime.paths.node_path $runtime.paths.npm_cli_path test
  & $runtime.paths.node_path $runtime.paths.npm_cli_path run build
} finally { Pop-Location }
```

Expected: all auth, tenancy, BFF, clips, dashboard, and `remote-stack` tests pass; build exits 0; `dist/server/index.js`, `dist/.openai/hosting.json`, and D1 migrations exist; the built hosting file contains `DB` and `CLIPS` and no runtime secret value.

- [ ] **Step 5: Commit the shared Worker merge**

```bash
git add dashboard/.openai/hosting.json dashboard/vite.config.ts dashboard/worker/index.ts dashboard/package.json dashboard/package-lock.json dashboard/tests/integration/remote-stack.test.ts
git commit -m "feat(sites): integrate authenticated PetCare BFF"
```

### Task 4: Prove the complete local remote path with real Task 12 and fake cloud providers

**Jetson vision override:** Validate `contracts/petcare-jetson-wire-v1.json` independently from `contracts/petcare-agent-wire-v1.json`; do not modify either fixture, and prove the existing `PETCARE-CLIP-V1` fixture remains byte-for-byte unchanged.

**Files:**
- Consume: `contracts/petcare-agent-wire-v1.json`
- Consume: `contracts/petcare-jetson-wire-v1.json`
- Consume: `backend/tests/test_jetson_wire_contract.py`
- Consume: `jetson/tests/test_wire_contract.py`
- Create: `backend/tests/integration/test_agent_wire_contract.py`
- Create: `dashboard/tests/integration/agent-wire-contract.test.ts`
- Create: `tools/run_remote_integration.ps1`
- Create: `tools/tests/test_run_remote_integration.ps1`
- Modify: `backend/tests/integration/test_remote_agent_stack.py`
- Modify: `dashboard/tests/integration/remote-stack.test.ts`

**Interfaces:**
- Produces: `powershell -NoProfile -ExecutionPolicy Bypass -File tools/run_remote_integration.ps1 -Mode Fake`.
- Fake mode uses: real PostgreSQL/Mosquitto/FastAPI/Task 11/12, both independent wire fixtures, validation-only ffprobe, fake Supabase/JWKS/SMTP, fake D1/R2, fake Tunnel/Access/provisioning, and no Internet or account credential.
- Real mode is disabled here and implemented as an approval gate in Task 10.

- [ ] **Step 1: Write failing orchestration tests**

```powershell
# tools/tests/test_run_remote_integration.ps1
$script = Get-Content -Raw -Encoding UTF8 "$PSScriptRoot/../run_remote_integration.ps1"
if ($script -notmatch "ValidateSet\('Fake','Real'\)") { throw 'mode gate missing' }
if ($script -notmatch "Mode\s*=\s*'Fake'") { throw 'fake mode is not the default' }
if ($script -match "--token\s") { throw 'tunnel token must never enter arguments' }
if ($script -match "Start-Process[^\r\n]*cloudflared|&\s+[^\r\n]*cloudflared") { throw 'fake mode must not start cloudflared' }
if ($script -match "Get-Command\s+(ffmpeg|cloudflared)|where\.exe\s+(ffmpeg|cloudflared)") { throw 'PATH fallback forbidden' }

$runner = (Get-Process -Id $PID).Path
$target = (Resolve-Path "$PSScriptRoot/../run_remote_integration.ps1").Path
$child = Start-Process -FilePath $runner -Wait -PassThru -WindowStyle Hidden -ArgumentList @(
  '-NoProfile','-ExecutionPolicy','Bypass','-File',$target,'-Mode','Real','-WhatIf'
)
if ($child.ExitCode -ne 3) { throw 'unapproved real mode must exit 3' }
Write-Output 'remote integration command contract PASS'
```

Task 1 alone creates `contracts/petcare-agent-wire-v1.json`; Jetson vision Task 1 alone creates `contracts/petcare-jetson-wire-v1.json`. Task 4 consumes both read-only and validates them independently. The agent fixture's enrollment request is exactly `{"enrollment_code":"AQEBAQEBAQEBAQEBAQEBAQ","algorithm":"Ed25519","public_key":"A6EHv_POEL4dcN0Y50vAmWfk1jCbpQ1fHdyGZBJVMbg","local_camera_id":"pc-webcam-01"}` and its response is exactly `{"agent_id":"agent_01","camera_id":"camera_01","connector_token":"fixture-only-connector-token"}`. Its clip section uses version `PETCARE-CLIP-V1`, body Base64 `bXA0LWJ5dGVz`, SHA-256 `Il4ucfaWNpVoTPXCrvfVgv_3asuMAo7Yt5ycUryTSV0`, seed `AAECAwQFBgcICQoLDA0ODxAREhMUFRYXGBkaGxwdHh8`, public key `A6EHv_POEL4dcN0Y50vAmWfk1jCbpQ1fHdyGZBJVMbg`, nonce `AAAAAAAAAAAAAAAAAAAAAA`, and this exact canonical UTF-8 string with one final newline:

```text
PETCARE-CLIP-V1
POST
/api/petcare/agent/clips
agent_01
camera_01
1784520000
AAAAAAAAAAAAAAAAAAAAAA
Il4ucfaWNpVoTPXCrvfVgv_3asuMAo7Yt5ycUryTSV0
2026-07-20T03:59:50.000000Z
2026-07-20T04:00:20.000000Z
bed_sensor_mismatch:7,eating:41,resting:105
```

The vector headers are exactly `Content-Type: video/mp4`, `Content-Length: 9`, `X-PetCare-Agent-Id: agent_01`, `X-PetCare-Camera-Id: camera_01`, `X-PetCare-Timestamp: 1784520000`, `X-PetCare-Nonce: AAAAAAAAAAAAAAAAAAAAAA`, `X-PetCare-Content-SHA256: Il4ucfaWNpVoTPXCrvfVgv_3asuMAo7Yt5ycUryTSV0`, `X-PetCare-Started-At: 2026-07-20T03:59:50.000000Z`, `X-PetCare-Ended-At: 2026-07-20T04:00:20.000000Z`, `X-PetCare-Events: bed_sensor_mismatch:7,eating:41,resting:105`, and `X-PetCare-Signature: fiTRBQk2p-2ny3LcFvBtHO2DdnqC0CqueJzuczGdC7xA_Idv0YAZ0nDCuGBiPVqS8SwldyHTrhDatHMBFUW5Aw`. Its response is HTTP `201` with `{"id":"clip_01","createdAt":"2026-07-20T04:00:21.000Z","expiresAt":"2026-07-27T04:00:21.000Z"}`. `backend/tests/integration/test_agent_wire_contract.py` must drive the real Python enrollment serializer and upload signer against that file. `dashboard/tests/integration/agent-wire-contract.test.ts` must drive the real TypeScript enrollment parser, signed-header parser, Ed25519 verifier, upload handler, and receipt against the same file. Neither test may restate the canonical string or field list.

- [ ] **Step 2: Run the command test and verify RED**

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File tools/tests/test_run_remote_integration.ps1
```

Expected: FAIL because `tools/run_remote_integration.ps1` does not exist.

- [ ] **Step 3: Implement one orchestration script with a hard real-mode stop**

```powershell
[CmdletBinding(SupportsShouldProcess)]
param(
  [ValidateSet('Fake','Real')][string]$Mode = 'Fake',
  [string]$RuntimePath = '.runtime/toolchain.json',
  [string]$ApprovalPath = '.runtime/remote/external-approval.json',
  [string]$SecretPath = '.runtime/remote/real-secrets.json'
)
$ErrorActionPreference = 'Stop'
$Root = Split-Path -Parent $PSScriptRoot
$ManifestPath = Join-Path $PSScriptRoot 'platform-manifest.json'
$AgentRuntimePath = Join-Path $Root '.runtime/agent-tools.json'

function Assert-File([string]$Path, [string]$Label) {
  if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) { throw "$Label is missing: $Path" }
  return (Resolve-Path -LiteralPath $Path).Path
}

function Assert-ManifestHash([object]$Data, [string]$Label) {
  $expected = (Get-FileHash -LiteralPath $ManifestPath -Algorithm SHA256).Hash
  if ([string]$Data.manifest_sha256 -ne $expected) { throw "$Label manifest hash mismatch" }
}

if ($Mode -eq 'Real') {
  if (-not (Test-Path -LiteralPath $ApprovalPath)) { Write-Error 'real mode requires explicit external approval record'; exit 3 }
  $approval = Get-Content -Raw -Encoding UTF8 -LiteralPath $ApprovalPath | ConvertFrom-Json
  if ($approval.authorization -ne 'REMOTE_EXTERNAL_ACTIONS_APPROVED' -or $approval.public_sites -ne $true) {
    Write-Error 'approval record does not authorize real resources and public Sites'; exit 3
  }
  foreach ($name in 'supabase','smtp','cloudflare_tunnel_access','sites_d1_r2','two_test_accounts','cost_acknowledged') {
    if ($approval.$name -ne $true) { Write-Error "approval record is missing: $name"; exit 3 }
  }
  if (-not (Test-Path -LiteralPath $SecretPath -PathType Leaf)) { Write-Error 'real prerequisite file is missing'; exit 4 }
  $secrets = Get-Content -Raw -Encoding UTF8 -LiteralPath $SecretPath | ConvertFrom-Json
  foreach ($name in 'SUPABASE_URL','SUPABASE_PUBLISHABLE_KEY','CF_ACCOUNT_ID','CF_ZONE_ID','CF_ZONE_NAME','CF_ACCESS_TEAM_NAME','CF_TUNNEL_API_TOKEN','CF_ACCESS_SERVICE_TOKEN_ID','CF_ACCESS_CLIENT_ID','CF_ACCESS_CLIENT_SECRET') {
    if ([string]::IsNullOrWhiteSpace([string]$secrets.$name)) { Write-Error "real prerequisite is missing: $name"; exit 4 }
  }
  if ($WhatIfPreference) { Write-Output 'REMOTE_REAL_PREFLIGHT=PASS'; exit 0 }
  Write-Error 'real mutation is performed only by Tasks 11-13 after their separate approvals'; exit 3
}

if (-not [IO.Path]::IsPathRooted($RuntimePath)) { $RuntimePath = Join-Path $Root $RuntimePath }
$RuntimePath = Assert-File ([IO.Path]::GetFullPath($RuntimePath)) 'toolchain runtime'
$runtime = Get-Content -Raw -Encoding UTF8 -LiteralPath $RuntimePath | ConvertFrom-Json
Assert-ManifestHash $runtime 'toolchain runtime'
foreach ($key in 'git_path','bash_path','uv_path','python_path','node_path','npm_cli_path') {
  Assert-File ([string]$runtime.paths.$key) "toolchain $key" | Out-Null
}

& (Join-Path $PSScriptRoot 'bootstrap_agent_runtime.ps1')
if ($LASTEXITCODE) { throw 'agent runtime bootstrap failed' }
$AgentRuntimePath = Assert-File $AgentRuntimePath 'agent runtime'
$agentRuntime = Get-Content -Raw -Encoding UTF8 -LiteralPath $AgentRuntimePath | ConvertFrom-Json
Assert-ManifestHash $agentRuntime 'agent runtime'
foreach ($key in 'ffprobe_path','cloudflared_path') {
  $toolPath = Assert-File ([string]$agentRuntime.paths.$key) "agent $key"
  if ((Get-FileHash -LiteralPath $toolPath -Algorithm SHA256).Hash -ne [string]$agentRuntime.sha256.$key) {
    throw "agent $key hash mismatch"
  }
}

$savedAgentTools = $env:PETCARE_AGENT_TOOLS
try {
  $env:PETCARE_AGENT_TOOLS = $AgentRuntimePath
  & (Join-Path $PSScriptRoot 'run_integration.ps1') -Provider Native
  if ($LASTEXITCODE) { throw 'Task 12 native integration failed' }

  & $runtime.paths.uv_path run --project (Join-Path $Root 'backend') pytest `
    (Join-Path $Root 'backend/tests/integration/test_remote_agent_stack.py') `
    (Join-Path $Root 'backend/tests/integration/test_agent_wire_contract.py') `
    (Join-Path $Root 'backend/tests/test_jetson_wire_contract.py') `
    (Join-Path $Root 'backend/tests/test_clip_outbox.py') `
    (Join-Path $Root 'backend/tests/test_clip_delivery.py') `
    (Join-Path $Root 'backend/tests/test_clip_upload_queue.py') `
    (Join-Path $Root 'backend/tests/test_agent_config.py') `
    (Join-Path $Root 'backend/tests/test_agent_health.py') -q
  if ($LASTEXITCODE) { throw 'remote agent contract tests failed' }

  & $runtime.paths.python_path -m unittest discover `
    -s (Join-Path $Root 'jetson/tests') -p 'test_wire_contract.py'
  if ($LASTEXITCODE) { throw 'Jetson stdlib wire contract test failed' }

  Push-Location (Join-Path $Root 'dashboard')
  try {
    & $runtime.paths.node_path $runtime.paths.npm_cli_path test -- `
      tests/integration/remote-stack.test.ts `
      tests/integration/agent-wire-contract.test.ts `
      tests/cloudflare.test.ts `
      tests/enrollment.test.ts `
      tests/live-proxy.test.ts `
      tests/clip-upload.test.ts `
      tests/clips.test.ts `
      tests/reconcile.test.ts `
      tests/petcare-worker.test.ts
    if ($LASTEXITCODE) { throw 'fake provider integration tests failed' }
    & $runtime.paths.node_path $runtime.paths.npm_cli_path run build
    if ($LASTEXITCODE) { throw 'dashboard build failed' }
  } finally { Pop-Location }
} finally {
  $env:PETCARE_AGENT_TOOLS = $savedAgentTools
  Get-ChildItem -LiteralPath (Join-Path $Root '.runtime') -Filter '*.mp4' -Recurse -File -ErrorAction SilentlyContinue |
    Where-Object FullName -Match '[\\/]remote[\\/]' | Remove-Item -Force
}

Write-Output 'REMOTE_LOCAL_INTEGRATION=PASS'
```

`tools/run_integration.ps1 -Provider Native` is the prerequisite Todo 14 owner of PostgreSQL/Mosquitto/FastAPI startup, health waiting, and child cleanup. The sibling BFF/auth test files own fake Supabase/JWKS/SMTP/D1/R2/Tunnel/Access behavior, including asserting `cloudflared tunnel --no-autoupdate run --token-file` followed by the fixture's ignored token-file path, without starting cloudflared. The fake proxy asserts browser status maps only to `/api/dashboard/summary` and MJPEG only to `/api/video_feed`; forbidden aliases fail. The Python and TypeScript agent-wire tests consume only `petcare-agent-wire-v1.json` and require snake-case enrollment, exact `PETCARE-CLIP-V1` bytes, the eleven headers listed in Task 2, HTTP `201` with the exact receipt, and cross-language Ed25519 verification. The two Jetson wire tests consume only `petcare-jetson-wire-v1.json`; both also assert the agent fixture remains byte-for-byte unchanged. The account tests require exact `202 {"status":"cleanup_pending"}` for first/pending deletion, empty `204` for already-absent/completed deletion, idempotent logical cleanup/provider retries, secret redaction, dashboard `POST /auth/logout` plus `/login` redirect after either success, retained Supabase identity, and the other owner unchanged. This wrapper adds no second process supervisor or fake. It prints exactly one terminal marker:

```text
REMOTE_LOCAL_INTEGRATION=PASS
```

on success and exits nonzero on any child, cleanup, sentinel, or route failure.

- [ ] **Step 4: Run the full fake integration twice from clean state**

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File tools/run_remote_integration.ps1 -Mode Fake
powershell -NoProfile -ExecutionPolicy Bypass -File tools/run_remote_integration.ps1 -Mode Fake
```

Expected on each run: exit 0 and one `REMOTE_LOCAL_INTEGRATION=PASS`; Task 12 health, `/api/dashboard/summary`, calibration, zones, and `/api/video_feed` respond through the fake Access/Tunnel BFF with no aliases; the transactional outbox survives commit/admission boundaries; both wire fixtures validate independently and `PETCARE-CLIP-V1` remains byte-for-byte unchanged; fixture clips upload/read/delete with the exact header set and receipt; queue saturation degrades health without blocking sensor paths; runtime/token files are protected before atomic replace; account-data deletion denies the target immediately, retries external cleanup, and leaves the foreign owner unchanged; `no_meal_12h` creates no clip; expired reads fail; and no child/listener/temp MP4 remains after teardown. Exact Jetson H.264/YUV420P, 640x480, 30/45-second media evidence belongs only to Jetson vision Tasks 8-10.

- [ ] **Step 5: Commit the fake-stack gate**

```bash
git add tools/run_remote_integration.ps1 tools/tests/test_run_remote_integration.ps1 backend/tests/integration/test_remote_agent_stack.py backend/tests/integration/test_agent_wire_contract.py dashboard/tests/integration/remote-stack.test.ts dashboard/tests/integration/agent-wire-contract.test.ts
git commit -m "test(remote): add hermetic integration gate"
```

### Task 5: Extend the existing secret/privacy gate before browser work

**Files:**
- Modify: `tools/secret_sentinel.py`
- Modify: `tools/privacy_check.py`
- Modify: `tools/tests/test_secret_sentinel.py`
- Modify: `tools/tests/test_privacy_check.py`
- Modify: `tools/check_all.ps1`

**Interfaces:**
- Consumes: prerequisite Todo 14's existing encoder and artifact scanner.
- Produces: remote-specific sentinel classes through the same scanner/CLI; no second privacy checker or encoding implementation.

- [ ] **Step 1: Add one failing fixture per remote leak class to the existing tests**

```python
# append to tools/tests/test_privacy_check.py
from tools.privacy_check import scan_remote_artifacts


@pytest.mark.parametrize("leak", [
    "access-secret-A", "YWNjZXNzLXNlY3JldC1B", "6163636573732d7365637265742d41",
    "https://home-a.example.test/api/health", "eyJ0dW5uZWwiOiJ0b2tlbiJ9",
    "-----BEGIN PRIVATE KEY-----", "supabase-refresh-token-A", "current-password-A",
])
def test_remote_encoded_and_structural_leaks_fail(tmp_path: Path, leak: str) -> None:
    (tmp_path / "artifact.log").write_text(leak, encoding="utf-8")
    with pytest.raises(ValueError):
        scan_remote_artifacts(tmp_path, ["access-secret-A", "tunnel-token", "supabase-refresh-token-A", "current-password-A"])


def test_remote_captured_media_fails(tmp_path: Path) -> None:
    (tmp_path / "capture.mp4").write_bytes(b"video")
    with pytest.raises(ValueError, match="media residue"):
        scan_remote_artifacts(tmp_path, [])
```

Add parameterized cases to `tools/tests/test_secret_sentinel.py` for raw, percent/form, standard/URL-safe Base64 padded/unpadded, and upper/lower hex forms of Supabase access/refresh, current reauthentication password, SMTP, Access client secret, Cloudflare API, Tunnel connector, and device-private-key sentinels.

- [ ] **Step 2: Run the existing scanners and verify RED**

```powershell
& $runtime.paths.python_path -m pytest tools/tests/test_secret_sentinel.py tools/tests/test_privacy_check.py -q
```

Expected: FAIL because the existing modules do not yet accept the remote sentinel/artifact classes.

- [ ] **Step 3: Extend the existing scanner and explicit whitelist**

Add `scan_remote_artifacts(root: Path, sentinels: Sequence[str]) -> None` to `tools/privacy_check.py` and call `tools.secret_sentinel.encoded_forms`; do not copy the encoder. Extend the existing CLI with repeatable `--remote-sentinel` and optional `--remote-artifacts` arguments. Scan only regular files under the supplied artifact root and Git-tracked paths. Fail on:

- every encoded form listed in Step 1;
- `CF-Access-Client-Secret`, `CF_TUNNEL_API_TOKEN`, `CLOUDFLARE_API_TOKEN`, `TUNNEL_TOKEN`, or private-key content in `dashboard/dist/**`;
- any browser-visible tunnel hostname/local address or credential-bearing URL;
- tracked/runtime `.mp4`, `.mjpeg`, `.jpg`, `.jpeg`, or home-camera `.webp`, except exactly `dashboard/public/demo-camera.webp` and `dashboard/public/og.png`;
- R2 object keys containing `@`, `owner_sub`, email-like text, event names, or home names;
- logs containing cookies, current passwords, reset links, video bytes, or signed/object URLs.

Whitelist the deterministic test-only Ed25519 seed and `fixture-only-connector-token` only at `contracts/petcare-agent-wire-v1.json`, and only when the whole parsed vector equals Task 4's frozen values. Reject either value in every source, bundle, log, evidence, argument, environment capture, or runtime path outside that file.

Preserve Todo 14's existing success marker and output format; add no `REMOTE_PRIVACY` wrapper marker.

- [ ] **Step 4: Run the existing scanner and full fake stack**

```powershell
& $runtime.paths.python_path -m pytest tools/tests/test_secret_sentinel.py tools/tests/test_privacy_check.py -q
powershell -NoProfile -ExecutionPolicy Bypass -File tools/run_remote_integration.ps1 -Mode Fake
& $runtime.paths.python_path tools/privacy_check.py --repo . --remote-artifacts .runtime/remote/evidence `
  --remote-sentinel access-secret-A --remote-sentinel tunnel-token --remote-sentinel supabase-refresh-token-A --remote-sentinel current-password-A
```

Expected: pytest passes, integration prints `REMOTE_LOCAL_INTEGRATION=PASS`, the existing privacy command exits 0 with its unchanged PASS marker, and `git status --short` shows no generated media/evidence file.

- [ ] **Step 5: Commit the extended gate**

```bash
git add tools/secret_sentinel.py tools/privacy_check.py tools/tests/test_secret_sentinel.py tools/tests/test_privacy_check.py tools/check_all.ps1
git commit -m "test(security): extend privacy gate for remote data"
```

### Task 6: Prove two-account isolation across every central and live resource

**Files:**
- Create: `dashboard/e2e/remote-multitenant.spec.ts`
- Modify: `dashboard/playwright.config.ts`
- Modify: `dashboard/tests/integration/remote-stack.test.ts`

**Interfaces:**
- Consumes: sibling fake providers, `PetCareRemoteClient`, protected BFF routes, and exact opaque IDs returned to each account.
- Produces: two isolated Playwright BrowserContexts, `owner-a` and `owner-b`, using separate fake Supabase cookies and never sharing storage state.

- [ ] **Step 1: Write the failing multi-account test matrix**

```ts
// dashboard/e2e/remote-multitenant.spec.ts
import { expect, test } from "@playwright/test";

test("two accounts cannot enumerate or mutate each other's home", async ({ browser }) => {
  const a = await browser.newContext();
  const b = await browser.newContext();
  await a.addCookies([{ name: "sb-session", value: "fake-owner-a", domain: "127.0.0.1", path: "/" }]);
  await b.addCookies([{ name: "sb-session", value: "fake-owner-b", domain: "127.0.0.1", path: "/" }]);
  const pageA = await a.newPage();
  const pageB = await b.newPage();

  await pageA.goto("/");
  await pageB.goto("/");
  await expect(pageA.getByText("agent-a")).toBeVisible();
  await expect(pageB.getByText("agent-b")).toBeVisible();

  for (const path of [
    "/api/petcare/cameras/camera-a/stream.mjpeg",
    "/api/petcare/clips/clip-a.mp4",
  ]) {
    expect((await b.request.get(path)).status()).toBe(404);
  }
  expect((await b.request.delete("/api/petcare/clips/clip-a")).status()).toBe(404);
  expect((await a.request.delete("/api/petcare/clips/clip-b")).status()).toBe(404);

  await a.close();
  await b.close();
});
```

Extend the Worker test with the same owner-A/owner-B matrix for the canonical status, camera stream, clip list/read/delete, agent enroll/upload routes, enrollment-code reuse/expiry/collision, revoked-agent credentials, foreign nonce/signature, seven-day denial, and reconciliation. A crash or failure after partial provisioning must reconcile/clean the incomplete ledger and require a newly issued code; the original code never replays or returns the connector token. For `DELETE /api/petcare/account`, require same-origin, current-password recent reauthentication against the verified session email, exact `202 {"status":"cleanup_pending"}` for first/pending deletion, empty `204` for already-absent/completed deletion, dashboard `POST /auth/logout` plus `/login` redirect after both, immediate PetCare denial, queued idempotent Tunnel/Access/DNS/R2 cleanup, retry after injected provider failure, no password/token in logs, retained Supabase identity, enrollment blocked while pending, tenant-registry/cleanup-ledger removal after completion, a fresh home only after explicit new enrollment, no permanent tombstone/automatic reactivation, and byte-for-byte unchanged owner-B rows/objects.

- [ ] **Step 2: Run the isolation E2E and verify RED**

```powershell
Push-Location dashboard
try { & $runtime.paths.node_path node_modules/playwright/cli.js test e2e/remote-multitenant.spec.ts } finally { Pop-Location }
```

Expected: FAIL until the integrated fake seed, route ownership joins, and browser fixture are wired.

- [ ] **Step 3: Add only integration fixture/seed wiring**

Use the sibling fake APIs to seed:

```json
{
  "owner-a": {"home":"home-a","agent":"agent-a","camera":"camera-a","clip":"clip-a"},
  "owner-b": {"home":"home-b","agent":"agent-b","camera":"camera-b","clip":"clip-b"}
}
```

Do not add a production seed endpoint. Start the fixture only when `PETCARE_REMOTE_PROVIDER=fake` and fail build/runtime if that variable is present in production mode. Preserve separate browser contexts; do not export cookies/storage state to disk.

- [ ] **Step 4: Run Worker and browser isolation GREEN**

```powershell
Push-Location dashboard
try {
  & $runtime.paths.node_path $runtime.paths.npm_cli_path test -- tests/integration/remote-stack.test.ts
  & $runtime.paths.node_path node_modules/playwright/cli.js test e2e/remote-multitenant.spec.ts
} finally { Pop-Location }
```

Expected: both commands exit 0; every foreign selector returns 404, anonymous routes return 401, enrollment and upload replay fail without state change, each clip list contains only its home, and neither context sees the other's IDs in DOM/network payloads.

- [ ] **Step 5: Commit isolation evidence code**

```bash
git add dashboard/e2e/remote-multitenant.spec.ts dashboard/playwright.config.ts dashboard/tests/integration/remote-stack.test.ts
git commit -m "test(remote): prove multi-account isolation"
```

### Task 7: Run remote visual, responsive, and accessibility QA

**Files:**
- Create: `dashboard/e2e/remote-visual.spec.ts`
- Modify: `dashboard/playwright.config.ts`
- Modify only when a failing test proves a defect: files owned by `docs/superpowers/plans/2026-07-20-petcare-remote-dashboard.md`

**Interfaces:**
- Consumes: fake BFF state controls and the remote dashboard sibling components.
- Produces: browser evidence at `1440x900`, `768x1024`, `375x812`, and `320x568`; keyboard and axe checks; no deployed URL or real credential.

- [ ] **Step 1: Write failing state/accessibility tests**

```ts
// dashboard/e2e/remote-visual.spec.ts
import AxeBuilder from "@axe-core/playwright";
import { expect, test } from "@playwright/test";

for (const viewport of [
  { width: 1440, height: 900 },
  { width: 768, height: 1024 },
  { width: 375, height: 812 },
  { width: 320, height: 568 },
]) {
  test(`remote states are accessible at ${viewport.width}x${viewport.height}`, async ({ page }) => {
    await page.setViewportSize(viewport);
    await page.goto("/login");
    expect((await new AxeBuilder({ page }).analyze()).violations).toEqual([]);
    await page.goto("/?fixture=online");
    await expect(page.getByText("에이전트 온라인")).toBeVisible();
    await expect(page.locator("video, img").first()).toBeVisible();
    expect((await new AxeBuilder({ page }).analyze()).violations).toEqual([]);
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  });
}
```

Add cases for signup/verify/forgot/reset, needs-enrollment/one-time code, offline with last seen and stored clips, live MJPEG, clip playback/delete success/failure, expired clip denial, reduced motion, long Korean error text, visible focus, native video controls, and `/demo` network interception.

- [ ] **Step 2: Run remote visual tests and inspect RED screenshots**

```powershell
Push-Location dashboard
try { & $runtime.paths.node_path node_modules/playwright/cli.js test e2e/remote-visual.spec.ts } finally { Pop-Location }
```

Expected: initial failures identify only concrete route, focus, overflow, contrast, media, or state defects; screenshots remain under ignored `dashboard/test-results/`.

- [ ] **Step 3: Fix only observed dashboard defects in the sibling-owned files**

Return each defect to the remote-dashboard plan owner, create its focused test there, commit the smallest fix, then rerun Tasks 3, 6, and 7. Do not alter BFF authorization, fake data, or expected screenshots to hide a visual defect.

- [ ] **Step 4: Run all viewports, axe, build, and demo isolation**

```powershell
Push-Location dashboard
try {
  & $runtime.paths.node_path node_modules/playwright/cli.js test e2e/remote-visual.spec.ts e2e/remote-multitenant.spec.ts
  & $runtime.paths.node_path $runtime.paths.npm_cli_path test
  & $runtime.paths.node_path $runtime.paths.npm_cli_path run build
} finally { Pop-Location }
```

Expected: exit 0; zero axe violations; no horizontal overflow; all named states remain usable at all four viewports; keyboard focus is visible; `/demo` makes no forbidden request and still uses bundled demo assets.

- [ ] **Step 5: Commit the browser gate**

```bash
git add dashboard/e2e/remote-visual.spec.ts dashboard/playwright.config.ts
git commit -m "test(dashboard): add remote visual QA"
```

Any component correction is committed separately by its sibling owner before this test-only commit.

### Task 8: Add exact-SHA remote CI jobs with no cloud secrets

**Files:**
- Create: `tools/tests/test_remote_ci_workflow.py`
- Modify: `.github/workflows/ci.yml`
- Modify: `tools/bootstrap_ci.sh`
- Modify: `tools/tests/test_bootstrap_ci.py`

**Interfaces:**
- Preserves existing jobs `firmware-host`, `firmware-pico`, `backend-unit`, `integration-live`, `dashboard`, and aggregate `ci` from Todo 16.
- Adds exactly `remote-contract` and `remote-browser` to the same `ubuntu-24.04` workflow.
- Base CI uses fake providers and contains/references no Supabase, SMTP, Access, Tunnel, D1, R2, or Sites secret.

- [ ] **Step 1: Write the failing workflow parser**

```python
# tools/tests/test_remote_ci_workflow.py
from pathlib import Path

import yaml


def test_remote_jobs_are_hermetic_and_aggregated() -> None:
    workflow = yaml.safe_load(Path(".github/workflows/ci.yml").read_text(encoding="utf-8"))
    jobs = workflow["jobs"]
    assert set(jobs) == {
        "firmware-host", "firmware-pico", "backend-unit", "integration-live",
        "dashboard", "remote-contract", "remote-browser", "ci",
    }
    assert jobs["remote-contract"]["runs-on"] == "ubuntu-24.04"
    assert jobs["remote-browser"]["runs-on"] == "ubuntu-24.04"
    assert set(jobs["ci"]["needs"]) == set(jobs) - {"ci"}
    text = Path(".github/workflows/ci.yml").read_text(encoding="utf-8")
    for forbidden in ("SUPABASE_", "SMTP_", "CF_ACCESS_", "CLOUDFLARE_API_TOKEN", "TUNNEL_TOKEN", "secrets."):
        assert forbidden not in text


def test_linux_bootstrap_exports_pinned_remote_tool_paths() -> None:
    text = Path("tools/bootstrap_ci.sh").read_text(encoding="utf-8")
    for key in ("ffmpeg_path", "ffprobe_path", "cloudflared_path"):
        assert f"paths[{key}]" in text
        assert f"sha256[{key}]" in text
    assert "managed_exact.ffmpeg.linux_x64" in text
    assert "managed_exact.cloudflared.linux_x64" in text
```

- [ ] **Step 2: Run the parser and verify RED**

```powershell
& $runtime.paths.python_path -m pytest tools/tests/test_remote_ci_workflow.py -q
```

Expected: FAIL because the two remote jobs and aggregate dependencies are absent.

- [ ] **Step 3: Extend the Linux bootstrap with the exact remote binaries**

In `tools/bootstrap_ci.sh`, read `managed_exact.ffmpeg.linux_x64` and `managed_exact.cloudflared.linux_x64` through the existing `manifest_value` function. Add `ffmpeg_path`, `ffprobe_path`, and `cloudflared_path` to `keys`; download the FFmpeg archive and cloudflared executable through `download_verify`; extract FFmpeg through `extract_tar`; locate the two FFmpeg binaries with `find`; mark cloudflared executable; and require all three paths to be absolute/executable. Probe exactly:

```bash
"${paths[ffmpeg_path]}" -version | head -1 | grep -F "$FFMPEG_VERSION"
"${paths[ffprobe_path]}" -version | head -1 | grep -F "$FFMPEG_VERSION"
"${paths[cloudflared_path]}" --version | grep -F "$CLOUDFLARED_VERSION"
```

Set both FFmpeg runtime versions to `8.1.2-22-g94138f6973` and cloudflared to `2026.7.2`. Extend `write_runtime` with a top-level `sha256` object containing uppercase SHA-256 values for the three executable files. The fixture branch writes executable stubs and their real fixture hashes. Update `LINUX_PATH_KEYS` and fixture assertions in `tools/tests/test_bootstrap_ci.py`; add wrong-byte and missing/non-executable-path cases. Do not duplicate any URL, version, or archive hash outside `tools/platform-manifest.json`.

- [ ] **Step 4: Add the two jobs using only the emitted runtime paths**

```yaml
remote-contract:
  runs-on: ubuntu-24.04
  steps:
    - uses: actions/checkout@93cb6efe18208431cddfb8368fd83d5badbf9bfd
    - run: bash tools/bootstrap_ci.sh
    - shell: pwsh
      run: |
        $runtime = Get-Content -Raw -Encoding UTF8 .runtime/platform-linux.json | ConvertFrom-Json
        $env:UV_PYTHON = $runtime.paths.python_path
        $env:PETCARE_AGENT_TOOLS = (Resolve-Path .runtime/platform-linux.json).Path
        & $runtime.paths.uv_path sync --project backend --frozen
        if ($LASTEXITCODE) { exit $LASTEXITCODE }
        & $runtime.paths.uv_path run --project backend pytest `
          backend/tests/integration/test_remote_agent_stack.py `
          backend/tests/integration/test_agent_wire_contract.py `
          backend/tests/test_jetson_wire_contract.py `
          backend/tests/test_clip_delivery.py `
          backend/tests/test_clip_upload_queue.py `
          tools/tests/test_remote_manifest_integration.py::test_remote_manifest_pins_are_immutable `
          tools/tests/test_remote_manifest_integration.py::test_auth_and_agent_dependencies_are_exact `
          tools/tests/test_secret_sentinel.py tools/tests/test_privacy_check.py -q
        if ($LASTEXITCODE) { exit $LASTEXITCODE }
        & $runtime.paths.python_path -m unittest discover -s jetson/tests -p 'test_wire_contract.py'
        if ($LASTEXITCODE) { exit $LASTEXITCODE }
        Push-Location dashboard
        try {
          & $runtime.paths.node_path $runtime.paths.npm_cli_path ci
          if ($LASTEXITCODE) { exit $LASTEXITCODE }
          & $runtime.paths.node_path $runtime.paths.npm_cli_path test -- tests/integration/remote-stack.test.ts tests/integration/agent-wire-contract.test.ts tests/cloudflare.test.ts tests/enrollment.test.ts tests/live-proxy.test.ts tests/clip-upload.test.ts tests/clips.test.ts tests/reconcile.test.ts tests/petcare-worker.test.ts
          if ($LASTEXITCODE) { exit $LASTEXITCODE }
        } finally { Pop-Location }

remote-browser:
  runs-on: ubuntu-24.04
  needs: remote-contract
  steps:
    - uses: actions/checkout@93cb6efe18208431cddfb8368fd83d5badbf9bfd
    - run: bash tools/bootstrap_ci.sh
    - shell: pwsh
      run: |
        $runtime = Get-Content -Raw -Encoding UTF8 .runtime/platform-linux.json | ConvertFrom-Json
        Push-Location dashboard
        try {
          & $runtime.paths.node_path $runtime.paths.npm_cli_path ci
          if ($LASTEXITCODE) { exit $LASTEXITCODE }
          & $runtime.paths.node_path node_modules/playwright/cli.js install chromium
          if ($LASTEXITCODE) { exit $LASTEXITCODE }
          & $runtime.paths.node_path node_modules/playwright/cli.js test e2e/remote-multitenant.spec.ts e2e/remote-visual.spec.ts
          if ($LASTEXITCODE) { exit $LASTEXITCODE }
        } finally { Pop-Location }
```

The commands above use the exact `.runtime/platform-linux.json` keys emitted by the extended bootstrap; no guessed managed-directory layout is allowed. Runner `python`, `node`, `npm`, `ffmpeg`, and `cloudflared` are forbidden after bootstrap. The test suite may use this x64 CI runtime as `PETCARE_AGENT_TOOLS`; the Raspberry Pi installer still accepts only its arm64 agent runtime. Record the manifest's absolute paths, versions, and hashes in ignored CI evidence.

- [ ] **Step 5: Run local workflow checks and the exact candidate gate**

```powershell
& $runtime.paths.python_path -m pytest tools/tests/test_ci_workflow.py tools/tests/test_remote_ci_workflow.py tools/tests/test_bootstrap_ci.py tools/tests/test_remote_manifest_integration.py -q
powershell -NoProfile -ExecutionPolicy Bypass -File tools/run_remote_integration.ps1 -Mode Fake
Push-Location dashboard
try { & $runtime.paths.node_path node_modules/playwright/cli.js test e2e/remote-multitenant.spec.ts e2e/remote-visual.spec.ts } finally { Pop-Location }
```

Expected: all commands exit 0. Only after the commit below and explicit push authorization, set:

```powershell
$CandidateSha = (& $runtime.paths.git_path rev-parse HEAD).Trim()
& $runtime.paths.git_path merge-base --is-ancestor $RemotePlanningSha $CandidateSha
```

Expected: exit 0. Push only the locally green `CandidateSha`, then require all eight jobs green for exactly that SHA. Never push a deliberately broken commit.

- [ ] **Step 6: Commit CI separately**

```bash
git add .github/workflows/ci.yml tools/bootstrap_ci.sh tools/tests/test_bootstrap_ci.py tools/tests/test_remote_ci_workflow.py
git commit -m "ci: verify remote PetCare integration"
```

### Task 9: Add operator, privacy, and external-resource documentation

**Files:**
- Create: `docs/remote-operations.md`
- Create: `docs/remote-privacy.md`
- Create: `docs/external-resource-checklist.md`
- Create: `tools/docs_check.py`
- Create: `tools/tests/test_docs_check.py`
- Modify: `README.md`
- Modify: `tools/check_all.ps1`

**Interfaces:**
- Produces exact Windows and Raspberry Pi commands that load `.runtime` paths, start Task 12 on loopback, use cloudflared `--token-file`, check health, stop/revoke, and run Fake versus Real gates.
- The external checklist records `PASS|FAIL|NOT RUN` without credentials and distinguishes approval, account readiness, permission, quota, and cost.

- [ ] **Step 1: Write failing structured-doc tests**

```python
# tools/tests/test_docs_check.py
from pathlib import Path

from tools.docs_check import validate_remote_docs


def test_remote_docs_have_exact_safe_contracts() -> None:
    errors = validate_remote_docs(Path("."))
    assert errors == []
```

The checker validates these exact facts: `127.0.0.1:8000`, `DB`, `CLIPS`, FFmpeg `8.1.2-22-g94138f6973`, cloudflared `2026.7.2`, `--token-file`, `PETCARE-CLIP-V1`, snake-case enrollment, HTTP `201` receipt, seven days, ten-second pre-roll, twenty-second post-roll, eligible events, `no_meal_12h` exclusion, same-origin/recent-reauth `DELETE /api/petcare/account`, first/pending `202` with `{"status":"cleanup_pending"}`, completed/absent empty `204`, retained Supabase identity, `REMOTE_LOCAL`, `REMOTE_EXTERNAL`, public Sites plus protected live routes, and the Fake/Real commands. It rejects `--token `, public FastAPI/MQTT/PostgreSQL, public R2, continuous recording, service-role key, Supabase-user deletion, and claims that missing external/hardware evidence passed.

- [ ] **Step 2: Run docs tests and verify RED**

```powershell
& $runtime.paths.python_path -m pytest tools/tests/test_docs_check.py -q
```

Expected: FAIL because the generic checker and remote documents do not exist yet.

- [ ] **Step 3: Write the exact operator documents**

`docs/external-resource-checklist.md` must contain this ordered table, initialized to `NOT RUN`:

| Gate | Required evidence | Initial status |
| --- | --- | --- |
| Explicit authorization | Named approval for Supabase project/config, SMTP, Cloudflare DNS/Tunnel/Access/token, Sites D1/R2/runtime values, two test accounts, public Sites deploy, and any cost | `NOT RUN` |
| Cost/quota | User-approved free/paid tier and quota for Supabase, SMTP, Cloudflare, R2, D1, and Sites | `NOT RUN` |
| Supabase | Project URL, publishable key, asymmetric JWKS, email/password, verification, reset, redirect URLs | `NOT RUN` |
| SMTP | Verified sender and successful verification/reset delivery without storing message bodies | `NOT RUN` |
| Cloudflare | Account/zone/domain, scoped token permissions, Access service token, no global API key | `NOT RUN` |
| Home tunnel | One test tunnel/hostname/Access app, Service Auth policy, origin `http://127.0.0.1:8000`, connector token stored once in ACL file | `NOT RUN` |
| Sites state | `DB` D1 and private `CLIPS` R2 bindings, runtime values registered through Sites, public URL approval | `NOT RUN` |
| Two accounts | Two distinct verified users and one home/agent/camera/clip set per user | `NOT RUN` |
| Cleanup | Test PetCare enrollment/tunnel/Access/DNS/objects retained or deleted through the canonical account route per explicit choice; Supabase identities remain | `NOT RUN` |

`docs/remote-operations.md` documents `-Mode Fake` as the default completion command and says `-Mode Real` exits 3 without the ignored approval record. It documents Windows service/task and systemd as operator choices, not automatic actions in this plan. Its account-deletion procedure uses same-origin `DELETE /api/petcare/account`, requires current-password recent reauthentication, expects exact first/pending `202 {"status":"cleanup_pending"}` or already-absent/completed empty `204`, then has the dashboard `POST /auth/logout` and follow the `/login` redirect after either success before observing agent revocation, Tunnel/Access/DNS cleanup, clip/D1 logical deletion, and R2 deletion/retry. Enrollment remains blocked while pending; completion removes the tenant registry/cleanup ledger, and the retained Supabase identity may explicitly enroll a new home. Permanent tombstones, automatic reactivation, identity deletion, and service-role keys remain out of scope. `docs/remote-privacy.md` states provider encryption at rest and TLS, explicitly says end-to-end encryption from the operator is not provided, and documents immediate logical expiry plus scheduled physical reconciliation.

- [ ] **Step 4: Validate docs and full local closure**

```powershell
& $runtime.paths.python_path -m pytest tools/tests/test_docs_check.py -q
& $runtime.paths.python_path tools/docs_check.py --root . --remote
powershell -NoProfile -ExecutionPolicy Bypass -File tools/check_all.ps1
powershell -NoProfile -ExecutionPolicy Bypass -File tools/run_remote_integration.ps1 -Mode Fake
```

Expected: the existing docs checker exits 0 with its unchanged PASS marker; full checks exit 0; integration prints `REMOTE_LOCAL_INTEGRATION=PASS`.

- [ ] **Step 5: Commit docs and their machine check**

```bash
git add README.md docs/remote-operations.md docs/remote-privacy.md docs/external-resource-checklist.md tools/docs_check.py tools/tests/test_docs_check.py tools/check_all.ps1
git commit -m "docs(remote): add operations and approval runbook"
```

### Task 10: Seal local completion and stop unless external actions are approved

**Files:**
- Generated ignored evidence only under `.runtime/remote/evidence/`; no tracked file change.

**Interfaces:**
- Produces: immutable `LOCAL_CANDIDATE_SHA`, green exact-SHA CI, `REMOTE_LOCAL=PASS`, and one of the external statuses.

- [ ] **Step 1: Prove the local candidate is clean, descended from the planning commit, and exact-SHA green**

```powershell
$GitPath = $runtime.paths.git_path
$LocalCandidateSha = (& $GitPath rev-parse HEAD).Trim()
& $GitPath merge-base --is-ancestor $RemotePlanningSha $LocalCandidateSha
if ($LASTEXITCODE) { throw 'remote planning commit is not an ancestor' }
& $GitPath diff --quiet
& $GitPath diff --cached --quiet
if ($LASTEXITCODE) { throw 'candidate worktree/index is not clean' }
```

Require the official branch head and all eight CI jobs to equal/pass `LOCAL_CANDIDATE_SHA`. Record only repo, branch, SHA, run URL, conclusions, manifest hashes, and artifact SHA-256 under ignored evidence.

- [ ] **Step 2: Rerun the local acceptance commands against the same SHA**

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File tools/run_remote_integration.ps1 -Mode Fake
& $runtime.paths.python_path tools/privacy_check.py --repo . --remote-artifacts .runtime/remote/evidence `
  --remote-sentinel access-secret-A --remote-sentinel tunnel-token --remote-sentinel supabase-refresh-token-A --remote-sentinel current-password-A
Push-Location dashboard
try {
  & $runtime.paths.node_path node_modules/playwright/cli.js test e2e/remote-multitenant.spec.ts e2e/remote-visual.spec.ts
  & $runtime.paths.node_path $runtime.paths.npm_cli_path run build
} finally { Pop-Location }
```

Expected: all commands exit 0 and print their PASS markers; no source changes occur.

- [ ] **Step 3: Record the split completion state**

Write ignored `.runtime/remote/evidence/local-status.json` from the verified variable, never from a hand-edited sample:

```powershell
if ($LocalCandidateSha -notmatch '^[0-9a-f]{40}$') { throw 'invalid LOCAL_CANDIDATE_SHA' }
$Root = (Resolve-Path '.').Path
$statusPath = Join-Path $Root '.runtime/remote/evidence/local-status.json'
New-Item -ItemType Directory -Force -Path (Split-Path $statusPath -Parent) | Out-Null
$status = [ordered]@{
  candidate_sha = $LocalCandidateSha
  remote_local = 'PASS'
  remote_external = 'NOT_RUN_APPROVAL'
}
[IO.File]::WriteAllText($statusPath, ($status | ConvertTo-Json), [Text.UTF8Encoding]::new($false))
```

Reload the file and reject a non-hex SHA or any value unequal to `$LocalCandidateSha`.

- [ ] **Step 4: Ask for external authorization only now**

Present the exact checklist in `docs/external-resource-checklist.md`, including known pricing/quota information verified at execution time from official sources. Ask one focused question covering which external actions and costs are approved. If approval is absent, stop successfully with `REMOTE_LOCAL=PASS` and `REMOTE_EXTERNAL=NOT_RUN_APPROVAL`.

If approved, create the ignored approval record from the user's exact response, with no credential values:

```json
{
  "authorization": "REMOTE_EXTERNAL_ACTIONS_APPROVED",
  "public_sites": true,
  "supabase": true,
  "smtp": true,
  "cloudflare_tunnel_access": true,
  "sites_d1_r2": true,
  "two_test_accounts": true,
  "cost_acknowledged": true
}
```

- [ ] **Step 5: Do not commit evidence or approval records**

Run:

```powershell
& $GitPath check-ignore .runtime/remote/external-approval.json .runtime/remote/evidence/local-status.json
& $GitPath status --short
```

Expected: both files are ignored and status is clean. This task has no commit.

### Task 11: Verify approved real prerequisites without provisioning application resources yet

**Files:**
- Ignored evidence only; no tracked file change.

**Interfaces:**
- Consumes: approved external checklist and runtime secrets supplied through Sites/ACL files, never arguments.
- Produces: `REMOTE_EXTERNAL=NOT_RUN_PREREQUISITES` or a green prerequisite record that permits public deployment.

- [ ] **Step 1: Validate the approval record and secret transport**

Run:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File tools/run_remote_integration.ps1 -Mode Real -WhatIf
```

Expected: exit 0 only with the exact approval record and all required secret sources readable; output is names/status only and contains no values. Missing approval exits 3. Missing prerequisite exits 4 and records `REMOTE_EXTERNAL=NOT_RUN_PREREQUISITES`.

- [ ] **Step 2: Read-only verify Supabase and SMTP configuration**

Compute `$ExpectedIssuer = $SupabaseUrl.TrimEnd('/') + '/auth/v1'`, then verify the official project URL, asymmetric JWKS endpoint, that exact issuer, audience `authenticated`, publishable key type, email/password enabled, verification/reset redirect URLs targeting the intended Sites hostname, and SMTP configuration. Do not create users or send mail in this step.

Expected: JWKS returns at least one asymmetric public key; no service-role/secret key is present in PetCare runtime configuration.

- [ ] **Step 3: Read-only verify Cloudflare account/zone/token/service credential**

Verify the scoped token can read the intended account/zone and has only the permissions required by the sibling provisioner for Tunnel, DNS hostname, Access application/policy, and revocation. Verify the Access service token exists and its secret is available only in the Sites runtime-secret channel. Do not use a global API key and do not create a tunnel yet.

- [ ] **Step 4: Verify Sites identity/capability and public-access approval**

List/reuse exact title `PetCare AIoT Dashboard`, slug `kr-hrd-petcare-aiot`, and description `Authenticated multi-tenant PetCare remote dashboard`. A unique exact match is reusable; a conflicting slug/title with a different identity stops without create/retry. Confirm the connector supports logical D1/R2 bindings, runtime values, version save, public `deploy_site_version`, and status polling.

- [ ] **Step 5: Record redacted prerequisite results; no commit**

Expected record fields: candidate SHA, checklist item, `PASS|FAIL|NOT RUN`, provider resource opaque ID when safe, checked-at UTC, and evidence hash. No email, hostname containing a user/home name, token, secret, cookie, reset URL, or credential-bearing object URL is recorded.

### Task 12: Commit Sites identity metadata, prove tree equality, and publicly deploy the exact source

**Files:**
- Modify only after approved project create/reuse: `dashboard/.openai/hosting.json`
- Generated ignored archive/evidence under `.runtime/remote/sites/`

**Interfaces:**
- Produces: `PUBLIC_CANDIDATE_SHA`, deterministic `SITES_SOURCE_SHA`, exact source push, archive, version ID, public deployment ID, terminal URL, and live runtime bindings.
- Replaces: the earlier private deployment gate. Do not call `deploy_private_site_version`.

- [ ] **Step 1: Resolve or create the Sites project exactly once under approval**

Use the exact identity from Task 11. Let `$SiteProjectId` be the connector response's unchanged `project_id`; reject null, empty, or multiple identity matches. Persist it alongside only the logical bindings:

```powershell
if ([string]::IsNullOrWhiteSpace($SiteProjectId)) { throw 'Sites project_id missing' }
$hosting = [ordered]@{ d1 = 'DB'; r2 = 'CLIPS'; project_id = $SiteProjectId }
[IO.File]::WriteAllText(
  (Resolve-Path 'dashboard/.openai').Path + [IO.Path]::DirectorySeparatorChar + 'hosting.json',
  ($hosting | ConvertTo-Json -Compress),
  [Text.UTF8Encoding]::new($false)
)
```

Reload the JSON and require its key set to equal `d1,r2,project_id`; the connector ID must be byte-for-byte equal to `$SiteProjectId`. Run dashboard tests/build and `git diff --check`, then commit:

```bash
git add dashboard/.openai/hosting.json
git commit -m "chore(sites): bind remote PetCare resources"
```

Push this locally green commit only with explicit push authorization and require all eight CI jobs green for its exact SHA.

- [ ] **Step 2: Compute and prove the exact candidate/subtree identities**

```powershell
$PublicCandidateSha = (& $GitPath rev-parse HEAD).Trim()
& $GitPath merge-base --is-ancestor $RemotePlanningSha $PublicCandidateSha
if ($LASTEXITCODE) { throw 'planning ancestry failed' }
$SitesSourceSha = (& $GitPath subtree split --prefix=dashboard $PublicCandidateSha).Trim()
$CandidateDashboardTree = (& $GitPath rev-parse "$PublicCandidateSha`:dashboard").Trim()
$SitesTree = (& $GitPath rev-parse "$SitesSourceSha`^{tree}").Trim()
if ($CandidateDashboardTree -ne $SitesTree) { throw 'dashboard subtree tree mismatch' }
& $GitPath diff --quiet $PublicCandidateSha -- dashboard
if ($LASTEXITCODE) { throw 'working dashboard differs from candidate' }
```

Expected: planning ancestry exit 0 and the two tree hashes are byte-identical.

- [ ] **Step 3: Push only `SITES_SOURCE_SHA` using an ephemeral header**

Obtain a current Sites source credential and copy its returned fields unchanged into `$AuthMode`, `$Token`, `$RemoteUrl`, and `$SourceBranch`. Validate `auth_mode` with `^[A-Za-z][A-Za-z0-9+.-]*$`; reject empty values and control characters in all four fields. Set the ephemeral Git header only for the push/verification scope:

```powershell
if ($AuthMode -notmatch '^[A-Za-z][A-Za-z0-9+.-]*$') { throw 'invalid Sites auth mode' }
foreach ($value in $Token,$RemoteUrl,$SourceBranch) {
  if ([string]::IsNullOrWhiteSpace($value) -or $value -match '[\x00-\x1F\x7F]') { throw 'invalid Sites source credential field' }
}
if ($SourceBranch -notmatch '^[A-Za-z0-9._/-]+$' -or $SourceBranch.Contains('..')) { throw 'invalid Sites source branch' }
$savedGitConfig = @($env:GIT_CONFIG_COUNT,$env:GIT_CONFIG_KEY_0,$env:GIT_CONFIG_VALUE_0)
try {
  $env:GIT_CONFIG_COUNT = '1'
  $env:GIT_CONFIG_KEY_0 = 'http.extraHeader'
  $env:GIT_CONFIG_VALUE_0 = "Authorization: $AuthMode $Token"
  & $GitPath push -- $RemoteUrl "$SitesSourceSha`:refs/heads/$SourceBranch"
  if ($LASTEXITCODE) { throw 'Sites source push failed' }
  $remoteHead = ((& $GitPath ls-remote --refs $RemoteUrl "refs/heads/$SourceBranch") -split '\s+')[0]
  if ($LASTEXITCODE -or $remoteHead -ne $SitesSourceSha) { throw 'Sites source SHA mismatch' }
} finally {
  $env:GIT_CONFIG_COUNT = $savedGitConfig[0]
  $env:GIT_CONFIG_KEY_0 = $savedGitConfig[1]
  $env:GIT_CONFIG_VALUE_0 = $savedGitConfig[2]
}
```

Do not add a Git remote or persist Git config. The token remains in process environment only and must not be logged.

- [ ] **Step 4: Build/package the detached exact subtree and save one version**

Create an ignored detached worktree from `SITES_SOURCE_SHA` under `.runtime/remote/sites/source`, run `npm ci` and `npm run build` with manifest Node/npm and `npm_config_script_shell=$runtime.paths.bash_path`, then invoke:

```powershell
$ArchivePath = Join-Path (Resolve-Path '.runtime/remote/sites').Path 'petcare-remote-site.tar.gz'
& $runtime.paths.bash_path 'C:/Users/전산1-4/.codex/plugins/cache/openai-bundled/sites/0.1.30/scripts/package-site.sh' `
  (Resolve-Path '.runtime/remote/sites/source').Path `
  $ArchivePath
```

Expected archive entries include `dist/server/index.js`, `dist/.openai/hosting.json`, and files under `dist/.openai/drizzle/`; the staged hosting file contains only the exact `$SiteProjectId`, `DB`, and `CLIPS`. Call `save_site_version({ project_id: siteProjectId, commit_sha: sitesSourceSha, archive: archivePath })`, where all three lower-camel identifiers are the unchanged values computed above; use its returned `id` unchanged as `version_id`.

- [ ] **Step 5: Register runtime values, publicly deploy, and poll exact IDs**

Register `SUPABASE_URL`, `SUPABASE_PUBLISHABLE_KEY`, `CF_ACCOUNT_ID`, `CF_ZONE_ID`, `CF_ZONE_NAME`, `CF_ACCESS_TEAM_NAME`, `CF_TUNNEL_API_TOKEN`, `CF_ACCESS_SERVICE_TOKEN_ID`, `CF_ACCESS_CLIENT_ID`, and `CF_ACCESS_CLIENT_SECRET` through Sites runtime-value APIs. Mark only `SUPABASE_URL`, `SUPABASE_PUBLISHABLE_KEY`, `CF_ACCOUNT_ID`, `CF_ZONE_ID`, `CF_ZONE_NAME`, `CF_ACCESS_TEAM_NAME`, `CF_ACCESS_SERVICE_TOKEN_ID`, and `CF_ACCESS_CLIENT_ID` as publishable server runtime configuration; mark `CF_TUNNEL_API_TOKEN` and `CF_ACCESS_CLIENT_SECRET` secret. None are client-bundled values, and secret values never enter evidence.

After a final explicit “publish publicly” approval, call `deploy_site_version({ project_id: siteProjectId, version_id: versionId })`, not the private deploy operation. Use its returned `id` unchanged as `deployment_id`. Poll `get_deployment_status({ project_id: siteProjectId, version_id: versionId, deployment_id: deploymentId })` until `succeeded|failed`. On success require `status.url` to be a non-null HTTPS URL and call `open_in_codex({ target: { type: 'browser', url: status.url } })` with `threadId` omitted. On failure or null/non-HTTPS URL, do not open and record `REMOTE_EXTERNAL=FAIL`.

### Task 13: Provision two real test homes and prove deployed isolation/retention

**Files:**
- Ignored redacted evidence only; no tracked file change.

**Interfaces:**
- Consumes: the exact deployed URL and approved real-resource mutations.
- Produces: real Supabase/SMTP/Tunnel/Access/D1/R2 evidence for two distinct test accounts.

- [ ] **Step 1: Exercise real auth flows with two distinct accounts**

Create only the two approved test accounts. For each, prove signup, verification mail, login, refresh, logout, forgot-password non-enumeration, reset mail, reset, expired/malformed/wrong issuer/wrong audience rejection, and authenticated root access. Never save password/reset/access/refresh values in evidence.

Expected: public auth pages and `/demo` return 200; anonymous protected pages redirect or return 401; verified sessions reach only their own home.

- [ ] **Step 2: Enroll account A and B independently**

Each account requests one ten-minute code. Each home agent generates its device key locally, enrolls once, receives its connector token once into an ACL-restricted ignored file, and launches pinned cloudflared with `--token-file`. Prove code reuse, expiry, collision, second active agent, and foreign ownership fail without changing the current binding. A partial provisioning failure/crash must reconcile and revoke the incomplete Tunnel/DNS/Access ledger; it never replays the same code or connector token, and a retry begins only with a newly issued code.

Expected: one active home/agent/camera per account; local FastAPI still listens only on `127.0.0.1:8000`; each Tunnel is outbound and Access Service Auth protected.

- [ ] **Step 3: Prove live Task 12 proxying and offline behavior**

For each account, exercise status polling, summary, devices, sensors, behaviors, anomalies, camera status, bed status/calibration, zones, and MJPEG through `/api/petcare/**`. Copy every A opaque selector into B requests and vice versa; require 404. Stop A cloudflared/agent; require `503 agent_offline` with real last-seen, no demo fallback, while A's stored clips remain readable.

- [ ] **Step 4: Prove real clip upload/read/delete/expiry/reconciliation**

Trigger only `eating`, `resting`, and `bed_sensor_mismatch`; verify the transactional trigger outbox before Jetson admission and require first acceptance within the frozen real three-second deadline; require the exact `PETCARE-CLIP-V1` canonical bytes, eleven Task 2 request headers, and HTTP `201` with `{ id, createdAt, expiresAt }`; use manifest ffprobe only to validate the Jetson MP4 as H.264/YUV420P 640x480 with `30.0 ± 0.5` seconds for one ten-second pre-roll/twenty-second post-roll trigger and `45.0 ± 0.5` seconds after one overlap extension; require all coalesced reasons in metadata, bounded memory/queue, queue-full abort/degraded health, signed upload replay rejection, pre-write ACL on runtime/token files, temporary-file deletion, private R2, and no `no_meal_12h` clip. Use a clock-controlled test record to prove read denial at exactly seven days and reconciliation of metadata/orphan objects without waiting seven wall-clock days. Copy A clip IDs to B list/read/delete and require 404.

- [ ] **Step 5: Run deployed browser/visual/privacy checks**

Use two fresh browser profiles/contexts without exporting storage state. Repeat login, enrollment, online/offline, live video, clip playback/delete, `1440x900`, `768x1024`, `375x812`, `320x568`, keyboard, reduced motion, and axe. Inspect network/DOM/logs for tunnel origins, local addresses, Access/device credentials, Supabase tokens, R2 object URLs, and secret values.

- [ ] **Step 6: Apply the approved PetCare data-retention/deletion decision**

If the checklist approves deletion of owner A's test PetCare data, send same-origin `DELETE /api/petcare/account` with owner A's current password after a verified session. Require request-scoped `signInWithPassword`, exact first/pending `202 {"status":"cleanup_pending"}`, dashboard `POST /auth/logout` plus `/login` redirect, immediate old-home denial, revoked agent/enrollment, completed or queued-retry Tunnel/Access/DNS/R2 cleanup, redacted logs, and owner B unchanged. Prove enrollment remains blocked while pending. After cleanup completes, repeat the deletion route and require an empty `204` plus the same logout/redirect behavior, then require tenant-registry/cleanup-ledger removal, log owner A in with the same retained Supabase identity, explicitly request a new enrollment code, and prove a fresh home can be enrolled; there is no automatic reactivation. If cleanup fails or remains pending, record `REMOTE_EXTERNAL=FAIL`; never delete the Supabase identity. If the approved decision is retention, retain both users/resources exactly as documented and perform no deletion.

- [ ] **Step 7: Record external status**

If all approved checks/actions pass, record `REMOTE_EXTERNAL=PASS`. If any check fails, record `FAIL`, make no fix inside this gate, return to the owning component/integration task, create a new commit, rerun local CI, derive a new subtree, save/deploy a new version, and rerun all Task 13 checks.

### Task 14: Run F2/F3/F4 in parallel, then F1 last against one unchanged candidate

**Files:**
- Ignored evidence only under `.runtime/remote/evidence/final/`; no tracked file change.

**Interfaces:**
- Consumes: one immutable `FINAL_CANDIDATE_SHA`, its exact-SHA CI, component plans, deployed `SITES_SOURCE_SHA` when external PASS, and split local/external statuses.
- Produces: read-only F2/F3/F4 results followed by the final F1 compliance result.

- [ ] **Step 1: Freeze final identities**

```powershell
$FinalCandidateSha = (& $GitPath rev-parse HEAD).Trim()
& $GitPath merge-base --is-ancestor $RemotePlanningSha $FinalCandidateSha
if ($LASTEXITCODE) { throw 'planning ancestry failed' }
& $GitPath diff --quiet
& $GitPath diff --cached --quiet
if ($LASTEXITCODE) { throw 'final candidate is dirty' }
```

Require official branch head and all eight CI jobs green for `FINAL_CANDIDATE_SHA`. If `REMOTE_EXTERNAL=PASS`, rederive `SITES_SOURCE_SHA`, re-prove tree equality, and verify the successful deployment IDs still bind to that exact source. If external is not run, require the honest `NOT_RUN_*` status and no production-complete claim.

- [ ] **Step 2: Run F2, F3, and F4 concurrently as read-only reviews**

- **F2 Code quality:** inspect only `FINAL_CANDIDATE_SHA` for unnecessary abstractions/dependencies, duplicate authorization, client-side secrets, PATH fallback, unbounded memory/queues, and incorrect lifecycle ownership. Require `PASS`.
- **F3 Security/privacy/manual:** rerun Fake integration, two-account isolation, sentinel, cache headers, CSRF/rate limit, signed-upload replay, seven-day denial, `/demo` isolation, and—only when `REMOTE_EXTERNAL=PASS`—the deployed real two-account evidence. Require local `PASS`; external remains its exact split status.
- **F4 Scope fidelity:** compare production/runtime surfaces and current operator docs against the approved remote design and four sibling plans. Reject sharing/roles/multi-camera/WebRTC/WebSocket remote proxy/continuous recording/public R2/service-role storage or stale owner-only/private-deploy claims. Require `PASS`.

Each reviewer records candidate SHA, reviewed paths, commands, result, findings, and artifact SHA-256. Reviewers do not edit.

- [ ] **Step 3: Correct findings through the owning task, never inside a final gate**

Any F2/F3/F4 finding creates an atomic corrective commit in the owning plan, resets `FINAL_CANDIDATE_SHA`, reruns all local tests/CI, reruns Sites if any dashboard/hosting/runtime input changed (otherwise re-proves subtree equality), and reruns all three parallel gates. Do not carry forward a prior PASS to a new SHA.

- [ ] **Step 4: Run F1 plan compliance last**

Only after F2/F3/F4 pass for one unchanged SHA, audit every requirement in this plan, the approved design, Task 11/12, four sibling plans, exact-SHA CI, manifest/runtime hashes, source/tree/archive/version/deployment identities, evidence hashes, and split completion statuses. Verify every generated evidence path is ignored and every required tracked path is in `FINAL_CANDIDATE_SHA`.

F1 passes only when:

```text
F2=PASS
F3_LOCAL=PASS
F4=PASS
REMOTE_LOCAL=PASS
REMOTE_EXTERNAL=PASS|NOT_RUN_APPROVAL|NOT_RUN_PREREQUISITES
```

`REMOTE_EXTERNAL=FAIL` cannot pass F1. `NOT_RUN_*` may pass local plan compliance but the final response must state that production deployment/real-resource acceptance is incomplete.

- [ ] **Step 5: Final evidence and handoff**

Run:

```powershell
& $GitPath check-ignore .runtime/remote/evidence/final/*
& $GitPath status --short
& $GitPath rev-parse HEAD
```

Expected: final evidence is ignored, worktree/index are clean, HEAD equals `FINAL_CANDIDATE_SHA`. Do not fast-forward/push `main`, delete real test resources, or retain them beyond the approved checklist without a separate explicit user decision.

## Atomic Commit Sequence

1. `chore(remote): pin agent and cloud runtimes`
2. `feat(agent): compose remote lifecycle with local API`
3. `feat(sites): integrate authenticated PetCare BFF`
4. `test(remote): add hermetic integration gate`
5. `test(security): extend privacy gate for remote data`
6. `test(remote): prove multi-account isolation`
7. `test(dashboard): add remote visual QA`
8. `ci: verify remote PetCare integration`
9. `docs(remote): add operations and approval runbook`
10. `chore(sites): bind remote PetCare resources` — only after approved Sites create/reuse returns the exact `project_id`.

Every commit stages only its listed paths. Component corrections use the sibling plan's own commit message and are inserted before the dependent integration commit. No commit, push, external mutation, or deployment is implied by this plan document.

## Success Criteria

- Todo 11/12 behavior and exact local REST/WebSocket contract remain green and loopback-only; the home-agent lifecycle wraps them without moving hardware/rule state into Sites.
- Manifest/runtime closure records exact FFmpeg/cloudflared paths, versions, and hashes for Windows x64, Linux x64 CI, and Raspberry Pi arm64; no runtime PATH fallback exists.
- Jetson vision Tasks 8-10 prove bounded ten-second pre-roll, twenty-second post-roll, `30.0 ± 0.5` second normal and `45.0 ± 0.5` second overlap clips plus H.264/YUV420P 640x480 output; Remote Task 4 separately proves both wire contracts, transactional admission/delivery retry, queue-full degraded health, validation-only ffprobe, pre-write ACL, and temp cleanup under fake cloud providers.
- Supabase/JWKS, D1 tenancy, enrollment/provisioning, Tunnel/Access, R2 retention, signed uploads, and BFF proxying pass hermetically with two accounts and no external credential.
- Every protected resource path uses verified `sub` first and returns 404 for a copied foreign selector; anonymous access is 401/redirect as appropriate.
- Remote live data polls every two seconds, live/clip responses are private/no-store/no-transform, offline is explicit, and demo data is never substituted for live failure.
- `/demo` remains public, bundled, and network-isolated. Auth pages are public; operational pages and all live/clip controls are protected.
- Browser QA passes all four viewports, keyboard, reduced motion, native media controls, focus, overflow, and axe checks.
- Base CI has exactly eight jobs plus no external secret dependency; exact candidate SHA, planning ancestry, manifest identities, and aggregate result are recorded.
- The sentinel proves no secret encoding, credential URL, tunnel origin, local address, captured media, private key, cookie/reset token, or sensitive R2 key leaks to source, bundle, arguments, logs, evidence, or browser.
- Local completion reports `REMOTE_LOCAL=PASS` independently of external state.
- Real external work runs only after named approval/cost/prerequisite gates. Public Sites deployment replaces the private gate, but protected deployed routes still reject anonymous/foreign access.
- When external PASS is claimed, the deployed version comes from a pushed `SITES_SOURCE_SHA` whose tree exactly equals `FINAL_CANDIDATE_SHA:dashboard`, the archive/version/deployment IDs are unmodified, and two real accounts see only their own agent/camera/clips.
- F2/F3/F4 pass in parallel for one unchanged candidate and F1 passes last; no final-gate finding is fixed in place.

## Plan Self-Review

- Spec coverage: Task 1 covers the unchanged FFmpeg/cloudflared manifest pins; Tasks 2-4 integrate Task 11/12, the Jetson-aware Home agent, both wire contracts, BFF, and D1/R2/Tunnel fakes without a Home encoder; Tasks 5-7 cover privacy, two-account E2E, and browser/accessibility QA; Task 8 covers CI; Task 9 covers operator/external docs; Tasks 10-13 separate local completion from approved real resources and public Sites; Task 14 enforces F2/F3/F4 parallel and F1 last.
- Component boundary: Auth/tenancy, home-agent clips, BFF/Tunnel/R2, and remote dashboard implementations remain in their four sibling plans. This plan owns only shared wiring, orchestration, release, and cross-component evidence.
- Placeholder scan: Runtime IDs and SHAs are always computed from exact commands or copied unchanged from connector results. No `TBD`, speculative endpoint, secret example, or “implement later” step remains.
- Type consistency: The integration consumes sibling exports and the remote dashboard's exact same-origin routes; it does not rename their models or add a second interface.
- External honesty: `REMOTE_LOCAL` and `REMOTE_EXTERNAL` cannot overwrite one another. Public deployment, real resources, test accounts, mail, and cleanup remain explicit approval gates.

## Execution Handoff

Plan complete and saved to `docs/superpowers/plans/2026-07-20-petcare-remote-integration-deployment.md`. Two execution options:

1. **Subagent-Driven (recommended)** — use `superpowers:subagent-driven-development`, execute Task 1 first in parallel with Todo 12, then run the disjoint sibling component tasks before Tasks 2–9 with review between atomic commits; stop at Task 10 unless external actions are approved.
2. **Inline Execution** — use `superpowers:executing-plans`, execute Tasks 1–9 in batches with exact-SHA checkpoints; request approval before Task 10 external work.
