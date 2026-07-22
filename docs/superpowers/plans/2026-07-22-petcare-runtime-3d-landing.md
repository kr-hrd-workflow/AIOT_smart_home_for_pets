# PetCare Runtime 3D Landing Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship a cinematic, accessible PetCare landing and auth entry experience with a real-time React Three Fiber home, GSAP scroll direction, a separate Remotion promo, and a generated social card without changing authenticated dashboard behavior.

**Architecture:** The request proxy verifies Supabase claims and overwrites a private request header so the server-rendered root can choose the public landing or existing dashboard safely. The landing keeps all copy and links in semantic HTML while a dynamically loaded client island owns capability detection, React Three Fiber, and one cleanup-scoped GSAP timeline. Remotion lives under an isolated `remotion/` entry and is never imported by application routes.

**Tech Stack:** Next.js 16, Vinext, React 19, TypeScript, React Three Fiber, Drei, Three.js, GSAP ScrollTrigger, Remotion, Vitest, Testing Library, Cloudflare Workers/Sites.

## Global Constraints

- Preserve `/login`, `/signup`, `/forgot-password`, `/reset-password`, `/demo`, and authenticated `/` behavior.
- Keep ordinary email/password form actions, field names, autocomplete values, error states, and focus behavior unchanged.
- Public landing code must not call private APIs, Supabase, webcam, clips, sensors, D1, or R2.
- Use one dark theme with restrained cyan ambient light and amber sensor accents. Use no pure black, pure white, neon outer glow, fake metrics, fake customers, decorative status dots, scroll cues, em-dashes, or en-dashes in visible copy.
- Use real HTML for copy, links, and forms. WebGL is enhancement only.
- Honor `prefers-reduced-motion`, data saver, WebGL failure, and 320px-wide viewports.
- Cap device pixel ratio and lazy-load the Three.js island so semantic HTML is usable first.
- GSAP may mutate Three objects but must not drive React state on scroll. Every observer, context, and timeline needs cleanup.
- Remotion animation is driven only by `useCurrentFrame()` and `interpolate()`; 3D uses `ThreeCanvas`, never `useFrame()`.
- Do not add a second general UI animation framework.
- During edits run only focused tests. Run the landing component suite once after the feature bundle, and the full dashboard suite once at the final candidate gate.

---

### Task 1: Verified Root Surface Routing

**Files:**
- Modify: `dashboard/proxy.ts`
- Modify: `dashboard/app/page.tsx`
- Create: `dashboard/tests/landing/root-routing.test.tsx`
- Modify: `dashboard/tests/auth/session.test.ts`
- Modify: `dashboard/tests/dashboard.test.tsx`

**Interfaces:**
- Produces: request header `x-petcare-authenticated` with exact values `1` or `0`, always overwritten by `proxy()`.
- Produces: `Home()` server component selecting `<RemoteDashboard />` only for `1`, otherwise `<LandingPage />`.
- Consumes: existing `createSupabaseSession()`, `requireAuth()`, and `RemoteDashboard`.

- [ ] **Step 1: Write failing routing tests**

```tsx
it("overwrites a forged root auth header for an anonymous request", async () => {
  mocks.requireAuth.mockRejectedValue(new AuthError("Authentication required"));
  const request = new NextRequest("https://app.test/", {
    headers: { "x-petcare-authenticated": "1" },
  });
  const response = await proxy(request);
  expect(response.status).toBe(200);
  expect(response.headers.get("location")).toBeNull();
  expect(mocks.forwardedRequestHeader("x-petcare-authenticated")).toBe("0");
});

it("renders the public landing unless the proxy supplied a verified marker", async () => {
  mocks.headers.mockResolvedValue(new Headers({ "x-petcare-authenticated": "0" }));
  render(await Home());
  expect(screen.getByRole("heading", { name: /petcare/i })).toBeInTheDocument();
  expect(screen.queryByTestId("remote-dashboard")).not.toBeInTheDocument();
});
```

- [ ] **Step 2: Run the focused tests and verify RED**

Run: `npm test -- tests/landing/root-routing.test.tsx tests/auth/session.test.ts tests/dashboard.test.tsx`

Expected: FAIL because root still redirects anonymous users and `LandingPage` does not exist.

- [ ] **Step 3: Overwrite the internal marker and branch only at the server root**

```tsx
const ROOT_AUTH_HEADER = "x-petcare-authenticated";

function nextWithAuth(request: NextRequest, authenticated: boolean) {
  const requestHeaders = new Headers(request.headers);
  requestHeaders.set(ROOT_AUTH_HEADER, authenticated ? "1" : "0");
  return NextResponse.next({ request: { headers: requestHeaders } });
}

if (request.nextUrl.pathname === "/") {
  return session.applySessionCookies(nextWithAuth(request, authenticated));
}
```

