# PetCare Sites BFF, Tunnel, and Private Clips Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the tenant-scoped Sites server surface that provisions and revokes one Access-protected Cloudflare Tunnel per home, proxies live data/video through a same-origin BFF, verifies signed agent clip uploads, and serves private seven-day R2 clips without exposing infrastructure credentials.

**Architecture:** This plan produces pure PetCare modules, a `/api/petcare/**` router export, and an hourly reconciliation export; the remote-integration plan alone wires them into `dashboard/worker/index.ts` and declares bindings in `dashboard/.openai/hosting.json`. The router consumes the auth/tenancy plan's `AuthUser`, `requireAuth`, and `TenantRepository.requireHome`, resolves all browser requests from JWT `sub`, and uses D1 route/clip metadata plus a single server-only Access service token. Enrollment uses the auth plan's one-time-code consumer; Cloudflare provisioning durably records every created resource ID or immediately reverses it before returning the connector token exactly once, while signed agent uploads implement the home-agent plan's frozen `PETCARE-CLIP-V1` contract.

**Tech Stack:** TypeScript 5.9, Cloudflare Worker Fetch API, native Web Crypto Ed25519, `node:crypto` streaming SHA-256 under the existing `nodejs_compat` flag, D1/Drizzle 0.45, private R2, Vitest 4.1, Vinext 0.0.50, Wrangler 4.92.

## Global Constraints

- Execute this plan only after `docs/superpowers/plans/2026-07-20-petcare-auth-tenancy.md`; consume its exports rather than redefining them.
- Apply the auth plan's `dashboard/drizzle/0000_petcare_tenancy.sql` first. Only then generate/apply this plan's `dashboard/drizzle/0001_petcare_tunnels_clips.sql`; these schema tasks are serial and never run concurrently.
- The remote-integration plan exclusively owns `dashboard/.openai/hosting.json` and `dashboard/worker/index.ts`. This plan must not edit, stage, or commit either shared file; it exports `routePetCare` and `reconcilePetCare` for the integration plan's final composition.
- Consume exactly `export type AuthUser = { sub: string; email: string | null }`, `requireAuth(request: Request, env: AuthEnv): Promise<AuthUser>`, and `AuthError` (`status = 401`, `code = "unauthorized"`) from `dashboard/lib/auth/require-auth.ts`.
- Consume exactly `new TenantRepository(getDb()).requireHome(ownerSub: string): Promise<HomeRecord>` and `consumeEnrollment(input)` from `dashboard/lib/tenancy/repository.ts`; the caller always passes `AuthUser.sub`, never a client-provided owner or home ID.
- While an owner has a `tenant_cleanup` row, all PetCare access, enrollment, and home creation are blocked. Reconciliation atomically removes the old tenant registry and cleanup row only after R2 and Cloudflare cleanup succeeds; the same retained Supabase identity may then explicitly start a fresh enrollment, but no old agent/key/tunnel/clip can reactivate.
- `consumeEnrollment` receives `{ codeHash, consumedAt, agent: { id, publicKey, tunnelOrigin }, camera: { id, localCameraId } }` and returns `{ homeId, agentId, cameraId }`; do not alter that contract in this plan.
- Keep one active home, one active agent, and one active camera per owner. Unowned selectors return `404`; missing/invalid browser authentication returns `401`.
- Keep `/demo` network-free: no Supabase client, PetCare API call, tunnel/loopback request, WebSocket, or cross-origin image request. The Worker must bypass all PetCare routing unless the path begins with `/api/petcare/`.
- Browser code never receives a tunnel origin, Cloudflare API token, Access client ID/secret, connector token, agent public/private key, signed upload headers, or private R2 object key.
- The Cloudflare provisioning API token is server-only and scoped to the chosen account/zone with only `Cloudflare Tunnel Write`, `DNS Write`, and `Access: Apps and Policies Write`. Tests use a mocked `fetch`; local tests make no Cloudflare calls.
- One global Access service token is pre-created outside this implementation. Its ID is used in each per-home Service Auth policy; its client ID/secret are attached by the BFF on every tunnel request and never logged.
- Provisioning creates a remotely managed tunnel, proxied CNAME, self-hosted Access app, service-token policy, and ingress configuration. Any partial failure triggers reverse-order deletion and leaves a D1 `cleanup_pending` row for hourly reconciliation if a delete fails.
- Return the per-home connector token exactly once, only from the first successful `POST /api/petcare/agent/enroll`. Never persist, hash, log, or expose it to a browser; never re-fetch or re-return it for a consumed code. If the process/response is lost after consumption, retry returns `409 enrollment_rejected`; stale incomplete provisioning is revoked by reconciliation and the owner must issue a new code.
- Live operational REST has a 2,000 ms upstream timeout. Live MJPEG is streamed without buffering and always returns `Cache-Control: private, no-store, no-transform`.
- A revoked, timed-out, non-HTTPS, malformed, or unreachable tunnel route returns `503` status JSON `{ "code": "agent_offline", "agent_id": string | null, "camera_id": string | null, "last_seen_at": string | null }`; a home that has never enrolled returns `200` with `home.state = "needs_enrollment"`. Never substitute demo data.
- Clip upload accepts only `video/mp4` and committed event types `eating`, `resting`, and `bed_sensor_mismatch`; reject `no_meal_12h`.
- Clip upload signatures are Ed25519 over the exact `PETCARE-CLIP-V1` canonical request defined in Task 6 and the integration-owned root fixture `contracts/petcare-agent-wire-v1.json`. The server verifies the registered active agent public key, plus/minus 300-second timestamp, one-use nonce, the 43-character case-preserving unpadded base64url content SHA-256, signed camera/time/event fields, and the actual streamed body digest.
- R2 keys are `clips/<random UUID>.mp4`; they contain no email, owner, home, camera, event type, or event ID. R2 remains private.
- Rate-limit enrollment to 10 attempts per 10 minutes per `CF-Connecting-IP` and 5 attempts per 10 minutes per enrollment-code hash; rate-limit uploads to 30 attempts per minute per agent. D1 counters are authoritative and expire during reconciliation.
- Set `expires_at = created_at + 604800000 ms` exactly. List/read deny at `now >= expires_at` even before physical deletion. An R2 lifecycle rule deletes `clips/` objects after seven days; hourly reconciliation deletes expired metadata, missed objects, orphan objects, expired nonces, and failed tunnel cleanup.
- Manual clip deletion removes D1 metadata before deleting R2 so new reads are denied immediately. If object deletion fails, reconciliation removes the orphan later.
- Never log request headers or bodies. Redaction tests must prove token, cookie, signature, nonce, digest, connector token, tunnel origin, and object key values are absent from logs and error responses.
- Do not add a Cloudflare SDK, validation library, multipart parser, queue, WebSocket proxy, public bucket URL, custom auth type, or generic reverse-proxy abstraction.
- Use the managed Node runtime for every dashboard command:

```powershell
$nodeDir = (Resolve-Path ".runtime/managed/node/PFiles64/nodejs").Path
$env:Path = "$nodeDir;$env:Path"
```

## File Map

- Modify `dashboard/.env.example` — document only runtime key names and non-secret host/account identifiers.
- Modify `dashboard/db/schema.ts` — add tunnel operation, clip, clip-event, and upload-nonce tables after the auth plan's tenancy tables.
- Create `dashboard/drizzle/0001_petcare_tunnels_clips.sql` — D1 migration for this plan only.
- Create `dashboard/lib/petcare/env.ts` — exact Worker environment and safe configuration parsing.
- Create `dashboard/lib/petcare/errors.ts` — fixed public errors and secret-safe response mapping.
- Create `dashboard/lib/petcare/repository.ts` — D1 tunnel routing, revocation state, clip ownership, nonce, rate-limit, deletion-job, tenant-cleanup, and reconciliation queries.
- Create `dashboard/lib/petcare/cloudflare.ts` — minimal Cloudflare REST client with injected `fetch` for tests.
- Create `dashboard/lib/petcare/enrollment.ts` — one-time enrollment/provisioning and logical/remote revocation orchestration.
- Create `dashboard/lib/petcare/agent-enroll.ts` — strict public agent-enrollment HTTP trust boundary.
- Create `dashboard/lib/petcare/live-proxy.ts` — allowlisted two-second REST and streaming MJPEG proxy.
- Create `dashboard/lib/petcare/clip-signature.ts` — strict signed-header parser and Ed25519 verification.
- Create `dashboard/lib/petcare/clip-upload.ts` — streamed hash/R2 upload plus D1 publication/rollback.
- Create `dashboard/lib/petcare/clips.ts` — authenticated list/read/delete.
- Create `dashboard/lib/petcare/account-delete.ts` — recently reauthenticated, idempotent PetCare tenant/data deletion state machine.
- Create `dashboard/lib/petcare/reconcile.ts` — exact retention, orphan cleanup, nonce cleanup, and remote cleanup retry.
- Create `dashboard/lib/petcare/router.ts` — explicit same-origin route table and method guards.
- Create `dashboard/tests/helpers/petcare-fakes.ts` — small in-memory D1/R2/fetch/log fakes reused by this plan's tests.
- Create `dashboard/tests/petcare-schema.test.ts` — bindings, schema, and generated-SQL contract.
- Create `dashboard/tests/cloudflare.test.ts` — exact provisioning API requests, token secrecy, and reverse rollback.
- Create `dashboard/tests/enrollment.test.ts` — success, one-time return, reuse/expiry/collision, rollback, ownership, and revocation.
- Create `dashboard/tests/agent-enroll.test.ts` — frozen request/response validation, rate-limit, cookie independence, and redaction.
- Create `dashboard/tests/live-proxy.test.ts` — Access headers, tenant route resolution, timeout/offline, MJPEG streaming, and cache headers.
- Create `dashboard/tests/clip-upload.test.ts` — key/timestamp/nonce/digest/signature/event/revocation/upload cleanup cases.
- Create `dashboard/tests/clips.test.ts` — two-user list/read/delete isolation and immediate expiry denial.
- Create `dashboard/tests/account-delete.test.ts` — recent reauthentication handoff, idempotent logical deletion, retry cleanup, recreation guard, and two-tenant isolation.
- Create `dashboard/tests/reconcile.test.ts` — exact seven-day edge, R2/D1 orphan cleanup, nonce cleanup, and failed tunnel cleanup retry.
- Create `dashboard/tests/petcare-router.test.ts` — closed router table, same-origin protection, secret redaction, and `/demo` no-network bypass.

## Exact Interfaces Added by This Plan

