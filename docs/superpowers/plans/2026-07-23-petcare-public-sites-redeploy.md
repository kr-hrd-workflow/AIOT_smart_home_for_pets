# PetCare Public Sites Redeploy Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Keep the polished PetCare landing and bundled demo publicly reachable from a new Sites project in the current account, degrade missing authentication configuration without a 500, document the real Jetson/Pico onboarding path, and deploy the exact verified candidate publicly.

**Architecture:** `dashboard/proxy.ts` remains the single public-entry authority and checks for both Supabase runtime values before constructing a client. Anonymous visitors receive the landing/demo, while unavailable account/live paths converge on one explicit login status. The existing landing gains one semantic connection chapter; real sensor, camera, enrollment, and clip routes keep the current Supabase and tenant boundary.

**Tech Stack:** Next.js 16, vinext, React 19, Vitest, Playwright, Cloudflare Sites/D1/R2, Supabase SSR, React Three Fiber/GSAP, Python 3.12 repository checks.

## Global Constraints

- Public: `/`, `/demo`, account-entry surfaces, landing copy, metadata, fonts, and the approved `dashboard/public/og.png`.
- Authenticated and tenant-scoped: real Pico readings, Jetson video, enrollment, device state, clips, account deletion, and mutating PetCare APIs.
- Missing `SUPABASE_URL` or `SUPABASE_PUBLISHABLE_KEY` must never make `/` or `/demo` return `500`.
- Do not expose PostgreSQL, MQTT, Jetson/Home Agent listeners, tunnel origins, R2 objects, or credentials.
- Reuse the existing landing design and dependencies; add no UI, auth, realtime, or deployment dependency.
- Preserve `DB` and `CLIPS` binding names; replace only the inaccessible Sites `project_id`.
- Preserve `.codex/`, `.omo/drafts/petcare-sites-completion.md`, and root `node_modules/` as user-owned untracked paths.
- Use repository-pinned Node `22.23.1` and Python `3.12.13+20260623` from `.runtime/toolchain.json`.
- Run only focused tests while editing, one component verification for the completed feature bundle, and one final full verification on the exact deployment candidate.
- Launch Node, Python, full-suite, browser, and Remotion work BelowNormal with redirected stdio and poll at approximately 30-second intervals; only one heavy process may run at a time.
- Create no second Sites project when version save, deployment, or polling can be retried against the first new project.

---

### Task 1: Missing-auth public entry fallback

**Files:**
- Modify: `dashboard/proxy.ts`
- Modify: `dashboard/app/login/page.tsx`
- Test: `dashboard/tests/auth/session.test.ts`
- Test: `dashboard/tests/auth/public-routes.test.tsx`

**Interfaces:**
- Consumes: Cloudflare `env`, `AuthEnv`, `createSupabaseSession()`, `nextWithAuth()` and the existing `x-petcare-authenticated` request marker.
- Produces: missing-auth root response with marker `0`; canonical `/login?error=unavailable` fallback; login status copy `실시간 연결 준비 중입니다` with no active form.

- [ ] **Step 1: Make the Cloudflare auth environment mutable in the proxy test**

Replace the literal `cloudflare:workers` mock in `dashboard/tests/auth/session.test.ts` with:

```ts
const runtimeEnv = vi.hoisted(
  (): {
    SUPABASE_URL?: string;
    SUPABASE_PUBLISHABLE_KEY?: string;
  } => ({
    SUPABASE_URL: "https://project-ref.supabase.co",
    SUPABASE_PUBLISHABLE_KEY: "sb_publishable_test",
  }),
);

vi.mock("cloudflare:workers", () => ({ env: runtimeEnv }));
```

At the start of `beforeEach`, restore both values so the existing configured-auth tests remain independent:

```ts
runtimeEnv.SUPABASE_URL = "https://project-ref.supabase.co";
runtimeEnv.SUPABASE_PUBLISHABLE_KEY = "sb_publishable_test";
```

- [ ] **Step 2: Write the failing proxy regression test**

Add this focused contract to `dashboard/tests/auth/session.test.ts`:

```ts
it("keeps the public entry usable when Supabase runtime configuration is absent", async () => {
  runtimeEnv.SUPABASE_URL = undefined;
  runtimeEnv.SUPABASE_PUBLISHABLE_KEY = undefined;

  const root = await proxy(new NextRequest("https://app.test/"));
  expect(root.status).toBe(200);
  expect(
    root.headers.get("x-middleware-request-x-petcare-authenticated"),
  ).toBe("0");

  const login = await proxy(new NextRequest("https://app.test/login"));
  expect(login.status).toBe(307);
  expect(login.headers.get("location")).toBe(
    "https://app.test/login?error=unavailable",
  );

  const unavailable = await proxy(
    new NextRequest("https://app.test/login?error=unavailable"),
  );
  expect(unavailable.status).toBe(200);

  const protectedPage = await proxy(
    new NextRequest("https://app.test/settings"),
  );
  expect(protectedPage.status).toBe(307);
  expect(protectedPage.headers.get("location")).toBe(
    "https://app.test/login?error=unavailable",
  );
  expect(mocks.createServerClient).not.toHaveBeenCalled();
  expect(mocks.requireAuth).not.toHaveBeenCalled();
});
```

- [ ] **Step 3: Write the failing login-state test**

Add to `dashboard/tests/auth/public-routes.test.tsx`:

```tsx
it("renders an unavailable account state without a broken login form", async () => {
  render(
    await LoginPage({
      searchParams: Promise.resolve({ error: "unavailable" }),
    }),
  );
  expect(
    screen.getByText(/실시간 연결 준비 중입니다/),
  ).toHaveAttribute("role", "status");
  expect(screen.getByRole("link", { name: "데모 보기" })).toHaveAttribute(
    "href",
    "/demo",
  );
  expect(document.querySelector("form")).toBeNull();
});
```

- [ ] **Step 4: Run the focused RED tests**

Run with the pinned Node runtime and redirected output:

```powershell
$runtime = Get-Content -Raw .runtime/toolchain.json | ConvertFrom-Json
& $runtime.paths.node_path $runtime.paths.npm_cli_path --prefix dashboard test -- tests/auth/session.test.ts tests/auth/public-routes.test.tsx
```

Expected: FAIL because the proxy constructs Supabase with absent values or does not redirect, and the login page still renders the form.

- [ ] **Step 5: Add the minimum proxy guard**

In `dashboard/proxy.ts`, immediately after the existing `/demo` and loopback-root bypasses, treat `env` as partial and return before `createSupabaseSession()`:

```ts
const partialAuthEnv = env as unknown as Partial<AuthEnv>;
if (
  !partialAuthEnv.SUPABASE_URL ||
  !partialAuthEnv.SUPABASE_PUBLISHABLE_KEY
) {
  if (request.nextUrl.pathname === "/") {
    return nextWithAuth(request, false);
  }
  if (
    request.nextUrl.pathname === "/login" &&
    request.nextUrl.searchParams.get("error") === "unavailable"
  ) {
    return nextWithAuth(request, false);
  }
  return NextResponse.redirect(
    new URL("/login?error=unavailable", request.url),
  );
}
const authEnv = partialAuthEnv as AuthEnv;
```

Delete the later unconditional `const authEnv = env as unknown as AuthEnv;` line. Do not change configured-auth claim or cookie behavior.

- [ ] **Step 6: Render the explicit unavailable state**

In `dashboard/app/login/page.tsx`, derive `const unavailable = query.error === "unavailable";`. Keep password-reset success visible, but render this branch instead of the form and account links:

```tsx
{query.reset === "1" && <p role="status">비밀번호가 변경되었습니다.</p>}
{unavailable ? (
  <>
    <p role="status">
      실시간 연결 준비 중입니다. 현재는 공개 데모를 확인해 주세요.
    </p>
    <p>
      <a href="/demo">데모 보기</a>
    </p>
  </>
) : (
  <>
    {query.error && <p role="alert">이메일 또는 비밀번호를 확인하세요.</p>}
    <form className="auth-form" action="/auth/login" method="post">
      <label>
        이메일
        <input name="email" type="email" autoComplete="email" required />
      </label>
      <label>
        비밀번호
        <input
          name="password"
          type="password"
          autoComplete="current-password"
          required
        />
      </label>
      <button type="submit">로그인</button>
    </form>
    <p>
      <a href="/forgot-password">비밀번호를 잊으셨나요?</a>
    </p>
    <p>
      <a href="/signup">계정 만들기</a>
    </p>
  </>
)}
```

