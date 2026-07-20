# PetCare Auth and Tenancy Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add Supabase email/password authentication with PKCE cookie sessions and a D1-backed, owner-isolated one-home/one-agent/one-camera enrollment registry.

**Architecture:** Supabase owns credentials, verification, reset, logout, refresh, and JWT issuance; PetCare verifies each session server-side and uses only the immutable JWT `sub` as `owner_sub`. D1 stores central tenancy and enrollment metadata, with partial unique indexes enforcing one active home, agent, and camera, while a repository and tenant guard ensure every lookup begins from the verified owner. Enrollment codes are 128-bit random values, stored only as SHA-256 hashes, expire after exactly ten minutes, and are consumed with agent/camera binding in one atomic D1 batch.

**Tech Stack:** Next.js 16 App Router on vinext, TypeScript 5.9, Supabase Auth (`@supabase/ssr` 0.12.3 and `@supabase/supabase-js` 2.110.7), Cloudflare D1, Drizzle ORM 0.45.2, Miniflare 4.20260515.0, Vitest 4.1.10, React Testing Library.

## Global Constraints

- Preserve the existing warm-homecare tokens and responsive behavior in `dashboard/app/globals.css`.
- Public pages are exactly `/login`, `/signup`, `/forgot-password`, `/reset-password`, and `/demo`; `/auth/callback` and form POST handlers are public protocol endpoints, not dashboard pages.
- `/demo` must not construct a Supabase client or make PetCare, tunnel, loopback, WebSocket, or cross-origin media requests.
- Supabase owns password hashes, email verification, password reset, refresh tokens, logout, and session issuance. Do not add a password, reset-token, or application-session table to D1.
- Runtime auth configuration is exactly `SUPABASE_URL` and `SUPABASE_PUBLISHABLE_KEY`. Do not add or use a Supabase service-role key.
- Rely on Supabase Auth's provider-side signup/login/reset throttles; preserve provider `429` as generic `{ error: "rate_limited" }` without adding a D1 rate-limit table.
- Because auth is server-form-only and no browser Supabase client is created, every production session cookie is `HttpOnly`, `Secure`, `SameSite=Lax`, and `Path=/`.
- JWT verification must use Supabase `auth.getClaims()`, which verifies provider-issued tokens through the provider claims/JWKS path, then enforce issuer `${SUPABASE_URL}/auth/v1`, audience `authenticated`, a non-empty `sub`, and expiration.
- Use the immutable verified JWT `sub` as `owner_sub`; email is display-only and nullable.
- Never authorize from client-supplied `user_id`, `owner_sub`, `home_id`, `agent_id`, or `camera_id`.
- Authentication failures return `401`; missing or foreign tenant resources return `404`.
- D1 stores only `homes`, `agents`, `cameras`, and `enrollment_tokens` in this plan. Operational readings, behaviors, anomalies, calibration, and hardware state remain local.
- Enforce one active home per owner, one active agent per home, and one active camera per home with D1 partial unique indexes.
- Enrollment codes use 16 cryptographically random bytes, are stored only as lowercase SHA-256 hex, expire at issuance time plus exactly `600_000` milliseconds, and are single-use.
- Keep these shared interfaces exact:

```ts
export type AuthUser = { sub: string; email: string | null };

export async function requireAuth(
  request: Request,
  env: AuthEnv,
): Promise<AuthUser>;

export class TenantRepository {
  requireHome(ownerSub: string): Promise<HomeRecord>;
}

export function issueEnrollment(
  ownerSub: string,
): Promise<{ code: string; expiresAt: string }>;
```

- Supabase network calls and Cloudflare provisioning/revocation are mocked in automated tests. No account, SMTP, tunnel, Access, D1, paid-plan, public deployment, or other external mutation is part of this plan.
- PetCare account deletion deletes PetCare tenant/data only, not the Supabase identity. It requires immediate current-password reauthentication and never calls a Supabase admin or identity-deletion API.
- PetCare deletion keeps `tenant_cleanup` only while cleanup is pending. Home reads, enrollment, and automatic home creation stay blocked during that window; after cleanup removes the tenant registry and cleanup ledger, the same Supabase identity may start a fresh PetCare flow and create a new home.
- The tunnel provisioning workstream consumes `TenantRepository.consumeEnrollment(input: ConsumeEnrollmentInput): Promise<EnrollmentBinding>` after provisioning and revokes the newly provisioned tunnel if atomic D1 binding fails; it must not redefine these tables or interfaces.