```ts
// dashboard/lib/petcare/repository.ts
export type ActiveRoute = {
  homeId: string;
  agentId: string;
  cameraId: string;
  tunnelOrigin: string;
  publicKey: string;
  lastSeenAt: string | null;
};
export type OwnedClip = {
  id: string;
  objectKey: string;
  startedAt: string;
  endedAt: string;
  createdAt: string;
  expiresAt: string;
  events: Array<{ eventType: "eating" | "resting" | "bed_sensor_mismatch"; eventId: string }>;
};
export class PetCareRepository {
  constructor(db: D1Database);
  findEnrollmentHome(codeHash: string, now: string): Promise<{ homeId: string }>;
  getHomeConnection(homeId: string): Promise<{ state: "needs_enrollment" } | { state: "ready"; route: ActiveRoute; revoked: boolean }>;
  requireActiveRoute(homeId: string): Promise<ActiveRoute>;
  requireActiveAgent(agentId: string, cameraId: string): Promise<ActiveRoute>;
  markAgentSeen(agentId: string, seenAt: string): Promise<void>;
  consumeNonce(agentId: string, nonce: string, now: string): Promise<void>;
  checkRateLimit(subject: string, route: string, limit: number, windowSeconds: number, now: Date): Promise<void>;
  listOwnedClips(homeId: string, now: string): Promise<OwnedClip[]>;
  requireOwnedClip(homeId: string, clipId: string, now: string): Promise<OwnedClip>;
  deleteClipAndQueueObject(homeId: string, clipId: string, now: string): Promise<{ objectKey: string }>;
  recordCleanupPending(homeId: string, agentId: string, ledger: ResourceLedger, code: string): Promise<void>;
  markActivationPending(homeId: string, activationExpiresAt: string, now: string): Promise<void>;
  beginTenantCleanup(ownerSub: string, now: string): Promise<{ homeId: string; status: "cleanup_pending" } | { status: "absent" }>;
  getTenantCleanup(ownerSub: string): Promise<{ homeId: string; status: "cleanup_pending" } | null>;
  completeTenantCleanup(ownerSub: string, homeId: string): Promise<void>;
}

// dashboard/lib/petcare/cloudflare.ts
export type CloudflareConfig = ReturnType<typeof readPetCareConfig>;
export class CloudflareClient {
  constructor(config: CloudflareConfig, fetchImpl?: typeof fetch);
  createTunnel(name: string): Promise<{ id: string }>;
  createDnsRecord(hostname: string, tunnelId: string): Promise<{ id: string }>;
  createAccessApp(hostname: string, name: string): Promise<{ id: string; aud: string }>;
  createAccessPolicy(appId: string): Promise<{ id: string }>;
  configureTunnel(tunnelId: string, hostname: string, aud: string): Promise<void>;
  getConnectorToken(tunnelId: string): Promise<string>;
  deleteAccessPolicy(appId: string, policyId: string): Promise<void>;
  deleteAccessApp(appId: string): Promise<void>;
  deleteDnsRecord(recordId: string): Promise<void>;
  deleteTunnel(tunnelId: string): Promise<void>;
}

// dashboard/lib/petcare/enrollment.ts
export type EnrollmentInput = { code: string; publicKey: string; localCameraId: string; connectingIp: string };
export class EnrollmentProvisioningService {
  constructor(tenants: TenantRepository, petcare: PetCareRepository, cloudflare: CloudflareClient, now?: () => Date);
  enroll(input: EnrollmentInput): Promise<{ agentId: string; cameraId: string; connectorToken: string }>;
  revoke(ownerSub: string, now: Date): Promise<{ status: "revoked" | "revocation_pending" }>;
}

// dashboard/lib/petcare/agent-enroll.ts
export type AgentEnrollWireRequest = {
  enrollment_code: string;
  algorithm: "Ed25519";
  public_key: string;
  local_camera_id: "pc-webcam-01";
};
export type AgentEnrollWireResponse = {
  agent_id: string;
  camera_id: string;
  connector_token: string;
};
export function handleAgentEnroll(request: Request, env: PetCareEnv, now: Date): Promise<Response>;

// dashboard/lib/petcare/live-proxy.ts
export function proxyStatus(user: AuthUser, env: PetCareEnv, now: Date): Promise<Response>;
export function proxyMjpeg(user: AuthUser, env: PetCareEnv, cameraId: string): Promise<Response>;

// dashboard/lib/petcare/clip-signature.ts
export type ClipEvent = { eventType: "eating" | "resting" | "bed_sensor_mismatch"; eventId: string };
export type SignedClipHeaders = {
  agentId: string; cameraId: string; timestamp: number; nonce: string; digest: string;
  startedAt: string; endedAt: string; events: ClipEvent[]; signature: Uint8Array;
};
export function parseSignedClipHeaders(request: Request): SignedClipHeaders;
export function verifyClipSignature(headers: SignedClipHeaders, publicKey: string): Promise<void>;

// dashboard/lib/petcare/clip-upload.ts
export function uploadSignedClip(request: Request, env: PetCareEnv, now: Date): Promise<Response>;

// dashboard/lib/petcare/clips.ts
export function listClips(request: Request, env: PetCareEnv, now: Date): Promise<Response>;
export function readClip(request: Request, env: PetCareEnv, clipId: string, now: Date): Promise<Response>;
export function deleteClip(request: Request, env: PetCareEnv, clipId: string, now: Date): Promise<Response>;

// dashboard/lib/petcare/account-delete.ts
export function deletePetCareAccountData(request: Request, env: PetCareEnv, now: Date): Promise<Response>;

// dashboard/lib/petcare/reconcile.ts
export function reconcilePetCare(env: PetCareEnv, now: Date): Promise<ReconcileResult>;

// dashboard/lib/petcare/router.ts
export type PetCareExecutionContext = { waitUntil(promise: Promise<unknown>): void };
export function routePetCare(request: Request, env: PetCareEnv, ctx: PetCareExecutionContext): Promise<Response | null>;
```

---

### Task 1: Extend the completed auth schema for tunnels and clips

**Files:**
- Modify: `dashboard/.env.example`
- Modify: `dashboard/db/schema.ts`
- Create: `dashboard/drizzle/0001_petcare_tunnels_clips.sql`
- Create: `dashboard/lib/petcare/env.ts`
- Test: `dashboard/tests/petcare-schema.test.ts`

**Interfaces:**
- Consumes: auth-plan exports `homes`, `agents`, `cameras`, `enrollmentTokens` from `dashboard/db/schema.ts`; existing `getDb()` from `dashboard/db/index.ts`.
- Produces: `tunnelRoutes`, `clips`, `clipEvents`, `uploadNonces`, `objectDeletionJobs`, `requestLimits`, `tenantCleanup`, `reconcileState`; `PetCareEnv`; `readPetCareConfig(env)`.

- [ ] **Step 1: Write the failing serial-migration test**

```ts
// dashboard/tests/petcare-schema.test.ts
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { describe, expect, it } from "vitest";

const root = resolve(import.meta.dirname, "..");

describe("PetCare Sites storage contract", () => {
  it("ships the tunnel and clip migration after the tenancy migration", () => {
    expect(readFileSync(resolve(root, "drizzle/0000_petcare_tenancy.sql"), "utf8")).toContain("CREATE TABLE `homes`");
    const sql = readFileSync(resolve(root, "drizzle/0001_petcare_tunnels_clips.sql"), "utf8");
    for (const table of ["tunnel_routes", "clips", "clip_events", "upload_nonces", "object_deletion_jobs", "request_limits", "tenant_cleanup", "reconcile_state"]) {
      expect(sql).toContain(`CREATE TABLE \`${table}\``);
    }
    expect(sql).toContain("CHECK (`status` IN ('provisioning','activation_pending','active','cleanup_pending','revocation_pending','revoked'))");
    expect(sql).toContain("UNIQUE (`agent_id`, `nonce`)");
    expect(sql).toContain("ON `clips` (`home_id`, `expires_at`)");
    expect(sql).toContain("CREATE TRIGGER `block_home_recreation_during_petcare_cleanup`");
    expect(sql).not.toMatch(/connector_token|access_client_secret|api_token/i);
  });
});
```

- [ ] **Step 2: Run the test and verify RED**

Run from the repository root:

```powershell
$nodeDir = (Resolve-Path ".runtime/managed/node/PFiles64/nodejs").Path
$env:Path = "$nodeDir;$env:Path"
npm.cmd --prefix dashboard test -- tests/petcare-schema.test.ts
```

Expected: FAIL because `0001_petcare_tunnels_clips.sql` does not exist; the auth plan's `0000_petcare_tenancy.sql` assertion already passes.

- [ ] **Step 3: Add the minimum schema and environment contract**

Append these names to the auth plan's `dashboard/.env.example`; values remain empty:

```dotenv
CF_ACCOUNT_ID=
CF_ZONE_ID=
CF_ZONE_NAME=
CF_ACCESS_TEAM_NAME=
CF_TUNNEL_API_TOKEN=
CF_ACCESS_SERVICE_TOKEN_ID=
CF_ACCESS_CLIENT_ID=
CF_ACCESS_CLIENT_SECRET=
```

Append these Drizzle definitions to `dashboard/db/schema.ts`, reusing the file's existing imports and `homes` export:

```ts
export const tunnelRoutes = sqliteTable("tunnel_routes", {
  homeId: text("home_id").primaryKey().references(() => homes.id),
  agentId: text("agent_id").notNull(),
  tunnelId: text("tunnel_id"),
  tunnelOrigin: text("tunnel_origin"),
  accessAppId: text("access_app_id"),
  accessPolicyId: text("access_policy_id"),
  accessAud: text("access_aud"),
  dnsRecordId: text("dns_record_id"),
  activationExpiresAt: text("activation_expires_at"),
  status: text("status", { enum: ["provisioning", "activation_pending", "active", "cleanup_pending", "revocation_pending", "revoked"] }).notNull(),
  createdAt: text("created_at").notNull(),
  updatedAt: text("updated_at").notNull(),
  lastError: text("last_error"),
});

export const clips = sqliteTable("clips", {
  id: text("id").primaryKey(),
  homeId: text("home_id").notNull().references(() => homes.id),
  cameraId: text("camera_id").notNull(),
  objectKey: text("object_key").notNull().unique(),
  sha256: text("sha256").notNull(),
  sizeBytes: integer("size_bytes").notNull(),
  startedAt: text("started_at").notNull(),
  endedAt: text("ended_at").notNull(),
  expiresAt: text("expires_at").notNull(),
  createdAt: text("created_at").notNull(),
}, (table) => [index("clips_home_expires_idx").on(table.homeId, table.expiresAt)]);

export const clipEvents = sqliteTable("clip_events", {
  clipId: text("clip_id").notNull().references(() => clips.id, { onDelete: "cascade" }),
  eventType: text("event_type", { enum: ["eating", "resting", "bed_sensor_mismatch"] }).notNull(),
  eventId: text("event_id").notNull(),
}, (table) => [primaryKey({ columns: [table.clipId, table.eventType, table.eventId] })]);