- [ ] **Step 7: Run the focused GREEN tests**

Run the same two files. Expected: all selected tests PASS, with configured-auth tests unchanged.

- [ ] **Step 8: Review Task 1 diff**

Run:

```powershell
git diff --check
git diff -- dashboard/proxy.ts dashboard/app/login/page.tsx dashboard/tests/auth/session.test.ts dashboard/tests/auth/public-routes.test.tsx
```

Expected: no whitespace errors, no session construction on the missing-env path, no weakening of protected routes when auth is configured.

---

### Task 2: Public hardware connection guide and documentation truth

**Files:**
- Modify: `dashboard/components/landing/landing-copy.ts`
- Test: `dashboard/tests/landing/landing-page.test.tsx`
- Modify: `README.md`
- Modify: `dashboard/README.md`
- Modify: `docs/demo-runbook.md`
- Modify: `docs/implementation-plan.md`
- Modify: `docs/privacy.md`
- Modify: `tools/docs_check.py`
- Test: `tools/tests/test_docs_check.py`

**Interfaces:**
- Consumes: `LANDING_CHAPTERS`, existing semantic chapter rendering, Home Agent/Jetson design contracts, structured documentation blocks parsed by `tools/docs_check.py`.
- Produces: public `connect` chapter; consistent `public shell + protected live data` documentation; docs checker expecting the new public deployment contract.

- [ ] **Step 1: Write the failing landing guide test**

Add these assertions to the existing no-WebGL landing test in `dashboard/tests/landing/landing-page.test.tsx`:

```tsx
expect(
  screen.getByRole("heading", { name: "기기는 Home Agent를 통해 연결합니다" }),
).toBeInTheDocument();
expect(screen.getByText(/연결만으로 사이트에 표시되지는 않습니다/)).toBeInTheDocument();
expect(screen.getByText(/Jetson.*Pico 2 W.*MQTT/s)).toBeInTheDocument();
```

- [ ] **Step 2: Run the focused landing test RED**

Run:

```powershell
$runtime = Get-Content -Raw .runtime/toolchain.json | ConvertFrom-Json
& $runtime.paths.node_path $runtime.paths.npm_cli_path --prefix dashboard test -- tests/landing/landing-page.test.tsx
```

Expected: FAIL because the connection chapter does not exist.

- [ ] **Step 3: Add one semantic connection chapter**

Append to `LANDING_CHAPTERS` in `dashboard/components/landing/landing-copy.ts`:

```ts
{
  id: "connect",
  title: "기기는 Home Agent를 통해 연결합니다",
  body: "Jetson은 USB 카메라를 분석해 Home Agent로 전달하고, Pico 2 W는 Wi-Fi와 MQTT로 센서 값을 보냅니다. 연결만으로 사이트에 표시되지는 않습니다. Home Agent 등록과 서비스 실행이 완료되어야 합니다.",
},
```

Do not add another component, icon system, animation, dependency, or direct device link. The existing chapter map and responsive CSS own the new section.

- [ ] **Step 4: Run the focused landing test GREEN**

Run the same landing file. Expected: PASS with semantic heading/body available without WebGL.

- [ ] **Step 5: Replace stale private-Sites documentation**

Make these exact semantic changes while preserving historical hardware `NOT RUN` claims:

- `README.md`: replace `소유자 전용 Sites /demo` with `공개 Sites 랜딩·데모와 로그인 보호 실데이터`.
- `dashboard/README.md`: describe a public landing/demo and authenticated remote dashboard; remove the recommendation to retain an owner-only outer Sites boundary.
- `docs/privacy.md`: state that Sites itself is public, `/demo` remains fixture-only, and real camera/sensor/enrollment/clip routes remain Supabase-authenticated and tenant-scoped.
- `docs/implementation-plan.md`: change the Sites row and structured `implemented` entry from private Sites to public Sites shell with protected live data.
- `docs/demo-runbook.md`: rename `Private Sites` to `Public Sites`, change the source chain to `public deployment`, set access to `public`, and verify anonymous `/` plus `/demo` instead of owner authentication.

