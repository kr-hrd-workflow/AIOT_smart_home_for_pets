# PetCare Photoreal Scroll World Design

**Status:** Approved current direction
**Supersedes:** `2026-07-20-petcare-runtime-3d-landing-design.md` for the
landing visual runtime.

## Goal

Deliver a cinematic, photoreal landing page that begins at the apartment
entrance and advances through the home as the visitor scrolls. The result must
avoid the previous low-poly diorama, decorative shaking, and signal-ring-only
motion.

## Selected Media

The accepted Higgsfield Seedance generation
`1a1ee5f7-5dc8-4398-b991-301b7393089d` is the production journey. It is
normalized to a silent 1280x720 H.264 asset with frequent keyframes for
responsive seeking:

- video: `dashboard/public/landing/scroll-world/desktop/scene-01-arrival.mp4`
- poster: `dashboard/public/landing/scroll-world/source/scene-01-arrival.png`

The release uses the user's selected first Higgsfield journey: one continuous
entrance-to-living-room camera flight. Its duration and direct scroll-to-time
interaction follow the current Outpost reference without adding cuts, synthetic
camera motion, or unrelated generated scenes.

## Experience

- `/` presents the public landing.
- `/dashboard` presents the authenticated live Home dashboard.
- `/demo` remains fixture-only.
- Native browser scroll progress maps directly to video time in both directions.
- Wheel input is never intercepted or converted into chapter jumps.
- Media remains paused between scroll events; no elapsed-time playback, idle
  drift, synthetic shake, crop animation, or decorative parallax is allowed.
- The scroll track is long enough for deliberate frame-level control, following
  the reference's continuous full-screen flight rather than a slide deck.
- Semantic copy and calls to action remain crawlable HTML.
- The video is decorative and contains no text or private data.
- Reduced motion, data saver, playback failure, and decode failure use the
  approved poster without downloading or forcing motion.

## Visual Direction

- Contemporary Korean 50-pyeong apartment with a natural flush or shallow
  entry transition.
- Photoreal materials, believable exposure, and grounded architectural scale.
- One anatomically correct cream-gold retriever with a dark green collar.
- No low-poly geometry, isometric/cutaway framing, duplicate or reflected pets,
  stairs, generated text, logos, card grids, HUD, or signal rings.

## Runtime

- A single typed journey segment supplies the selected video and poster.
- Page progress is clamped and translated into the clip duration.
- Seeking is coalesced to avoid redundant work.
- Media listeners and transient resources are cleaned up on unmount.
- No autoplay loop or runtime generative AI is used.

## Quality Gates

- Focused runtime and route-contract tests.
- Lint and production build.
- Desktop, tablet, and mobile browser E2E.
- Reduced-motion, data-saver, and poster-fallback behavior.
- Browser proof that 0%, 50%, and 100% page progress reaches the corresponding
  encoded video time while the video remains paused at rest.
- Exact-candidate CI followed by a public Sites deployment.

## Non-Goals

- No public live camera or sensor data.
- No real-time photoreal 3D apartment simulation.
- No replacement authentication system.