export const uploadNonces = sqliteTable("upload_nonces", {
  agentId: text("agent_id").notNull(),
  nonce: text("nonce").notNull(),
  usedAt: text("used_at").notNull(),
  expiresAt: text("expires_at").notNull(),
}, (table) => [primaryKey({ columns: [table.agentId, table.nonce] }), index("upload_nonces_expires_idx").on(table.expiresAt)]);

export const objectDeletionJobs = sqliteTable("object_deletion_jobs", {
  objectKey: text("object_key").primaryKey(),
  homeId: text("home_id").notNull().references(() => homes.id),
  requestedAt: text("requested_at").notNull(),
  lastError: text("last_error"),
}, (table) => [index("object_deletion_jobs_home_idx").on(table.homeId)]);

export const requestLimits = sqliteTable("request_limits", {
  subject: text("subject").notNull(),
  route: text("route").notNull(),
  windowStart: integer("window_start").notNull(),
  count: integer("count").notNull(),
  expiresAt: text("expires_at").notNull(),
}, (table) => [primaryKey({ columns: [table.subject, table.route, table.windowStart] }), index("request_limits_expires_idx").on(table.expiresAt)]);

export const tenantCleanup = sqliteTable("tenant_cleanup", {
  ownerSub: text("owner_sub").primaryKey(),
  homeId: text("home_id").notNull().unique(),
  status: text("status", { enum: ["cleanup_pending"] }).notNull(),
  startedAt: text("started_at").notNull(),
  updatedAt: text("updated_at").notNull(),
  lastError: text("last_error"),
});

export const reconcileState = sqliteTable("reconcile_state", {
  name: text("name").primaryKey(),
  cursor: text("cursor"),
  updatedAt: text("updated_at").notNull(),
});
```

Add any missing imports from `drizzle-orm/sqlite-core`: `index`, `integer`, `primaryKey`, `sqliteTable`, and `text`. Generate `dashboard/drizzle/0001_petcare_tunnels_clips.sql` with `npm run db:generate`, inspect it, and add the explicit status check because Drizzle's text enum is TypeScript-only:

```sql
CREATE TABLE `tunnel_routes` (
  `home_id` text PRIMARY KEY NOT NULL REFERENCES `homes`(`id`),
  `agent_id` text NOT NULL,
  `tunnel_id` text,
  `tunnel_origin` text,
  `access_app_id` text,
  `access_policy_id` text,
  `access_aud` text,
  `dns_record_id` text,
  `activation_expires_at` text,
  `status` text NOT NULL CHECK (`status` IN ('provisioning','activation_pending','active','cleanup_pending','revocation_pending','revoked')),
  `created_at` text NOT NULL,
  `updated_at` text NOT NULL,
  `last_error` text
);
CREATE TABLE `clips` (
  `id` text PRIMARY KEY NOT NULL,
  `home_id` text NOT NULL REFERENCES `homes`(`id`),
  `camera_id` text NOT NULL,
  `object_key` text NOT NULL UNIQUE,
  `sha256` text NOT NULL,
  `size_bytes` integer NOT NULL,
  `started_at` text NOT NULL,
  `ended_at` text NOT NULL,
  `expires_at` text NOT NULL,
  `created_at` text NOT NULL
);
CREATE INDEX `clips_home_expires_idx` ON `clips` (`home_id`,`expires_at`);
CREATE TABLE `clip_events` (
  `clip_id` text NOT NULL REFERENCES `clips`(`id`) ON DELETE CASCADE,
  `event_type` text NOT NULL CHECK (`event_type` IN ('eating','resting','bed_sensor_mismatch')),
  `event_id` text NOT NULL,
  PRIMARY KEY (`clip_id`,`event_type`,`event_id`)
);
CREATE TABLE `upload_nonces` (
  `agent_id` text NOT NULL,
  `nonce` text NOT NULL,
  `used_at` text NOT NULL,
  `expires_at` text NOT NULL,
  PRIMARY KEY (`agent_id`,`nonce`)
);
CREATE INDEX `upload_nonces_expires_idx` ON `upload_nonces` (`expires_at`);
CREATE TABLE `object_deletion_jobs` (
  `object_key` text PRIMARY KEY NOT NULL,
  `home_id` text NOT NULL REFERENCES `homes`(`id`),
  `requested_at` text NOT NULL,
  `last_error` text
);
CREATE INDEX `object_deletion_jobs_home_idx` ON `object_deletion_jobs` (`home_id`);
CREATE TABLE `request_limits` (
  `subject` text NOT NULL,
  `route` text NOT NULL,
  `window_start` integer NOT NULL,
  `count` integer NOT NULL,
  `expires_at` text NOT NULL,
  PRIMARY KEY (`subject`,`route`,`window_start`)
);
CREATE INDEX `request_limits_expires_idx` ON `request_limits` (`expires_at`);
CREATE TABLE `tenant_cleanup` (
  `owner_sub` text PRIMARY KEY NOT NULL,
  `home_id` text NOT NULL UNIQUE,
  `status` text NOT NULL CHECK (`status` = 'cleanup_pending'),
  `started_at` text NOT NULL,
  `updated_at` text NOT NULL,
  `last_error` text
);
CREATE TRIGGER `block_home_recreation_during_petcare_cleanup`
BEFORE INSERT ON `homes`
WHEN EXISTS (SELECT 1 FROM `tenant_cleanup` WHERE `owner_sub` = NEW.`owner_sub`)
BEGIN
  SELECT RAISE(ABORT, 'petcare_cleanup_pending');
END;
CREATE TABLE `reconcile_state` (
  `name` text PRIMARY KEY NOT NULL,
  `cursor` text,
  `updated_at` text NOT NULL
);
```

`tenant_cleanup` is a finite cleanup ledger, not a permanent tombstone. Every `ensureHome`/enrollment insert path checks it and maps trigger code `petcare_cleanup_pending` to generic `410 account_deleted` while present. `completeTenantCleanup` removes the old home registry and this ledger atomically only after all remote/object cleanup is verified, which automatically disables the trigger condition and reopens only the explicit fresh-enrollment path.

Create `dashboard/lib/petcare/env.ts`:

```ts
import type { AuthEnv } from "../auth/require-auth";

export interface PetCareEnv extends AuthEnv {
  DB: D1Database;
  CLIPS: R2Bucket;
  CF_ACCOUNT_ID: string;
  CF_ZONE_ID: string;
  CF_ZONE_NAME: string;
  CF_ACCESS_TEAM_NAME: string;
  CF_TUNNEL_API_TOKEN: string;
  CF_ACCESS_SERVICE_TOKEN_ID: string;
  CF_ACCESS_CLIENT_ID: string;
  CF_ACCESS_CLIENT_SECRET: string;
}

export function readPetCareConfig(env: PetCareEnv) {
  const keys = [
    "CF_ACCOUNT_ID", "CF_ZONE_ID", "CF_ZONE_NAME", "CF_ACCESS_TEAM_NAME",
    "CF_TUNNEL_API_TOKEN", "CF_ACCESS_SERVICE_TOKEN_ID",
    "CF_ACCESS_CLIENT_ID", "CF_ACCESS_CLIENT_SECRET",
  ] as const;
  for (const key of keys) if (!env[key]) throw new Error(`missing_runtime_secret:${key}`);
  return {
    accountId: env.CF_ACCOUNT_ID,
    zoneId: env.CF_ZONE_ID,
    zoneName: env.CF_ZONE_NAME,
    accessTeamName: env.CF_ACCESS_TEAM_NAME,
    apiToken: env.CF_TUNNEL_API_TOKEN,
    serviceTokenId: env.CF_ACCESS_SERVICE_TOKEN_ID,
    accessClientId: env.CF_ACCESS_CLIENT_ID,
    accessClientSecret: env.CF_ACCESS_CLIENT_SECRET,
  };
}
```

- [ ] **Step 4: Generate/inspect and run GREEN**

```powershell
$nodeDir = (Resolve-Path ".runtime/managed/node/PFiles64/nodejs").Path
$env:Path = "$nodeDir;$env:Path"
npm.cmd --prefix dashboard run db:generate
npm.cmd --prefix dashboard test -- tests/petcare-schema.test.ts
```

Expected: Drizzle reports one new migration after `0000_petcare_tenancy.sql`, and `petcare-schema.test.ts` passes 1/1.

- [ ] **Step 5: Commit**

```powershell
git add dashboard/.env.example dashboard/db/schema.ts dashboard/drizzle dashboard/lib/petcare/env.ts dashboard/tests/petcare-schema.test.ts
git commit -m "feat(sites): add tunnel and clip storage"
```

### Task 2: Add fixed public errors and D1 repository operations

**Files:**
- Create: `dashboard/lib/petcare/errors.ts`
- Create: `dashboard/lib/petcare/repository.ts`
- Create: `dashboard/tests/helpers/petcare-fakes.ts`
- Test: `dashboard/tests/petcare-repository.test.ts`

**Interfaces:**
- Consumes: `getDb()`, auth-plan `agents`, `cameras`, and Task 1 tables.
- Produces: `PetCareError`; `errorResponse(error)`; `PetCareRepository.requireActiveRoute(homeId)`; `reserveTunnel`, `updateTunnelResource`, `recordCleanupPending`, `markActivationPending`, `requestRevocation`, `markTunnelState`, `consumeNonce`, `checkRateLimit`, guarded `publishClip`, `listOwnedClips`, `requireOwnedClip`, `queueObjectDeletion`, `deleteClipAndQueueObject`, `beginTenantCleanup`, `getTenantCleanup`, `completeTenantCleanup`, and reconciliation selectors.

- [ ] **Step 1: Write failing repository tests**

Create tests proving these exact outcomes with a two-home fake database:

```ts
it("never resolves another home's active tunnel", async () => {
  await expect(repo.requireActiveRoute("home-b")).rejects.toMatchObject({ status: 503, code: "agent_offline" });
});

it("consumes an agent nonce once", async () => {
  await expect(repo.consumeNonce("agent-a", "nonce-1234567890123456", now)).resolves.toBeUndefined();
  await expect(repo.consumeNonce("agent-a", "nonce-1234567890123456", now)).rejects.toMatchObject({ status: 409, code: "replay" });
});

it("denies a clip at the exact expiry instant", async () => {
  await expect(repo.requireOwnedClip("home-a", "clip-a", "2026-07-27T00:00:00.000Z"))
    .rejects.toMatchObject({ status: 404, code: "not_found" });
});

it("enforces a fixed-window request limit atomically", async () => {
  for (let attempt = 0; attempt < 5; attempt += 1) {
    await repo.checkRateLimit("code-hash", "enroll-code", 5, 600, now);
  }
  await expect(repo.checkRateLimit("code-hash", "enroll-code", 5, 600, now))
    .rejects.toMatchObject({ status: 429, code: "rate_limited" });
});