**Authoritative implementation references:** [Supabase SSR sessions and cookie refresh](https://supabase.com/docs/guides/auth/server-side/advanced-guide), [Supabase `getClaims()`](https://supabase.com/docs/reference/javascript/auth-getclaims), [Supabase PKCE flow](https://supabase.com/docs/guides/auth/sessions/pkce-flow), and [Cloudflare D1 atomic batches](https://developers.cloudflare.com/d1/worker-api/d1-database/#batch).

---

## File Structure

### Create

- `dashboard/.env.example` — documents the two non-secret runtime auth configuration names without values.
- `dashboard/drizzle/0000_petcare_tenancy.sql` — creates the four central tables, foreign keys, and partial unique indexes.
- `dashboard/drizzle/meta/0000_snapshot.json` — Drizzle schema snapshot generated with the migration.
- `dashboard/lib/auth/require-auth.ts` — owns `AuthEnv`, `AuthUser`, `AuthError`, and `requireAuth(request, env)`.
- `dashboard/lib/auth/session.ts` — creates request-scoped Supabase SSR clients, applies rotated cookies and no-store headers, validates same-origin form posts, and exposes runtime auth configuration.
- `dashboard/lib/auth/recent-reauth.ts` — verifies the authenticated user's current password immediately before destructive PetCare tenant deletion.
- `dashboard/lib/tenancy/repository.ts` — owns `HomeRecord`, `TenantRepository`, active-home resolution, home creation, enrollment persistence, and atomic enrollment consumption.
- `dashboard/lib/tenancy/enrollment.ts` — generates/hashes codes and owns exact `issueEnrollment(ownerSub)`.
- `dashboard/proxy.ts` — refreshes Supabase cookies and redirects anonymous protected-page requests while leaving the exact public pages available.
- `dashboard/components/auth-card.tsx` — provides the repeated accessible visual frame for four auth forms.
- `dashboard/app/login/page.tsx`, `dashboard/app/signup/page.tsx`, `dashboard/app/forgot-password/page.tsx`, `dashboard/app/reset-password/page.tsx` — public auth pages.
- `dashboard/app/auth/login/route.ts`, `dashboard/app/auth/signup/route.ts`, `dashboard/app/auth/forgot-password/route.ts`, `dashboard/app/auth/reset-password/route.ts`, `dashboard/app/auth/callback/route.ts`, `dashboard/app/auth/logout/route.ts` — Supabase password/PKCE protocol handlers.
- `dashboard/app/api/petcare/enrollment/route.ts` — authenticated owner-only enrollment-code issuance.
- `dashboard/tests/db/schema.test.ts` — exercises the generated migration and database constraints with Miniflare D1.
- `dashboard/tests/tenancy/repository.test.ts` — proves owner-scoped home resolution and atomic enrollment binding.
- `dashboard/tests/tenancy/enrollment.test.ts` — proves hashing, ten-minute expiry, replacement, and collision retry.
- `dashboard/tests/auth/require-auth.test.ts` — proves verified claim mapping and rejection cases.
- `dashboard/tests/auth/session.test.ts` — proves refresh, callback, logout, no-store cookies, and protected/public route behavior.
- `dashboard/tests/auth/recent-reauth.test.ts` — proves current-password verification, generic throttling, and password non-retention.
- `dashboard/tests/auth/public-routes.test.tsx` — proves accessible public forms and mocked Supabase method calls.
- `dashboard/tests/tenancy/enrollment-route.test.ts` — proves two-user isolation, one authentication call, and no client owner override.

### Modify

- `dashboard/db/schema.ts` — define the four Drizzle tables and indexes.
- `dashboard/db/index.ts` — preserve the current no-argument runtime pattern and allow explicit D1 injection for tests.
- `dashboard/drizzle/meta/_journal.json` — register migration `0000_petcare_tenancy`.
- `dashboard/app/globals.css` — add only auth-card/form styles using existing color, border, focus, and reduced-motion tokens.

### Explicitly Unchanged

- `dashboard/package.json` and `dashboard/package-lock.json` — integration Task 1 is the sole writer. This plan consumes its exact dependency pins, lock resolution, and `test:d1` script read-only.
- `dashboard/worker/index.ts` and `dashboard/.openai/hosting.json` — shared Worker routing and real binding declarations are owned by the integration workstream.
- `dashboard/app/demo/page.tsx`, `dashboard/components/dashboard.tsx`, and `dashboard/lib/demo-data.ts` — keeping them untouched preserves the no-client/no-network demo invariant.
- `progress-monitor/**` — excluded from this plan and all validation commands.

---

### Task 1: Create the D1 tenancy schema and executable constraint tests

**Files:**
- Reference read-only: `dashboard/package.json`
- Reference read-only: `dashboard/package-lock.json`
- Modify: `dashboard/db/schema.ts`
- Create: `dashboard/tests/db/schema.test.ts`
- Create: `dashboard/drizzle/0000_petcare_tenancy.sql`
- Create: `dashboard/drizzle/meta/0000_snapshot.json`
- Modify: `dashboard/drizzle/meta/_journal.json`

**Interfaces:**
- Produces: Drizzle exports `homes`, `agents`, `cameras`, and `enrollmentTokens`.
- Produces: SQL columns `owner_sub`, `home_id`, `agent_id`, `public_key`, `tunnel_origin`, `local_camera_id`, `token_hash`, `expires_at`, and nullable lifecycle timestamps.
- Produces: partial unique indexes `homes_one_active_owner`, `agents_one_active_home`, and `cameras_one_active_home`.

- [ ] **Step 1: Verify the integration-owned dependency and test-script prerequisite**

Run from `dashboard/`:

```powershell
npm ls miniflare@4.20260515.0 @supabase/ssr@0.12.3 @supabase/supabase-js@2.110.7 --depth=0
node -e "const p=require('./package.json'); if(p.scripts?.['test:d1']!=='vitest run tests/db tests/tenancy/repository.d1.test.ts') process.exit(1)"
```

Expected: both commands exit `0` after integration Task 1. Do not edit either package manifest in this plan and do not add a second Vitest configuration file. D1 files select Node with Vitest's file-level environment directive while existing UI tests keep the current jsdom default.

- [ ] **Step 2: Write the failing D1 constraint test**

Create `dashboard/tests/db/schema.test.ts` with a Miniflare D1 instance that applies `drizzle/0000_petcare_tenancy.sql`, then assert:

```ts
// @vitest-environment node

import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { Miniflare } from "miniflare";
import { afterEach, beforeEach, describe, expect, it } from "vitest";

let mf: Miniflare;
let db: D1Database;

beforeEach(async () => {
  mf = new Miniflare({
    modules: true,
    script: "export default { fetch() { return new Response('ok') } }",
    d1Databases: ["DB"],
  });
  db = await mf.getD1Database("DB");
  const migration = readFileSync(
    resolve(import.meta.dirname, "../../drizzle/0000_petcare_tenancy.sql"),
    "utf8",
  ).replaceAll("--> statement-breakpoint", "");
  await db.exec(migration);
});

afterEach(async () => mf.dispose());

describe("petcare tenancy schema", () => {
  it("allows only one active home per owner", async () => {
    await db.prepare(
      "INSERT INTO homes (id, owner_sub, created_at) VALUES (?, ?, ?)",
    ).bind("home-a", "owner-a", "2026-07-20T00:00:00.000Z").run();

    await expect(
      db.prepare(
        "INSERT INTO homes (id, owner_sub, created_at) VALUES (?, ?, ?)",
      ).bind("home-b", "owner-a", "2026-07-20T00:00:01.000Z").run(),
    ).rejects.toThrow(/UNIQUE/);
  });

  it("allows only one active agent and camera per home", async () => {
    await db.batch([
      db.prepare("INSERT INTO homes (id, owner_sub, created_at) VALUES (?, ?, ?)")
        .bind("home-a", "owner-a", "2026-07-20T00:00:00.000Z"),
      db.prepare("INSERT INTO agents (id, home_id, public_key, tunnel_origin) VALUES (?, ?, ?, ?)")
        .bind("agent-a", "home-a", "key-a", "https://a.invalid"),
      db.prepare("INSERT INTO cameras (id, home_id, agent_id, local_camera_id, created_at) VALUES (?, ?, ?, ?, ?)")
        .bind("camera-a", "home-a", "agent-a", "usb-0", "2026-07-20T00:00:00.000Z"),
    ]);

    await expect(
      db.prepare("INSERT INTO agents (id, home_id, public_key, tunnel_origin) VALUES (?, ?, ?, ?)")
        .bind("agent-b", "home-a", "key-b", "https://b.invalid").run(),
    ).rejects.toThrow(/UNIQUE/);
    await expect(
      db.prepare("INSERT INTO cameras (id, home_id, agent_id, local_camera_id, created_at) VALUES (?, ?, ?, ?, ?)")
        .bind("camera-b", "home-a", "agent-a", "usb-1", "2026-07-20T00:00:01.000Z").run(),
    ).rejects.toThrow(/UNIQUE/);
  });
});
```

- [ ] **Step 3: Run the test and verify the red state**

Run:

```powershell
npm run test:d1 -- tests/db/schema.test.ts
```

Expected: FAIL because `drizzle/0000_petcare_tenancy.sql` does not exist.

- [ ] **Step 4: Define the four Drizzle tables and indexes**

Replace `dashboard/db/schema.ts` with table definitions equivalent to:

```ts
import { sql } from "drizzle-orm";
import { index, sqliteTable, text, uniqueIndex } from "drizzle-orm/sqlite-core";

export const homes = sqliteTable("homes", {
  id: text("id").primaryKey(),
  ownerSub: text("owner_sub").notNull(),
  createdAt: text("created_at").notNull().default(sql`CURRENT_TIMESTAMP`),
  deletedAt: text("deleted_at"),
}, (table) => [
  uniqueIndex("homes_one_active_owner")
    .on(table.ownerSub)
    .where(sql`${table.deletedAt} IS NULL`),
]);

export const agents = sqliteTable("agents", {
  id: text("id").primaryKey(),
  homeId: text("home_id").notNull().references(() => homes.id, { onDelete: "restrict" }),
  publicKey: text("public_key").notNull(),
  tunnelOrigin: text("tunnel_origin").notNull(),
  lastSeenAt: text("last_seen_at"),
  revokedAt: text("revoked_at"),
}, (table) => [
  uniqueIndex("agents_one_active_home")
    .on(table.homeId)
    .where(sql`${table.revokedAt} IS NULL`),
  index("agents_home_idx").on(table.homeId),
]);

export const cameras = sqliteTable("cameras", {
  id: text("id").primaryKey(),
  homeId: text("home_id").notNull().references(() => homes.id, { onDelete: "restrict" }),
  agentId: text("agent_id").notNull().references(() => agents.id, { onDelete: "restrict" }),
  localCameraId: text("local_camera_id").notNull(),
  createdAt: text("created_at").notNull().default(sql`CURRENT_TIMESTAMP`),
  disabledAt: text("disabled_at"),
}, (table) => [
  uniqueIndex("cameras_one_active_home")
    .on(table.homeId)
    .where(sql`${table.disabledAt} IS NULL`),
  index("cameras_agent_idx").on(table.agentId),
]);

export const enrollmentTokens = sqliteTable("enrollment_tokens", {
  id: text("id").primaryKey(),
  homeId: text("home_id").notNull().references(() => homes.id, { onDelete: "cascade" }),
  tokenHash: text("token_hash").notNull().unique(),
  expiresAt: text("expires_at").notNull(),
  consumedAt: text("consumed_at"),
}, (table) => [index("enrollment_tokens_home_idx").on(table.homeId)]);
```

- [ ] **Step 5: Generate and inspect the exact migration**

Run:

```powershell
npm run db:generate -- --name petcare_tenancy
```

Expected: Drizzle creates `drizzle/0000_petcare_tenancy.sql`, `drizzle/meta/0000_snapshot.json`, and updates `drizzle/meta/_journal.json`. Inspect the SQL and confirm it contains all four `CREATE TABLE` statements, all foreign keys, and partial predicates `WHERE "deleted_at" IS NULL`, `WHERE "revoked_at" IS NULL`, and `WHERE "disabled_at" IS NULL`.

- [ ] **Step 6: Run the D1 test and verify the green state**

Run:

```powershell
npm run test:d1 -- tests/db/schema.test.ts
```

Expected: PASS, 2 tests.

- [ ] **Step 7: Commit the schema unit**

```powershell
git add dashboard/db/schema.ts dashboard/tests/db/schema.test.ts dashboard/drizzle
git commit -m "feat(dashboard): add tenant registry schema"
```

---

### Task 2: Implement owner-scoped home resolution

**Files:**
- Modify: `dashboard/db/index.ts`
- Create: `dashboard/lib/tenancy/repository.ts`
- Create: `dashboard/tests/tenancy/repository.d1.test.ts`

**Interfaces:**
- Consumes: Drizzle `homes`, `agents`, `cameras`, and `enrollmentTokens` from Task 1.
- Produces: `HomeRecord = typeof homes.$inferSelect`.
- Produces: `TenantRepository.requireHome(ownerSub: string): Promise<HomeRecord>`.
- Produces: `TenantRepository.ensureHome(ownerSub: string): Promise<HomeRecord>` for first verified login/callback only.
- Produces: `AccountDeletedError` with `status = 410` and `code = "account_deleted"` while deletion cleanup still blocks automatic home creation.

- [ ] **Step 1: Write failing owner-isolation tests**

Create `dashboard/tests/tenancy/repository.d1.test.ts` using the Task 1 Miniflare setup and assert:

```ts
// @vitest-environment node

it("resolves only the active home owned by the verified subject", async () => {
  const repository = new TenantRepository(getDb(db));
  await repository.ensureHome("owner-a");
  await repository.ensureHome("owner-b");

  const homeA = await repository.requireHome("owner-a");
  const homeB = await repository.requireHome("owner-b");

  expect(homeA.ownerSub).toBe("owner-a");
  expect(homeB.ownerSub).toBe("owner-b");
  expect(homeA.id).not.toBe(homeB.id);
});

it("returns not found when the subject has no active home", async () => {
  const repository = new TenantRepository(getDb(db));
  await expect(repository.requireHome("unknown-owner")).rejects.toMatchObject({
    status: 404,
    code: "home_not_found",
  });
});

it("does not recreate a home while deletion cleanup remains pending", async () => {
  const repository = new TenantRepository(getDb(db));
  const home = await repository.ensureHome("owner-a");
  await db.prepare("UPDATE homes SET deleted_at = ? WHERE id = ?")
    .bind("2026-07-20T03:00:00.000Z", home.id).run();

  await expect(repository.ensureHome("owner-a")).rejects.toMatchObject({
    status: 410,
    code: "account_deleted",
  });
  expect(await db.prepare("SELECT COUNT(*) AS count FROM homes WHERE owner_sub = ?")
    .bind("owner-a").first()).toEqual({ count: 1 });
});
```

- [ ] **Step 2: Run the tests and verify they fail**

Run:

```powershell
npm run test:d1 -- tests/tenancy/repository.d1.test.ts
```

Expected: FAIL because `TenantRepository` and injectable `getDb(db)` do not exist.

- [ ] **Step 3: Make `getDb` injectable without changing its runtime call site**

Change `dashboard/db/index.ts` to retain `getDb()` while accepting a test binding:

```ts
import { env } from "cloudflare:workers";
import { drizzle } from "drizzle-orm/d1";
import * as schema from "./schema";

export function getDb(binding: D1Database | undefined = env.DB) {
  if (!binding) {
    throw new Error("Cloudflare D1 binding `DB` is unavailable.");
  }
  return drizzle(binding, { schema });
}

export type PetCareDb = ReturnType<typeof getDb>;
```

- [ ] **Step 4: Implement the minimal repository**

Create `dashboard/lib/tenancy/repository.ts` with the exact shared method and a narrow typed error:

```ts
import { and, eq, isNotNull, isNull } from "drizzle-orm";
import type { PetCareDb } from "../../db";
import { homes } from "../../db/schema";

export type HomeRecord = typeof homes.$inferSelect;

export class TenantNotFoundError extends Error {
  readonly status = 404;
  readonly code = "home_not_found";
}

export class AccountDeletedError extends Error {
  readonly status = 410;
  readonly code = "account_deleted";
}

export class TenantRepository {
  constructor(private readonly db: PetCareDb) {}

  async requireHome(ownerSub: string): Promise<HomeRecord> {
    const [home] = await this.db.select().from(homes).where(and(
      eq(homes.ownerSub, ownerSub),
      isNull(homes.deletedAt),
    )).limit(1);
    if (!home) throw new TenantNotFoundError("Active home not found");
    return home;
  }

  async ensureHome(ownerSub: string): Promise<HomeRecord> {
    if (!ownerSub) throw new TenantNotFoundError("Active home not found");
    const [active] = await this.db.select().from(homes).where(and(
      eq(homes.ownerSub, ownerSub),
      isNull(homes.deletedAt),
    )).limit(1);
    if (active) return active;
    const [deleted] = await this.db.select({ id: homes.id }).from(homes).where(and(
      eq(homes.ownerSub, ownerSub),
      isNotNull(homes.deletedAt),
    )).limit(1);
    if (deleted) throw new AccountDeletedError("PetCare account deleted");
    try {
      await this.db.insert(homes).values({
        id: crypto.randomUUID(),
        ownerSub,
        createdAt: new Date().toISOString(),
      }).onConflictDoNothing();
    } catch (error) {
      if (error instanceof Error && error.message.includes("account_deleted")) {
        throw new AccountDeletedError("PetCare account deleted");
      }
      throw error;
    }
    return this.requireHome(ownerSub);
  }
}
```

- [ ] **Step 5: Run the focused D1 tests**

Run:

```powershell
npm run test:d1 -- tests/tenancy/repository.d1.test.ts
```

Expected: PASS, 3 tests, including `410 account_deleted` and no recreation while the soft-deleted owner row marks pending cleanup. The later BFF cleanup test owns proof that removing both the tenant registry and cleanup ledger restores a fresh `ensureHome` flow for the same `owner_sub`.

- [ ] **Step 6: Commit the owner-isolation unit**

```powershell
git add dashboard/db/index.ts dashboard/lib/tenancy/repository.ts dashboard/tests/tenancy/repository.d1.test.ts
git commit -m "feat(dashboard): resolve homes by verified owner"
```

---

### Task 3: Issue hashed ten-minute enrollment codes

**Files:**
- Modify: `dashboard/lib/tenancy/repository.ts`
- Create: `dashboard/lib/tenancy/enrollment.ts`
- Create: `dashboard/tests/tenancy/enrollment.test.ts`
- Reference: `contracts/petcare-agent-wire-v1.json` as the cross-plan enrollment wire-contract fixture.

**Interfaces:**
- Consumes: `TenantRepository.requireHome(ownerSub)`.
- Produces: exact `issueEnrollment(ownerSub: string): Promise<{ code: string; expiresAt: string }>`.
- Produces: `hashEnrollmentCode(code: string): Promise<string>` for the unauthenticated agent redemption path.
- Produces: `TenantRepository.replaceEnrollmentToken(homeId, tokenHash, expiresAt)`; a new issue invalidates any older unconsumed code for that home.
- Produces: a code from exactly 16 random bytes, encoded as exactly 22 unpadded base64url characters; the canonical deterministic fixture is `AQEBAQEBAQEBAQEBAQEBAQ`.

- [ ] **Step 1: Write the failing issue, hashing, expiry, replacement, and collision tests**

Create `dashboard/tests/tenancy/enrollment.test.ts` with a repository mock and deterministic clock/code source:

```ts
import { afterEach, beforeEach, expect, it, vi } from "vitest";

const mocks = vi.hoisted(() => ({
  requireHome: vi.fn(),
  replaceEnrollmentToken: vi.fn(),
}));

vi.mock("../../db", () => ({ getDb: vi.fn(() => ({ binding: "test" })) }));
vi.mock("../../lib/tenancy/repository", () => ({
  TenantRepository: class {
    requireHome = mocks.requireHome;
    replaceEnrollmentToken = mocks.replaceEnrollmentToken;
  },
}));

import { hashEnrollmentCode, issueEnrollment } from "../../lib/tenancy/enrollment";

beforeEach(() => {
  vi.useFakeTimers();
  vi.setSystemTime(new Date("2026-07-20T03:00:00.000Z"));
  mocks.requireHome.mockResolvedValue({ id: "home-a" });
  mocks.replaceEnrollmentToken.mockResolvedValue(undefined);
});

afterEach(() => {
  vi.useRealTimers();
  vi.restoreAllMocks();
  vi.clearAllMocks();
});

it("stores only the hash and expires exactly ten minutes after issue", async () => {
  vi.spyOn(crypto, "getRandomValues").mockImplementation((array) => {
    (array as Uint8Array).fill(1);
    return array;
  });

  const issued = await issueEnrollment("owner-a");
  expect(issued).toEqual({
    code: "AQEBAQEBAQEBAQEBAQEBAQ",
    expiresAt: "2026-07-20T03:10:00.000Z",
  });
  expect(issued.code).toHaveLength(22);
  expect(issued.code).toMatch(/^[A-Za-z0-9_-]{22}$/);
  expect(mocks.requireHome).toHaveBeenCalledWith("owner-a");
  expect(mocks.requireHome).toHaveBeenCalledTimes(1);
  expect(mocks.replaceEnrollmentToken).toHaveBeenCalledWith(
    "home-a",
    await hashEnrollmentCode("AQEBAQEBAQEBAQEBAQEBAQ"),
    "2026-07-20T03:10:00.000Z",
  );
  expect(mocks.replaceEnrollmentToken).not.toHaveBeenCalledWith(
    "home-a",
    "AQEBAQEBAQEBAQEBAQEBAQ",
    expect.any(String),
  );
});

it("retries a unique-hash collision and returns only the successful code", async () => {
  mocks.replaceEnrollmentToken
    .mockRejectedValueOnce(Object.assign(new Error("UNIQUE"), { code: "SQLITE_CONSTRAINT_UNIQUE" }))
    .mockResolvedValueOnce(undefined);
  vi.spyOn(crypto, "getRandomValues")
    .mockImplementationOnce((array) => {
      (array as Uint8Array).fill(0);
      return array;
    })
    .mockImplementationOnce((array) => {
      (array as Uint8Array).fill(1);
      return array;
    });

  await expect(issueEnrollment("owner-a")).resolves.toEqual({
    code: "AQEBAQEBAQEBAQEBAQEBAQ",
    expiresAt: "2026-07-20T03:10:00.000Z",
  });
  expect(crypto.getRandomValues).toHaveBeenCalledTimes(2);
});
```

- [ ] **Step 2: Run the tests and verify they fail**

Run:

```powershell
npm test -- tests/tenancy/enrollment.test.ts
```

Expected: FAIL because `issueEnrollment` and `hashEnrollmentCode` do not exist.

- [ ] **Step 3: Add atomic token replacement to the repository**

Add this method to `TenantRepository`:

```ts
async replaceEnrollmentToken(
  homeId: string,
  tokenHash: string,
  expiresAt: string,
): Promise<void> {
  await this.db.batch([
    this.db.delete(enrollmentTokens).where(and(
      eq(enrollmentTokens.homeId, homeId),
      isNull(enrollmentTokens.consumedAt),
    )),
    this.db.insert(enrollmentTokens).values({
      id: crypto.randomUUID(),
      homeId,
      tokenHash,
      expiresAt,
    }),
  ]);
}
```

Import `enrollmentTokens` from `dashboard/db/schema.ts`.

- [ ] **Step 4: Implement code generation, hashing, and issuance**

Create `dashboard/lib/tenancy/enrollment.ts`:

```ts
import { getDb } from "../../db";
import { TenantRepository } from "./repository";

const ENROLLMENT_TTL_MS = 600_000;
const MAX_COLLISION_ATTEMPTS = 3;

function generateEnrollmentCode(): string {
  const bytes = crypto.getRandomValues(new Uint8Array(16));
  const binary = Array.from(bytes, (byte) => String.fromCharCode(byte)).join("");
  return btoa(binary).replaceAll("+", "-").replaceAll("/", "_").replace(/=+$/, "");
}

export async function hashEnrollmentCode(code: string): Promise<string> {
  const digest = await crypto.subtle.digest(
    "SHA-256",
    new TextEncoder().encode(code),
  );
  return Array.from(new Uint8Array(digest), (byte) =>
    byte.toString(16).padStart(2, "0"),
  ).join("");
}

export async function issueEnrollment(
  ownerSub: string,
): Promise<{ code: string; expiresAt: string }> {
  const repository = new TenantRepository(getDb());
  const home = await repository.requireHome(ownerSub);
  const expiresAt = new Date(Date.now() + ENROLLMENT_TTL_MS).toISOString();

  for (let attempt = 0; attempt < MAX_COLLISION_ATTEMPTS; attempt += 1) {
    const code = generateEnrollmentCode();
    try {
      await repository.replaceEnrollmentToken(
        home.id,
        await hashEnrollmentCode(code),
        expiresAt,
      );
      return { code, expiresAt };
    } catch (error) {
      if (!String(error).includes("UNIQUE") || attempt === MAX_COLLISION_ATTEMPTS - 1) {
        throw error;
      }
    }
  }
  throw new Error("Enrollment code generation failed");
}
```

- [ ] **Step 5: Run the focused tests**

Run:

```powershell
npm test -- tests/tenancy/enrollment.test.ts
npm run test:d1 -- tests/tenancy/repository.d1.test.ts
```

Expected: both commands PASS; the issue suite reports 2 tests.

- [ ] **Step 6: Commit the issuance unit**

```powershell
git add dashboard/lib/tenancy/repository.ts dashboard/lib/tenancy/enrollment.ts dashboard/tests/tenancy/enrollment.test.ts dashboard/tests/tenancy/repository.d1.test.ts
git commit -m "feat(dashboard): issue one-time enrollment codes"
```

---

### Task 4: Atomically consume enrollment and bind one agent/camera

**Files:**
- Modify: `dashboard/lib/tenancy/repository.ts`
- Modify: `dashboard/tests/tenancy/repository.d1.test.ts`

**Interfaces:**
- Consumes: SHA-256 `codeHash` produced by `hashEnrollmentCode`.
- Produces:

```ts
export type ConsumeEnrollmentInput = {
  codeHash: string;
  consumedAt: string;
  agent: { id: string; publicKey: string; tunnelOrigin: string };
  camera: { id: string; localCameraId: string };
};

export type EnrollmentBinding = {
  homeId: string;
  agentId: string;
  cameraId: string;
};

TenantRepository.consumeEnrollment(
  input: ConsumeEnrollmentInput,
): Promise<EnrollmentBinding>;
```

- Produces: `EnrollmentRejectedError` with `status = 409` and `code = "enrollment_rejected"` for expiry, reuse, active-binding conflicts, and unknown codes.
- Produces: `TenantInfrastructureError` with `status = 503` and `code = "tenancy_unavailable"` for unexpected D1/runtime failures; it never includes the raw database error.

- [ ] **Step 1: Add failing D1 tests for success, reuse, expiry, active-binding conflicts, and rollback**

Extend `dashboard/tests/tenancy/repository.d1.test.ts` with a helper that seeds a home and hashed token, then assert:

```ts
it("consumes a valid code once and binds its home atomically", async () => {
  const repository = new TenantRepository(getDb(db));
  const home = await repository.ensureHome("owner-a");
  await repository.replaceEnrollmentToken(
    home.id,
    "valid-hash",
    "2026-07-20T03:10:00.000Z",
  );
  const input = {
    codeHash: "valid-hash",
    consumedAt: "2026-07-20T03:05:00.000Z",
    agent: { id: "agent-a", publicKey: "public-a", tunnelOrigin: "https://a.invalid" },
    camera: { id: "camera-a", localCameraId: "usb-0" },
  };

  await expect(repository.consumeEnrollment(input)).resolves.toEqual({
    homeId: home.id,
    agentId: "agent-a",
    cameraId: "camera-a",
  });
  await expect(repository.consumeEnrollment(input)).rejects.toMatchObject({
    status: 409,
    code: "enrollment_rejected",
  });
});

it("rejects expiry without consuming or binding", async () => {
  const repository = new TenantRepository(getDb(db));
  const home = await repository.ensureHome("owner-a");
  await repository.replaceEnrollmentToken(
    home.id,
    "expired-hash",
    "2026-07-20T03:00:00.000Z",
  );

  await expect(repository.consumeEnrollment({
    codeHash: "expired-hash",
    consumedAt: "2026-07-20T03:00:00.001Z",
    agent: { id: "agent-a", publicKey: "public-a", tunnelOrigin: "https://a.invalid" },
    camera: { id: "camera-a", localCameraId: "usb-0" },
  })).rejects.toMatchObject({ code: "enrollment_rejected" });

  const token = await db.prepare(
    "SELECT consumed_at FROM enrollment_tokens WHERE token_hash = ?",
  ).bind("expired-hash").first<{ consumed_at: string | null }>();
  expect(token?.consumed_at).toBeNull();
  expect(await db.prepare("SELECT COUNT(*) AS count FROM agents").first<{ count: number }>()).toEqual({ count: 0 });
});
```

Add these two conflict/rollback tests:

```ts
it("rejects a second active agent and leaves the fresh token unused", async () => {
  const repository = new TenantRepository(getDb(db));
  const home = await repository.ensureHome("owner-a");
  await db.prepare(
    "INSERT INTO agents (id, home_id, public_key, tunnel_origin) VALUES (?, ?, ?, ?)",
  ).bind("agent-existing", home.id, "key-existing", "https://existing.invalid").run();
  await repository.replaceEnrollmentToken(
    home.id,
    "second-agent-hash",
    "2026-07-20T03:10:00.000Z",
  );

  await expect(repository.consumeEnrollment({
    codeHash: "second-agent-hash",
    consumedAt: "2026-07-20T03:05:00.000Z",
    agent: { id: "agent-new", publicKey: "key-new", tunnelOrigin: "https://new.invalid" },
    camera: { id: "camera-new", localCameraId: "usb-1" },
  })).rejects.toMatchObject({ code: "enrollment_rejected" });

  expect(await db.prepare("SELECT id FROM agents WHERE id = ?").bind("agent-new").first()).toBeNull();
  expect(await db.prepare("SELECT consumed_at FROM enrollment_tokens WHERE token_hash = ?")
    .bind("second-agent-hash").first()).toEqual({ consumed_at: null });
});

it("rolls back a new agent when an active camera blocks binding", async () => {
  const repository = new TenantRepository(getDb(db));
  const home = await repository.ensureHome("owner-a");
  await db.batch([
    db.prepare("INSERT INTO agents (id, home_id, public_key, tunnel_origin, revoked_at) VALUES (?, ?, ?, ?, ?)")
      .bind("agent-old", home.id, "key-old", "https://old.invalid", "2026-07-20T03:00:00.000Z"),
    db.prepare("INSERT INTO cameras (id, home_id, agent_id, local_camera_id, created_at) VALUES (?, ?, ?, ?, ?)")
      .bind("camera-existing", home.id, "agent-old", "usb-0", "2026-07-20T02:00:00.000Z"),
  ]);
  await repository.replaceEnrollmentToken(
    home.id,
    "camera-conflict-hash",
    "2026-07-20T03:10:00.000Z",
  );

  await expect(repository.consumeEnrollment({
    codeHash: "camera-conflict-hash",
    consumedAt: "2026-07-20T03:05:00.000Z",
    agent: { id: "agent-new", publicKey: "key-new", tunnelOrigin: "https://new.invalid" },
    camera: { id: "camera-new", localCameraId: "usb-1" },
  })).rejects.toMatchObject({ code: "enrollment_rejected" });

  expect(await db.prepare("SELECT id FROM agents WHERE id = ?").bind("agent-new").first()).toBeNull();
  expect(await db.prepare("SELECT consumed_at FROM enrollment_tokens WHERE token_hash = ?")
    .bind("camera-conflict-hash").first()).toEqual({ consumed_at: null });
});
```

These checks prove the D1 batch rolls back the agent insert when the camera constraint fails.

Add a failure-classification test that does not expose infrastructure detail:

```ts
it("preserves an unexpected D1 failure as a secret-safe 503", async () => {
  const client = {
    prepare: vi.fn(() => ({ bind: vi.fn(() => ({})) })),
    batch: vi.fn().mockRejectedValue(new Error("D1_ERROR: connection reset at internal-host")),
  };
  const repository = new TenantRepository({ $client: client } as never);

  const failure = repository.consumeEnrollment({
    codeHash: "valid-hash",
    consumedAt: "2026-07-20T03:05:00.000Z",
    agent: { id: "agent-a", publicKey: "public-a", tunnelOrigin: "https://a.invalid" },
    camera: { id: "camera-a", localCameraId: "usb-0" },
  });
  await expect(failure).rejects.toMatchObject({
    status: 503,
    code: "tenancy_unavailable",
    message: "Tenancy unavailable",
  });
  await expect(failure).rejects.not.toThrow(/connection reset|internal-host/);
});
```

- [ ] **Step 2: Run the D1 tests and verify they fail**

Run:

```powershell
npm run test:d1 -- tests/tenancy/repository.d1.test.ts
```

Expected: FAIL because `consumeEnrollment`, `EnrollmentRejectedError`, and `TenantInfrastructureError` do not exist.

- [ ] **Step 3: Implement one atomic D1 batch**

Add the exported input/result types, error, and this method to `TenantRepository`:

```ts
export class EnrollmentRejectedError extends Error {
  readonly status = 409;
  readonly code = "enrollment_rejected";
}

export class TenantInfrastructureError extends Error {
  readonly status = 503;
  readonly code = "tenancy_unavailable";
}

function isEnrollmentConstraint(error: unknown): boolean {
  const message = error instanceof Error ? `${error.name} ${error.message}` : "";
  return /SQLITE_CONSTRAINT|UNIQUE constraint failed|FOREIGN KEY constraint failed/.test(message);
}

async consumeEnrollment(
  input: ConsumeEnrollmentInput,
): Promise<EnrollmentBinding> {
  const client = this.db.$client;
  try {
    const results = await client.batch([
      client.prepare(`
        INSERT INTO agents (id, home_id, public_key, tunnel_origin)
        SELECT ?, home_id, ?, ? FROM enrollment_tokens
        WHERE token_hash = ? AND consumed_at IS NULL AND expires_at > ?
      `).bind(
        input.agent.id,
        input.agent.publicKey,
        input.agent.tunnelOrigin,
        input.codeHash,
        input.consumedAt,
      ),
      client.prepare(`
        INSERT INTO cameras (id, home_id, agent_id, local_camera_id, created_at)
        SELECT ?, home_id, ?, ?, ? FROM enrollment_tokens
        WHERE token_hash = ? AND consumed_at IS NULL AND expires_at > ?
      `).bind(
        input.camera.id,
        input.agent.id,
        input.camera.localCameraId,
        input.consumedAt,
        input.codeHash,
        input.consumedAt,
      ),
      client.prepare(`
        UPDATE enrollment_tokens SET consumed_at = ?
        WHERE token_hash = ? AND consumed_at IS NULL AND expires_at > ?
        RETURNING home_id
      `).bind(input.consumedAt, input.codeHash, input.consumedAt),
    ]);
    const homeId = results[2].results[0]?.home_id;
    if (results[0].meta.changes !== 1 || results[1].meta.changes !== 1 || typeof homeId !== "string") {
      throw new EnrollmentRejectedError("Enrollment rejected");
    }
    return { homeId, agentId: input.agent.id, cameraId: input.camera.id };
  } catch (error) {
    if (error instanceof EnrollmentRejectedError) throw error;
    if (isEnrollmentConstraint(error)) {
      throw new EnrollmentRejectedError("Enrollment rejected");
    }
    throw new TenantInfrastructureError("Tenancy unavailable");
  }
}
```

Do not use a broad `catch` that converts every failure to `409`; only expected constraint/rejection paths are client conflicts. All other failures remain retryable `503` responses with no D1 message, SQL, binding name, or internal address.

The prepared batch is the transaction boundary: D1 executes it sequentially and rolls back the entire batch if the active-agent or active-camera constraint fails. A reused, expired, or unknown hash changes zero rows and produces the same non-enumerating error.

- [ ] **Step 4: Run all tenancy D1 tests**

Run:

```powershell
npm run test:d1
```

Expected: PASS for schema and repository suites, including success, reuse, expiry, active-agent conflict, active-camera conflict, and rollback assertions.

- [ ] **Step 5: Commit the atomic-consumption unit**

```powershell
git add dashboard/lib/tenancy/repository.ts dashboard/tests/tenancy/repository.d1.test.ts
git commit -m "feat(dashboard): consume enrollment atomically"
```

---

### Task 5: Verify Supabase JWT claims server-side

**Files:**
- Reference read-only: `dashboard/package.json`
- Reference read-only: `dashboard/package-lock.json`
- Create: `dashboard/.env.example`
- Create: `dashboard/lib/auth/require-auth.ts`
- Create: `dashboard/tests/auth/require-auth.test.ts`

**Interfaces:**
- Produces: `AuthEnv { SUPABASE_URL: string; SUPABASE_PUBLISHABLE_KEY: string }`.
- Produces: exact `AuthUser { sub: string; email: string | null }`.
- Produces: exact `requireAuth(request, env): Promise<AuthUser>`.
- Produces: `AuthError` with `status = 401` and `code = "unauthorized"`.

- [ ] **Step 1: Verify the integration-owned Supabase dependencies and create the environment template**

Run from `dashboard/`:

```powershell
npm ls @supabase/ssr@0.12.3 @supabase/supabase-js@2.110.7 --depth=0
```

Create `dashboard/.env.example`:

```dotenv
SUPABASE_URL=
SUPABASE_PUBLISHABLE_KEY=
```

Expected: the dependency check exits `0`; only the example file changes in this step, and no real keys are created or stored. If the check fails, return to integration Task 1 rather than editing either package manifest here.

- [ ] **Step 2: Write failing verified-claims tests**

Create `dashboard/tests/auth/require-auth.test.ts`. Mock `createServerClient` so `auth.getClaims()` returns each case, and use this table:

```ts
const env = {
  SUPABASE_URL: "https://project-ref.supabase.co",
  SUPABASE_PUBLISHABLE_KEY: "sb_publishable_test",
};

it("returns only sub and nullable email from verified JWKS claims", async () => {
  getClaims.mockResolvedValue({
    data: { claims: {
      sub: "user-a",
      email: "a@example.com",
      iss: "https://project-ref.supabase.co/auth/v1",
      aud: "authenticated",
      exp: Math.floor(Date.now() / 1000) + 60,
    } },
    error: null,
  });
  await expect(requireAuth(new Request("https://app.test", {
    headers: { cookie: "sb-test-auth-token=encoded" },
  }), env)).resolves.toEqual({ sub: "user-a", email: "a@example.com" });
});

it.each([
  ["provider error", { data: null, error: new Error("expired") }],
  ["wrong issuer", { data: { claims: { sub: "user-a", iss: "https://evil.test", aud: "authenticated", exp: 4_102_444_800 } }, error: null }],
  ["wrong audience", { data: { claims: { sub: "user-a", iss: "https://project-ref.supabase.co/auth/v1", aud: "anon", exp: 4_102_444_800 } }, error: null }],
  ["malformed subject", { data: { claims: { sub: "", iss: "https://project-ref.supabase.co/auth/v1", aud: "authenticated", exp: 4_102_444_800 } }, error: null }],
  ["expired claim", { data: { claims: { sub: "user-a", iss: "https://project-ref.supabase.co/auth/v1", aud: "authenticated", exp: 1 } }, error: null }],
])("rejects %s", async (_name, result) => {
  getClaims.mockResolvedValue(result);
  await expect(requireAuth(new Request("https://app.test"), env)).rejects.toMatchObject({
    status: 401,
    code: "unauthorized",
  });
});
```

Also assert `email` becomes `null` when the verified claim is absent or non-string.

- [ ] **Step 3: Run the tests and verify they fail**

Run:

```powershell
npm test -- tests/auth/require-auth.test.ts
```

Expected: FAIL because `requireAuth` is missing.

- [ ] **Step 4: Implement request-scoped verification**

Create `dashboard/lib/auth/require-auth.ts`:

```ts
import { createServerClient } from "@supabase/ssr";

export type AuthEnv = {
  SUPABASE_URL: string;
  SUPABASE_PUBLISHABLE_KEY: string;
};

export type AuthUser = { sub: string; email: string | null };

export class AuthError extends Error {
  readonly status = 401;
  readonly code = "unauthorized";
}

function requestCookies(request: Request) {
  return (request.headers.get("cookie") ?? "").split(";").flatMap((part) => {
    const separator = part.indexOf("=");
    if (separator < 1) return [];
    return [{
      name: part.slice(0, separator).trim(),
      value: part.slice(separator + 1).trim(),
    }];
  });
}

function hasAudience(aud: unknown): boolean {
  return aud === "authenticated" ||
    (Array.isArray(aud) && aud.includes("authenticated"));
}

export async function requireAuth(
  request: Request,
  env: AuthEnv,
): Promise<AuthUser> {
  const supabase = createServerClient(
    env.SUPABASE_URL,
    env.SUPABASE_PUBLISHABLE_KEY,
    {
      cookies: {
        getAll: () => requestCookies(request),
        setAll: () => undefined,
      },
    },
  );
  const { data, error } = await supabase.auth.getClaims();
  const claims = data?.claims;
  const now = Math.floor(Date.now() / 1000);
  if (
    error ||
    !claims ||
    typeof claims.sub !== "string" ||
    claims.sub.length === 0 ||
    claims.iss !== `${env.SUPABASE_URL.replace(/\/$/, "")}/auth/v1` ||
    !hasAudience(claims.aud) ||
    typeof claims.exp !== "number" ||
    claims.exp <= now
  ) {
    throw new AuthError("Authentication required");
  }
  return {
    sub: claims.sub,
    email: typeof claims.email === "string" ? claims.email : null,
  };
}
```

- [ ] **Step 5: Run the auth verifier tests**

Run:

```powershell
npm test -- tests/auth/require-auth.test.ts
```

Expected: PASS, 7 tests covering success, nullable email, provider expiry/error, wrong issuer, wrong audience, empty subject, and expired claims.

- [ ] **Step 6: Commit the JWT-verification unit**

```powershell
git add dashboard/.env.example dashboard/lib/auth/require-auth.ts dashboard/tests/auth/require-auth.test.ts
git commit -m "feat(dashboard): verify Supabase sessions"
```

---

### Task 6: Add PKCE callback, refresh middleware, protected-page routing, and logout

**Files:**
- Create: `dashboard/lib/auth/session.ts`
- Create: `dashboard/proxy.ts`
- Create: `dashboard/app/auth/callback/route.ts`
- Create: `dashboard/app/auth/logout/route.ts`
- Create: `dashboard/tests/auth/session.test.ts`

**Interfaces:**
- Consumes: `AuthEnv`, `requireAuth`, `TenantRepository.ensureHome`, and `getDb()`.
- Produces: `createSupabaseSession(request, env)` returning a request-scoped client plus `applySessionCookies(response)`.
- Produces: `requireSameOrigin(request): void` for mutating auth and enrollment handlers.
- Produces: Next 16 `proxy(request)` that refreshes cookies and protects pages without importing auth into `/demo`.

- [ ] **Step 1: Write failing session-boundary tests**

Create `dashboard/tests/auth/session.test.ts` and mock Supabase and D1 boundaries. Cover these exact outcomes:

```ts
it.each(["/login", "/signup", "/forgot-password", "/reset-password", "/demo"])(
  "keeps %s public without verified claims",
  async (pathname) => {
    getClaims.mockResolvedValue({ data: null, error: new Error("anonymous") });
    requireAuthMock.mockRejectedValue(new AuthError("Authentication required"));
    const response = await proxy(new NextRequest(`https://app.test${pathname}`));
    expect(response.status).toBe(200);
    expect(response.headers.get("location")).toBeNull();
  },
);