```tsx
export default async function Home() {
  const requestHeaders = await headers();
  return requestHeaders.get("x-petcare-authenticated") === "1"
    ? <RemoteDashboard />
    : <LandingPage />;
}
```

- [ ] **Step 4: Run focused routing tests and verify GREEN**

Run: `npm test -- tests/landing/root-routing.test.tsx tests/auth/session.test.ts tests/dashboard.test.tsx`

Expected: PASS; anonymous `/` is public, forged markers are replaced, authenticated `/` still renders `RemoteDashboard`, and `/demo` remains Supabase-independent.

- [ ] **Step 5: Commit the routing slice**

```bash
git add dashboard/proxy.ts dashboard/app/page.tsx dashboard/tests/landing/root-routing.test.tsx dashboard/tests/auth/session.test.ts dashboard/tests/dashboard.test.tsx
git commit -m "feat(dashboard): route verified users from landing"
```

### Task 2: Semantic Landing And Auth Shell

**Files:**
- Create: `dashboard/components/landing/landing-page.tsx`
- Create: `dashboard/components/landing/landing-overlay.tsx`
- Create: `dashboard/components/landing/landing-fallback.tsx`
- Create: `dashboard/components/landing/auth-scene-shell.tsx`
- Create: `dashboard/components/landing/landing-copy.ts`
- Modify: `dashboard/components/auth-card.tsx`
- Modify: `dashboard/app/globals.css`
- Create: `dashboard/tests/landing/landing-page.test.tsx`
- Modify: `dashboard/tests/auth/public-routes.test.tsx`

**Interfaces:**
- Produces: `LandingPage(): JSX.Element`, with `<PetHomeExperience />` behind a dynamic client boundary.
- Produces: `LANDING_CHAPTERS` containing exact `feeding`, `rest`, and `events` IDs and public copy only.
- Produces: `AuthSceneShell({ children })` used by `AuthCard` without changing form descendants.
- Consumes: `PetHomeExperience` from Task 3.

- [ ] **Step 1: Write semantic and auth-preservation tests**

```tsx
it("keeps primary actions and every product claim available without WebGL", () => {
  render(<LandingPage experience={<LandingFallback />} />);
  expect(screen.getByRole("link", { name: "로그인" })).toHaveAttribute("href", "/login");
  expect(screen.getByRole("link", { name: "데모 보기" })).toHaveAttribute("href", "/demo");
  expect(screen.getByRole("heading", { name: /반려동물의 하루를 필요한 순간만 기록/i })).toBeInTheDocument();
  expect(screen.getByText(/이벤트 클립은 7일 후 자동 삭제/i)).toBeInTheDocument();
});

it("does not change login form contract", async () => {
  render(await LoginPage({ searchParams: Promise.resolve({}) }));
  expect(screen.getByRole("form")).toHaveAttribute("action", "/auth/login");
  expect(screen.getByLabelText("이메일")).toHaveAttribute("name", "email");
  expect(screen.getByLabelText("비밀번호")).toHaveAttribute("name", "password");
});
```

- [ ] **Step 2: Run focused shell tests and verify RED**

Run: `npm test -- tests/landing/landing-page.test.tsx tests/auth/public-routes.test.tsx`

Expected: FAIL because the landing shell and auth scene wrapper do not exist.

- [ ] **Step 3: Implement concise semantic chapter copy**

```ts
export const LANDING_CHAPTERS = [
  { id: "feeding", title: "식사 순간을 알아봅니다", body: "Pico 2 W 센서와 카메라 이벤트를 함께 확인해 필요한 장면만 남깁니다." },
  { id: "rest", title: "휴식의 변화를 놓치지 않습니다", body: "평소 패턴과 다른 움직임을 발견하면 확인할 수 있는 기록을 준비합니다." },
  { id: "events", title: "이벤트만 안전하게 보관합니다", body: "감지 전후의 짧은 클립을 계정별로 분리하고 7일 후 자동 삭제합니다." },
] as const;
```

Implement one asymmetric hero, three full-height semantic chapters, and one final CTA. Keep the `<h1>` under eight Korean words per line and body copy under 25 words per block where natural.

- [ ] **Step 4: Wrap existing auth content without changing form markup**

