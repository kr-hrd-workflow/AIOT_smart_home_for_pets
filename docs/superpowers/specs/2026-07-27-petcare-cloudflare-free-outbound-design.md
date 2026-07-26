# PetCare Cloudflare-Free Outbound Design

**Status:** Approved direction

**Supersedes:**

- the Cloudflare Tunnel and Access transport in
  `2026-07-20-petcare-multitenant-remote-design.md`;
- only the Sites BFF-to-Home Agent transport in
  `2026-07-26-petcare-live30-activity-scroll-design.md`.

The Jetson capture, inference, private LAN TLS/HMAC, Home Agent authority,
Supabase authentication, tenant isolation, event clips, activity rules, and
landing behavior remain unchanged.

## Goal

Customers install one Windows Home Agent and connect their Pico and Jetson
products without creating a Cloudflare account, owning a domain, opening a
router port, or configuring Tailscale.

The public Sites dashboard must provide:

1. ten-minute Home Agent enrollment without `CF_*` runtime values;
2. fresh sensor, camera, activity, rest, anomaly, and service status;
3. smooth 30 FPS camera playback with a bounded two-to-three-second target
   latency;
4. the existing authenticated event clips and account cleanup behavior.

## Selected Architecture

```text
Pico -- Wi-Fi MQTT --> Home Agent
Jetson -- private TLS/HMAC --> Home Agent
                                |
                                +-- signed HTTPS snapshots --> Sites D1
                                +-- signed 1 s fMP4 parts --> private Sites R2
                                                               |
authenticated browser <--------------- Sites BFF --------------+
```

Every household connection is outbound from the Home Agent. Sites never opens
a connection to the customer's network.

No new paid service, production dependency, or customer-managed network
component is introduced. The implementation reuses the existing Supabase
session, D1 and R2 bindings, Ed25519 agent identity, nonce store, bounded
request readers, FFmpeg runtime, dashboard summary contract, and account
cleanup flow.

## Enrollment

The browser continues to issue one random ten-minute code for its authenticated
home. The Home Agent generates its Ed25519 identity locally and submits:

- the code;
- `algorithm: "Ed25519"`;
- the canonical public key;
- `local_camera_id: "pc-webcam-01"`.

Sites validates the body and rate limits before one D1 batch:

1. verifies that the code is active and unused;
2. rejects a second active agent or camera;
3. inserts the agent and camera;
4. marks the connection active in outbound mode;
5. consumes the code.

The response returns only `agent_id` and `camera_id`. It does not return a
connector token or tunnel origin. The runtime config persists the Sites origin,
agent identity, camera identity, private key, public key, and local service
settings. `cloudflared` is not started.

Code issuance, code consumption, and normal scheduled cleanup require only the
Supabase values plus existing Sites `DB` and `CLIPS` bindings.

## Signed Status Snapshots

Every two seconds, after enrollment, the Home Agent builds the existing strict
`DashboardSummary` from local state and sends it to a same-origin agent route.
The body is canonical UTF-8 JSON and is bounded to 128 KiB.

The signature contract is:

```text
PETCARE-SNAPSHOT-V1
POST
/api/petcare/agent/snapshot
<agent-id>
<timestamp-seconds>
<nonce>
<base64url-sha256-body>

```

Sites enforces:

- a registered, non-revoked Ed25519 public key;
- a timestamp inside the existing replay window;
- a canonical nonce consumed once per agent;
- exact content length and digest;
- the existing strict dashboard summary shape;
- camera and agent identities derived from D1, never from browser input.

D1 stores one latest snapshot per home, not an append-only history. An accepted
write updates `agents.last_seen_at`. A snapshot older than five seconds is
reported as `agent_offline`; the dashboard never falls back to demo data.

## 30 FPS Outbound Media

The Jetson continues to provide unique 640 x 480 JPEG frames at a target 30 FPS
to the Home Agent. Inference remains independent at its measured cadence.

The Home Agent feeds the existing live MJPEG stream into the manifest-pinned
FFmpeg binary and produces:

- H.264 constrained-baseline video;
- 640 x 480 at 30 FPS;
- one-second fragmented MP4 media parts;
- one initialization part per stream boot;
- no audio.

Completed files are uploaded only after atomic local rename. The agent signs
each body with `PETCARE-LIVE-V1`, binding the agent ID, camera ID, stream boot
ID, kind, sequence, start time, duration, content length, and SHA-256 digest.
The maximum init part is 256 KiB and the maximum media part is 1 MiB.