it("cannot publish after tenant cleanup starts", async () => {
  await repo.beginTenantCleanup("owner-a", now.toISOString());
  await expect(repo.publishClip(activeAgentClip)).rejects.toMatchObject({ status: 410, code: "account_deleted" });
});
```

The fake helper exposes only `FakeD1`, `FakeR2`, `jsonRequest`, and `fixedClock`; it must not contain product authorization logic.

- [ ] **Step 2: Run RED**

```powershell
npm.cmd --prefix dashboard test -- tests/petcare-repository.test.ts
```

Expected: FAIL because `PetCareRepository` does not exist.

- [ ] **Step 3: Implement fixed errors and prepared D1 queries**

Create `errors.ts` with a closed set of safe errors:

```ts
export class PetCareError extends Error {
  constructor(readonly status: number, readonly code: string) { super(code); }
}

export function errorResponse(error: unknown): Response {
  const safe = error instanceof AuthError
    ? error
    : error instanceof RecentReauthError
      ? error
    : error instanceof PetCareError
      ? error
      : new PetCareError(500, "internal_error");
  return Response.json({ error: safe.code }, {
    status: safe.status,
    headers: { "Cache-Control": "private, no-store" },
  });
}
```

Import `AuthError` from `../auth/require-auth` and `RecentReauthError` from `../auth/recent-reauth`; do not define another auth or reauthentication error. This preserves the auth plan's exact generic `401 unauthorized`, `401 reauthentication_failed`, `429 rate_limited`, and `503 auth_unavailable` mappings without provider/password detail.

In `repository.ts`, keep all ownership predicates in SQL. The active-route query must join home, agent, camera, and route and require every active marker:

```sql
SELECT tr.home_id, tr.agent_id, tr.tunnel_origin, a.public_key, c.id AS camera_id
FROM tunnel_routes tr
JOIN homes h ON h.id = tr.home_id AND h.deleted_at IS NULL
JOIN agents a ON a.id = tr.agent_id AND a.home_id = tr.home_id
JOIN cameras c ON c.agent_id = a.id AND c.home_id = tr.home_id
LEFT JOIN tenant_cleanup tc ON tc.home_id = tr.home_id
WHERE tr.home_id = ? AND tr.status = 'active'
  AND tc.home_id IS NULL
  AND a.revoked_at IS NULL AND c.disabled_at IS NULL
LIMIT 1
```

Validate `tunnel_origin` with `new URL`; require `https:`, an empty username/password, `/` pathname, and no search/hash. Every live, agent-upload, and clip query joins `homes` with `deleted_at IS NULL` and excludes any `tenant_cleanup` ledger; `account_deleted` is the sole internal signal and maps to a generic closed response. `consumeNonce` uses one `INSERT` guarded by the `(agent_id, nonce)` primary key and maps a constraint failure to `409 replay`. `checkRateLimit` uses one `INSERT ... ON CONFLICT DO UPDATE SET count = count + 1 RETURNING count`; compare the returned count with the fixed route limit and return `429 rate_limited` when exceeded. `requireOwnedClip` includes `id = ? AND home_id = ? AND expires_at > ?` plus the active-home predicates. Every `object_deletion_jobs` insert includes `home_id`, allowing account completion to prove its owner-scoped job set is empty after clip metadata is gone.

`publishClip` must make the home-state check part of the clip insert itself, using `INSERT INTO clips (...) SELECT ... FROM homes h LEFT JOIN tenant_cleanup tc ... WHERE h.id = ? AND h.deleted_at IS NULL AND tc.home_id IS NULL`; require exactly one inserted row before batching event rows. This closes the race where account deletion begins after upload authentication but before publication. `requestRevocation` batches agent `revoked_at`, camera `disabled_at`, and route `revocation_pending` updates before returning. `deleteClipAndQueueObject` batches metadata deletion with insertion of the object-key deletion job so failed physical deletion is retried without waiting seven days. Enrollment resource-state methods persist the non-secret ledger after every create; they store only an activation deadline, never the code hash or connector token. Tenant-cleanup methods are idempotent by `owner_sub`; their exact logical-delete batch is specified in Task 8.

- [ ] **Step 4: Run GREEN**

```powershell
npm.cmd --prefix dashboard test -- tests/petcare-repository.test.ts
```

Expected: all repository ownership, nonce, expiry, and revocation-state tests pass.

- [ ] **Step 5: Commit**

```powershell
git add dashboard/lib/petcare/errors.ts dashboard/lib/petcare/repository.ts dashboard/tests/helpers/petcare-fakes.ts dashboard/tests/petcare-repository.test.ts
git commit -m "feat(sites): add tenant scoped petcare repository"
```

### Task 3: Implement the mocked Cloudflare provisioning client

**Files:**
- Create: `dashboard/lib/petcare/cloudflare.ts`
- Test: `dashboard/tests/cloudflare.test.ts`

**Interfaces:**
- Consumes: parsed Task 1 Cloudflare config and injected `typeof fetch`.
- Produces: `CloudflareClient` primitive create/configure/delete methods and `CloudflareApiError` with no response body or credential fields.

- [ ] **Step 1: Write failing exact-request tests**

Use a queued fetch fake and assert, in order:

```ts
expect(calls.map((call) => `${call.method} ${new URL(call.url).pathname}`)).toEqual([
  "POST /client/v4/accounts/acct/cfd_tunnel",
  "POST /client/v4/zones/zone/dns_records",
  "POST /client/v4/accounts/acct/access/apps",
  "POST /client/v4/accounts/acct/access/apps/app-1/policies",
  "PUT /client/v4/accounts/acct/cfd_tunnel/tunnel-1/configurations",
  "GET /client/v4/accounts/acct/cfd_tunnel/tunnel-1/token",
]);
expect(JSON.parse(calls[0].body)).toEqual({ name: "petcare-home-a", config_src: "cloudflare" });
expect(JSON.parse(calls[1].body)).toEqual({ type: "CNAME", name: "home-a.agents.example.com", content: "tunnel-1.cfargotunnel.com", proxied: true, ttl: 1 });
expect(JSON.parse(calls[2].body)).toMatchObject({ name: "PetCare home-a", domain: "home-a.agents.example.com", type: "self_hosted", service_auth_401_redirect: true });
expect(JSON.parse(calls[3].body)).toEqual({ name: "PetCare Sites BFF", decision: "non_identity", include: [{ service_token: { token_id: "service-token-id" } }], precedence: 1 });
expect(JSON.parse(calls[4].body)).toEqual({ config: { ingress: [
  { hostname: "home-a.agents.example.com", service: "http://127.0.0.1:8000", originRequest: { access: { required: true, teamName: "petcare", audTag: ["aud-1"] } } },
  { service: "http_status:404" },
] } });
```

Also assert every request has `Authorization: Bearer scoped-token`, no URL/body contains it, API failures expose only `cloudflare_api_error`, and deletion runs policy → app → DNS → tunnel while treating `404` as already deleted.

- [ ] **Step 2: Run RED**

```powershell
npm.cmd --prefix dashboard test -- tests/cloudflare.test.ts
```

Expected: FAIL because `CloudflareClient` does not exist.

- [ ] **Step 3: Implement the minimal REST client**

Use the official endpoints directly, with a single private request helper:

```ts
private async request<T>(path: string, init: RequestInit): Promise<T> {
  const response = await this.fetchImpl(`https://api.cloudflare.com/client/v4${path}`, {
    ...init,
    headers: { Authorization: `Bearer ${this.config.apiToken}`, "Content-Type": "application/json", ...init.headers },
  });
  if (!response.ok) throw new CloudflareApiError(response.status);
  const payload = await response.json() as { success: boolean; result: T };
  if (!payload.success) throw new CloudflareApiError(502);
  return payload.result;
}
```

Provide exact methods `createTunnel`, `createDnsRecord`, `createAccessApp`, `createAccessPolicy`, `configureTunnel`, `getConnectorToken`, `deleteAccessPolicy`, `deleteAccessApp`, `deleteDnsRecord`, and `deleteTunnel`. Use these official API contracts: Cloudflare Tunnel create/token/configuration, DNS record create/delete, and Access application/policy create/delete. Do not import a Cloudflare SDK.

- [ ] **Step 4: Run GREEN**

```powershell
npm.cmd --prefix dashboard test -- tests/cloudflare.test.ts
```

Expected: all exact-request, permission-header, error-redaction, and reverse-delete tests pass.

- [ ] **Step 5: Commit**

```powershell
git add dashboard/lib/petcare/cloudflare.ts dashboard/tests/cloudflare.test.ts
git commit -m "feat(sites): add scoped cloudflare provisioner"
```

### Task 4: Provision and revoke one tunnel per enrolled home

**Files:**
- Create: `dashboard/lib/petcare/enrollment.ts`
- Create: `dashboard/lib/petcare/agent-enroll.ts`
- Test: `dashboard/tests/enrollment.test.ts`
- Test: `dashboard/tests/agent-enroll.test.ts`

**Interfaces:**
- Consumes: auth-plan `hashEnrollmentCode` and `TenantRepository.consumeEnrollment`; `PetCareRepository`; `CloudflareClient`.
- Produces: `EnrollmentProvisioningService.enroll(input): Promise<{ agentId: string; cameraId: string; connectorToken: string }>`; `revoke(ownerSub)`; and strict `handleAgentEnroll(request, env, now)`.

- [ ] **Step 1: Write failing enrollment and revocation tests**

Cover all of these cases: valid ten-minute code; reused code; exact expiry; second active agent/camera; malformed 32-byte Ed25519 public key; Cloudflare failure after each created resource; D1 failure immediately after each remote create; reverse-delete failure retaining that just-created ID in `cleanup_pending`; D1 consume collision after successful remote provisioning; process failure immediately before consume, immediately after consume, after `activation_pending`, and before response; every post-consume retry returns `409` without token lookup/Cloudflare calls; stale `provisioning`/`activation_pending` is reconciled to `cleanup_pending` and remotely revoked; connector token absent from every D1 row/log and returned from only the first successful response; owner A cannot revoke owner B; logical revocation blocks proxy/upload before remote deletes; failed remote delete returns `revocation_pending`; sixth attempt for one code hash and eleventh attempt for one `CF-Connecting-IP` within ten minutes return `429 rate_limited` without a Cloudflare call.

The success assertion is:

```ts
expect(await service.enroll({ code: "AQEBAQEBAQEBAQEBAQEBAQ", publicKey, localCameraId: "pc-webcam-01", connectingIp: "203.0.113.10" })).toEqual({
  agentId: expect.stringMatching(/^agent_/),
  cameraId: expect.stringMatching(/^camera_/),
  connectorToken: "connector-once",
});
expect(JSON.stringify(fakeD1.rows)).not.toContain("connector-once");
expect(JSON.stringify(logs)).not.toContain("connector-once");
```

The HTTP-boundary success test must construct a request with `Content-Type: application/json` and `CF-Connecting-IP: 203.0.113.10`, invoke `handleAgentEnroll`, and assert the service received `connectingIp: "203.0.113.10"`. Add negative cases for absent/duplicate/malformed `CF-Connecting-IP`, duplicate or non-JSON `Content-Type`, missing/invalid/duplicate `Content-Length`, declared length over 4096, and a streamed body that exceeds 4096 despite a smaller declared length; none may call the provisioning service.

- [ ] **Step 2: Run RED**

```powershell
npm.cmd --prefix dashboard test -- tests/enrollment.test.ts tests/agent-enroll.test.ts
```

Expected: FAIL because `EnrollmentProvisioningService` and `handleAgentEnroll` do not exist.

- [ ] **Step 3: Implement provisioning with durable step state**

Use server-generated IDs and a DNS-safe home label derived from the opaque home UUID (`home-${home.id.replaceAll("-", "").slice(0, 20)}`). Validate the public key as canonical unpadded base64url decoding to exactly 32 bytes. Consume the auth plan's `hashEnrollmentCode`; do not implement a second hash helper. Before any token lookup or Cloudflare call, apply both fixed-window limits using the platform-provided `CF-Connecting-IP` and the code hash; reject a missing/invalid connecting IP with `400 invalid_request` rather than trusting `X-Forwarded-For`.

The service sequence is exact:

1. Hash and look up the unconsumed, unexpired code; reserve or resume that home's `tunnel_routes` row as `provisioning` with generated server agent/camera IDs. Never store the code hash. A retry may resume only while the code remains unconsumed; every consumed/reused code returns `409 enrollment_rejected` before token lookup or Cloudflare calls.
2. If an unconsumed retry finds partially persisted remote IDs, resume from the next missing create; if the persisted state is inconsistent, move it to `cleanup_pending`, reverse-delete, and leave the still-unconsumed code available only after cleanup completes.
3. Create the tunnel, assign its ID to an in-memory `ResourceLedger`, and immediately persist `tunnel_id` before the next Cloudflare call.
4. Create the proxied CNAME and immediately persist `dns_record_id`/`tunnel_origin`.
5. Create the self-hosted Access application and immediately persist `access_app_id`/`access_aud`.
6. Create the `non_identity` policy and immediately persist `access_policy_id`.
7. Configure ingress to `http://127.0.0.1:8000` plus terminal `http_status:404`, then retrieve the connector token into a local variable.
8. Call `TenantRepository.consumeEnrollment` with the frozen auth-plan input.
9. Mark `tunnel_routes` `activation_pending` with `activation_expires_at = now + 10 minutes`, then return the local connector token in the first and only success response. Do not mark the route active during enrollment.

