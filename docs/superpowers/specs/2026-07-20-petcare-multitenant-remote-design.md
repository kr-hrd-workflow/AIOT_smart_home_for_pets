# PetCare Multi-Tenant Remote Access Design

## Decision Summary

- Publish the PetCare Sites application at a public URL, but require ordinary email/password authentication before any live household data, camera stream, device enrollment, or event clip is accessible.
- Use Supabase Auth as the managed identity provider. Do not store password hashes, password-reset tokens, or application sessions in PetCare code.
- Give each authenticated account one home and one active webcam in the MVP. Sharing, invitations, roles, and multiple active cameras are out of scope.
- Run an always-on PetCare home agent on the user's Windows PC or Raspberry Pi. The agent owns the USB webcam, Pico/MQTT connection, local PostgreSQL state, detection pipeline, and rule engine.
- Reach the home agent only through an outbound Cloudflare Tunnel protected by Cloudflare Access. Do not open router ports or bind the local FastAPI service publicly.
- Put a same-origin Sites backend-for-frontend (BFF) in front of live data and video. The BFF verifies the Supabase identity, resolves the caller's home, and adds the Access service credential server-side.
- Stream live video as private, non-cacheable MJPEG for the MVP and poll operational state every two seconds. Add WebRTC only when measured traffic or concurrency makes MJPEG inadequate.
- Never record continuously. Keep ten seconds of annotated frames in memory, then record twenty seconds after an eligible committed event. Upload one private event clip and delete it automatically after exactly seven days.
- Record clips for `eating`, `resting`, and `bed_sensor_mismatch`. Do not create a clip for `no_meal_12h`, because that deadline has no contemporaneous scene to record.
- Preserve the existing two behavior types, two warning anomaly types, Pico contracts, local PostgreSQL/MQTT boundaries, and `/demo` no-network rule.

## Scope Replacement

This design supersedes the earlier owner-only Sites and remote-demo-only decisions. It does not make PostgreSQL, MQTT, the webcam, or FastAPI directly public. The public surface is the Sites application; live household routes remain authenticated and tenant-scoped.

The following earlier decisions remain authoritative:

- two Pico 2 W profiles and the approved sensor map;
- exactly `eating` and `resting` behaviors;
- exactly `no_meal_12h` and `bed_sensor_mismatch` warning anomalies;
- backend-owned FSR calibration and camera fusion;
- raw video is ephemeral except for the explicitly requested event clips;
- `/demo` uses bundled demo data and makes no PetCare API, WebSocket, loopback, tunnel, or cross-origin media request.

## Approaches Considered

### 1. Supabase Auth, Cloudflare Tunnel/Access, Sites BFF, private R2 (selected)

The home agent makes only outbound connections. The browser talks only to the Sites origin. The BFF verifies identity and ownership before proxying to the correct Access-protected tunnel or serving a private clip. This reuses the current FastAPI/MJPEG implementation and keeps credentials out of the browser.

### 2. Managed WebRTC relay

WebRTC is more bandwidth-efficient and scales better across many concurrent viewers, but it adds signaling, TURN, session routing, and another operational dependency. It is the upgrade path after measured MJPEG traffic, latency, or Worker costs exceed the MVP ceiling.

### 3. Browser-published webcam

The browser can publish `getUserMedia()` without installing an agent, but monitoring stops when the tab closes or the PC sleeps. It does not satisfy always-on remote monitoring and is rejected.

Direct port forwarding, public FastAPI binding, public R2 URLs, and self-managed password storage are rejected.

## System Architecture

```text
Pico nodes ---- MQTT ----+
                         |
USB webcam -> home agent +-> local PostgreSQL
              |          +-> YOLO/rules/event commit
              |
              +-- outbound cloudflared --> Access-protected tunnel
                                               |
public browser --> Sites app/BFF ---------------+
       |              |                         |
       |              +-- D1 tenant metadata    +-- live REST/MJPEG
       |              +-- private R2 clips
       +-- Supabase email/password session
```

The home agent is the existing local backend packaged with installation, enrollment, tunnel, clip, and health management. It remains the only owner of hardware and real-time rule state.

## Authentication And Sessions

Supabase Auth owns signup, email verification, login, logout, forgotten-password, reset, session refresh, and account identity. PetCare uses the immutable JWT `sub` as `owner_sub`; email is display data only.

The browser uses Supabase's PKCE/cookie flow. Every protected Sites page and BFF route verifies the session server-side. A client-supplied `user_id`, `owner_sub`, `home_id`, `agent_id`, or object key is never trusted for authorization.

The MVP has these public routes:

- `/login`
- `/signup`
- `/forgot-password`
- `/reset-password`
- `/demo`

The operational dashboard, enrollment, live data, live video, clip list, clip playback, deletion, and account deletion require authentication. Authentication failures return `401`; a resource not owned by the caller returns `404` to avoid tenant enumeration.

Sites runtime configuration contains `SUPABASE_URL` and the Supabase publishable key. JWT verification uses the provider's public claims/JWKS path and does not require a service-role key. PetCare adds no administrative Supabase API in the MVP.

Production verification and password-reset mail require an SMTP provider configured in Supabase. Account creation, SMTP credentials, or paid upgrades are external actions and require explicit user approval before they are performed.

## Tenant And Device Model

D1 stores only central tenancy, enrollment, tunnel routing metadata, and clip metadata. Local operational readings, behaviors, anomalies, and calibration stay in each home's PostgreSQL database.

Minimum central records:

- `homes(id, owner_sub, created_at, deleted_at)` with one active home per `owner_sub`;
- `agents(id, home_id, public_key, tunnel_origin, last_seen_at, revoked_at)` with one active agent per home;
- `cameras(id, home_id, agent_id, local_camera_id, created_at, disabled_at)` with one active camera per home;
- `enrollment_tokens(id, home_id, token_hash, expires_at, consumed_at)`;
- `clips(id, home_id, camera_id, object_key, started_at, ended_at, expires_at, created_at)`;
- `clip_events(clip_id, event_type, event_id)` for coalesced event windows.

Every lookup begins with the verified `owner_sub`. Foreign identifiers are accepted only as opaque selectors and are joined back through the caller's home.

## Agent Enrollment And Revocation

1. A logged-in user requests a ten-minute enrollment code.
2. The BFF stores only a strong hash of the one-time code.
3. The home agent generates its key material locally and submits the code plus its public identity.
4. A server-side provisioner uses a scoped Cloudflare account token to create one named tunnel, hostname, and Access application for that home. The account token never leaves the server.
5. The server atomically consumes the code, binds the agent and single active camera to the caller's home, and returns the per-home connector token exactly once.
6. D1 stores the tunnel ID and origin, but never the connector token. The Sites BFF uses one server-only Access service credential accepted by every managed home tunnel.
7. Reuse, expiry, ownership mismatch, provisioning failure, or a second active agent fails without changing the current binding. A partially created tunnel is revoked before the request returns.

The device credential is separate from the user's browser session. The agent signs clip-upload requests with its locally generated private key; the BFF verifies the registered public key, timestamp, nonce, and body digest before streaming the clip into R2. The private key and connector token are stored in an ACL-restricted local runtime file, never in Git, command arguments, URLs, logs, or browser storage. Revocation immediately blocks tunnel proxying and new clip uploads.

## Live Data And Video

The BFF exposes same-origin authenticated routes under `/api/petcare/**`. It resolves the caller's active agent and proxies to its Access-protected tunnel using a server-only service token.

- Operational data uses two-second REST polling in the MVP.
- Live video uses the existing MJPEG stream through the BFF.
- The BFF enforces `Cache-Control: private, no-store, no-transform` on live and clip responses.
- The browser never receives the tunnel origin, Access token, device credential, or local address.
- The local FastAPI service remains bound to loopback; `cloudflared` reaches that loopback service on the same machine.
- An offline, revoked, timed-out, or unavailable agent returns `503 agent_offline`. The authenticated dashboard shows the last successful timestamp and never falls back silently to demo data.

The first version deliberately omits WebSocket proxying. Add it only if two-second polling fails a measured product requirement.

## Event Clip Capture

The agent retains ten seconds of annotated JPEG frames in a bounded in-memory ring. No pre-event frame is written to disk.

When a committed `eating`, `resting`, or `bed_sensor_mismatch` event arrives, the agent retains twenty additional seconds, pipes the frames into a pinned FFmpeg process, and creates one temporary H.264 MP4. The temporary file is uploaded, verified, and deleted. Failed uploads are retried from a bounded local queue with an expiry; they do not enable continuous recording.

Overlapping event windows coalesce into one clip. Each triggering event is recorded in `clip_events`, so the UI can show every reason without storing duplicate video.

`no_meal_12h` does not trigger video because its deadline does not represent a scene occurring at that time.

## Clip Storage, Retention, And Deletion

Clips are private R2 objects with random opaque keys. Email addresses, home names, event types, and user IDs are not placed in object keys. The browser receives clips only through an ownership-checked BFF response; `r2.dev` and public bucket access remain disabled.