Sites stores parts under a server-derived private key. Agent-provided object
keys are forbidden. D1 records the current stream boot, init object, newest
sequence, and at most eight one-second media parts. On every accepted part,
Sites deletes metadata and R2 objects older than the rolling eight-second
window. Scheduled reconciliation removes abandoned parts older than one minute.

The authenticated browser requests a same-origin manifest and private part
URLs. A small native `MediaSource` player appends the init part and ordered
media parts, keeps two to three seconds behind the newest sequence, and drops
buffered data older than eight seconds. It reconnects after a stream boot
change and shows the existing offline state when no fresh part arrives for
three seconds.

Thirty FPS describes encoded frame cadence, not inference FPS. A hardware gate
must count unique decoded frames; repeated frames do not pass.

## Event Clips

Event clips keep the existing signed `PETCARE-CLIP-V1` upload and seven-day
retention. Active-agent authorization changes from “active tunnel” to
“registered, non-revoked outbound agent.” Live rolling parts and event clips
use separate paths, limits, metadata, and cleanup rules.

## Dashboard Reads

Authenticated dashboard status reads D1 directly after owner-to-home-to-agent
resolution. It returns:

- the stored strict summary;
- derived Home Agent and camera freshness;
- server-owned agent and camera IDs;
- a live manifest URL only while the stream is fresh.

The browser never receives the agent public key, private key, local address,
Jetson address, Wi-Fi password, MQTT credential, or R2 object key.

## Account and Device Cleanup

Revocation immediately rejects new snapshot, live-part, and clip uploads.
Account deletion:

1. queues the existing signed local activity cleanup command;
2. removes the latest snapshot;
3. deletes every rolling live object and its metadata;
4. retains existing clip cleanup behavior;
5. removes agent, camera, and home rows only after the local cleanup ACK.

There are no Cloudflare resources to provision, reconcile, or delete. Legacy
tunnel rows from earlier deployments are cleaned by the existing reconciliation
path until none remain; new outbound homes never create them.

## Failure Behavior

- Invalid, oversized, stale, replayed, or incorrectly signed agent requests
  fail without mutating D1 or R2.
- A snapshot upload failure leaves the last accepted summary intact and retries
  on the next two-second tick.
- A media upload failure drops that part. It does not queue unbounded video or
  block capture, inference, rules, MQTT, event clips, or status uploads.
- Stream boot or sequence discontinuity starts a new browser buffer.
- D1 success followed by R2 cleanup failure leaves a bounded reconciliation
  job; it never exposes a public object.
- Supabase failure blocks browser reads but does not stop local pet care.
- Sites outage does not stop Jetson capture, local inference, MQTT, rules,
  PostgreSQL, or event recording.

## Privacy and Resource Bounds

- Live video exists in R2 for no longer than the rolling eight-second window,
  plus a one-minute failure-cleanup ceiling.
- No continuous recording, public object URL, raw frame history, audio, or
  cloud inference is added.
- One active agent, one camera, one live producer, and one stream boot per home
  remain the MVP ceiling.
- Local media staging is bounded to twelve one-second parts and is deleted after
  upload or stream restart.
- The implementation adds no WebRTC, TURN, message broker, Durable Object, or
  new JavaScript/Python package.

## Migration and Compatibility

A forward-only D1 migration:

- rebuilds `agents` so `tunnel_origin` is nullable and adds a non-null
  `connection_mode` constrained to `outbound` or `tunnel`; migrated rows use
  `tunnel`, and new rows use `outbound`;
- adds one latest-snapshot table;
- adds live-stream and live-part metadata tables with home and expiry indexes.

Existing tunnel-enrolled homes remain readable during one release. New
enrollment is outbound-only. The Home Agent accepts the old
`connector_token` field only when loading a legacy config and never writes it
for a new config. Once production confirms no legacy active tunnels remain,
Cloudflare provisioning code and `CF_*` documentation can be deleted in a
separate cleanup commit.

## Verification

Focused tests must prove:

- codes issue and consume with only Supabase plus D1/R2;
- enrollment is atomic and returns no connector token;
- the Home Agent runs without `cloudflared`;
- snapshot signature, body, replay, tenant, and stale checks;
- strict snapshot replacement and five-second offline behavior;
- live init/part signature, ordering, size, boot, retention, and cleanup;
- authenticated manifest/part ownership and private cache headers;
- MediaSource ordering, reconnect, buffering, offline, and cleanup;
- clip authorization no longer depends on a tunnel;
- account cleanup removes snapshots and rolling media.

Component validation covers dashboard tests/build and backend focused suites.
The final candidate requires one exact-SHA full suite, CI, a public Sites
deployment, two-account isolation QA, idle/offline QA, and a Jetson hardware
gate measuring unique decoded 30 FPS through the deployed browser path.