```tsx
export function AuthSceneShell({ children }: { children: ReactNode }) {
  return (
    <main className="auth-scene-shell">
      <div className="auth-scene-still" aria-hidden="true" />
      <div className="auth-scene-content">{children}</div>
    </main>
  );
}
```

Move only the existing `auth-card` section inside this wrapper. Preserve all title, description, `action`, `method`, input `name`, input `type`, `autocomplete`, alert, and status nodes.

- [ ] **Step 5: Add responsive single-theme CSS and motion fallbacks**

Define landing-only variables under `.landing-page`; do not replace dashboard tokens. Use `min-height: 100dvh`, `clamp()`, one 8px/18px radius system, off-black surfaces, off-white text, one cyan CTA, amber only for actual sensor emphasis, visible `:focus-visible`, `overflow-x: clip`, and a strict single-column layout below `768px`. Disable transitions and hide the Canvas under `prefers-reduced-motion: reduce` only when the static scene already carries the meaning.

- [ ] **Step 6: Run focused shell tests and verify GREEN**

Run: `npm test -- tests/landing/landing-page.test.tsx tests/auth/public-routes.test.tsx`

Expected: PASS with original form contracts and accessible fallback copy intact.

- [ ] **Step 7: Commit the semantic shell**

```bash
git add dashboard/components/landing dashboard/components/auth-card.tsx dashboard/app/globals.css dashboard/tests/landing/landing-page.test.tsx dashboard/tests/auth/public-routes.test.tsx
git commit -m "feat(dashboard): add cinematic landing shell"
```

### Task 3: Real-Time Pet Home Scene And Scroll Director

**Files:**
- Modify: `dashboard/package.json`
- Modify: `dashboard/package-lock.json`
- Create: `dashboard/components/landing/pet-home-experience.tsx`
- Create: `dashboard/components/landing/pet-home-scene.tsx`
- Create: `dashboard/components/landing/scene-director.ts`
- Create: `dashboard/components/landing/scene-quality.ts`
- Create: `dashboard/tests/landing/pet-home-experience.test.tsx`
- Create: `dashboard/tests/landing/scene-director.test.ts`

**Interfaces:**
- Produces: `PetHomeExperience({ chapterRootId = "petcare-story" })`.
- Produces: `detectSceneMode(): "animated" | "reduced" | "fallback"` based on reduced motion, data saver, and WebGL availability.
- Produces: `createSceneDirector({ root, camera, bowlLight, bedLight, eventScreen }): () => void`, returning cleanup.
- Consumes: DOM chapter IDs from `LANDING_CHAPTERS`.

- [ ] **Step 1: Install only requested real-time dependencies**

Run: `npm install three @react-three/fiber @react-three/drei gsap`

Expected: exact packages appear in `dependencies`; no Motion or second animation framework is added.

- [ ] **Step 2: Write capability and cleanup tests**

```tsx
it.each([
  [true, false, true, "reduced"],
  [false, true, true, "fallback"],
  [false, false, false, "fallback"],
  [false, false, true, "animated"],
])("selects a safe scene mode", (reduced, saveData, webgl, expected) => {
  expect(detectSceneMode({ reduced, saveData, webgl })).toBe(expected);
});

it("reverts the scoped GSAP context and every ScrollTrigger", () => {
  const cleanup = createSceneDirector(fixture);
  cleanup();
  expect(mocks.contextRevert).toHaveBeenCalledOnce();
});
```

- [ ] **Step 3: Run focused scene tests and verify RED**

Run: `npm test -- tests/landing/pet-home-experience.test.tsx tests/landing/scene-director.test.ts`

Expected: FAIL because scene mode and director modules do not exist.

- [ ] **Step 4: Build one bounded low-poly pet home**

Use procedural `boxGeometry`, `cylinderGeometry`, and `roundedBoxGeometry` only. Build a room shell, bowl, bed, camera, Pico sensor block, pet silhouette, and event screen. Reuse materials, cap Canvas DPR to `[1, 1.5]`, use one ambient light, one directional light, two restrained point lights, and shadows only on the hero quality tier. Do not download GLTF assets or add particles in the first version.

- [ ] **Step 5: Bind one motivated GSAP timeline to three chapters**

```ts
export function createSceneDirector(nodes: SceneDirectorNodes) {
  const context = gsap.context(() => {
    const timeline = gsap.timeline({
      scrollTrigger: {
        trigger: nodes.root,
        start: "top top",
        end: "bottom bottom",
        scrub: 1,
        invalidateOnRefresh: true,
      },
    });
    timeline
      .to(nodes.camera.position, { x: -2.4, y: 1.8, z: 4.2 })
      .to(nodes.bowlLight, { intensity: 1.15 }, "<")
      .to(nodes.camera.position, { x: 2.2, y: 1.45, z: 4.6 })
      .to(nodes.bedLight, { intensity: 0.8 }, "<")
      .to(nodes.camera.position, { x: 0.2, y: 2.1, z: 5.6 })
      .to(nodes.eventScreen.scale, { x: 1.08, y: 1.08, z: 1.08 }, "<");
  }, nodes.root);
  return () => context.revert();
}
```