it("redirects an anonymous dashboard request to login", async () => {
  getClaims.mockResolvedValue({ data: null, error: new Error("anonymous") });
  requireAuthMock.mockRejectedValue(new AuthError("Authentication required"));
  const response = await proxy(new NextRequest("https://app.test/"));
  expect(response.status).toBe(307);
  expect(response.headers.get("location")).toBe("https://app.test/login");
});

it("copies refreshed cookies and private no-store headers", async () => {
  requireAuthMock.mockResolvedValue({ sub: "user-a", email: "a@example.com" });
  getClaims.mockImplementation(async () => {
    setAll([{ name: "sb-session", value: "rotated", options: { path: "/" } }], {
      "cache-control": "private, no-store",
    });
    return { data: { claims: validClaims }, error: null };
  });
  const response = await proxy(new NextRequest("https://app.test/"));
  expect(response.headers.get("set-cookie")).toContain("sb-session=rotated");
  expect(response.headers.get("set-cookie")).toMatch(/HttpOnly/i);
  expect(response.headers.get("set-cookie")).toMatch(/Secure/i);
  expect(response.headers.get("set-cookie")).toMatch(/SameSite=Lax/i);
  expect(response.headers.get("set-cookie")).toMatch(/Path=\//i);
  expect(response.headers.get("cache-control")).toBe("private, no-store");
});

it("exchanges a PKCE code, creates the subject home, and redirects safely", async () => {
  exchangeCodeForSession.mockResolvedValue({ data: {}, error: null });
  getClaims.mockResolvedValue({ data: { claims: validClaims }, error: null });
  requireAuthMock.mockResolvedValue({ sub: "user-a", email: "a@example.com" });
  const response = await GET(new NextRequest(
    "https://app.test/auth/callback?code=pkce-code&next=/reset-password",
  ));
  expect(exchangeCodeForSession).toHaveBeenCalledWith("pkce-code");
  expect(ensureHome).toHaveBeenCalledWith("user-a");
  expect(response.headers.get("location")).toBe("https://app.test/reset-password");
});
```

Add tests that callback errors redirect to `/login?error=callback`, unknown `next` values resolve to `/`, logout rejects a cross-origin POST with `403`, same-origin logout calls `signOut({ scope: "local" })`, clears cookies, and redirects to `/login`.

- [ ] **Step 2: Run the session tests and verify they fail**

Run:

```powershell
npm test -- tests/auth/session.test.ts
```

Expected: FAIL because session helpers, proxy, callback, and logout do not exist.

- [ ] **Step 3: Implement the request-scoped SSR session adapter**

Create `dashboard/lib/auth/session.ts` using `createServerClient`, request cookies, and a local cookie/header accumulator:

```ts
import { env } from "cloudflare:workers";
import { createServerClient, type CookieOptions } from "@supabase/ssr";
import type { SupabaseClient } from "@supabase/supabase-js";
import { NextRequest, NextResponse } from "next/server";
import type { AuthEnv } from "./require-auth";

type PendingCookie = {
  name: string;
  value: string;
  options: CookieOptions;
};

export type SessionHandle = {
  supabase: SupabaseClient;
  applySessionCookies(response: NextResponse): NextResponse;
};

export function createSupabaseSession(
  request: NextRequest,
  authEnv: AuthEnv,
): SessionHandle {
  const pendingCookies: PendingCookie[] = [];
  const pendingHeaders = new Headers();
  const supabase = createServerClient(
    authEnv.SUPABASE_URL,
    authEnv.SUPABASE_PUBLISHABLE_KEY,
    {
      cookies: {
        getAll: () => request.cookies.getAll(),
        setAll(cookiesToSet, headersToSet) {
          for (const cookie of cookiesToSet) {
            request.cookies.set(cookie.name, cookie.value);
            pendingCookies.push({
              ...cookie,
              options: {
                ...cookie.options,
                httpOnly: true,
                secure: request.nextUrl.protocol === "https:",
                sameSite: "lax",
                path: "/",
              },
            });
          }
          for (const [name, value] of Object.entries(headersToSet ?? {})) {
            pendingHeaders.set(name, value);
          }
        },
      },
    },
  );
  return {
    supabase,
    applySessionCookies(response) {
      for (const cookie of pendingCookies) {
        response.cookies.set(cookie.name, cookie.value, cookie.options);
      }
      pendingHeaders.forEach((value, name) => response.headers.set(name, value));
      response.headers.set("Cache-Control", "private, no-store");
      return response;
    },
  };
}

export function runtimeAuthEnv(): AuthEnv {
  const runtime = env as unknown as Partial<AuthEnv>;
  if (!runtime.SUPABASE_URL || !runtime.SUPABASE_PUBLISHABLE_KEY) {
    throw new Error("Supabase runtime configuration is unavailable");
  }
  return {
    SUPABASE_URL: runtime.SUPABASE_URL,
    SUPABASE_PUBLISHABLE_KEY: runtime.SUPABASE_PUBLISHABLE_KEY,
  };
}

export function requireSameOrigin(request: Request): void {
  const origin = request.headers.get("origin");
  if (!origin || origin !== new URL(request.url).origin) {
    throw Object.assign(new Error("Cross-origin request rejected"), { status: 403 });
  }
}
```

`applySessionCookies` sets every provider cookie and cache header on the final response. Instantiate a new Supabase client inside every request; never cache a user client at module scope.

- [ ] **Step 4: Implement the Next 16 proxy**

Create `dashboard/proxy.ts` with an exact public-page set and no import from `/demo`:

```ts
import { env } from "cloudflare:workers";
import { NextRequest, NextResponse } from "next/server";
import { requireAuth, type AuthEnv } from "./lib/auth/require-auth";
import { createSupabaseSession } from "./lib/auth/session";

const PUBLIC_PAGES = new Set([
  "/login",
  "/signup",
  "/forgot-password",
  "/reset-password",
  "/demo",
]);

export async function proxy(request: NextRequest) {
  const authEnv = env as unknown as AuthEnv;
  const session = createSupabaseSession(request, authEnv);
  await session.supabase.auth.getClaims();
  let authenticated = true;
  try {
    await requireAuth(request, authEnv);
  } catch {
    authenticated = false;
  }
  if (!PUBLIC_PAGES.has(request.nextUrl.pathname) && !authenticated) {
    return session.applySessionCookies(
      NextResponse.redirect(new URL("/login", request.url)),
    );
  }
  return session.applySessionCookies(NextResponse.next({ request }));
}

export const config = {
  matcher: ["/((?!api/|auth/|_next/|favicon.svg|og.png).*)"],
};
```

The API and auth protocol routes perform their own explicit checks; excluding them prevents page redirects from replacing JSON or form responses.

- [ ] **Step 5: Implement callback and logout handlers**

Create `dashboard/app/auth/callback/route.ts`:

```ts
import { NextRequest, NextResponse } from "next/server";
import { getDb } from "../../../db";
import { requireAuth } from "../../../lib/auth/require-auth";
import { createSupabaseSession, runtimeAuthEnv } from "../../../lib/auth/session";
import { TenantRepository } from "../../../lib/tenancy/repository";

export async function GET(request: NextRequest) {
  const authEnv = runtimeAuthEnv();
  const session = createSupabaseSession(request, authEnv);
  const code = request.nextUrl.searchParams.get("code");
  const next = request.nextUrl.searchParams.get("next") === "/reset-password"
    ? "/reset-password"
    : "/";

  if (code) {
    const { error } = await session.supabase.auth.exchangeCodeForSession(code);
    if (!error) {
      try {
        const user = await requireAuth(request, authEnv);
        await new TenantRepository(getDb()).ensureHome(user.sub);
        return session.applySessionCookies(
          NextResponse.redirect(new URL(next, request.url), 303),
        );
      } catch {
        // Return the same generic callback error for invalid claims and D1 failure.
      }
    }
  }
  return session.applySessionCookies(
    NextResponse.redirect(new URL("/login?error=callback", request.url), 303),
  );
}
```

Create `dashboard/app/auth/logout/route.ts`:

```ts
export async function POST(request: NextRequest) {
  try {
    requireSameOrigin(request);
  } catch {
    return Response.json({ error: "forbidden" }, { status: 403 });
  }
  const session = createSupabaseSession(request, runtimeAuthEnv());
  const { error } = await session.supabase.auth.signOut({ scope: "local" });
  if (error) return Response.json({ error: "logout_failed" }, { status: 503 });
  return session.applySessionCookies(
    NextResponse.redirect(new URL("/login", request.url), 303),
  );
}
```

`runtimeAuthEnv()` belongs in `session.ts`, reads `SUPABASE_URL` and `SUPABASE_PUBLISHABLE_KEY` from `cloudflare:workers` `env`, and throws a configuration error without printing their values.

- [ ] **Step 6: Run the session tests**

Run:

```powershell
npm test -- tests/auth/session.test.ts
```

Expected: PASS for public paths, protected redirect, refresh-cookie propagation, production `HttpOnly; Secure; SameSite=Lax; Path=/`, no-store headers, callback success/error/safe redirect, and logout origin/success/error.

- [ ] **Step 7: Commit the session unit**

```powershell
git add dashboard/lib/auth/session.ts dashboard/proxy.ts dashboard/app/auth/callback/route.ts dashboard/app/auth/logout/route.ts dashboard/tests/auth/session.test.ts
git commit -m "feat(dashboard): add PKCE session lifecycle"
```

---

### Task 7: Require immediate password reauthentication for PetCare account deletion

**Files:**
- Create: `dashboard/lib/auth/recent-reauth.ts`
- Create: `dashboard/tests/auth/recent-reauth.test.ts`

**Interfaces:**
- Consumes: a verified `AuthUser`, exact `AuthEnv`, the incoming destructive request, and the request-scoped Supabase session adapter.
- Produces:

```ts
export async function requireRecentPassword(
  request: Request,
  env: AuthEnv,
  user: AuthUser,
): Promise<void>;
```

- Produces: `RecentReauthError` with generic codes `reauthentication_failed` (`401`), `rate_limited` (`429`), or `auth_unavailable` (`503`).
- Handoff: the authenticated dashboard account form sends `{ currentPassword }` directly to the BFF `DELETE /api/petcare/account` route; that route calls `requireSameOrigin(request)`, `requireAuth(request, env)`, and then `requireRecentPassword(request, env, user)` before deleting PetCare tenant/data. No new public page or auth protocol route is added, and Supabase identity is not deleted or administered.

- [ ] **Step 1: Write failing recent-reauthentication tests**

Create `dashboard/tests/auth/recent-reauth.test.ts` with the request-scoped session mocked:

```ts
const env = {
  SUPABASE_URL: "https://project-ref.supabase.co",
  SUPABASE_PUBLISHABLE_KEY: "sb_publishable_test",
};
const user = { sub: "user-a", email: "owner@example.com" };

it("verifies the current password only against the verified JWT email", async () => {
  signInWithPassword.mockResolvedValue({ data: { user: { id: "user-a" } }, error: null });
  const log = vi.spyOn(console, "log").mockImplementation(() => undefined);
  const errorLog = vi.spyOn(console, "error").mockImplementation(() => undefined);
  const request = new Request("https://app.test/api/petcare/account", {
    method: "DELETE",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ currentPassword: "correct horse battery staple" }),
  });

  await expect(requireRecentPassword(request, env, user)).resolves.toBeUndefined();
  expect(signInWithPassword).toHaveBeenCalledWith({
    email: "owner@example.com",
    password: "correct horse battery staple",
  });
  expect(log).not.toHaveBeenCalled();
  expect(errorLog).not.toHaveBeenCalled();
});