Use these structured values in `docs/demo-runbook.md` and `tools/docs_check.py`:

```json
{
  "source_chain": [
    "dashboard subtree split",
    "tree equality",
    "per-command source credential",
    "vinext build",
    "Sites archive",
    "saved version",
    "public deployment",
    "status poll",
    "anonymous / and /demo"
  ],
  "access": "public"
}
```

Use this delivery status in `docs/implementation-plan.md` and `tools/docs_check.py`:

```json
{
  "implemented": [
    "pico firmware",
    "backend",
    "dashboard",
    "local-live integration",
    "CI",
    "public Sites shell with protected live data"
  ],
  "sites_production": "PASS",
  "physical_hardware": "NOT RUN",
  "deferred": ["physical installation evidence"]
}
```

- [ ] **Step 6: Run the focused documentation check**

Run with the pinned Python runtime:

```powershell
$runtime = Get-Content -Raw .runtime/toolchain.json | ConvertFrom-Json
& $runtime.paths.python_path -m pytest tools/tests/test_docs_check.py -q
& $runtime.paths.python_path tools/docs_check.py --root .
```

Expected: tests PASS and `docs_check.py` exits `0` with the new public contract.

- [ ] **Step 7: Review Task 2 diff**

Run:

```powershell
git diff --check
rg -n -i "owner-only Sites|private Sites|private deployment|소유자 전용 Sites|custom owner-only" README.md dashboard/README.md docs tools/docs_check.py
```

Expected: no stale deployment-access claim; owner-only local secret/file permissions may remain because they are unrelated and correct.

---

### Task 3: New current-account Sites project and exact candidate gate

**Files:**
- Modify: `dashboard/.openai/hosting.json`
- Verify only: all Task 1 and Task 2 files

**Interfaces:**
- Consumes: the current account's Sites connector, binding names `DB` and `CLIPS`, repository-pinned runtime, current branch `codex/petcare-mvp`.
- Produces: one new Sites `project_id`; one exact committed/pushed candidate; local full-suite and remote CI evidence for that SHA.

- [ ] **Step 1: Measure resources and ensure no heavy process is running**

Record free RAM and three-sample average CPU. Spawn no new worker when free RAM is below 3 GB or CPU is above 80%. Keep the final full suite as the only heavy local process.

- [ ] **Step 2: Create exactly one new Sites project**

Call the current account's Sites create-project operation once with the PetCare project name/slug. Preserve the returned project ID, source repository credential, and URL in memory only. If the slug conflicts, follow the connector's explicit conflict response; do not create speculative duplicates.

- [ ] **Step 3: Persist only the new project ID**

Update `dashboard/.openai/hosting.json` to keep `d1` equal to `DB`, keep `r2` equal to `CLIPS`, and set `project_id` to the exact opaque ID returned by Step 2. Do not synthesize, shorten, or copy the inaccessible old ID.

Do not change or add environment variables during this deployment.

- [ ] **Step 4: Run the completed feature component gate once**

Run the selected dashboard auth/landing suites and docs checker together, BelowNormal with redirected stdio:

```powershell
$runtime = Get-Content -Raw .runtime/toolchain.json | ConvertFrom-Json
& $runtime.paths.node_path $runtime.paths.npm_cli_path --prefix dashboard test -- tests/auth/session.test.ts tests/auth/public-routes.test.tsx tests/landing/landing-page.test.tsx tests/landing/metadata.test.ts tests/landing/scene-runtime-contract.test.ts
& $runtime.paths.python_path -m pytest tools/tests/test_docs_check.py -q
& $runtime.paths.python_path tools/docs_check.py --root .
```

Expected: all selected tests PASS and docs check exits `0`.

- [ ] **Step 5: Run the exact-candidate full gate once**

Run `tools/check_all.ps1` detached/BelowNormal with stdout/stderr redirected, polling every approximately 30 seconds. Do not start a browser, Remotion render, or another full suite concurrently.

Expected: exit `0`, including backend, firmware-host, dashboard, docs, build, and repository checks defined by the script.

- [ ] **Step 6: Inspect and commit only the requested changes**