The timeline communicates the feeding, rest, and event-review story. No `window` scroll listener and no React state update may run per frame.

- [ ] **Step 6: Add error boundary and client-only lazy loading**

Render `LandingFallback` until capability detection completes. Catch Canvas initialization failures locally and permanently select fallback for that mount. For reduced motion render a static camera and no `SceneDirector`.

- [ ] **Step 7: Run focused scene tests and verify GREEN**

Run: `npm test -- tests/landing/pet-home-experience.test.tsx tests/landing/scene-director.test.ts`

Expected: PASS; fallback/reduced modes never construct the director, and cleanup is exact.

- [ ] **Step 8: Commit the real-time scene**

```bash
git add dashboard/package.json dashboard/package-lock.json dashboard/components/landing/pet-home-experience.tsx dashboard/components/landing/pet-home-scene.tsx dashboard/components/landing/scene-director.ts dashboard/components/landing/scene-quality.ts dashboard/tests/landing/pet-home-experience.test.tsx dashboard/tests/landing/scene-director.test.ts
git commit -m "feat(dashboard): render scroll-directed pet home"
```

### Task 4: Separate 15-Second Remotion Promo

**Files:**
- Modify: `dashboard/package.json`
- Modify: `dashboard/package-lock.json`
- Create: `dashboard/remotion/index.ts`
- Create: `dashboard/remotion/petcare-promo.tsx`
- Create: `dashboard/remotion/petcare-promo.test.tsx`

**Interfaces:**
- Produces: composition ID `PetCarePromo`, 1920x1080, 30 fps, 450 frames.
- Consumes: no application module. It may share only literal palette values and `public/og.png` through `staticFile()`.

- [ ] **Step 1: Install Remotion with matched package versions**

Run: `npm install --save-dev remotion @remotion/cli @remotion/three`

Expected: all three Remotion packages resolve to the same version and remain in `devDependencies`.

- [ ] **Step 2: Write registration and frame-determinism tests**

```tsx
it("registers the exact 15 second composition", () => {
  expect(PETCARE_PROMO).toEqual({ id: "PetCarePromo", width: 1920, height: 1080, fps: 30, durationInFrames: 450 });
});

it("derives every scene transition from the frame", () => {
  expect(sceneAtFrame(0)).toBe("home");
  expect(sceneAtFrame(120)).toBe("feeding");
  expect(sceneAtFrame(240)).toBe("rest");
  expect(sceneAtFrame(360)).toBe("events");
});
```

- [ ] **Step 3: Run focused Remotion test and verify RED**

Run: `npm test -- remotion/petcare-promo.test.tsx`

Expected: FAIL because the composition is not registered.

- [ ] **Step 4: Implement four uncluttered scenes**

Use `AbsoluteFill`, `Sequence`, `ThreeCanvas`, `useCurrentFrame()`, `useVideoConfig()`, and inline `interpolate()` calls. Keep important text at least 80px from horizontal edges and 100px from vertical edges. Use headline text at least 84px and supporting text at least 44px. Show one message and one dominant visual per scene. Never use CSS animation, CSS transition, Tailwind animation, or R3F `useFrame()`.

- [ ] **Step 5: Run focused Remotion test and one-frame render**

Run: `npm test -- remotion/petcare-promo.test.tsx`

Expected: PASS.

Run: `npx remotion still remotion/index.ts PetCarePromo .runtime/petcare-promo-frame.png --frame=240 --scale=0.25`

Expected: render exits 0 and the representative frame contains no clipped or overlapping text.

- [ ] **Step 6: Prove Remotion stays out of route imports and commit**

Run: `rg -n "remotion|@remotion" app components lib proxy.ts`

Expected: no matches.

```bash
git add dashboard/package.json dashboard/package-lock.json dashboard/remotion
git commit -m "feat(video): add PetCare launch composition"
```

### Task 5: Bespoke Social Card

**Files:**
- Modify: `dashboard/public/og.png`
- Modify: `dashboard/app/layout.tsx`
- Create: `dashboard/tests/landing/metadata.test.ts`