it.each([
  [429, 429, "rate_limited"],
  [400, 401, "reauthentication_failed"],
  [503, 503, "auth_unavailable"],
])("maps provider status %i to generic %i", async (providerStatus, status, code) => {
  signInWithPassword.mockResolvedValue({
    data: { user: null },
    error: Object.assign(new Error("provider detail"), { status: providerStatus }),
  });
  const request = new Request("https://app.test/api/petcare/account", {
    method: "DELETE",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ currentPassword: "not logged or returned" }),
  });
  const failure = requireRecentPassword(request, env, user);
  await expect(failure).rejects.toMatchObject({ status, code });
  await expect(failure).rejects.not.toThrow(/provider detail|not logged or returned/);
});

it("rejects a missing verified email or password before calling Supabase", async () => {
  const request = new Request("https://app.test/api/petcare/account", {
    method: "DELETE",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ currentPassword: "" }),
  });
  await expect(requireRecentPassword(request, env, { sub: "user-a", email: null }))
    .rejects.toMatchObject({ status: 401, code: "reauthentication_failed" });
  expect(signInWithPassword).not.toHaveBeenCalled();
});
```

- [ ] **Step 2: Run the test and verify it fails**

Run:

```powershell
npm test -- tests/auth/recent-reauth.test.ts
```

Expected: FAIL because `requireRecentPassword` and `RecentReauthError` do not exist.

- [ ] **Step 3: Implement the request-local password check**

Create `dashboard/lib/auth/recent-reauth.ts`:

```ts
import { NextRequest } from "next/server";
import type { AuthEnv, AuthUser } from "./require-auth";
import { createSupabaseSession } from "./session";