Run:

```powershell
git diff --check
git status --short
git diff --stat
```

Confirm `.codex/`, `.omo/drafts/petcare-sites-completion.md`, and root `node_modules/` remain untracked and unstaged. Stage the Task 1/Task 2 files plus `dashboard/.openai/hosting.json` and this plan, then commit:

```powershell
git commit -m "fix: keep public PetCare entry available"
```

- [ ] **Step 7: Push the exact candidate and verify CI**

Push `codex/petcare-mvp`, identify the GitHub Actions run whose `headSha` equals the new commit, and poll without re-running local tests. Expected: every required job succeeds for the exact SHA.

---

### Task 4: Save, publicly deploy, and verify the exact dashboard source

**Files:**
- Verify only: `dashboard/**` at the committed candidate tree
- Do not modify: Sites runtime environment values

**Interfaces:**
- Consumes: new Sites project ID/source credential, exact candidate dashboard tree, Sites packaging helper version `0.1.30`.
- Produces: saved Sites version, public succeeded deployment, final public URL, browser QA evidence, Jetson/Pico onboarding handoff.

- [ ] **Step 1: Push the exact dashboard source to the Sites repository**

Create a temporary dashboard subtree commit, prove its tree equals the candidate's `dashboard` tree, and push it to the returned Sites source repository using the credential for that one Git process only. Do not save the credential in a remote, config, file, log, or shell history.

- [ ] **Step 2: Package and save one Sites version**

Build with the pinned Node runtime and package using:

```text
C:\Users\전산1-4\.codex\plugins\cache\openai-bundled\sites\0.1.30\scripts\package-site.sh
```

Save one version against the new project. If save fails, retry against the same project and source SHA.

- [ ] **Step 3: Deploy the saved version publicly**

Deploy the saved version with public access, then poll the exact deployment ID until it reaches `succeeded` or a terminal failure. Confirm the final access policy is public; do not rely on the old owner-only project.

- [ ] **Step 4: Run one production browser QA pass**

As the only heavy process, inspect desktop and mobile widths and verify:

- anonymous `/` renders the 3D-capable landing and semantic fallback;
- `/demo` renders the bundled PetCare operating dashboard;
- no Supabase configuration produces the explicit `실시간 연결 준비 중입니다` state rather than a stack trace;
- the connection guide explains Jetson, Home Agent, Pico 2 W, MQTT, and the non-automatic publishing boundary;
- keyboard focus, reduced motion, contrast, responsive layout, and approved Open Graph media remain usable;
- anonymous requests cannot read live sensor, camera, enrollment, or clip data.

- [ ] **Step 5: Verify the physical onboarding boundary without mutating hardware**

Report the operational sequence:

```text
Sites 10-minute enrollment code
  -> Home Agent `agent_runtime enroll`
  -> Jetson private-LAN pairing and Home Agent service
  -> Pico firmware built with Wi-Fi/MQTT secrets and flashed
  -> Pico MQTT readings reach the enrolled Home Agent
  -> authenticated Sites dashboard polls the Home Agent BFF
```

SSH connectivity alone is not proof of Jetson service readiness. Do not install remote Codex or packages. Keep Jetson 60-minute soak and Pico physical acceptance as `NOT RUN` until executed on the actual devices.

- [ ] **Step 6: Final evidence report**

Return the new URL, project/version/deployment IDs, exact Git SHA, local full-gate result, exact-SHA CI result, browser QA result, and the remaining hardware `NOT RUN` items. Never include secrets or source credentials.

---

## Self-Review

- Spec coverage: public routes, missing-auth failure behavior, connection copy, protected live boundary, new-account project, public deployment, exact candidate verification, browser QA, and Jetson/Pico onboarding map to Tasks 1-4.
- Completeness scan: no unfinished marker, deferred code comment, or invented runtime identifier remains; the Sites project ID is sourced only from the create-project response.
- Type consistency: `Partial<AuthEnv>` is narrowed before the existing `AuthEnv` session path; the request marker remains `x-petcare-authenticated`; documentation values match `tools/docs_check.py` exactly.
- Scope: no anonymous live data, new auth provider, new UI library, direct device-to-Sites path, or hardware mutation is introduced.
