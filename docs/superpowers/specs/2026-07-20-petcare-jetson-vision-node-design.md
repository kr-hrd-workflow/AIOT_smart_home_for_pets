# PetCare Jetson Nano Vision Node Design Addendum

## Status And Authority

The user approved one NVIDIA Jetson Nano Developer Kit P3450, carrier revision B01, with the existing USB webcam as the PetCare LAN vision node. This addendum narrows and supersedes only the camera-capture, inference, preview, pre-roll, and H.264 assembly parts of `2026-07-20-petcare-multitenant-remote-design.md`. Authentication, tenancy, Pico MQTT, rules, PostgreSQL, signed cloud upload, R2 retention, Cloudflare Tunnel/Access, Sites, and the exact Task 12 API remain owned by the Home Agent and Sites plans.

The second Jetson is not part of the MVP. It remains an unpowered spare and creates no second node, failover protocol, replication, or fleet-management work.

## Decision Summary

- Connect one USB webcam directly to one Jetson Nano over USB.
- Run the Jetson as a private LAN vision appliance: capture, TensorRT inference, class/bounding-box annotation, 10 Hz RAM pre-roll, low-FPS preview, and temporary H.264 MP4 assembly.
- Keep the Home Agent as the system authority: Pico MQTT, sensor/camera fusion, rules, local PostgreSQL, transactional clip intent, account and device ownership, signed R2 upload, tunnel, and Sites-facing API.
- The Home Agent pulls authenticated observations and preview frames from the Jetson. The Jetson never calls PostgreSQL, MQTT, Supabase, R2, Cloudflare, or Sites.
- A committed eligible Home event sends an idempotent clip-commit request to the Jetson. The Jetson returns the completed clip only to the Home Agent; the Home Agent verifies it, puts it in the existing bounded upload queue, signs the existing `PETCARE-CLIP-V1` upload, and deletes the Jetson temporary copy.
- Record no continuous file. The Jetson keeps exactly 100 annotated JPEG buckets in RAM for ten seconds and writes a file only after an eligible committed event.
- Preserve the logical camera ID `pc-webcam-01`, 640x480 half-open geometry, three-second camera freshness, eligible event set, exact 10-second pre-roll, exact 20-second post-roll, 120-second coalescing ceiling, private clip upload, and seven-day cloud retention.
- Flash NVIDIA's official Jetson Nano JetPack 4.6.1 SD-card image, then use the NVIDIA R32 APT path to reach JetPack 4.6.6 / L4T 32.7.6 and the JetPack 4.6 TensorRT 8.2.1 generation. Do not install the Home Agent's Python 3.12, current Ultralytics runtime, FastAPI, Supabase, or cloud toolchain on the Jetson.

## Hardware And Software Ceiling

NVIDIA's archive lists JetPack 4.6.6 with L4T 32.7.6 as the Jetson Nano line's archived release. JetPack 4.6.6 is a security-update release on the 4.6 line, whose supported TensorRT generation is 8.2.1. The implementation therefore treats this as a hard compatibility ceiling rather than trying to upgrade the Nano into the current Home Agent environment.

Authoritative references:

- [NVIDIA JetPack archive](https://developer.nvidia.com/embedded/jetpack-archive)
- [Jetson Linux 32.7.6 release notes](https://developer.nvidia.com/downloads/embedded/L4T/r32_Release_v7.6/Jetson_Linux_Driver_Package_Release_Notes_R32.7.6_GA.pdf)
- [JetPack 4.6.2 component versions, including TensorRT 8.2.1](https://developer.nvidia.com/embedded/jetpack-sdk-462)
- [Jetson Nano Developer Kit setup](https://developer.nvidia.com/embedded/learn/get-started-jetson-nano-devkit)

The selected operating profile is:

- Jetson Nano P3450 B01, one unit only;
- JetPack 4.6.6 / L4T 32.7.6;
- stock JetPack Python 3 and `python3-libnvinfer`, system NumPy/OpenCV/GStreamer bindings only;
- TensorRT engine built and smoke-tested on this exact Jetson/TensorRT runtime from a pinned ONNX export;
- one UVC USB webcam at 640x480;
- wired Ethernet with a DHCP reservation;
- regulated center-positive 5 V, 4 A barrel supply on J25 with J48 jumpered;
- a 5 V PWM fan on the B01 fan header;
- microSD for the OS and program, RAM for pre-roll, and a bounded private directory for event-only temporary MP4 files.

No TensorRT engine built for another TensorRT version or another GPU is accepted. The source model remains the repository-pinned `yolo11n.pt`; the Jetson consumes only its pinned ONNX export and locally generated TensorRT engine.

## Architecture And Ownership

```text
USB webcam
    |
    v
Jetson Nano vision node (private wired LAN)
  - 640x480 capture
  - TensorRT inference
  - class/bbox annotation
  - latest observation + 2 FPS preview
  - 100-frame RAM ring at 10 Hz
  - event-only H.264 MP4 temp file
    |
    | pinned TLS + HMAC request authentication
    v
Home Agent (Windows PC)
  - camera contract validation and zone assignment
  - Pico MQTT and sensor ingestion
  - eating/resting/bed mismatch fusion and rules
  - PostgreSQL event + clip-intent transaction
  - Jetson clip commit/download/ack
  - separate fast clip-admission worker, media-delivery worker, and concrete bounded local upload queue
  - PETCARE-CLIP-V1 Ed25519 upload to Sites BFF/R2
  - loopback FastAPI and outbound Cloudflare Tunnel
    |
    v
Authenticated Sites dashboard and private R2 clips
```

The Jetson does not become a second Home Agent. It has no tenant database, user session, Cloudflare connector, public hostname, R2 credential, Supabase key, MQTT credential, or Pico connection.

The one-time Jetson pairing bundle is imported by `python -m app.agent_runtime pair-jetson` into owner-only Home certificate, PSK, and strict Jetson config files. The deployed Windows service registry or Linux systemd unit passes exactly `PETCARE_CAMERA_SOURCE=jetson` and `PETCARE_JETSON_CONFIG=<absolute owner-only config>` to the Home supervisor child. Runtime startup fails closed before launching the backend when that file, its certificate/PSK references, or ACLs are invalid; neither secret value is placed directly in the environment.

## Preserved Camera And Rule Contracts

The Home Agent remains the compatibility boundary for current code:

- `camera_id` remains exactly `pc-webcam-01`.
- persisted and API detection geometry remains 640x480 with half-open boxes.
- recognized classes remain exactly `person`, `dog`, and `cat`.
- the Home Agent derives `subject_id`, `center_x`, `center_y`, and `zone_name` using the current `SUBJECTS` and `zone_for_center` logic. The Jetson is not authoritative for PetCare zones.
- an authenticated observation must arrive at least once per UTC second for `CameraService.available_for` to remain true.
- camera evidence is fresh only for the existing three-second TTL. No timestamp is rewritten on the Home Agent to make stale Jetson evidence appear fresh.
- the Jetson `observed_at` is the actual capture timestamp in UTC. The Home Agent rejects future timestamps and observations older than three seconds.
- the live route remains `/api/video_feed`. The Home Agent converts the latest authenticated Jetson preview JPEG into the existing MJPEG chunk; the browser never receives a Jetson URL.
- camera failure leaves the current sensor ingestion, MQTT worker, database, and API running. Camera-dependent rule state follows existing `camera_loss` and unavailable semantics.

## LAN Protocol

### Transport And Identity

The Jetson listens on exactly one configured RFC1918 Ethernet address at TCP 9443. It must reject loopback, wildcard, multicast, link-local, and public bind addresses. The host firewall permits that port only from the configured Home Agent IPv4 address. No router port forward, UPnP rule, public DNS record, Cloudflare tunnel, Wi-Fi listener, or `0.0.0.0` listener is created for the Jetson.

HTTPS is mandatory. The Jetson uses a locally generated self-signed device certificate with an IP subjectAltName. The Home Agent installs that exact certificate as its sole Jetson trust anchor and verifies the configured IP hostname; `verify=False` is forbidden. This certificate pin identifies the Jetson. A separate random 32-byte pre-shared key authenticates the Home Agent and is stored in owner-only runtime files on both machines.

Every request uses these exact headers:

```text
X-PetCare-Jetson-Version: PETCARE-JETSON-V1
X-PetCare-Jetson-Boot-Id: <32 lowercase hex characters, or bootstrap for GET /v1/status>
X-PetCare-Jetson-Timestamp: <decimal Unix seconds>
X-PetCare-Jetson-Nonce: <22-character unpadded base64url for 16 random bytes>
X-PetCare-Jetson-Content-SHA256: <64 lowercase hex characters>
X-PetCare-Jetson-Signature: <43-character unpadded base64url HMAC-SHA256>
```

The HMAC input is UTF-8 and includes its final newline:

```text
PETCARE-JETSON-V1
<METHOD>
<path and canonical query>
<boot-id>
<unix-seconds>
<nonce>
<lowercase body sha256>

```

The Jetson accepts a request only when TLS verification succeeded, the body digest matches, the timestamp skew is at most 30 seconds, the boot ID matches, the nonce has not appeared during the current boot, and the HMAC matches under constant-time comparison. The in-memory nonce cache retains accepted nonces for 120 seconds. A random boot ID invalidates every pre-restart request. `GET /v1/status` alone uses `bootstrap` in the signed request and returns the current boot ID.

Each state-changing clip command uses a Home-generated random 32-character lowercase hexadecimal `command_id` in the resource path. The Jetson stores the command ID, canonical body digest, immutable admission receipt, state, event set, accepted time, media metadata, and bounded tombstone in an owner-only SQLite database from the Python standard library. While a command is recording, finalizing, or ready, repeating the same command and body returns HTTP `200` with the original immutable admission receipt; reusing a command ID with a different digest returns `409 command_conflict`. Delivered, expired, or restart-lost commands return `410 clip_gone`. This persists lost-response idempotency across restart without persisting pre-roll frames. Recording/finalizing commands interrupted by restart become `gone`; ready commands remain downloadable when their verified MP4 remains. Deleted/gone tombstones remain for 24 hours, are pruned before admission, and the database holds at most 1024 command rows.

### Endpoints

The Jetson exposes exactly six authenticated operations across four resource paths:

| Method | Path | Success | Purpose |
|---|---|---:|---|
| `GET` | `/v1/status` | `200` | boot ID, signed `server_time`, camera state, clip state, versions, temperature and throttling flags |
| `GET` | `/v1/observations?after=<sequence>&wait_ms=1000` | `200` or `204` | long-poll the next fresh inference observation |
| `GET` | `/v1/preview.jpg` | `200` | latest annotated JPEG, limited globally to 2 FPS for the single paired Home |
| `PUT` | `/v1/clips/<command_id>` | `201` or `200` | idempotently start/coalesce a new command or replay its existing result |
| `GET` | `/v1/clips/<command_id>` | `200` | fetch the command's ready MP4 |
| `DELETE` | `/v1/clips/<command_id>` | `204` | acknowledge durable Home receipt and remove the temporary MP4 |

Unknown paths return `404`. Unsupported methods return `405`. Validation failures return a strict JSON object with exactly `code` and `message`. Timestamp, nonce, boot-ID, digest, and HMAC failures are distinct internal reasons but all map externally to exactly `401 {"code":"unauthorized","message":"Unauthorized"}` and are never logged with supplied credential values. A reused command ID with different bytes returns `409 command_conflict`; a correctly signed first PUT received more than three seconds after `committed_at` returns `409 command_expired` without creating a command. Capacity or temporary camera failures return `503`. A clip that is still recording returns `425 clip_not_ready`; a clip invalidated by restart or expiry returns `410 clip_gone`.

`GET /v1/preview.jpg` returns raw JPEG with exactly these headers:

```text
Content-Type: image/jpeg
Content-Length: <decimal integer from 1 through 1048576>
Cache-Control: private, no-store, no-transform
X-PetCare-Jetson-Boot-Id: <32 lowercase hex characters>
X-PetCare-Jetson-Sequence: <unsigned decimal integer>
X-PetCare-Jetson-Observed-At: <UTC ISO-8601 with six fractional digits and Z>
X-PetCare-Jetson-Content-SHA256: <64 lowercase hex characters>
```

No other `X-PetCare-Jetson-*` preview response header is allowed. “Exactly these headers” means this exact application-header set; standard transport headers `Date` and `Connection: close` may also be present and are ignored. `Server`, `Transfer-Encoding`, `Content-Encoding`, and every other application header are forbidden. The Home Agent bounds the body before reading, verifies length and SHA-256, decodes it as JPEG, and accepts only one three-channel 640x480 image. A malformed, oversized, stale, wrong-boot, or wrong-sequence preview is discarded without replacing the last valid MJPEG frame.

### Observation Schema

`GET /v1/observations` returns exactly:

```json
{
  "boot_id": "0123456789abcdef0123456789abcdef",
  "sequence": 42,
  "observed_at": "2026-07-20T04:00:00.100000Z",
  "width": 640,
  "height": 480,
  "fps": 4.8,
  "inference_ms": 191.2,
  "detections": [
    {
      "detected_type": "dog",
      "confidence": 0.94,
      "bbox_x": 100,
      "bbox_y": 80,
      "bbox_width": 220,
      "bbox_height": 260
    }
  ]
}
```

Fields are strict and extras are rejected. `sequence` is an unsigned monotonically increasing integer within one boot. There is at most one detection per recognized class, selected with the current confidence-and-geometry ordering. The Home Agent computes center, subject, and zone, constructs the existing `CameraDetectionIn`, persists it through the existing camera transaction, and only then resolves `CameraFrameCommitted` into rule ingress.

### Clip Commit And Delivery Schema

The Home Agent first stores a random command ID in the durable outbox state, then sends one committed event with `PUT /v1/clips/<command_id>`:

```json
{
  "committed_at": "2026-07-20T04:00:00.000000Z",
  "event_type": "eating",
  "event_id": 41,
  "occurred_at": "2026-07-20T03:59:30.000000Z"
}
```

Allowed event types are exactly `eating`, `resting`, and `bed_sensor_mismatch`. `no_meal_12h` is rejected. The first accepted PUT returns HTTP `201`; an identical replay returns HTTP `200`. Both use exactly:

```json
{
  "accepted_boot_id": "0123456789abcdef0123456789abcdef",
  "command_id": "0123456789abcdef0123456789abcdef",
  "state": "recording",
  "accepted_at": "2026-07-20T04:00:00.000000Z"
}
```

`accepted_boot_id`, `command_id`, `state`, and `accepted_at` form the immutable admission receipt. `accepted_at` is the Jetson wall-clock timestamp captured at the first PUT's actual socket receipt; it is not a sampler bucket time. At that same instant the service captures `accepted_monotonic_ns`, and the first 10 Hz sampler boundary at or after that monotonic instant becomes `trigger_bucket`. Wall-clock adjustments can therefore neither move nor lengthen the media window.

The Home Agent sets signed `committed_at` from outbox `created_at`, which is explicitly stamped by the guarded Home process clock immediately before the event/outbox transaction commit rather than by a separate database-server clock; commit latency consumes the admission window. Both Home and Jetson processes maintain a 100 ms wall-clock-versus-monotonic discontinuity guard; a delta change over 25 ms disables first admission for 60 seconds. Immediately before every first PUT, the Home admission worker performs a signed `/v1/status` round trip and calculates the Jetson-minus-Home midpoint offset and half-RTT uncertainty. The sample must be at most one second old at Jetson receipt and `abs(offset) + half_RTT + 50 ms drift/step budget <= 200 ms`. The Jetson accepts a first PUT only when its direct wall-clock comparison satisfies `-0.200 seconds <= accepted_at - committed_at <= 2.800 seconds`; otherwise it returns `409 command_expired` without creating a command. The fresh calibration, local discontinuity guards, and reserved 200 ms error budget preserve a real elapsed ceiling of three seconds even under bounded slew. Idempotent replay uses the stored receipt and never reruns this age test. Pairing and the hardware soak must prove the bound; an absent/stale calibration or clock discontinuity is camera/clip degraded, never silently accepted.

The clip window is based on monotonic `trigger_bucket`, matching the approved Home plan's recorder-acceptance rule. `occurred_at` remains the persisted domain label and is not the media anchor; an eating/resting event can legitimately have started before the rule commits it. The Jetson owns the 100 buckets `[trigger_bucket - 100, trigger_bucket)` and the 200 buckets `[trigger_bucket, trigger_bucket + 200)`. A trigger at +15 seconds coalesces and yields 450 frames / 45 seconds. Coalescing is allowed only while the post-roll is open and only when the extended clip remains at most 120 seconds. Otherwise the Jetson returns `503 clip_busy`; the fast Home admission worker retries only inside the same real three-second admission window.

When ready, `GET /v1/clips/<command_id>` returns raw `video/mp4` with exactly these headers:

```text
Content-Type: video/mp4
Content-Length: <decimal integer from 1 through 268435456>
X-PetCare-Jetson-Boot-Id: <32 lowercase hex characters>
X-PetCare-Jetson-Command-Id: <the requested 32-character lowercase hex command ID>
X-PetCare-Jetson-Content-SHA256: <64 lowercase hex characters>
X-PetCare-Jetson-Started-At: <UTC ISO-8601 with six fractional digits and Z>
X-PetCare-Jetson-Ended-At: <UTC ISO-8601 with six fractional digits and Z>
X-PetCare-Jetson-Events: <event_type:event_id values sorted by type then decimal ID, comma-separated, unique>
X-PetCare-Jetson-Frame-Count: <decimal integer from 1 through 1200>
X-PetCare-Jetson-Video-Codec: h264
X-PetCare-Jetson-Pixel-Format: yuv420p
```

No other `X-PetCare-Jetson-*` response header is allowed. This is the exact application-header set; standard `Date` and `Connection: close` may also be present and are ignored, while `Server`, `Transfer-Encoding`, `Content-Encoding`, and every other application header are forbidden. The Home media-delivery worker streams the body into an ACL-restricted partial file, verifies length and digest, and uses retained validation-only ffprobe only for media facts: one H.264 video stream, YUV420P, 640x480, 10/1 frame rate, duration, and frame count. Event identity and Home event ownership are not media facts: command/event headers must match accepted PostgreSQL rows. Time headers must be canonical and ordered, contain the stored `accepted_at`, and have a duration equal to header frame count / 10 within 100 ms; ffprobe duration/frame count must independently agree with those header values within the same tolerance. Exact media start/end bucket timestamps are Jetson-owned and are not compared to nonexistent Home bucket rows.

The worker derives a stable queue ID from version, accepted boot ID, sorted event set, and content SHA-256; atomically creates an unreleased queue item whose sidecar includes every included remote command ID; then calls `DELETE`. After DELETE returns 204 or idempotent 410 it first marks every included outbox row processed in one database transaction, then releases the item for cloud upload. On restart it reconciles an unreleased item by remote command ID before interpreting 410: processed rows cause release only; unprocessed rows retry/idempotently accept DELETE, commit processed state, then release. Thus crashes after durable enqueue, DELETE, or database commit cannot create a second queue item, upload before database acknowledgement, or lose the first. Deleting any command for a coalesced clip removes the one Jetson media file and marks every command associated with that clip delivered.

The existing cloud upload remains byte-for-byte `PETCARE-CLIP-V1`; Jetson headers and HMAC never cross the Sites boundary.

## Jetson Clip State Machine

```text
IDLE
  | eligible commit + 100 RAM buckets available
  v
RECORDING -- eligible overlap <= 120 s --> RECORDING (extend end/event set)
  | exact post-roll collected
  v
FINALIZING -- GStreamer EOS/hash/metadata success --> READY
  |                                      |  authenticated GET (no state change)
  | failure                              +---------------------> READY
  v                                      |
FAILED                                   | authenticated DELETE
  |                                      v
  +-- cleanup -> IDLE                  DELIVERED tombstone
                                           |
                                           +-- prune after 24 h -> IDLE
```

Only one clip may be `RECORDING` or `FINALIZING`. At most two ready temporary clips, 256 MiB total, may exist. A ready clip expires one hour after finalization. `.partial.mp4` is created mode `0600`, atomically renamed after successful muxing, and removed on every encoder error, shutdown, expiry, or validation failure. Owner-only SQLite contains command metadata and bounded tombstones only; it contains no frame or video bytes. No pre-roll JPEG is written to disk.

## Failure And Fallback Behavior

- Home-to-Jetson connect timeout is 1 second; ordinary read timeout is 2 seconds; observation long-poll timeout is 2 seconds; MP4 download timeout is 45 seconds. Clip admission has its own fast worker and never shares a thread, queue, semaphore, or connection slot with download, ffprobe, DELETE, or cloud upload.
- There is at most one observation poll, one preview fetch, and one clip transfer in flight. Status/observation/preview and already accepted command polling retry after 1, 2, 4, 8, 16, then 30 seconds with the 30-second cap.
- After three seconds without a valid fresh observation, the Home camera status is `offline` with a bounded reason. Sensors, MQTT, PostgreSQL, rule scheduling, dashboard polling, and the tunnel continue.
- The Home Agent never substitutes demo frames, stale detections, or a silently different local model. Camera-dependent behavior follows current unavailable/camera-loss semantics.
- An accepted recording lost by Jetson restart is persistently marked gone and returns `410`. The Home Agent assigns a new random command ID and retries the still-unprocessed intent only if the three-second admission deadline has not elapsed; otherwise it records a terminal secret-free `clip_missed` reason while preserving the behavior/anomaly row.
- A new event intent tries immediately and once more after one second, but must be accepted within three seconds of its Home outbox `created_at`. After that, no later scene is labeled as the missed event.
- Cloud or tunnel outages do not affect Jetson capture/inference. Completed clips already copied to the Home Agent use the concrete persistent eight-item, one-hour `ClipUploadQueue`; that queue owns its files after an atomic enqueue and alone retries signed cloud upload. The Jetson never retries a cloud upload.
- If the hardware performance gate fails, the supported fallback is sensor-only operation with explicit camera-offline status while the existing PC camera path remains available for development fixtures. Automatic cross-machine CPU inference is out of scope.

## Security And Privacy

- The Jetson has no public route and no cloud credential.
- TLS private key, CA material, HMAC PSK, model engine, and temporary clips are owner-readable only. Secrets never appear in argv, environment dumps, URLs, health payloads, logs, or exception text.
- Logs contain only boot, sequence, clip, request, and bounded error codes. They contain no JPEG/MP4 bytes, PSK, signature, certificate private key, cloud key, user ID, email, or object URL.
- The Home Agent validates all Jetson JSON and media as untrusted input before persistence or upload.
- The Home Agent's authenticated Sites and tenant boundary remains unchanged. Browser requests can never choose a Jetson address, boot ID, clip path, or Home-side event ID.

## Performance, Thermal, And Hardware Acceptance Gate

The Jetson is not promoted from development fixture to the production camera source until one current-run 60-minute soak with the real USB webcam and final TensorRT engine proves all of these:

- 640x480 capture remains active for 60 minutes with no reconnect or invalid frame;
- inference throughput is at least 3.0 FPS and p99 valid-observation gap is at most 1.0 second;
- Home-observed end-to-end camera age p99 is at most 1.5 seconds and never exceeds the three-second rule TTL while healthy;
- preview is capped at 2 FPS and remains usable through the existing `/api/video_feed` route;
- one default clip is 300 frames / 30.000 seconds and one +15-second overlap clip is 450 frames / 45.000 seconds, both H.264/YUV420P and within 100 ms by ffprobe;
- the RAM ring never exceeds 100 JPEGs; temporary disk never exceeds two clips or 256 MiB; no file exists before a trigger;
- `boot_id` from `/proc/sys/kernel/random/boot_id` is unchanged for the whole soak; `sudo jetson_clocks` is active during the load interval; one-second `tegrastats`, CPU/GPU current-clock samples, and before/after kernel logs show no locked-clock drop or new line matching `soctherm|throttl|OC ALARM|under.?voltage|vdd.*fail`; the measured module temperature stays below the configurable 80 C guard; and the service recovers after webcam unplug/replug. Nano has no Raspberry Pi-style undervoltage bit, so unexpected reboot, those kernel indications, or a locked-clock drop is a gate failure;
- a fresh signed status clock calibration precedes every first PUT, is at most one second old, satisfies `abs(Jetson-minus-Home midpoint offset) + half RTT + 50 ms drift budget <= 200 ms`, and neither host's wall/monotonic guard reports a step;
- disconnecting the Jetson makes only the camera degraded/offline; Pico MQTT and sensor APIs continue passing.

If the gate fails, first lower TensorRT input size or detector frequency while keeping 640x480 contract output and the three-second TTL. Do not weaken security, clip timing, data validation, or sensor independence to pass the gate.

## Existing Plan Supersession Map

The following parts of `2026-07-20-petcare-home-agent-clips.md` are superseded:

| Existing item | New authority |
|---|---|
| Task 2 `FrameRing` on Home | Jetson RAM ring; Home retains shared clip metadata/value objects only |
| Task 4 Home 10 Hz sampler, FFmpeg writer, local `ClipRecorder` | Jetson sampler and GStreamer hardware H.264 writer; Home retains only verified download and bounded cloud-upload queue |
| Task 5 camera-to-recorder latest-frame callback | Jetson observation adapter plus transactional event intent; no Home frame callback |
| Task 5 dispatcher calling `ClipRecorder.on_trigger` | old dispatcher is removed; Task 6 `ClipAdmissionWorker` alone owns idempotent PUT/admission state and Task 6 `ClipDeliveryWorker` alone owns GET/validation/unreleased queue handoff/DELETE/reconciliation |
| Task 6 lifecycle `latest_frame_sink` and recorder start/stop | lifecycle starts/stops Jetson client, separate admission and delivery workers, and concrete upload queue through the exact `start_agent_components` / `stop_agent_components` API; there is no replacement frame hook |
| Task 7 Home FFmpeg/ffprobe arm64 and Windows runtime requirement | remove Home FFmpeg encoding but retain ffprobe for untrusted MP4 validation; Jetson uses JetPack GStreamer, while Home keeps Python/cloudflared/ffprobe tooling |
| Task 10 local FFmpeg smoke | Jetson hardware clip/thermal soak plus Home integration smoke |

Home enrollment, Ed25519 cloud identity, signed `PETCARE-CLIP-V1` upload, ACL files, upload retry/expiry, Windows service, optional Raspberry Pi Home service, and loopback/tunnel verification remain required.

In `2026-07-20-petcare-remote-integration-deployment.md`, Integration Task 2 must delete its old recorder/`latest_frame_sink` assumptions and consume only the revised Jetson-aware lifecycle start/stop API. Integration Task 4 must add the separate `PETCARE-JETSON-V1` fixture while preserving the current `petcare-agent-wire-v1.json`. All auth, D1/R2, browser, CI, documentation, deployment, and two-account gates remain unchanged.

## Validation

Automated evidence must prove:

- exact HMAC canonicalization, digest, 30-second skew, nonce replay rejection, boot-ID restart rejection, persistent SQLite command idempotency/conflict/tombstone bounds, TLS verification, private-address binding, and source-IP firewall contract;
- actual first-request `accepted_at`, monotonic trigger anchoring, signed clock calibration, harmless negative clock skew, real three-second admission rejection, and replay reuse of the immutable receipt;
- strict observation and clip JSON schemas, sequence rules, timestamp freshness, 640x480 geometry, class selection, Home-derived subject/center/zone, and unchanged `CameraDetectionIn` persistence;
- exact 100/200 frame windows, +15-second 450-frame overlap, 120-second cap, `no_meal_12h` rejection, duplicate request idempotency, partial cleanup, ready-file limits, and one-hour expiry;
- Home transaction/outbox recovery through lost responses, Jetson restart, 425, 410, 503, digest mismatch, truncated MP4, and queue saturation;
- separation of fast clip admission from slow download/ffprobe/cloud work, plus the concrete persistent eight-item/one-hour upload queue's ownership and restart behavior;
- camera-offline isolation from Pico MQTT, rules that do not require camera, PostgreSQL, Task 12 API, and Sites tunnel;
- existing `PETCARE-CLIP-V1` golden vector and seven-day R2 behavior remain unchanged;
- no public Jetson listener, no browser-visible Jetson URL/secret, and no cloud secret on the Jetson;
- the real 60-minute performance/thermal gate on P3450 B01 with 5 V/4 A barrel power, J48, fan, wired Ethernet, and the real USB webcam.

## Explicitly Skipped

Two Jetsons, camera failover, CSI cameras, multiple cameras, audio, continuous recording, WebRTC, direct browser-to-Jetson access, Jetson-hosted Home Agent services, Kubernetes, Docker on Nano, DeepStream, Triton, gRPC, message brokers, cloud credentials on Jetson, and automatic PC CPU fallback are not part of the MVP. Add one only after a measured requirement makes the additional operating burden necessary.