type RecentReauthCode =
  | "reauthentication_failed"
  | "rate_limited"
  | "auth_unavailable";

export class RecentReauthError extends Error {
  constructor(
    readonly status: 401 | 429 | 503,
    readonly code: RecentReauthCode,
  ) {
    super(code);
  }
}

export async function requireRecentPassword(
  request: Request,
  env: AuthEnv,
  user: AuthUser,
): Promise<void> {
  if (!user.email) {
    throw new RecentReauthError(401, "reauthentication_failed");
  }
  let payload: unknown;
  try {
    payload = await request.json();
  } catch {
    throw new RecentReauthError(401, "reauthentication_failed");
  }
  const password = (payload as { currentPassword?: unknown }).currentPassword;
  if (typeof password !== "string" || password.length === 0) {
    throw new RecentReauthError(401, "reauthentication_failed");
  }

  const sessionRequest = new NextRequest(request.url, {
    method: request.method,
    headers: request.headers,
  });
  const session = createSupabaseSession(sessionRequest, env);
  const { error } = await session.supabase.auth.signInWithPassword({
    email: user.email,
    password,
  });
  if (!error) return;
  if (error.status === 429) throw new RecentReauthError(429, "rate_limited");
  if (error.status === 400 || error.status === 401) {
    throw new RecentReauthError(401, "reauthentication_failed");
  }
  throw new RecentReauthError(503, "auth_unavailable");
}
```

The password variable remains inside this function, is passed only to the request-scoped Supabase `signInWithPassword` call, and is never logged, persisted, returned, placed in an error, or passed into the BFF repository/deletion layer. Do not add a service-role key, admin client, Supabase identity deletion, public auth route, or public page.

- [ ] **Step 4: Run the recent-reauthentication tests**

Run:

```powershell
npm test -- tests/auth/recent-reauth.test.ts
```

Expected: PASS for success, missing input, invalid credentials, provider throttling, provider outage, verified-email use, and password non-disclosure.

- [ ] **Step 5: Commit the recent-reauthentication unit**

```powershell
git add dashboard/lib/auth/recent-reauth.ts dashboard/tests/auth/recent-reauth.test.ts
git commit -m "feat(dashboard): require recent password verification"
```

---

### Task 8: Build the public email/password auth pages and handlers

**Files:**
- Create: `dashboard/components/auth-card.tsx`
- Create: `dashboard/app/login/page.tsx`
- Create: `dashboard/app/signup/page.tsx`
- Create: `dashboard/app/forgot-password/page.tsx`
- Create: `dashboard/app/reset-password/page.tsx`
- Create: `dashboard/app/auth/login/route.ts`
- Create: `dashboard/app/auth/signup/route.ts`
- Create: `dashboard/app/auth/forgot-password/route.ts`
- Create: `dashboard/app/auth/reset-password/route.ts`
- Modify: `dashboard/app/globals.css`
- Create: `dashboard/tests/auth/public-routes.test.tsx`

**Interfaces:**
- Consumes: `createSupabaseSession`, `runtimeAuthEnv`, and `requireSameOrigin`.
- Produces: native HTML forms with email autocomplete, current/new password autocomplete, labels, status copy, and same-origin POST targets.
- Produces: Supabase calls `signInWithPassword`, `signUp`, `resetPasswordForEmail`, and `updateUser` with PKCE redirects through `/auth/callback`.

- [ ] **Step 1: Write failing public-page and mocked-provider tests**

Create `dashboard/tests/auth/public-routes.test.tsx`. Render each page and assert:

```ts
it("renders the four public auth forms with accessible labels", async () => {
  render(await LoginPage({ searchParams: Promise.resolve({}) }));
  expect(screen.getByRole("heading", { name: "로그인" })).toBeInTheDocument();
  expect(screen.getByLabelText("이메일")).toHaveAttribute("autocomplete", "email");
  expect(screen.getByLabelText("비밀번호")).toHaveAttribute("autocomplete", "current-password");
  expect(screen.getByRole("button", { name: "로그인" })).toBeInTheDocument();

  cleanup();
  render(await SignupPage({ searchParams: Promise.resolve({}) }));
  expect(screen.getByRole("heading", { name: "계정 만들기" })).toBeInTheDocument();

  cleanup();
  render(await ForgotPasswordPage({ searchParams: Promise.resolve({}) }));
  expect(screen.getByRole("heading", { name: "비밀번호 재설정" })).toBeInTheDocument();

  cleanup();
  render(await ResetPasswordPage({ searchParams: Promise.resolve({}) }));
  expect(screen.getByLabelText("새 비밀번호")).toHaveAttribute("autocomplete", "new-password");
});
```

Call each route handler with a same-origin `NextRequest` and form data. Assert exact SDK calls:

```ts
expect(signInWithPassword).toHaveBeenCalledWith({
  email: "owner@example.com",
  password: "correct horse battery staple",
});
expect(requireAuthMock).toHaveBeenCalled();
expect(ensureHomeMock).toHaveBeenCalledWith("user-a");
expect(signUp).toHaveBeenCalledWith({
  email: "owner@example.com",
  password: "correct horse battery staple",
  options: { emailRedirectTo: "https://app.test/auth/callback" },
});
expect(resetPasswordForEmail).toHaveBeenCalledWith(
  "owner@example.com",
  { redirectTo: "https://app.test/auth/callback?next=/reset-password" },
);
expect(updateUser).toHaveBeenCalledWith({
  password: "new correct horse battery staple",
});
```

For each signup, login, forgot-password, and reset handler, mock its Supabase method with `{ error: { status: 429 } }` and assert:

```ts
expect(response.status).toBe(429);
await expect(response.json()).resolves.toEqual({ error: "rate_limited" });
expect(response.headers.get("cache-control")).toBe("private, no-store");
```

Also assert every cross-origin POST returns `403`, malformed form data returns `400`, login success redirects to `/`, signup/forgot success redirects to a same-page `sent=1` status, reset success redirects to `/login?reset=1`, and provider errors use generic public error codes without returning provider tokens or raw messages.

- [ ] **Step 2: Run the tests and verify they fail**

Run:

```powershell
npm test -- tests/auth/public-routes.test.tsx
```

Expected: FAIL because pages and handlers do not exist.

- [ ] **Step 3: Add the shared accessible auth frame and four pages**

Create `dashboard/components/auth-card.tsx`:

```tsx
import type { ReactNode } from "react";