**Interfaces:**
- Produces: one 1200x630 raster card with a single isometric pet home, cyan/amber lighting, and no generated text.
- Consumes: existing `/og.png` metadata URL.

- [ ] **Step 1: Generate exactly one primary image**

Prompt the image generator for a 1200x630 premium consumer-tech social card: dark charcoal single isometric pet home, visible bowl, pet bed, small webcam, restrained cyan ambient lighting, warm amber sensor accent, generous negative space for metadata overlays, no text, no logo, no UI screenshot, no people, no extra rooms.

- [ ] **Step 2: Inspect the generated image**

Verify the image has one recognizable home composition, no malformed pet, no accidental text, no purple/neon outer glow, and enough contrast at thumbnail size. Retry at most once only if unusable.

- [ ] **Step 3: Replace the social asset and assert metadata**

```ts
it("publishes the local 1200x630 PetCare social card", async () => {
  const metadata = await generateMetadata();
  expect(metadata.openGraph?.images).toEqual([
    expect.objectContaining({ url: "https://app.test/og.png", width: 1200, height: 630 }),
  ]);
});
```

- [ ] **Step 4: Run the focused metadata test and commit**

Run: `npm test -- tests/landing/metadata.test.ts`

Expected: PASS.

```bash
git add dashboard/public/og.png dashboard/app/layout.tsx dashboard/tests/landing/metadata.test.ts
git commit -m "feat(dashboard): publish PetCare social card"
```

### Task 6: Component Gate, Visual QA, And Sites Build

**Files:**
- Modify only if a focused failure proves necessary: files owned by Tasks 1-5.

**Interfaces:**
- Consumes: reviewed outputs from Tasks 1-5 at one frozen source SHA.
- Produces: exact test/build evidence for the landing feature bundle.

- [ ] **Step 1: Run the landing component suite once**

Run: `npm test -- tests/landing tests/auth/public-routes.test.tsx tests/auth/session.test.ts tests/dashboard.test.tsx remotion/petcare-promo.test.tsx`

Expected: all landing, auth-preservation, root-routing, dashboard-preservation, metadata, and promo tests PASS.

- [ ] **Step 2: Run static checks**

Run: `npm run lint`

Expected: exit 0.

Run: `npm run build`

Expected: Vinext Cloudflare build exits 0; no Node-only Remotion import appears in the route bundle.

- [ ] **Step 3: Inspect representative responsive states**

Start the project with `npm run dev`. Inspect `/`, `/login`, `/demo` at 320x568, 375x812, 768x1024, and 1440x900. Verify no horizontal overflow, primary CTA before the fold, visible keyboard focus, meaningful fallback with WebGL disabled, static scene under reduced motion, and unchanged dashboard/demo content.

- [ ] **Step 4: Run copy and dependency audits**

Run: `rg -n "[—–]|Scroll|99\.99%|Quietly|Stage [0-9]|Phase [0-9]" app components remotion`

Expected: no visible-copy matches.

Run: `rg -n "remotion|@remotion" app components lib proxy.ts`

Expected: no application import matches.

- [ ] **Step 5: Request parallel identical-SHA reviews**

Dispatch separate reviewers for specification coverage, code quality/Ponytail, accessibility/performance, and security/privacy. Reviewers inspect the existing component/build evidence and run focused checks only when needed. Do not rerun the full suite on an unchanged SHA.

- [ ] **Step 6: Run the dashboard full suite once at final candidate**

Run: `npm test`

Expected: full Vitest suite PASS exactly once for the final candidate SHA.

- [ ] **Step 7: Commit any evidence-driven final fixes**

If a review identifies a real defect, add a focused failing test, make the smallest fix, rerun only that focused test, then repeat the component/build gate once for the new SHA before the final candidate full suite.

```bash
git status --short
git log --oneline --max-count=8
```

Expected: only the planned 3D landing, auth-shell, Remotion, social-card, tests, and design/plan commits are present. Deployment remains a separate explicit Sites hosting action after the final integrated project SHA is frozen.

## Self-Review Results

- Spec coverage: public/auth routing, semantic fallback, feeding/rest/events chapters, 3D runtime, reduced motion, capability failure, existing dashboard/demo preservation, Remotion isolation, OG card, responsive QA, Cloudflare build, privacy, and exact test cadence all map to Tasks 1-6.
- Placeholder scan: no implementation placeholder or unspecified error-handling step remains.
- Type consistency: `x-petcare-authenticated`, `LANDING_CHAPTERS`, `PetHomeExperience`, `detectSceneMode`, `createSceneDirector`, and `PetCarePromo` names are stable across producers and consumers.