Every create uses this invariant: assign the returned ID to the local ledger first; try its D1 update; if that update fails, immediately delete that exact resource. If the delete also fails, call `recordCleanupPending(homeId, agentId, ledger, "resource_state_write_failed")`, whose upsert writes every non-null local/persisted resource ID without provider text or credentials. Only after persistence succeeds may the next resource be created. A later failure reverses policy → app → DNS → tunnel from the union of the local ledger and D1 row; a fully cleaned route row is deleted, otherwise it remains `cleanup_pending`.

Crash-boundary behavior is explicit: a crash before atomic consume leaves the code unconsumed and retry resumes/cleans the `provisioning` row. A crash or response loss at any point after consume is not recoverable with that code: retry is `409`, the connector token is never re-fetched/re-returned, and reconciliation moves stale `provisioning` or `activation_pending` through `cleanup_pending` to revoked before the owner issues a new code. `markAgentSeen` and the first valid signed upload atomically change `activation_pending` to `active` and clear `activation_expires_at`. A delivered connector token therefore must activate within ten minutes; otherwise it is deliberately revoked as indistinguishable from a lost response.

Revocation first resolves `requireHome(ownerSub)`, atomically sets agent/camera inactive, clears `activation_expires_at`, and sets route `revocation_pending`, then performs reverse deletion. It returns `revoked` after all deletes or `revocation_pending` after a safe failure; both states already block proxy and upload.

- [ ] **Step 4: Implement and test the strict agent HTTP boundary**

`handleAgentEnroll` is the sole executable public enrollment boundary. It accepts only `POST`, exactly one `Content-Type: application/json` (optional `charset=utf-8` only), exactly one decimal `Content-Length` in `1..4096`, an actual UTF-8 body no larger than 4096 bytes, and exactly these four snake_case keys. Read exactly one platform `CF-Connecting-IP`; accept only a canonical IPv4 or IPv6 address and never fall back to `X-Forwarded-For`, `Forwarded`, a cookie, or a JSON field.

```json
{"enrollment_code":"AQEBAQEBAQEBAQEBAQEBAQ","algorithm":"Ed25519","public_key":"INsaaxAGzWH6psMGL2Y-gJaRBGfO4on_-P_-e8Qiais","local_camera_id":"pc-webcam-01"}
```

Read the body through a bounded stream reader and abort as soon as byte 4097 arrives; decoded text must round-trip as strict UTF-8. Require a canonical 22-character enrollment-code base64url value decoding to 16 bytes, exact algorithm `Ed25519`, canonical 43-character public-key base64url decoding to 32 bytes, and exact camera ID `pc-webcam-01`; reject missing/extra keys, arrays, duplicate JSON keys, control characters, wrong casing, non-canonical padding, and trailing JSON. Parse duplicate keys with a small top-level key scanner before `JSON.parse`; add no validation dependency. Convert to internal camelCase only after validation, construct `EnrollmentProvisioningService` from injected env dependencies, and call exactly:

```ts
const result = await service.enroll({
  code: payload.enrollment_code,
  publicKey: payload.public_key,
  localCameraId: payload.local_camera_id,
  connectingIp,
});
```

On success return exactly:

```json
{"agent_id":"agent_01","camera_id":"camera_01","connector_token":"connector-secret"}
```

with HTTP `201` and `Cache-Control: private, no-store`. Do not return `tunnel_origin`, hostname, Access service-token ID/client ID/client secret, Cloudflare account/API token, home/owner ID, or R2 data. Ignore browser cookies entirely: the code and agent public key are the credential; never call `requireAuth`. Invalid shape/key/algorithm/camera is `400 invalid_request`; reused/expired/collision is `409 enrollment_rejected`; rate limit is `429 rate_limited`; retry-safe provisioning/cleanup failure is `503 enrollment_retryable`. Error bodies/logs contain none of the code, key, connector token, cookie, IP, Cloudflare body, or remote IDs.

- [ ] **Step 5: Run GREEN**

```powershell
npm.cmd --prefix dashboard test -- tests/enrollment.test.ts tests/agent-enroll.test.ts
```

Expected: all wire-validation, one-time success, crash cleanup, expiry/reuse/collision, per-create persistence/rollback, rate-limit, cookie-independence, redaction, tenant ownership, and revocation tests pass.

- [ ] **Step 6: Commit**

```powershell
git add dashboard/lib/petcare/enrollment.ts dashboard/lib/petcare/agent-enroll.ts dashboard/tests/enrollment.test.ts dashboard/tests/agent-enroll.test.ts
git commit -m "feat(sites): provision and revoke home tunnels"
```

### Task 5: Add tenant-scoped REST and private MJPEG proxying

**Files:**
- Create: `dashboard/lib/petcare/live-proxy.ts`
- Test: `dashboard/tests/live-proxy.test.ts`

**Interfaces:**
- Consumes: `AuthUser`, `TenantRepository.requireHome`, `PetCareRepository.requireActiveRoute`, Access client ID/secret.
- Produces: `proxyStatus(user, env, now)` and `proxyMjpeg(user, env, cameraId)`.

- [ ] **Step 1: Write failing proxy tests**

Assert owner A resolves only home A's D1 route; browser `GET /api/petcare/status` translates exactly to `https://home-a.agents.example.com/api/dashboard/summary`; browser `GET /api/petcare/cameras/camera-a/stream.mjpeg` first requires `camera-a` to equal the registered active camera and then translates exactly to `https://home-a.agents.example.com/api/video_feed`; both upstream requests carry only `CF-Access-Client-Id` and `CF-Access-Client-Secret`; status and MJPEG header establishment abort at 2,000 ms; after valid MJPEG headers the timeout is cleared so the body can stream indefinitely and downstream cancellation cancels upstream. Malformed status JSON, wrong status content type, wrong MJPEG content type, timeout, or fetch failure maps to `503` without updating `last_seen_at`. A never-enrolled home returns `200 { home: { id, state: "needs_enrollment" }, agent: null, camera: null, dashboard: null }`; a foreign camera selector returns `404 not_found` without any upstream call; a revoked/unavailable route or upstream `401`, `403`, `404`, `5xx`, timeout, or fetch failure maps to `503 { code: "agent_offline", agent_id, camera_id, last_seen_at }`; response bodies/headers never contain the tunnel origin or credentials.

- [ ] **Step 2: Run RED**

```powershell
npm.cmd --prefix dashboard test -- tests/live-proxy.test.ts
```

Expected: FAIL because the proxy functions do not exist.

- [ ] **Step 3: Implement only two allowlisted upstream routes**

Use `AbortSignal.timeout(2000)` for summary:

```ts
const upstream = await fetch(new URL("/api/dashboard/summary", route.tunnelOrigin), {
  method: "GET",
  headers: accessHeaders(config),
  signal: AbortSignal.timeout(2000),
});
```

On a successful status fetch, validate that the upstream body is JSON and return the dashboard-facing envelope exactly:

```ts
return Response.json({
  home: { id: home.id, state: "ready" },
  agent: { id: route.agentId, state: "online", last_seen_at: now.toISOString() },
  camera: { id: route.cameraId, state: "online", last_seen_at: now.toISOString() },
  dashboard: await upstream.json(),
}, { headers: { "Cache-Control": "private, no-store, no-transform" } });
```

Call `markAgentSeen(route.agentId, now.toISOString())` only after the upstream status response and JSON validation succeed. A failed status request preserves the previous `last_seen_at` used in the `agent_offline` body.

Translate the camera route without forwarding the browser selector:

```ts
if (cameraId !== route.cameraId) throw new PetCareError(404, "not_found");
const controller = new AbortController();
const timeout = setTimeout(() => controller.abort(), 2000);
const upstream = await fetch(new URL("/api/video_feed", route.tunnelOrigin), {
  method: "GET",
  headers: accessHeaders(config),
  signal: controller.signal,
});
clearTimeout(timeout); // only after response headers arrive; stream lifetime is not capped
```