export function AuthCard({
  title,
  description,
  children,
}: {
  title: string;
  description: string;
  children: ReactNode;
}) {
  return (
    <main className="auth-page">
      <section className="auth-card" aria-labelledby="auth-title">
        <a className="brand" href="/demo"><span>PC</span><strong>PetCare</strong></a>
        <h1 id="auth-title">{title}</h1>
        <p>{description}</p>
        {children}
      </section>
    </main>
  );
}
```

Create the four server pages with native forms. `dashboard/app/login/page.tsx`:

```tsx
import { AuthCard } from "../../components/auth-card";

export default async function LoginPage({
  searchParams,
}: {
  searchParams: Promise<{ error?: string; reset?: string }>;
}) {
  const query = await searchParams;
  return (
    <AuthCard title="로그인" description="PetCare 홈에 안전하게 연결합니다.">
      {query.error && <p role="alert">이메일 또는 비밀번호를 확인하세요.</p>}
      {query.reset === "1" && <p role="status">비밀번호가 변경되었습니다.</p>}
      <form className="auth-form" action="/auth/login" method="post">
        <label>이메일<input name="email" type="email" autoComplete="email" required /></label>
        <label>비밀번호<input name="password" type="password" autoComplete="current-password" required /></label>
        <button type="submit">로그인</button>
      </form>
      <p><a href="/forgot-password">비밀번호를 잊으셨나요?</a></p>
      <p><a href="/signup">계정 만들기</a></p>
    </AuthCard>
  );
}
```

`dashboard/app/signup/page.tsx`:

```tsx
import { AuthCard } from "../../components/auth-card";

