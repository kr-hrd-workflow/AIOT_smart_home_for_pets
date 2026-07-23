# PetCare Photoreal Scroll World Implementation Plan

## Current Decision

Use the already accepted Higgsfield Seedance generation
`1a1ee5f7-5dc8-4398-b991-301b7393089d` as the complete landing journey.
The clip already moves from the Korean apartment entrance into the home, so no
new Higgsfield generation, CLI work, connector synthesis, or Remotion render is
part of this release.

Production media:

- `dashboard/public/landing/scroll-world/desktop/scene-01-arrival.mp4`
- `dashboard/public/landing/scroll-world/source/scene-01-arrival.png`

Unreferenced experimental media is not part of the release candidate.

## Product Contract

- `/` is the public photoreal landing page.
- `/dashboard` is the authenticated Home dashboard.
- `/demo` remains a fixture-only public demo with no live home data.
- Login, signup, password recovery, and reset routes remain available.
- The accepted clip is the only landing video source.
- Scroll position scrubs the clip; it does not autoplay or loop independently.
- Reduced motion, data saver, failed decode, and rejected playback remain usable
  through the poster fallback.
- Generated media contains no text, logos, cards, HUD, signal rings, or live
  private data.

## Implementation

1. Configure one `journey` segment in
   `dashboard/components/landing/scroll-world-config.ts`.
2. Map normalized page progress to the accepted clip's `currentTime` in the
   landing scene director.
3. Keep semantic copy, navigation, and calls to action as HTML above the
   decorative media.
4. Remove the obsolete low-poly/signal-ring landing runtime from the route.
5. Keep the approved poster as the no-video fallback.

## Validation

- Focused landing, auth, dashboard, and scene-director tests.
- Dashboard lint and production build.
- Browser E2E for the production landing and the authenticated connected
  dashboard at desktop, tablet, and mobile viewports.
- Reduced-motion and static-fallback checks.
- Exact-candidate CI after commit and push.

## Release

Reuse the existing Sites project ID. Save and deploy only the exact verified
dashboard source state, set Sites access to private, and confirm owner access to
`/` and `/demo` while anonymous access is denied by the Sites boundary.
