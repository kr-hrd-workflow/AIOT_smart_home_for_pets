# PetCare Live 30 FPS, Activity, and Smooth Scroll Design

**Status:** Approved implementation direction; scrub timing revised to 1,000 ms after production review
**Extends:**

- `2026-07-20-petcare-jetson-vision-node-design.md`
- `2026-07-20-petcare-remote-dashboard-design.md`
- `2026-07-23-petcare-photoreal-scroll-world-design.md`

## Goal

Deliver four connected improvements without replacing the released architecture:

1. show a real 640 x 480 camera stream that targets 30 unique frames per
   second from the Jetson through the Home Agent and authenticated Sites BFF;
2. keep TensorRT object inference independent from live display cadence;
3. show camera-observed activity time next to the existing fused rest time and
   raise one conservative, non-medical repeated-movement warning;
4. make the accepted Higgsfield landing video advance smoothly for about
   1,000 milliseconds after each native scroll update, then remain paused.

## Design Read

This is a preserve-mode redesign of a photoreal consumer landing page plus a
trust-first home operations dashboard. The landing uses
`DESIGN_VARIANCE 7 / MOTION_INTENSITY 7 / VISUAL_DENSITY 3`. The dashboard
keeps its existing information architecture and uses crisp, short feedback
without decorative perpetual motion.

## Considered Approaches

### A. Existing MJPEG media plane plus existing detector geometry

Use the installed OpenCV, TensorRT, HTTPS, HMAC, FastAPI, and Sites streaming
paths. Split camera capture from inference, add a private Jetson MJPEG endpoint,
proxy it through the Home Agent, and derive activity from the existing dog and
cat bounding-box centers.

This is selected. It adds no production dependency, preserves the current
security boundary, and gives the browser unique live frames even while
inference remains near the measured 3-5 FPS range.

### B. H.264 or WebRTC plus pose or temporal-action inference

This could reduce bandwidth and support richer behavior classification, but it
requires a new transport, signaling or segmenting logic, browser compatibility
work, model training data, and a Jetson Nano performance budget that is not yet
proven.

This is deferred until measured MJPEG bandwidth or a validated behavior model
justifies the added system.

### C. Cloud video analysis

Uploading continuous camera video could provide larger models, but it adds
privacy exposure, recurring cost, latency, and a new availability dependency.

This is rejected for the current release.

## Architecture

### Jetson capture and inference

`VisionNode` owns three independent loops:

- **capture loop:** reads the configured V4L2 webcam at 640 x 480 with MJPEG and
  a requested 30 FPS, renders a JPEG with the most recent valid detections, and
  publishes only the newest live frame;
- **inference loop:** consumes the newest unprocessed raw frame, drops
  superseded frames, runs the existing TensorRT YOLO model, and publishes the
  existing strict observation plus its sequence-matched preview JPEG;
- **clip sampler:** samples the newest live JPEG into the existing 100 ms
  buckets. Event clips remain 10 FPS with the existing pre-roll and post-roll
  rules.

The latest-frame buffers are bounded overwrite slots. Camera capture never
queues frames behind inference. A slow browser or disconnected client never
blocks capture or inference.

The existing observation `fps` field remains inference FPS. It is not relabeled
as live FPS. The live-stream hardware gate measures live frame uniqueness
directly.

### Jetson live endpoint

Add authenticated `GET /v1/live.mjpeg`.

- TLS certificate pinning and the existing HMAC request signature remain
  mandatory.
- The endpoint returns
  `multipart/x-mixed-replace; boundary=frame`,
  `Cache-Control: private, no-store, no-transform`, and the current Jetson boot
  ID.
- Each part contains exactly one bounded JPEG and sequence and observation-time
  headers.
- A client waits for a sequence newer than the last emitted sequence. It never
  repeats an unchanged frame to manufacture 30 FPS.
- One live stream is admitted at a time. A second stream receives the existing
  camera-unavailable response and can retry through the Home Agent.
- Disconnect, shutdown, camera loss, and broken-pipe paths release the stream
  admission slot.

The existing `/v1/preview.jpg` endpoint and its 2 FPS admission behavior stay
unchanged because it is coupled to inference observations.

### Home Agent stream proxy

`JetsonVisionClient` gains a fourth dedicated connection for the live stream.
Control, clip admission, and clip media connections retain their existing
isolation.

For a Jetson camera source, `/api/video_feed` passes the validated multipart
byte stream through without decoding or re-encoding. For local USB or fixture
sources, the existing Home Agent MJPEG behavior remains available.

The Home Agent validates the upstream status and content type before returning
the first byte. It closes the Jetson response when the browser disconnects.
The authenticated Sites BFF continues to use its current streaming proxy, so no
new browser-visible endpoint or public Jetson address is introduced.

## Activity Estimate

### Persistence

Add `activity_observations` with one mutable bucket per camera, pet subject, and
UTC second:

- camera ID;
- subject ID;
- observed UTC second;
- latest bounding-box center;
- whether any valid transition in that second was moving;
- maximum observed transition distance in pixels.

The unique key is `(camera_id, subject_id, observed_at)`. Dog and cat remain the
only subject types. Person detections are not treated as pet activity.

For every persisted dog or cat detection, the Home Agent compares its center
with the latest preceding bucket for that subject:

- the gap must be greater than zero and no more than 3 seconds;
- Euclidean center travel of at least 24 pixels at 640 x 480 is moving;
- same-second updates keep the latest center and OR the moving flag;
- a camera gap does not become still time.

