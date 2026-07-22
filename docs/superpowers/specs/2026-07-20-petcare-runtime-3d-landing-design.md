# PetCare Runtime 3D Landing Design

**Status:** Approved
**Scope:** Public landing and authentication entry surfaces only. The authenticated operational dashboard, API routes, tenancy, event clips, and `/demo` behavior remain intact.

## Goal

Create a high-end cinematic PetCare landing experience that makes the connected pet home feel tangible before sign-in. The page must use a real-time 3D scene, remain fast and usable on mobile, preserve ordinary email/password authentication, and degrade safely when WebGL or motion is unavailable.

## Chosen Direction

Three directions were considered:

1. **Cinematic single-home journey (chosen):** one carefully art-directed pet-home scene with scroll-driven camera chapters. This gives the strongest visual identity while reusing one bounded scene.
2. **Abstract data sculpture:** lighter and easier to optimize, but less clearly communicates webcam, feeding, and rest monitoring.
3. **Multiple room scenes:** visually broad, but duplicates assets, increases load time, and makes mobile quality harder to control.

The chosen design uses one dark, premium contemporary Korean apartment with cool cyan ambient light and restrained warm amber sensor highlights. It represents a spacious 50-pyeong home, approximately 165 square meters, as one connected single-level scene. The wide living room, window wall, island kitchen and dining zone, entry middle door, and built-in storage make it read as a lived-in Korean home rather than a compact diorama or Western mansion. The scene moves through feeding, rest, and event-review chapters without page-theme changes.

The architecture must not include an internal staircase, double-height mansion foyer, glass-walled bedroom, visible human bed, or duplicate pet. Feeding, rest, camera, and event-review zones remain spatially distinct inside the one connected apartment.

## Experience Architecture

### Public landing route

The public landing page is a server-rendered shell with a client-only 3D island:

- semantic HTML contains the headline, product explanation, feature chapters, sign-in and demo links;
- React Three Fiber renders the pet-home scene only after capability detection;
- GSAP ScrollTrigger maps chapter progress to camera position, focus, lighting, and a small number of object states;
- the existing authentication routes and forms remain native HTML forms;
- the authenticated `/` dashboard continues to render `RemoteDashboard` and is not placed inside the 3D scene.

The clean route split is:

- unauthenticated `/`: cinematic landing;
- `/login`, `/signup`, `/forgot-password`, `/reset-password`: shared premium scene backdrop plus accessible HTML form;
- authenticated `/`: existing operational dashboard;
- `/demo`: existing disconnected demo behavior, with no new external requests.

### Scene chapters

1. **Hero:** an isometric 50-pyeong Korean apartment appears from darkness. A slow idle camera move establishes the wide living core, kitchen, dining area, and entry. The primary CTA is `로그인`; the secondary CTA opens `/demo`.
2. **Feeding:** camera travels to the bowl area. A restrained amber pulse explains Pico sensor events and event-only recording.
3. **Rest:** camera moves to the bed area. Lighting softens while copy explains rest detection and anomaly alerts.
4. **Event review:** a floating in-world display frames an event clip preview and the seven-day retention promise.
5. **Final CTA:** camera returns to the whole home and the sign-in action is repeated once.

No scroll cue, decorative status dots, fake metrics, or invented customer logos are added.

## Runtime Components

- `LandingPage`: server component that owns semantic content and CTA links.
- `PetHomeExperience`: client boundary that owns Canvas setup, capability checks, quality tier, and fallback selection.
- `PetHomeScene`: one scene graph with instanced/simple geometry, baked-looking materials, real shadows only where they matter, and no downloaded model requirement for the first version.
- `SceneDirector`: GSAP context scoped to the scene. It creates one timeline, uses ScrollTrigger cleanup, and respects `prefers-reduced-motion`.
- `LandingOverlay`: HTML content layered above the canvas with keyboard-visible focus and WCAG AA contrast.
- `AuthSceneShell`: reuses the same visual language at reduced GPU cost behind the existing auth forms.

## Dependencies

Add only the explicitly requested runtime packages:

- `three`
- `@react-three/fiber`
- `@react-three/drei`
- `gsap`

Remotion stays in a separate promotional-video entry and is not imported by application routes or included in the Sites runtime bundle. It is added only when the first promotional composition is implemented. No general animation framework is added alongside GSAP.

## Live State

The public landing uses deterministic showcase state and never calls private home APIs. After authentication, the existing dashboard remains the source of truth for webcam, sensor, event, and connectivity data. The landing does not duplicate polling or expose a new endpoint.

## Image Generation

Use one image-generation request after the visual copy and palette are frozen to produce a bespoke 1200x630 social card matching the final scene. It must contain only verified PetCare copy, cyan/amber lighting, and the recognizable single-home composition. The runtime hero remains WebGL, not a generated-image substitute. If the generated card contains incorrect text, retry once; otherwise omit it.

## Performance And Fallbacks

- desktop DPR is capped and adaptive quality lowers shadow resolution before geometry;
- mobile starts with lower DPR, fewer particles, no depth-of-field, and reduced shadow work;
- Canvas loads after the semantic hero and has a fixed aspect/viewport footprint to prevent layout shift;
- WebGL failure, data-saver, or low capability renders a project-local still image with the same copy and CTA hierarchy;
- `prefers-reduced-motion` disables camera travel and object pulses while preserving chapter content;
- no continuous React state updates are driven by scroll; GSAP mutates Three objects inside a scoped timeline;
- the landing target is no horizontal overflow from 320px upward and a usable first CTA before the fold.

## Error And Safety Behavior

- a scene initialization error is caught locally and switches to the still fallback;
- no camera, Supabase, connector, or clip secret reaches the public 3D component;
- links remain functional before JavaScript hydration;
- all animation effects and observers have cleanup functions;
- the existing auth, account deletion, retention, and privacy copy are not rewritten by the visual redesign.

## Testing And Validation

TDD coverage must prove:

- landing CTAs and semantic content render without WebGL;
- reduced-motion and fallback modes do not initialize the animated director;
- auth form action, field names, error states, and keyboard focus remain unchanged;
- `/demo` makes no external requests;
- authenticated `/` still renders the existing dashboard;
- 320x568, 375x812, 768x1024, and 1440x900 layouts have no horizontal overflow;
- build remains Cloudflare Workers-compatible;
- the 3D packages are absent from server-only authentication and BFF modules;
- the final browser gate checks visible focus, contrast, reduced motion, and the WebGL fallback.

## Non-Goals

- no game controls, avatar, virtual pet, physics simulation, or room editor;
- no always-on 3D rendering inside the operational dashboard;
- no new API, database table, authentication method, or public webcam stream;
- no multi-scene asset download system;
- no Remotion runtime dependency in the deployed app.

## Acceptance Criteria

The result is complete when the public and auth entry surfaces feel like one cinematic PetCare product, the runtime scene visibly supports the feeding/rest/event story, existing product behavior is unchanged, reduced-motion and no-WebGL paths are fully usable, focused tests and one component build pass, and the final Sites candidate is packaged from the exact reviewed source.