export default async function SignupPage({
  searchParams,
}: {
  searchParams: Promise<{ error?: string; sent?: string }>;
}) {
  const query = await searchParams;
  return (
    <AuthCard title="계정 만들기" description="이메일 확인 후 하나의 PetCare 홈이 생성됩니다.">
      {query.error && <p role="alert">계정을 만들 수 없습니다.</p>}
      {query.sent === "1" && <p role="status">확인 이메일을 보냈습니다.</p>}
      <form className="auth-form" action="/auth/signup" method="post">
        <label>이메일<input name="email" type="email" autoComplete="email" required /></label>
        <label>비밀번호<input name="password" type="password" autoComplete="new-password" required /></label>
        <button type="submit">계정 만들기</button>
      </form>
      <p><a href="/login">로그인으로 돌아가기</a></p>
    </AuthCard>
  );
}
```

`dashboard/app/forgot-password/page.tsx`:

```tsx
import { AuthCard } from "../../components/auth-card";

export default async function ForgotPasswordPage({
  searchParams,
}: {
  searchParams: Promise<{ error?: string; sent?: string }>;
}) {
  const query = await searchParams;
  return (
    <AuthCard title="비밀번호 재설정" description="등록된 이메일로 재설정 링크를 보냅니다.">
      {query.error && <p role="alert">재설정 이메일을 보낼 수 없습니다.</p>}
      {query.sent === "1" && <p role="status">재설정 이메일을 보냈습니다.</p>}
      <form className="auth-form" action="/auth/forgot-password" method="post">
        <label>이메일<input name="email" type="email" autoComplete="email" required /></label>
        <button type="submit">재설정 링크 보내기</button>
      </form>
      <p><a href="/login">로그인으로 돌아가기</a></p>
    </AuthCard>
  );
}
```

`dashboard/app/reset-password/page.tsx`:

```tsx
import { AuthCard } from "../../components/auth-card";

export default async function ResetPasswordPage({
  searchParams,
}: {
  searchParams: Promise<{ error?: string }>;
}) {
  const query = await searchParams;
  return (
    <AuthCard title="새 비밀번호 설정" description="새 비밀번호를 입력하세요.">
      {query.error && <p role="alert">재설정 링크를 다시 요청하세요.</p>}
      <form className="auth-form" action="/auth/reset-password" method="post">
        <label>새 비밀번호<input name="password" type="password" autoComplete="new-password" required /></label>
        <button type="submit">비밀번호 변경</button>
      </form>
      <p><a href="/login">로그인으로 돌아가기</a></p>
    </AuthCard>
  );
}
```

Do not import Supabase, `fetch`, browser state, or effects into these page files.

- [ ] **Step 4: Implement the four POST handlers**

Each handler must:

1. Call `requireSameOrigin(request)` before reading credentials.
2. Read `request.formData()` and require non-empty strings.
3. Create a request-scoped Supabase session.
4. Call only its assigned Supabase method.
5. Apply session cookies and `private, no-store` to the final response.
6. Return generic error query parameters; never include a password, token, raw provider error, or reset link.

The login implementation is:

```ts
import { NextRequest, NextResponse } from "next/server";
import { getDb } from "../../../db";
import { requireAuth } from "../../../lib/auth/require-auth";
import { createSupabaseSession, requireSameOrigin, runtimeAuthEnv } from "../../../lib/auth/session";
import { TenantRepository } from "../../../lib/tenancy/repository";

export async function POST(request: NextRequest) {
  try {
    requireSameOrigin(request);
  } catch {
    return Response.json({ error: "forbidden" }, { status: 403 });
  }
  const form = await request.formData();
  const email = form.get("email");
  const password = form.get("password");
  if (typeof email !== "string" || !email.trim() || typeof password !== "string" || !password) {
    return Response.json({ error: "invalid_form" }, { status: 400 });
  }
  const authEnv = runtimeAuthEnv();
  const session = createSupabaseSession(request, authEnv);
  const { error } = await session.supabase.auth.signInWithPassword({
    email: email.trim(),
    password,
  });
  if (error?.status === 429) {
    return session.applySessionCookies(
      NextResponse.json({ error: "rate_limited" }, { status: 429 }),
    );
  }
  let destination = error ? "/login?error=credentials" : "/";
  if (!error) {
    try {
      const user = await requireAuth(request, authEnv);
      await new TenantRepository(getDb()).ensureHome(user.sub);
    } catch {
      destination = "/login?error=unavailable";
    }
  }
  return session.applySessionCookies(
    NextResponse.redirect(new URL(destination, request.url), 303),
  );
}
```

Create `dashboard/app/auth/signup/route.ts`:

```ts
import { NextRequest, NextResponse } from "next/server";
import { createSupabaseSession, requireSameOrigin, runtimeAuthEnv } from "../../../lib/auth/session";

export async function POST(request: NextRequest) {
  try {
    requireSameOrigin(request);
  } catch {
    return Response.json({ error: "forbidden" }, { status: 403 });
  }
  const form = await request.formData();
  const email = form.get("email");
  const password = form.get("password");
  if (typeof email !== "string" || !email.trim() || typeof password !== "string" || !password) {
    return Response.json({ error: "invalid_form" }, { status: 400 });
  }
  const session = createSupabaseSession(request, runtimeAuthEnv());
  const { error } = await session.supabase.auth.signUp({
    email: email.trim(),
    password,
    options: { emailRedirectTo: new URL("/auth/callback", request.url).toString() },
  });
  if (error?.status === 429) {
    return session.applySessionCookies(
      NextResponse.json({ error: "rate_limited" }, { status: 429 }),
    );
  }
  const destination = error ? "/signup?error=signup" : "/signup?sent=1";
  return session.applySessionCookies(
    NextResponse.redirect(new URL(destination, request.url), 303),
  );
}
```

Create `dashboard/app/auth/forgot-password/route.ts`:

```ts
import { NextRequest, NextResponse } from "next/server";
import { createSupabaseSession, requireSameOrigin, runtimeAuthEnv } from "../../../lib/auth/session";

export async function POST(request: NextRequest) {
  try {
    requireSameOrigin(request);
  } catch {
    return Response.json({ error: "forbidden" }, { status: 403 });
  }
  const form = await request.formData();
  const email = form.get("email");
  if (typeof email !== "string" || !email.trim()) {
    return Response.json({ error: "invalid_form" }, { status: 400 });
  }
  const session = createSupabaseSession(request, runtimeAuthEnv());
  const { error } = await session.supabase.auth.resetPasswordForEmail(
    email.trim(),
    { redirectTo: new URL("/auth/callback?next=/reset-password", request.url).toString() },
  );
  if (error?.status === 429) {
    return session.applySessionCookies(
      NextResponse.json({ error: "rate_limited" }, { status: 429 }),
    );
  }
  const destination = error
    ? "/forgot-password?error=unavailable"
    : "/forgot-password?sent=1";
  return session.applySessionCookies(
    NextResponse.redirect(new URL(destination, request.url), 303),
  );
}
```

Create `dashboard/app/auth/reset-password/route.ts`:

```ts
import { NextRequest, NextResponse } from "next/server";
import { createSupabaseSession, requireSameOrigin, runtimeAuthEnv } from "../../../lib/auth/session";

export async function POST(request: NextRequest) {
  try {
    requireSameOrigin(request);
  } catch {
    return Response.json({ error: "forbidden" }, { status: 403 });
  }
  const form = await request.formData();
  const password = form.get("password");
  if (typeof password !== "string" || !password) {
    return Response.json({ error: "invalid_form" }, { status: 400 });
  }
  const session = createSupabaseSession(request, runtimeAuthEnv());
  const { error } = await session.supabase.auth.updateUser({ password });
  if (error?.status === 429) {
    return session.applySessionCookies(
      NextResponse.json({ error: "rate_limited" }, { status: 429 }),
    );
  }
  const destination = error
    ? "/reset-password?error=invalid_session"
    : "/login?reset=1";
  return session.applySessionCookies(
    NextResponse.redirect(new URL(destination, request.url), 303),
  );
}
```

Keep validation to presence and rely on Supabase password policy; do not duplicate policy rules in PetCare.

- [ ] **Step 5: Add scoped warm-homecare auth styles**

Append styles using existing tokens. The minimum rules are:

```css
.auth-page {
  display: grid;
  min-height: 100vh;
  place-items: center;
  padding: 24px;
}

.auth-card {
  width: min(100%, 420px);
  padding: 28px;
  border: 1px solid var(--border-control);
  border-radius: 10px;
  background: var(--surface);
}

.auth-form {
  display: grid;
  gap: 16px;
  margin-top: 24px;
}

.auth-form label {
  display: grid;
  gap: 6px;
  font-weight: 650;
}

.auth-form input,
.auth-form button {
  min-height: 44px;
  border: 1px solid var(--border-control);
  border-radius: 6px;
  font: inherit;
}

