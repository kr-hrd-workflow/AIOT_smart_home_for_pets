# PetCare Public Sites Redeploy Design

> **Superseded on 2026-07-24:** The active release uses the existing Sites
> project with private access. The public design below is historical and is not
> the current deployment contract.

## Status

Approved direction: publish the project shell and bundled demo publicly from the current ChatGPT account, while keeping real household sensors, camera video, enrollment, and clips behind team authentication.

This document narrows the deployment and public-entry behavior of `2026-07-20-petcare-multitenant-remote-design.md`. It does not replace the Home Agent, Jetson, Pico, tenancy, clip-retention, or tunnel contracts.

## Outcome

- `/` always opens as the public PetCare landing page for anonymous visitors.
- `/demo` remains a public, deterministic showcase using bundled data and same-origin assets only.
- Authenticated users may continue from `/` to the operational dashboard when production auth is configured.
- Missing Supabase configuration never makes the public landing page return `500`.
- The deployed Sites project is created under the current account, made public, and replaces the stale project reference that the current account cannot access.
- The landing page explains the physical connection path without implying that plugging in hardware automatically publishes it to the internet.

## Access Boundary

Public:

- `/`
- `/demo`
- `/login`, `/signup`, `/forgot-password`, and `/reset-password` as account-entry pages
- the landing-page connection guide
- static metadata, fonts, and approved Open Graph media

Authenticated and tenant-scoped:

- real Pico sensor readings and behaviors
- live Jetson camera preview
- device enrollment and online state
- event clips, playback, deletion, and retention metadata
- account deletion and all mutating PetCare APIs

No PostgreSQL, MQTT broker, Jetson listener, Home Agent listener, tunnel origin, R2 object, or device credential becomes anonymous or directly public.

## Approaches Considered

### 1. Public shell and demo, protected live data (selected)

This matches the team-project sharing goal while preserving the camera and household-data boundary. It also lets the site work before physical Pico assembly or production identity-provider setup is complete.

### 2. Anonymous live sensors and camera

Rejected because a public URL would expose private household telemetry and video without an ownership boundary.

### 3. Static demo only

Rejected because it removes the already implemented authenticated Home Agent path instead of degrading it cleanly until external services are configured.

## Runtime Entry Behavior

The proxy remains the single routing authority.

1. `/demo` bypasses Supabase session construction.
2. Local development `/` keeps the same anonymous landing behavior without contacting Supabase.
3. If either `SUPABASE_URL` or `SUPABASE_PUBLISHABLE_KEY` is missing, public pages continue anonymously without constructing a Supabase client.
4. In that unconfigured state, protected pages redirect to the login page with an explicit unavailable state instead of throwing.
5. Auth form submissions catch missing provider configuration and return to the login page with the same unavailable state.
6. When auth is configured, the existing claim verification, cookie refresh, tenant lookup, and protected-route behavior remain unchanged.

The landing and login surfaces show a concise `실시간 연결 준비 중` message when production auth is unavailable. The public demo remains fully usable and is the primary team-review path.

## Landing Experience

Keep the approved dark, warm 3D apartment and current typography, motion, and responsive structure. This is a targeted evolution, not a redesign.

Add one concise connection chapter to the existing scroll narrative:

1. Jetson: USB webcam connected, private LAN vision node running.
2. Home Agent: enrolls the home, owns MQTT/PostgreSQL/rules, and exposes only the authenticated tunnel path.
3. Pico 2 W: flash the repository firmware with Wi-Fi and MQTT secrets; after it connects to the Home Agent broker, its readings appear through the existing dashboard APIs.

The copy must state that cable or Wi-Fi connection alone is insufficient. Enrollment, services, credentials, and a healthy Home Agent are required. It must never display passwords, tokens, private keys, local IP addresses, or tunnel origins.

## Hardware Data Flow

```text
Pico 2 W -- Wi-Fi/MQTT --> Home Agent -- authenticated BFF --> Sites dashboard
USB webcam --> Jetson -- private LAN --> Home Agent ---------^
```

The Jetson never uploads directly to Sites. The Pico has no separate Sites registration: once flashed with the correct MQTT configuration, it publishes to the already enrolled Home Agent. The dashboard then reads tenant-scoped Home Agent data through the existing two-second polling and private media proxy contracts.

## Deployment

1. Build and validate the exact dashboard source with the repository-pinned runtime.
2. Create one new Sites project under the current ChatGPT account.
3. Preserve the `DB` and `CLIPS` binding names and replace only the inaccessible `project_id` in `dashboard/.openai/hosting.json`.
4. Commit and push the exact validated source.
5. Package and save one Sites version, deploy it with public access, and poll until the deployment reaches a terminal success state.
6. Verify the new production URL in a real browser at desktop and mobile widths.

The old `kr-hrd-petcare-aiot.parkccccc3.chatgpt.site` deployment is not modified because the current account receives `project_not_found` for its project ID.

## Validation

Automated checks:

- a missing-auth-environment regression test proving `/` does not construct Supabase and does not return `500`;
- configured-auth tests preserving authenticated root and protected-route behavior;
- public route and `/demo` no-network tests;
- dashboard component tests, lint/type/build checks required by the repository;
- final exact-candidate dashboard/full verification once before deployment.

Production browser checks:

- anonymous `/` returns the landing page;
- `/demo` renders bundled operational data without backend requests;
- auth-unavailable state is understandable and does not expose a stack trace;
- keyboard navigation, visible focus, reduced motion, contrast, and mobile layout remain usable;
- metadata and the approved `public/og.png` render from the new public URL;
- protected data is not reachable anonymously.

## Failure Handling

- Missing auth configuration degrades only the account/live path, never the public shell or demo.
- Missing or offline Home Agent returns the existing explicit offline state and never substitutes demo data as live data.
- Sites deployment failure leaves the prior source and Git history intact; no second project is created merely to retry a version.
- Jetson or Pico unavailability does not prevent the public site from loading.

## Explicitly Skipped

Anonymous camera/sensor access, direct browser-to-Jetson access, direct Pico-to-Sites publishing, a new realtime protocol, WebRTC, new UI libraries, a second Jetson, and automatic production account creation are out of scope. Add them only after a measured requirement and separate security decision.