For status, require `Content-Type: application/json` before parsing and validate the exact dashboard summary shape before `markAgentSeen`. For MJPEG, require an upstream `Content-Type` beginning with `multipart/x-mixed-replace`; return its `ReadableStream` directly and cancel it on downstream cancellation. Both successful responses replace cache headers with:

```ts
const PRIVATE_STREAM_HEADERS = {
  "Cache-Control": "private, no-store, no-transform",
  "Pragma": "no-cache",
  "X-Content-Type-Options": "nosniff",
};
```

Do not forward browser cookies, Authorization, Origin, Referer, or arbitrary headers. Do not accept a target path/query/origin from the browser.

- [ ] **Step 4: Run GREEN**

```powershell
npm.cmd --prefix dashboard test -- tests/live-proxy.test.ts
```

Expected: all tenant, exact URL, 2,000 ms timeout, Access header, MJPEG streaming/cancel, cache, and `agent_offline` tests pass.

- [ ] **Step 5: Commit**

```powershell
git add dashboard/lib/petcare/live-proxy.ts dashboard/tests/live-proxy.test.ts
git commit -m "feat(sites): proxy private home data and mjpeg"
```

### Task 6: Verify signed agent clip uploads and publish private R2 metadata

**Files:**
- Consume unchanged: `contracts/petcare-agent-wire-v1.json` (owned by the remote-integration plan's shared-contract prerequisite)
- Create: `dashboard/lib/petcare/clip-signature.ts`
- Create: `dashboard/lib/petcare/clip-upload.ts`
- Test: `dashboard/tests/clip-upload.test.ts`

**Interfaces:**
- Consumes: active agent/camera lookup, nonce insert, private `CLIPS`, clip publication batch.
- Produces: `parseSignedClipHeaders(request)`; `verifyClipSignature(headers, publicKey)`; `uploadSignedClip(request, env, now)`.

- [ ] **Step 1: Write failing cryptographic and upload tests**

Read `contracts/petcare-agent-wire-v1.json` in the TypeScript test and prove Web Crypto imports the fixture public key and verifies its signature over the fixture's exact canonical bytes. The home-agent Python test must read that same root file, not a copied or regenerated vector; the integration gate compares the Python-produced digest/canonical/signature and TypeScript verification against the one fixture. Its enrollment code is the canonical 22-character value `AQEBAQEBAQEBAQEBAQEBAQ`. Also generate Ed25519 keys in isolated Web Crypto tests. Cover valid upload; wrong registered key; timestamp at -300 s, +300 s, -301 s, and +301 s; duplicate nonce; malformed or case-folded base64url; wrong signature; claimed digest mismatch; actual body digest mismatch; revoked agent; camera not owned by agent; wrong content type; absent/invalid/mismatched `Content-Length`; `endedAt == startedAt`; zero bytes; 50 MiB + 1 byte; ineligible `no_meal_12h`; duplicate event tuple; 31st attempt for one agent in one minute; R2 failure; D1 publish failure deleting the object; failed cleanup queued in `object_deletion_jobs`; account cleanup racing publication; random opaque key; exact `201` response; no secrets in error/log output.

Use these exact headers:

```text
Content-Type: video/mp4
Content-Length: exact decimal body byte length
X-PetCare-Agent-Id: agent UUID
X-PetCare-Camera-Id: camera UUID
X-PetCare-Timestamp: Unix epoch seconds
X-PetCare-Nonce: 22-character base64url for 16 bytes
X-PetCare-Content-SHA256: 43-character base64url SHA-256
X-PetCare-Started-At: RFC3339 UTC
X-PetCare-Ended-At: RFC3339 UTC
X-PetCare-Events: eating:event-1,bed_sensor_mismatch:event-2
X-PetCare-Signature: base64url Ed25519 signature
```

- [ ] **Step 2: Run RED**

```powershell
npm.cmd --prefix dashboard test -- tests/clip-upload.test.ts
```

Expected: FAIL because signature and upload functions do not exist.

- [ ] **Step 3: Implement strict parsing and the exact canonical request**

Reject duplicate headers (including comma-coalesced duplicates), control characters, non-canonical base64url, unsorted/duplicate events, non-UTC timestamps, `endedAt <= startedAt`, durations over 120 seconds, and payloads outside `1..52428800` bytes. Base64url values are RFC 4648 URL-safe, unpadded, and case-preserving: never lowercase or uppercase a nonce, digest, key, or signature. Require exactly one decimal `Content-Length`; its value must equal the streamed byte count and the R2 object size.

The signed UTF-8 bytes are exactly:

```text
PETCARE-CLIP-V1
POST
/api/petcare/agent/clips
<agent-id>
<camera-id>
<unix-seconds>
<nonce>
<content-sha256>
<started-at>
<ended-at>
<comma-separated events sorted by event-type then event-id>
```

There is one final newline after the event line. Import the registered raw 32-byte Ed25519 public key and verify:

```ts
const key = await crypto.subtle.importKey("raw", publicKey, { name: "Ed25519" }, false, ["verify"]);
const valid = await crypto.subtle.verify({ name: "Ed25519" }, key, signature, canonicalBytes);
if (!valid) throw new PetCareError(401, "invalid_agent_signature");
```

Verify signature and timestamp before `consumeNonce`; apply the 30-per-minute agent rate limit, then consume the nonce before reading the body. The replay key is `(agent_id, nonce)` and remains spent after a later body/upload failure.

- [ ] **Step 4: Stream, hash, verify, and publish without a new dependency**

Use `createHash("sha256")` from `node:crypto` in a `TransformStream`: each chunk increments a byte counter, enforces 50 MiB, updates the hash, and passes the chunk to `CLIPS.put`. Write first to `clips/${crypto.randomUUID()}.mp4`; this object is not readable because no D1 metadata exists yet. After R2 completes, compare the actual base64url digest with the signed digest using `timingSafeEqual`. On mismatch delete the object and return `400 digest_mismatch`.

Set:

```ts
const createdAt = now.toISOString();
const expiresAt = new Date(now.getTime() + 7 * 24 * 60 * 60 * 1000).toISOString();
```

Publish clip and event rows only after actual digest verification through Task 2's active-home guarded insert, so a concurrent account deletion yields `410 account_deleted` and deletes/queues the unpublished R2 object. If D1 fails, delete the R2 object before returning `503 upload_retryable`; if that delete fails, insert `object_deletion_jobs` before returning. Success is HTTP `201`, `Cache-Control: private, no-store`, and exactly `{ "id": string, "createdAt": string, "expiresAt": string }` with no extra keys; never return `objectKey`.

- [ ] **Step 5: Run GREEN**

```powershell
npm.cmd --prefix dashboard test -- tests/clip-upload.test.ts
```

Expected: all signature, boundary, replay, digest, eligible-event, size, R2 rollback, D1 rollback, and secrecy tests pass.

- [ ] **Step 6: Commit**

```powershell
git add dashboard/lib/petcare/clip-signature.ts dashboard/lib/petcare/clip-upload.ts dashboard/tests/clip-upload.test.ts
git commit -m "feat(sites): verify and store signed event clips"
```

### Task 7: Add private tenant-owned clip list, read, and delete

**Files:**
- Create: `dashboard/lib/petcare/clips.ts`
- Test: `dashboard/tests/clips.test.ts`

**Interfaces:**
- Consumes: `AuthUser.sub`, `TenantRepository.requireHome`, Task 2 clip repository, private `CLIPS`.
- Produces: `listClips`, `readClip`, and `deleteClip`.

- [ ] **Step 1: Write failing two-user isolation tests**

Create home A and home B with one clip each. Prove A's list contains only A; `GET /api/petcare/clips/clip-a.mp4` returns only A's private media while unsuffixed media GET is not routed; `DELETE /api/petcare/clips/clip-a` uses the unsuffixed selector; A cannot read or delete B by guessed clip ID; B still reads after A's attempt; anonymous access is handled before these functions; exact-expiry reads return 404 without calling R2; successful read returns MP4 with `Cache-Control: private, no-store, no-transform` and no object key; delete removes D1 first then R2; R2 delete failure still denies a second read and leaves an orphan for reconciliation.

- [ ] **Step 2: Run RED**

```powershell
npm.cmd --prefix dashboard test -- tests/clips.test.ts
```

Expected: FAIL because clip handlers do not exist.

- [ ] **Step 3: Implement ownership-checked handlers**

Every handler must perform:

```ts
const user: AuthUser = await requireAuth(request, env);
const home = await new TenantRepository(getDb()).requireHome(user.sub);
```

List returns `{ clips: PetCareClip[] }` newest first using only non-expired metadata and event tuples. The browser DTO is exactly `{ id, camera_id, event_types, started_at, ended_at, expires_at }`, where `event_types` is the de-duplicated ordered subset of `eating`, `resting`, and `bed_sensor_mismatch`; event IDs remain server-side. Media URL construction remains the dashboard helper's canonical `/api/petcare/clips/:clipId.mp4` and is not stored in D1. Read calls `requireOwnedClip(home.id, clipId, now.toISOString())` before `CLIPS.get`; if the object is absent, delete stale metadata and return 404. Delete calls `deleteClipAndQueueObject(home.id, clipId, now.toISOString())` first, then `CLIPS.delete(objectKey)`; remove the deletion job on success and retain it on failure, returning 204 in both cases because logical access is already gone. Do not expose `homeId`, `objectKey`, `sha256`, or event IDs in browser JSON.

- [ ] **Step 4: Run GREEN**

```powershell
npm.cmd --prefix dashboard test -- tests/clips.test.ts
```

Expected: all two-user list/read/delete, exact-expiry, no-store, stale-object, and delete-order tests pass.

- [ ] **Step 5: Commit**

```powershell
git add dashboard/lib/petcare/clips.ts dashboard/tests/clips.test.ts
git commit -m "feat(sites): serve tenant owned private clips"
```

### Task 8: Delete one PetCare tenant with recent reauthentication and idempotent cleanup

**Files:**
- Create: `dashboard/lib/petcare/account-delete.ts`
- Test: `dashboard/tests/account-delete.test.ts`

**Interfaces:**
- Consumes exactly auth exports `requireSameOrigin(request): void`, `requireAuth(request, env): Promise<AuthUser>`, and `requireRecentPassword(request, env, user): Promise<void>`; Task 2 tenant-cleanup methods; private `CLIPS`; Task 3 Cloudflare reverse deletes.
- Produces `deletePetCareAccountData(request, env, now): Promise<Response>` for canonical `DELETE /api/petcare/account`.

- [ ] **Step 1: Write failing ordered security and state-machine tests**

Use owner A and owner B fixtures, injected call-order spies, fake D1/R2/Cloudflare, and sentinels for the password, cookie, token, object key, tunnel origin, and provider error. Prove all of these:

1. Missing/mismatched `Origin` returns `403 {"error":"csrf"}` before JWT, JSON/password, D1, R2, or Cloudflare work.
2. Invalid JWT returns the auth plan's exact `401 {"error":"unauthorized"}` before password parsing or mutation.
3. A valid session calls `requireRecentPassword(request, env, user)` before D1 mutation; its `401 reauthentication_failed`, `429 rate_limited`, and `503 auth_unavailable` remain generic.
4. The success call order is exactly CSRF, JWT, recent password, owner-scoped logical batch, then `202`. The password is passed only inside `requireRecentPassword`; it is never passed to `PetCareRepository`, `CloudflareClient`, R2, logs, response, cleanup jobs, or a local sign-out implementation.
5. The logical batch inserts one `tenant_cleanup` ledger, sets `homes.deleted_at`, revokes every old agent, disables every old camera, deletes/invalidate all unconsumed enrollment codes, clears activation state and marks the tunnel `revocation_pending`, inserts one `object_deletion_jobs` row per clip via `INSERT ... SELECT`, and deletes clip/event metadata. Immediately afterward old status/proxy/read/upload/enroll/`ensureHome` paths are denied, even if R2/Cloudflare deletion fails.
6. Owner B's home, agents, cameras, enrollment codes, clips, objects, and remote resource IDs are byte-for-byte unchanged.
7. The first and every pending retry return exactly HTTP `202`, `Cache-Control: private, no-store`, body `{"status":"cleanup_pending"}`, without duplicating deletion jobs or provider deletes. A request arriving after physical completion but before a fresh home exists returns idempotent `204`.
8. Cleanup completion cannot race a late upload publication: Task 2's active-home `INSERT ... SELECT` affects zero rows, the unpublished object is deleted/queued, and no old clip becomes readable.
9. After reconciliation proves every queued object and Tunnel/Access/DNS resource is gone, one D1 batch deletes the old route, agents, cameras, enrollment rows, home registry, and `tenant_cleanup` ledger. The old connector token, Ed25519 key, camera ID, tunnel hostname, and clip IDs remain unusable. Only then can the retained Supabase identity explicitly create a fresh home/enrollment with new opaque IDs.
10. Error bodies and captured logs contain none of the password, cookie, token, key, origin, object key, Cloudflare body, or remote IDs.

Also freeze the local sign-out handoff: after the dashboard receives the `202`, it must invoke the auth plan's existing same-origin `POST /auth/logout`, which performs `signOut({ scope: "local" })`, clears session cookies, and redirects to `/login`. The BFF neither deletes the Supabase identity nor reimplements Supabase sign-out; the remote-dashboard/integration plans own the UI call and end-to-end redirect assertion.

- [ ] **Step 2: Run RED**

```powershell
npm.cmd --prefix dashboard test -- tests/account-delete.test.ts
```

Expected: FAIL because `deletePetCareAccountData` and the cleanup repository operations do not exist.

- [ ] **Step 3: Implement the exact trust boundary and logical-delete batch**

The handler order is non-negotiable:

```ts
try {
  requireSameOrigin(request);
} catch {
  throw new PetCareError(403, "csrf");
}
const user = await requireAuth(request, env);
await requireRecentPassword(request, env, user);
const state = await repository.beginTenantCleanup(user.sub, now.toISOString());
```

`beginTenantCleanup` first returns the existing owner-scoped pending ledger when present. If neither a ledger nor any home exists, return `{ status: "absent" }` and the handler responds `204`. Otherwise execute one D1 batch scoped by `homes.owner_sub = ?`: insert the ledger; soft-delete the home; revoke/disable agent and camera state; invalidate enrollment rows; clear `activation_expires_at` and set the route `revocation_pending`; insert object-deletion jobs from that home's clips; then delete the clip rows (cascade events). Check every affected owner/home predicate and roll back the whole batch on mismatch. The handler schedules no provider call inline and returns:

```ts
return Response.json({ status: "cleanup_pending" }, {
  status: 202,
  headers: { "Cache-Control": "private, no-store" },
});
```

Do not call `TenantRepository.requireHome` before checking the cleanup ledger because pending retries must remain idempotent after the home is soft-deleted. Do not retain the current password after `requireRecentPassword` returns. Do not call a Supabase admin/user-delete API, and do not add a service-role key.

- [ ] **Step 4: Implement the owner-scoped physical-completion primitive**

Add repository selectors that return only pending owner/home rows and their opaque resource IDs, plus `completeTenantCleanup(ownerSub, homeId)`. The completion method refuses to run while any owner-scoped deletion job, clip metadata, or non-null remote resource ID remains; otherwise it performs one owner/home-scoped D1 batch deleting the old tunnel route, enrollment rows, cameras, agents, home registry, and the cleanup ledger last. If any predicate/delete fails, retain `cleanup_pending` plus only a fixed internal error code. The disappearing ledger is the completion marker; never persist a reusable old credential or provider body. Task 9 owns the retry loop that satisfies these preconditions.

- [ ] **Step 5: Run GREEN**

```powershell
npm.cmd --prefix dashboard test -- tests/account-delete.test.ts tests/petcare-repository.test.ts tests/clip-upload.test.ts
```

Expected: ordered trust-boundary, immediate denial, idempotent pending/absent responses, two-tenant isolation, cleanup retry/completion race, fresh-home-after-completion, old-credential denial, and redaction tests pass.

- [ ] **Step 6: Commit**

```powershell
git add dashboard/lib/petcare/account-delete.ts dashboard/lib/petcare/repository.ts dashboard/tests/account-delete.test.ts dashboard/tests/petcare-repository.test.ts dashboard/tests/clip-upload.test.ts
git commit -m "feat(sites): delete petcare tenant data safely"
```

### Task 9: Reconcile exact retention and failed remote cleanup

**Files:**
- Create: `dashboard/lib/petcare/reconcile.ts`
- Test: `dashboard/tests/reconcile.test.ts`

**Interfaces:**
- Consumes: Task 2 reconciliation selectors, `CLIPS.list/delete`, Cloudflare reverse-delete methods.
- Produces: `reconcilePetCare(env, now): Promise<ReconcileResult>`.

- [ ] **Step 1: Write failing retention/reconciliation tests**

Use a fixed `2026-07-27T00:00:00.000Z` clock and prove: `created + 7d - 1ms` remains readable; `created + 7d` is denied and reconciled; metadata expired before object is deleted; a queued manual-deletion object is retried immediately; an R2 `clips/` object with no D1 row and older than ten minutes is deleted; an upload-in-progress orphan younger than ten minutes is retained; a D1 row whose object is missing is deleted; unexpired objects/rows are retained; expired nonces and request-limit windows are deleted; stale `provisioning`/`activation_pending` rows become cleanup work without re-returning a connector token; `cleanup_pending` and `revocation_pending` remote resources retry in reverse order; account cleanup deletes R2/Cloudflare state before atomically removing the old registry/finite ledger; failed retries retain safe state with no provider body/secret.

Seed more than 200 lexically ordered R2 keys and make the first 100 ineligible. Prove run 1 persists the returned R2 cursor in D1, run 2 starts from that cursor and reaches later eligible orphans, end-of-list clears the cursor, an invalid cursor is cleared/restarted safely on the next run, and no key can be starved by always restarting at `clips/`.

- [ ] **Step 2: Run RED**

```powershell
npm.cmd --prefix dashboard test -- tests/reconcile.test.ts
```

Expected: FAIL because `reconcilePetCare` does not exist.

- [ ] **Step 3: Implement bounded hourly reconciliation**

Process at most 100 queued object deletions, 100 expired clip rows, 100 scanned `clips/` R2 objects, 500 expired nonces/request-limit windows, and 25 tunnel/account cleanup rows per run. Retry queued physical deletions first. D1 expired metadata is deleted and queued before its object. Load opaque cursor key `r2_clips_orphan_scan` from `reconcile_state`, pass it to `CLIPS.list({ prefix: "clips/", cursor, limit: remaining })`, and count every scanned key toward the 100-key budget. When a page is truncated, persist its returned cursor before ending the run; at end-of-list store null so the next run begins a new cycle. If R2 rejects a stale/invalid cursor, clear it and return one retryable failure rather than restarting within the same run. For each key, delete only when no D1 metadata/deletion job exists and the R2 `uploaded` timestamp is at least ten minutes old, which avoids racing an upload that has not published D1 metadata.

Move stale `provisioning` or `activation_pending` rows whose `activation_expires_at <= now` to `cleanup_pending`; never look up or return a connector token. Retry tunnel deletion policy → app → DNS → tunnel, clearing each persisted resource ID only after provider success; keep `cleanup_pending`/`revocation_pending` after failure. For a `tenant_cleanup` owner, satisfy Task 8's R2/remote preconditions and then call `completeTenantCleanup(ownerSub, homeId)`; the old registry and finite ledger disappear in one D1 batch. A failed object/provider/D1 step leaves the pending ledger and immediate access denial intact.

Return counters only:

```ts
export type ReconcileResult = {
  expiredClips: number;
  orphanObjects: number;
  staleMetadata: number;
  expiredNonces: number;
  expiredRateLimits: number;
  cleanedTunnels: number;
  cleanedTenants: number;
  retryableFailures: number;
};
```

Never return or log keys, origins, headers, IDs, or Cloudflare response bodies.

- [ ] **Step 4: Run GREEN**

```powershell
npm.cmd --prefix dashboard test -- tests/reconcile.test.ts
```

Expected: all exact seven-day boundary, durable cursor pagination/no-starvation, bounded cleanup, orphan/stale reconciliation, nonce, stale activation, tunnel/account retry/completion, and secrecy tests pass.

- [ ] **Step 5: Commit**

```powershell
git add dashboard/lib/petcare/reconcile.ts dashboard/tests/reconcile.test.ts
git commit -m "feat(sites): reconcile clip retention and tunnels"
```

### Task 10: Export the explicit `/api/petcare/**` router and preserve `/demo`

**Files:**
- Create: `dashboard/lib/petcare/router.ts`
- Test: `dashboard/tests/petcare-router.test.ts`

**Interfaces:**
- Consumes: all prior task handlers; the auth-owned enrollment path is delegated by returning `null`.
- Produces only `routePetCare(request, env, ctx): Promise<Response | null>` for the remote-integration plan to compose into the Worker.

- [ ] **Step 1: Write failing route/security regression tests**

Assert this exact table and reject all other methods/paths with 404/405 and `Allow` where applicable:

```text
POST   /api/petcare/enrollment                         delegate to auth-tenancy Vinext route; `201 { code, expiresAt }`
GET    /api/petcare/status                             authenticated owner; local `/api/dashboard/summary`
GET    /api/petcare/cameras/:cameraId/stream.mjpeg     authenticated owner; local `/api/video_feed`
GET    /api/petcare/clips                              authenticated owner
GET    /api/petcare/clips/:clipId.mp4                  authenticated owner; private MP4 body
DELETE /api/petcare/clips/:clipId                      authenticated owner + same-origin
DELETE /api/petcare/account                            same-origin + JWT + current-password reauth; `202 cleanup_pending` or idempotent `204`
POST   /api/petcare/agent/enroll                       one-time code, no browser session
POST   /api/petcare/agent/clips                        agent signature, no browser session
```

`POST /api/petcare/enrollment` is exclusively owned by the auth-tenancy plan's Vinext route and top-level `issueEnrollment(ownerSub: string): Promise<{ code: string; expiresAt: string }>` export. `routePetCare` returns `null` for that exact path/method so `handler.fetch` serves the frozen `201 { code, expiresAt }` response; do not reimplement or reshape it in the BFF. Tunnel revocation remains the server-side `EnrollmentProvisioningService.revoke(ownerSub, now)` integration hook used by account deletion/authorized operational cleanup; do not expose a conflicting browser revocation route.

Mutations with a browser session require `Origin === new URL(request.url).origin`; missing or mismatched Origin returns `403 csrf`. `DELETE /api/petcare/account` delegates only to Task 8, preserving its CSRF → JWT → reauth order. Agent endpoints rely on signed/code credentials and reject browser cookies as irrelevant. Prove `POST /api/petcare/enrollment` returns `null` from `routePetCare`; the remote-integration test proves Vinext receives it exactly once. Prove `/demo`, `/demo-camera.webp`, `/`, and `/_vinext/image` also return `null` without calling auth, D1, R2, or fetch. Prove a malicious camera/clip ID containing slash, percent-encoded slash, dot segment, NUL, or more than 64 characters returns 404. Prove error JSON and captured logs contain none of the supplied password/cookie/token/signature/nonce/digest/origin/object-key sentinels.

- [ ] **Step 2: Run RED**

```powershell
npm.cmd --prefix dashboard test -- tests/petcare-router.test.ts
```

Expected: FAIL because the router is absent.

- [ ] **Step 3: Implement the closed route table**

Start `routePetCare` with the network-free guard:

```ts
const url = new URL(request.url);
if (!url.pathname.startsWith("/api/petcare/")) return null;
if (url.pathname === "/api/petcare/enrollment" && request.method === "POST") return null;
```

Use exact string matches plus three non-overlapping regexes: camera stream `^/api/petcare/cameras/([A-Za-z0-9_-]{1,64})/stream\.mjpeg$`, clip media `^/api/petcare/clips/([A-Za-z0-9_-]{1,64})\.mp4$`, and clip delete `^/api/petcare/clips/([A-Za-z0-9_-]{1,64})$`. Match the media suffix only for `GET` and the unsuffixed selector only for `DELETE`; match account deletion only for the exact path/method. Every unmatched path/method returns 404 or 405 without proxying. Wrap handlers once with `try/catch` and `errorResponse`; log only `{ code, method, routeName, requestId }`, where `requestId = crypto.randomUUID()` is generated server-side.

Do not modify `dashboard/worker/index.ts`, `dashboard/.openai/hosting.json`, `app/demo/page.tsx`, or `components/dashboard.tsx`. The remote-integration plan exclusively imports `routePetCare`/`reconcilePetCare`, places routing after image optimization and before Vinext, adds the scheduled handler, declares bindings, and runs the composed build.

- [ ] **Step 4: Run focused and complete local validation**

```powershell
$nodeDir = (Resolve-Path ".runtime/managed/node/PFiles64/nodejs").Path
$env:Path = "$nodeDir;$env:Path"
npm.cmd --prefix dashboard test -- tests/petcare-router.test.ts tests/account-delete.test.ts
npm.cmd --prefix dashboard test
npm.cmd --prefix dashboard run lint
git diff --check
```

Expected: focused and full Vitest exit 0; ESLint exit 0; `git diff --check` prints nothing. The composed Vinext/Worker build is the remote-integration plan's exclusive gate.

- [ ] **Step 5: Commit**

```powershell
git add dashboard/lib/petcare/router.ts dashboard/tests/petcare-router.test.ts
git commit -m "feat(sites): route private petcare bff"
```

### Task 11: Configure and verify physical seven-day R2 lifecycle after approval

**Files:**
- No source-file changes.

**Interfaces:**
- Consumes: the integration plan's already composed/deployed Worker with hourly `reconcilePetCare`, the physical R2 bucket name in `$env:PETCARE_R2_BUCKET`, and approved Cloudflare credentials.
- Produces only Cloudflare lifecycle rule `petcare-clips-7d` for prefix `clips/` plus read-only evidence that the integration-owned hourly trigger exists.

- [ ] **Step 1: Stop for explicit approval and real resource values**

This step mutates external Cloudflare state. Obtain explicit approval plus the real Sites-managed R2 bucket name, deployed Worker name, scoped API token, account ID, zone ID/name, Access team name, and pre-created global service-token ID/client ID/client secret. Do not create a global service token in this plan.

- [ ] **Step 2: Add the exact lifecycle rule**

```powershell
$node = (Resolve-Path ".runtime/managed/node/PFiles64/nodejs/node.exe").Path
& $node dashboard/node_modules/wrangler/bin/wrangler.js r2 bucket lifecycle add $env:PETCARE_R2_BUCKET petcare-clips-7d clips/ --expire-days 7 --force
```

Expected: Wrangler reports lifecycle rule `petcare-clips-7d` with prefix `clips/` and expiration `7 days`.

- [ ] **Step 3: Verify, do not infer, the lifecycle and schedule**

```powershell
& $node dashboard/node_modules/wrangler/bin/wrangler.js r2 bucket lifecycle list $env:PETCARE_R2_BUCKET
```

Expected: exactly one PetCare clip rule named `petcare-clips-7d`, prefix `clips/`, expiration 7 days. Read back the integration-owned Worker's Cron Trigger and require `0 * * * *`; do not create or change it in this task. If it is absent, keep lifecycle deletion plus exact read denial active and report hourly reconciliation to the remote-integration plan as a production blocker; do not invent `wrangler.jsonc` beside Sites-owned hosting metadata.

- [ ] **Step 4: Run final two-tenant and secret evidence with fakes, then production smoke only when authorized**

Local evidence:

```powershell
$nodeDir = (Resolve-Path ".runtime/managed/node/PFiles64/nodejs").Path
$env:Path = "$nodeDir;$env:Path"
npm.cmd --prefix dashboard test
npm.cmd --prefix dashboard run lint
git status --short
```

Expected: all tests/lint pass; status shows only intended plan implementation files. The remote-integration plan owns the composed build and production smoke. After deployment approval its two-account evidence proves tenant isolation, account cleanup, fresh enrollment after completed cleanup, and credential secrecy.

No commit is created in this task because it changes external configuration only.

## Cross-Plan Execution DAG

1. The remote-integration plan first owns and commits the single root wire fixture `contracts/petcare-agent-wire-v1.json`; its enrollment code is `AQEBAQEBAQEBAQEBAQEBAQ`, and both Python and TypeScript tests consume this exact file.
2. Execute the auth-tenancy plan through `0000_petcare_tenancy.sql`, `requireAuth`, `requireSameOrigin`, `requireRecentPassword`, `TenantRepository`, enrollment issuance/consumption, and the finite-cleanup handoff.
3. Execute this plan's `0001_petcare_tunnels_clips.sql`, pure BFF modules, `routePetCare`, and `reconcilePetCare`. Tasks 1–5 are serial after auth; the home-agent/dashboard implementation may proceed in parallel only after the shared enrollment/upload/browser DTO contract tests are frozen. Task 6 requires the root fixture from step 1.
4. The remote-integration plan exclusively modifies `dashboard/.openai/hosting.json` and `dashboard/worker/index.ts`, composes `routePetCare` before Vinext, attaches `reconcilePetCare` to `0 * * * *`, declares DB/R2/secrets, and runs the final build and fake integration gates.
5. Deployment, real Tunnel/Access/DNS mutation, Cron attachment, and R2 lifecycle configuration remain approval-gated. Task 11 only adds the approved lifecycle rule after integration deployment and verifies the already-owned schedule.

## Official Cloudflare Contracts Used by the Plan

- Cloudflare Tunnel create/delete/config/token: `https://developers.cloudflare.com/api/resources/zero_trust/subresources/tunnels/subresources/cloudflared/`
- DNS CNAME create/delete: `https://developers.cloudflare.com/api/resources/dns/subresources/records/`
- Access application create/delete: `https://developers.cloudflare.com/api/resources/zero_trust/subresources/access/subresources/applications/`
- Access Service Auth policy: `https://developers.cloudflare.com/cloudflare-one/access-controls/policies/common-policies/`
- Access service-token request headers: `https://developers.cloudflare.com/cloudflare-one/access-controls/service-credentials/service-tokens/`
- R2 lifecycle behavior: `https://developers.cloudflare.com/r2/buckets/object-lifecycles/`

## Self-Review Checklist

- [ ] Every browser live/clip route calls `requireAuth` and `TenantRepository.requireHome(user.sub)`; account deletion checks the owner cleanup ledger before soft-deleted home state so retries remain idempotent.
- [ ] No task redefines `AuthUser`, `requireAuth`, `TenantRepository`, `HomeRecord`, or the auth plan's tenancy tables.
- [ ] Enrollment returns the connector token once; a consumed-code retry is `409`, and no storage/log assertion can find the token.
- [ ] Provision rollback and revocation delete Access policy/app, DNS, and tunnel in reverse order and retain retry state on failure.
- [ ] Live REST and MJPEG header-establishment timeout is exactly 2,000 ms; MJPEG then streams with private/no-store/no-transform.
- [ ] Agent upload verifies active registration, Ed25519 signature, ±300 s timestamp, unique nonce, signed digest, and actual digest.
- [ ] Only `eating`, `resting`, and `bed_sensor_mismatch` event tuples are accepted; `no_meal_12h` is rejected.
- [ ] Clip read/list/delete joins ownership through verified `sub → home`; two-user tests cover every selector.
- [ ] `expires_at` is exactly `created_at + 604800000 ms`; reads deny at equality; lifecycle and durable-cursor reconciliation are both covered.
- [ ] `/demo` and its same-origin assets bypass the PetCare router and remain network-free.
- [ ] Account deletion enforces CSRF → JWT → current-password reauth, immediate logical denial, a finite cleanup ledger, old-credential invalidation, and fresh enrollment only after complete physical cleanup.
- [ ] Secret/redaction tests cover passwords, Cloudflare API token, Access client secret, connector token, cookies, signatures, nonces, digests, tunnel origins, and R2 keys.
- [ ] No external resource mutation, lifecycle command, scheduled trigger, deployment, commit, or push occurs without its required approval.