.auth-form input { padding: 0 12px; }
.auth-form button { color: var(--surface); background: var(--primary); font-weight: 750; }
.auth-form input:focus-visible,
.auth-form button:focus-visible { outline: 2px solid var(--primary); outline-offset: 2px; }
```

- [ ] **Step 6: Run the public auth tests**

Run:

```powershell
npm test -- tests/auth/public-routes.test.tsx tests/auth/session.test.ts
```

Expected: PASS for all public forms, SDK calls, PKCE redirect URLs, same-origin rejection, generic errors, callback, refresh, and logout.

- [ ] **Step 7: Commit the public-auth unit**

```powershell
git add dashboard/components/auth-card.tsx dashboard/app/login dashboard/app/signup dashboard/app/forgot-password dashboard/app/reset-password dashboard/app/auth/login dashboard/app/auth/signup dashboard/app/auth/forgot-password dashboard/app/auth/reset-password dashboard/app/globals.css dashboard/tests/auth/public-routes.test.tsx
git commit -m "feat(dashboard): add email password auth routes"
```

---

### Task 9: Enforce owner-sub isolation in the enrollment route

**Files:**
- Create: `dashboard/app/api/petcare/enrollment/route.ts`
- Create: `dashboard/tests/tenancy/enrollment-route.test.ts`

**Interfaces:**
- Consumes: exact `requireAuth(request, env)` and `issueEnrollment(ownerSub)`; `issueEnrollment` performs the request's only `TenantRepository.requireHome(ownerSub)` lookup.
- Produces: authenticated `POST /api/petcare/enrollment` response `{ code, expiresAt }` with `Cache-Control: private, no-store`.

- [ ] **Step 1: Write failing anonymous and two-user isolation tests**

Create `dashboard/tests/tenancy/enrollment-route.test.ts` with `requireAuth` and `issueEnrollment` mocked at their module boundaries:

```ts
it.each(["owner-a", "owner-b"])("issues only for verified subject %s", async (ownerSub) => {
  requireAuthMock.mockResolvedValue({ sub: ownerSub, email: `${ownerSub}@example.com` });
  issueEnrollmentMock.mockResolvedValue({
    code: `code-${ownerSub}`,
    expiresAt: "2026-07-20T03:10:00.000Z",
  });
  const response = await POST(new Request("https://app.test/api/petcare/enrollment", {
    method: "POST",
    headers: { origin: "https://app.test", "content-type": "application/json" },
    body: JSON.stringify({ owner_sub: "attacker", home_id: "foreign-home" }),
  }));

  expect(response.status).toBe(201);
  await expect(response.json()).resolves.toEqual({
    code: `code-${ownerSub}`,
    expiresAt: "2026-07-20T03:10:00.000Z",
  });
  expect(issueEnrollmentMock).toHaveBeenCalledWith(ownerSub);
  expect(requireAuthMock).toHaveBeenCalledTimes(1);
  expect(issueEnrollmentMock).toHaveBeenCalledTimes(1);
  expect(issueEnrollmentMock).not.toHaveBeenCalledWith("attacker");
  expect(response.headers.get("cache-control")).toBe("private, no-store");
});

it("returns 401 before issuing for an anonymous request", async () => {
  requireAuthMock.mockRejectedValue(new AuthError("Authentication required"));
  const response = await POST(new Request("https://app.test/api/petcare/enrollment", {
    method: "POST",
    headers: { origin: "https://app.test" },
  }));
  expect(response.status).toBe(401);
  await expect(response.json()).resolves.toEqual({ error: "unauthorized" });
  expect(issueEnrollmentMock).not.toHaveBeenCalled();
});

it("returns 404 for a subject without an active home", async () => {
  requireAuthMock.mockResolvedValue({ sub: "owner-a", email: null });
  issueEnrollmentMock.mockRejectedValue(new TenantNotFoundError("Active home not found"));
  const response = await POST(new Request("https://app.test/api/petcare/enrollment", {
    method: "POST",
    headers: { origin: "https://app.test" },
  }));
  expect(response.status).toBe(404);
  await expect(response.json()).resolves.toEqual({ error: "not_found" });
});

it("rejects cross-origin issuance before auth or D1", async () => {
  const response = await POST(new Request("https://app.test/api/petcare/enrollment", {
    method: "POST",
    headers: { origin: "https://evil.test" },
  }));
  expect(response.status).toBe(403);
  expect(requireAuthMock).not.toHaveBeenCalled();
  expect(issueEnrollmentMock).not.toHaveBeenCalled();
});
```

- [ ] **Step 2: Run the tests and verify they fail**

Run:

```powershell
npm test -- tests/tenancy/enrollment-route.test.ts
```

Expected: FAIL because the enrollment route does not exist.

- [ ] **Step 3: Implement the owner-only enrollment issue route**

Create `dashboard/app/api/petcare/enrollment/route.ts`:

```ts
import { AuthError, requireAuth } from "../../../../lib/auth/require-auth";
import { requireSameOrigin, runtimeAuthEnv } from "../../../../lib/auth/session";
import { issueEnrollment } from "../../../../lib/tenancy/enrollment";
import { TenantNotFoundError } from "../../../../lib/tenancy/repository";

export async function POST(request: Request) {
  try {
    requireSameOrigin(request);
    const user = await requireAuth(request, runtimeAuthEnv());
    const result = await issueEnrollment(user.sub);
    return Response.json(result, {
      status: 201,
      headers: { "Cache-Control": "private, no-store" },
    });
  } catch (error) {
    const status = error instanceof AuthError
      ? 401
      : error instanceof TenantNotFoundError
        ? 404
        : Number((error as { status?: number }).status) === 403
          ? 403
          : 503;
    const code = status === 401
      ? "unauthorized"
      : status === 404
        ? "not_found"
        : status === 403
          ? "forbidden"
          : "enrollment_unavailable";
    return Response.json({ error: code }, {
      status,
      headers: { "Cache-Control": "private, no-store" },
    });
  }
}
```

- [ ] **Step 4: Run the owner-isolation and enrollment suites**

Run:

```powershell
npm test -- tests/tenancy/enrollment-route.test.ts tests/tenancy/enrollment.test.ts
npm run test:d1
```

Expected: PASS. The focused suite proves each verified subject is passed unchanged to `issueEnrollment`, client owner/home overrides are ignored, anonymous requests never issue a code, and `issueEnrollment` performs exactly one home lookup before storing a ten-minute single-use hash.

- [ ] **Step 5: Commit the route-isolation unit**

```powershell
git add dashboard/app/api/petcare/enrollment/route.ts dashboard/tests/tenancy/enrollment-route.test.ts
git commit -m "feat(dashboard): isolate enrollment by owner"
```

---

### Task 10: Run the complete local auth/tenancy gate

**Files:**
- Verify only; do not modify source or test files during this task unless a preceding test exposes an in-scope defect.

**Interfaces:**
- Verifies all exact shared interfaces and the handoff to tunnel provisioning.
- Verifies existing `/demo` no-network behavior remains unchanged.

- [ ] **Step 1: Verify no placeholder or conflicting interface names exist**

Run from the repository root:

```powershell
rg -n "T[B]D|T[O]DO|implement l[a]ter|fill in det[a]ils|requireUser|resolveTenant|createEnrollmentCode" dashboard/lib dashboard/app dashboard/tests
rg -n "type AuthUser|function requireAuth|class TenantRepository|requireHome\(|function issueEnrollment" dashboard/lib
```

Expected: the first command has no output. The second command shows only the exact interfaces defined in this plan and their imports/usages.

- [ ] **Step 2: Run focused and existing tests**

Run from `dashboard/`:

```powershell
npm test
npm run test:d1
```

Expected: both commands exit `0`; all existing dashboard/demo tests and all new auth/tenancy tests pass.

- [ ] **Step 3: Run static and build validation**

Run:

```powershell
npm run lint
npm run build
```

Expected: both commands exit `0` with no TypeScript, ESLint, vinext, route, proxy, or Cloudflare binding errors.

- [ ] **Step 4: Verify generated migration consistency and secret absence**

Run:

```powershell
npm run db:generate -- --name schema_check
git status --short dashboard/drizzle dashboard/db/schema.ts
rg -n "service_role|SUPABASE_SERVICE|password\s*=|refresh_token\s*=|connector_token\s*=" dashboard --glob '!package-lock.json' --glob '!tests/**'
```

Expected: Drizzle reports no schema changes and does not create a second migration; `git status` shows no new migration file; the secret scan has no output. Any generated `0001_schema_check.sql` means the checked-in migration is stale and Task 1 must be corrected before handoff.

- [ ] **Step 5: Inspect the final scoped diff**

Run:

```powershell
git diff --check
git diff --stat
git status --short
```

Expected: no whitespace errors; only the files listed by this plan are changed. There is no change under `progress-monitor/**`, backend, firmware, hardware docs, clip storage, live proxying, or the existing demo files.

## Execution Handoff

- Execute integration Task 1 first. It is the single owner of all shared dependency pins and lock resolution in `dashboard/package.json` and `dashboard/package-lock.json`; auth Tasks 1 and 5 verify those exact prerequisites without editing, installing, or staging either file.
- After that prerequisite passes, execute this auth/tenancy plan. It is the sole initial owner of `dashboard/db/schema.ts` and `dashboard/.env.example` through the Task 10 green commit; integration and auth never write the shared package manifests concurrently.
- Execute the remote BFF plan only after that exact commit passes. The BFF workstream may then serially extend those two shared files for tunnel routing and its runtime keys; it must preserve `homes`, `agents`, `cameras`, `enrollmentTokens`, `AuthUser`, `requireAuth`, `TenantRepository.requireHome`, and `issueEnrollment` without renaming or redefining them.
- The BFF enrollment provisioner calls `TenantRepository.consumeEnrollment(input)` after mocked or approved Cloudflare provisioning and revokes the new tunnel if D1 binding fails. Shared `dashboard/worker/index.ts` and `dashboard/.openai/hosting.json` changes remain integration-owned.
- The BFF `DELETE /api/petcare/account` route calls `requireSameOrigin(request)`, `requireAuth(request, env)`, then `requireRecentPassword(request, env, user)` before deleting PetCare tenant/data. It maps `RecentReauthError` without exposing provider detail, returns exact `202 {"status":"cleanup_pending"}` for first/pending deletion or an empty `204` for absent/completed deletion, and does not call a Supabase service-role, admin, or identity-deletion API. After either success response, the dashboard performs a same-origin `POST /auth/logout`; the BFF must not duplicate the existing local Supabase logout implementation.
- Auth `ensureHome` never recreates while a soft-deleted `homes` row remains. The BFF's serial `0001` migration adds a finite `tenant_cleanup` pending ledger and a D1 insert trigger that raises `account_deleted` only while that pending row exists, including after the original home is soft-deleted. Successful cleanup removes the owner-scoped tenant registry and its cleanup ledger; a later fresh PetCare flow for the same Supabase `owner_sub` may create a new home. Auth `0000` does not reference the later BFF table.

## External Verification Gate

Real Supabase signup/verification/reset mail, SMTP, Cloudflare D1 creation, tunnel/Access provisioning, public deployment, and two-account browser evidence require explicit approval and runtime resources. They are intentionally not executed by this plan. The local acceptance gate is complete when Task 10 passes with mocked Supabase/Cloudflare boundaries; the external integration plan then verifies two real accounts, confirms provider-side signup/login/reset throttles return generic `429`, and checks production session cookies carry `HttpOnly`, `Secure`, `SameSite=Lax`, and `Path=/` without changing the interfaces above.