Every clip has `expires_at = created_at + 7 days`. Reads are denied immediately after `expires_at`, even if physical deletion has not completed. R2 lifecycle cleanup removes expired objects, and a scheduled reconciliation removes stale metadata or orphaned objects.

Manual clip deletion removes access immediately and deletes both object and metadata. Account deletion revokes the agent, removes enrollment material, deletes every clip/object, and then removes the tenant registry. Local operational history remains on the user's home machine unless the user separately deletes it there.

Transport uses HTTPS/TLS. R2 provider encryption at rest is required. End-to-end encryption that hides video from the service operator is not part of the MVP.

## Trust Boundaries And Failure Handling

- No public PostgreSQL, MQTT, FastAPI, webcam port, or direct tunnel URL.
- Mutating BFF requests require a valid session and same-origin/CSRF protection.
- Signup, login, reset, enrollment, and upload endpoints are rate-limited.
- Access service credentials, tunnel credentials, SMTP credentials, and device private keys are runtime secrets only.
- Logs may contain opaque request, home, agent, camera, clip, and event IDs, but never passwords, tokens, cookies, service credentials, reset links, video bytes, or object URLs containing credentials.
- A failed database commit, failed upload, tunnel outage, or identity-provider outage must leave an explicit retryable or terminal state; it must not cross-link tenants or expose demo data as live data.

## Dashboard Behavior

The existing warm-homecare visual system remains. New surfaces are limited to:

- email/password signup, login, verification, forgotten-password, and reset screens;
- one-home enrollment status and one-time code;
- agent/camera online state and last-seen timestamp;
- live authenticated camera and operational panels;
- event clip list, playback, expiry timestamp, and delete control.

`/demo` remains a static, same-origin-assets-only showcase. It constructs no authenticated client and makes no PetCare, Supabase session, tunnel, loopback, WebSocket, or cross-origin image request.

## Validation

Required automated evidence includes:

- Supabase token/JWKS success, expiry, wrong issuer/audience, malformed token, refresh, logout, verification, and reset flows;
- anonymous protected-route rejection and public-auth-route availability;
- two-user isolation for every home, agent, camera, live route, clip route, and deletion path;
- one-time enrollment success, reuse, expiry, collision, revocation, and rollback;
- no browser-visible tunnel origin or service/device credential;
- live MJPEG non-cache headers, disconnect handling, and `agent_offline` behavior;
- exact ten-second pre-roll, twenty-second post-roll, eligible event set, no `no_meal_12h` clip, overlap coalescing, bounded memory, upload retry, and temporary-file cleanup;
- exact seven-day read denial and physical cleanup reconciliation;
- `/demo` no-client/no-network invariant;
- local PostgreSQL/MQTT loopback boundaries and existing behavior/anomaly contracts;
- browser QA for login, enrollment, online/offline, live video, clip playback/delete, mobile layouts, and accessibility.

Final manual evidence must use two distinct test accounts and prove that each account can enroll and view only its own home agent and event clips.

## Parallel Implementation Boundaries

After the implementation plan is approved, these disjoint workstreams may run concurrently:

1. home-agent packaging, enrollment client, in-memory pre-roll, FFmpeg clip assembly, upload queue, and tests;
2. Supabase auth routes/session verification and auth UI/tests;
3. D1 tenant/enrollment schema plus ownership middleware/tests;
4. private R2 clip metadata/upload/read/delete/retention APIs and tests;
5. dashboard device/live/clip components using mocked contracts;
6. Task 11 completion and the existing exact backend API contract where file ownership does not overlap.

A single integrator owns shared files such as `dashboard/package.json`, `dashboard/.openai/hosting.json`, `dashboard/worker/index.ts`, backend `main.py`, and final route wiring. Security review, code-quality review, and Ponytail over-engineering review run read-only in parallel against the same integrated commit.

Integration, exact-SHA CI, real Supabase/SMTP/Tunnel/Access/R2 setup, public Sites deployment, and final manual multi-account verification remain ordered gates because each consumes the previous gate's exact artifact or external state.

## External Prerequisites

Implementation can proceed locally with fakes and documented runtime keys, but production completion requires:

- a Supabase project with email/password enabled;
- a production SMTP provider for verification and password reset;
- Cloudflare Zero Trust Tunnel and Access service credentials;
- a scoped Cloudflare API token for per-home tunnel provisioning and revocation;
- Sites D1 and private R2 bindings;
- permission to create/update those external resources and register their runtime secrets;
- approval before any paid plan or charge is incurred.

No account, purchase, credential, public deployment, or external resource mutation is authorized merely by this design document.