This is an observation estimate, not a continuous pedometer. The UI always
shows observed coverage beside activity time.

### Dashboard contract

`DashboardSummary` gains a fixed dog-then-cat `activity` list. Each entry has:

- `subject_id`;
- `today_active_seconds`;
- `today_observed_seconds`;
- `current_state`: `active`, `still`, or `unknown`;
- `last_observed_at`.

Today uses the existing Asia/Seoul local-day boundary. Activity seconds cannot
exceed observed seconds. `current_state` is `unknown` when no observation is
fresh within 3 seconds.

The dashboard adds a summary value named `오늘 활동 추정` and the detail
`카메라 관측 N분 기준`. If no pet was observed, it displays `관측 없음`
instead of `0분 활동`. Existing `오늘 휴식 추정` remains unchanged and is
shown alongside activity.

## Conservative Repeated-Movement Warning

The release adds one anomaly type, `repetitive_motion`.

The detector evaluates the most recent 120 seconds of per-second observations
for one pet and emits a warning only when all conditions hold:

- at least 30 observed seconds;
- at least 12 moving transitions;
- at least 640 pixels of accumulated valid travel;
- at least 6 strong direction reversals, where consecutive normalized movement
  vectors have a dot product at or below -0.6.

Warnings are deduplicated per pet in 15-minute windows. They do not trigger
event clips in this release because the rule is an uncalibrated camera estimate.

Visible wording is:

> 짧은 시간에 반복 이동이 관측됐습니다. 건강 판단이 아닌 카메라 관측
> 알림입니다.

The product does not claim pain, seizure, fall, anxiety, diagnosis, or medical
certainty. A hardware soak must calibrate the thresholds before this warning is
described as more than an observation signal.

## Landing Motion

The selected Higgsfield video and native page scroll remain the single source
of the landing journey.

On every native scroll update:

1. calculate the exact video time from current document progress;
2. retarget from the video's actual current time, not from the previous target;
3. use the existing `playbackFrame` requestAnimationFrame slot to interpolate
   toward the target for 1,000 ms with an interruptible ease-out curve;
4. if more scroll arrives, keep the current visual position and retarget;
5. on completion, assign the exact target time once and cancel the frame;
6. keep the media paused throughout and while idle.

There is no `wheel` listener, `preventDefault`, `scrollTo`, chapter snap,
autoplay, idle drift, synthetic shake, or new animation dependency.

Reduced motion, data saver, decode failure, and poster fallback remain static.
Reverse scroll uses the same interpolation and reaches earlier video time.

## Security and Privacy

- Jetson live video remains private TLS plus HMAC traffic to the Home Agent.
- The browser receives video only after the existing Sites authentication,
  tenant, active-agent, and origin checks.
- No camera URL, PSK, Jetson certificate, Wi-Fi credential, Supabase secret, or
  Home Agent secret is bundled into the dashboard.
- MJPEG responses are private and non-cacheable at every hop.
- Activity rows contain coordinates and timestamps only. No new continuous
  video recording is introduced.
- Existing account deletion and retention paths must include the new activity
  table.

## Failure Behavior

- Camera open or read failure marks the Jetson camera offline and ends the live
  stream.
- Inference failure does not invent detections and does not block new live
  frames.
- Home Agent upstream validation failure returns the existing
  `camera_unavailable` response before streaming.
- A mid-stream disconnect closes the upstream response and permits reconnect.
- Missing activity coverage displays unknown, not zero activity.
- Database rollback cannot leave an activity row or anomaly partially
  committed.
- Landing media failure falls back to the approved poster and semantic HTML.

## Quality Gates

### Focused and component

- Landing fake-RAF tests prove intermediate movement, retargeting, reverse
  movement, exact settlement, idle pause, cleanup, reduced motion, and data
  saver.
- Jetson stdlib tests prove capture/inference decoupling, frame dropping,
  unique MJPEG parts, one-stream admission, disconnect cleanup, and unchanged
  preview and clip semantics.
- Home Agent tests prove fourth-connection isolation, signed streaming request,
  header validation, passthrough, disconnect cleanup, and local-source
  compatibility.
- Backend tests prove migration constraints, one-second aggregation, gap and
  distance thresholds, coverage-aware summaries, deterministic ordering,
  repeated-motion thresholds, deduplication, and non-medical wording.
- Dashboard and BFF tests prove strict activity and anomaly contracts and
  authenticated stream passthrough.

### Final candidate

- One backend full suite, one Jetson full suite, one dashboard full suite,
  lint, production build, and one E2E pass at the exact candidate SHA.
- Jetson hardware verifies a 640 x 480 webcam mode capable of 30 FPS.
- A 60-minute soak proves at least 30 unique live frames per second, inference
  at least 3 FPS, observation freshness no more than 3 seconds, 10 FPS clip
  buckets, no thermal throttling, temperature below 80 C, reconnect after
  disconnect, and clean shutdown.
- Anonymous Sites landing and demo remain public. Dashboard and camera routes
  remain authenticated.
- The exact pushed source state is saved and deployed publicly with the current
  ChatGPT Sites project.

## Non-Goals

- No WebRTC, H.264 live transport, pose model, cloud video analysis, face
  recognition, pet re-identification, medical diagnosis, continuous recording,
  extra generated landing media, or new production dependency.
