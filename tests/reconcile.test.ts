// @vitest-environment node

import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { Miniflare } from "miniflare";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("cloudflare:workers", () => ({ env: {} }));
vi.setConfig({ hookTimeout: 30_000, testTimeout: 30_000 });

import type { PetCareEnv } from "../lib/petcare/env";
import { CloudflareClient } from "../lib/petcare/cloudflare";
import { provisioningResourceNames } from "../lib/petcare/enrollment";
import { reconcilePetCare } from "../lib/petcare/reconcile";
import { PetCareRepository } from "../lib/petcare/repository";
import { miniflarePort } from "./helpers/miniflare";

const NOW = new Date("2026-07-27T00:00:00.000Z");
const NOW_ISO = NOW.toISOString();
const FUTURE = "2026-07-27T00:00:00.001Z";

let mf: Miniflare;
let db: D1Database;
let clips: R2Bucket;

function env(bucket: R2Bucket = clips): PetCareEnv {
  return {
    DB: db,
    CLIPS: bucket,
    SUPABASE_URL: "https://example.supabase.co",
    SUPABASE_PUBLISHABLE_KEY: "publishable",
    CF_ACCOUNT_ID: "account",
    CF_ZONE_ID: "zone",
    CF_ZONE_NAME: "agents.example.com",
    CF_ACCESS_TEAM_NAME: "team",
    CF_TUNNEL_API_TOKEN: "api-token-sentinel",
    CF_ACCESS_SERVICE_TOKEN_ID: "service-token",
    CF_ACCESS_CLIENT_ID: "access-client",
    CF_ACCESS_CLIENT_SECRET: "access-secret-sentinel",
  };
}

async function applyMigrations() {
  for (const migration of [
    "0000_petcare_tenancy.sql",
    "0001_petcare_tunnels_clips.sql",
  ]) {
    const sql = readFileSync(
      resolve(import.meta.dirname, `../drizzle/${migration}`),
      "utf8",
    );
    await db.batch(
      sql
        .split("--> statement-breakpoint")
        .map((statement) => statement.trim())
        .filter(Boolean)
        .map((statement) => db.prepare(statement)),
    );
  }
}

async function seedHome(
  suffix: string,
  options: { deleted?: boolean } = {},
) {
  const homeId = `home-${suffix}`;
  const agentId = `agent-${suffix}`;
  const cameraId = `camera-${suffix}`;
  await db.batch([
    db
      .prepare(
        "INSERT INTO homes (id, owner_sub, created_at, deleted_at) VALUES (?, ?, ?, ?)",
      )
      .bind(
        homeId,
        `owner-${suffix}`,
        "2026-07-20T00:00:00.000Z",
        options.deleted ? NOW_ISO : null,
      ),
    db
      .prepare(
        "INSERT INTO agents (id, home_id, public_key, tunnel_origin, revoked_at) VALUES (?, ?, ?, ?, ?)",
      )
      .bind(
        agentId,
        homeId,
        `public-${suffix}`,
        `https://${suffix}.invalid`,
        options.deleted ? NOW_ISO : null,
      ),
    db
      .prepare(
        "INSERT INTO cameras (id, home_id, agent_id, local_camera_id, created_at, disabled_at) VALUES (?, ?, ?, ?, ?, ?)",
      )
      .bind(
        cameraId,
        homeId,
        agentId,
        "pc-webcam-01",
        "2026-07-20T00:00:00.000Z",
        options.deleted ? NOW_ISO : null,
      ),
  ]);
  return { homeId, agentId, cameraId, ownerSub: `owner-${suffix}` };
}

async function seedHomeOnly(suffix: string) {
  const homeId = `home-${suffix}`;
  const ownerSub = `owner-${suffix}`;
  await db
    .prepare(
      "INSERT INTO homes (id, owner_sub, created_at) VALUES (?, ?, ?)",
    )
    .bind(homeId, ownerSub, "2026-07-20T00:00:00.000Z")
    .run();
  return { homeId, ownerSub };
}

async function seedClip(input: {
  id: string;
  homeId: string;
  cameraId: string;
  objectKey?: string;
  expiresAt?: string;
}) {
  const objectKey = input.objectKey ?? `clips/${input.id}.mp4`;
  await db
    .prepare(
      `INSERT INTO clips
       (id, home_id, camera_id, object_key, sha256, size_bytes, started_at, ended_at, expires_at, created_at)
       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)`,
    )
    .bind(
      input.id,
      input.homeId,
      input.cameraId,
      objectKey,
      "digest",
      3,
      "2026-07-19T23:59:30.000Z",
      "2026-07-20T00:00:00.000Z",
      input.expiresAt ?? "2026-07-28T00:00:00.000Z",
      "2026-07-20T00:00:00.000Z",
    )
    .run();
  return objectKey;
}

async function count(table: string) {
  const row = await db
    .prepare(`SELECT COUNT(*) AS count FROM ${table}`)
    .first<{ count: number }>();
  return row?.count ?? 0;
}

async function tenantSnapshot(homeId: string) {
  return Promise.all(
    [
      ["homes", "id"],
      ["agents", "home_id"],
      ["cameras", "home_id"],
      ["tunnel_routes", "home_id"],
      ["clips", "home_id"],
    ].map(([table, column]) =>
      db
        .prepare(`SELECT * FROM ${table} WHERE ${column} = ?`)
        .bind(homeId)
        .all()
        .then((result) => result.results),
    ),
  );
}

beforeEach(async () => {
  vi.stubGlobal("fetch", async () =>
    Response.json({ success: true, result: [] }),
  );
  mf = new Miniflare({
    modules: true,
    port: miniflarePort(2),
    script: "export default { fetch() { return new Response('ok') } }",
    d1Databases: ["DB"],
    r2Buckets: ["CLIPS"],
  });
  db = await mf.getD1Database("DB");
  clips = await mf.getR2Bucket("CLIPS");
  await applyMigrations();
});

afterEach(async () => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
  await mf.dispose();
});

describe("reconcilePetCare", () => {
  it("denies exactly at seven days before physical deletion and repairs stale state", async () => {
    const home = await seedHome("retention");
    const expiredKey = await seedClip({
      id: "expired",
      ...home,
      expiresAt: NOW_ISO,
    });
    const boundaryKey = await seedClip({
      id: "boundary-minus-1ms",
      ...home,
      expiresAt: FUTURE,
    });
    const staleKey = await seedClip({ id: "stale", ...home });
    const liveKey = await seedClip({ id: "live", ...home });
    const queuedKey = "clips/queued.mp4";
    await Promise.all([
      clips.put(expiredKey, "mp4"),
      clips.put(boundaryKey, "mp4"),
      clips.put(liveKey, "mp4"),
      clips.put(queuedKey, "mp4"),
    ]);
    await db.batch([
      db
        .prepare(
          "INSERT INTO object_deletion_jobs (object_key, home_id, requested_at) VALUES (?, ?, ?)",
        )
        .bind(queuedKey, home.homeId, NOW_ISO),
      db
        .prepare(
          "INSERT INTO upload_nonces (agent_id, nonce, used_at, expires_at) VALUES (?, ?, ?, ?), (?, ?, ?, ?)",
        )
        .bind(
          home.agentId,
          "expired-nonce",
          NOW_ISO,
          NOW_ISO,
          home.agentId,
          "live-nonce",
          NOW_ISO,
          FUTURE,
        ),
      db
        .prepare(
          "INSERT INTO request_limits (subject, route, window_start, count, expires_at) VALUES (?, ?, ?, ?, ?), (?, ?, ?, ?, ?)",
        )
        .bind(
          "expired",
          "upload",
          1,
          1,
          NOW_ISO,
          "live",
          "upload",
          2,
          1,
          FUTURE,
        ),
    ]);
    const result = await reconcilePetCare(env(), NOW);

    expect(result).toEqual({
      expiredClips: 1,
      orphanObjects: 0,
      staleMetadata: 1,
      expiredNonces: 1,
      expiredRateLimits: 1,
      cleanedTunnels: 0,
      cleanedTenants: 0,
      retryableFailures: 0,
    });
    expect(await clips.head(queuedKey)).toBeNull();
    expect(await clips.head(expiredKey)).not.toBeNull();
    expect(await clips.head(boundaryKey)).not.toBeNull();
    expect(await db.prepare("SELECT id FROM clips WHERE id = 'expired'").first()).toBeNull();
    expect(await db.prepare("SELECT id FROM clips WHERE id = 'stale'").first()).toBeNull();
    expect(await db.prepare("SELECT id FROM clips WHERE id = 'boundary-minus-1ms'").first()).not.toBeNull();
    expect(await db.prepare("SELECT id FROM clips WHERE id = 'live'").first()).not.toBeNull();
    expect(await db.prepare("SELECT object_key FROM object_deletion_jobs WHERE object_key = ?").bind(expiredKey).first()).not.toBeNull();
    expect(await count("upload_nonces")).toBe(1);
    expect(await count("request_limits")).toBe(1);
    expect(staleKey).toBe("clips/stale.mp4");
  });

  it("persists an R2 cursor so eligible orphans cannot be starved", async () => {
    const home = await seedHome("cursor");
    for (let index = 0; index < 101; index += 1) {
      const key = `clips/${String(index).padStart(3, "0")}.mp4`;
      await clips.put(key, "mp4");
      if (index < 100) {
        await seedClip({
          id: `kept-${index}`,
          ...home,
          objectKey: key,
        });
      }
    }
    await seedClip({
      id: "zz-stale-after-first-page",
      ...home,
      objectKey: "clips/not-in-r2.mp4",
    });
    const reconciliationBucket = {
      list: clips.list.bind(clips),
      delete: clips.delete.bind(clips),
      head: async (key: string) =>
        key === "clips/not-in-r2.mp4" ? null : ({ key }) as R2Object,
    } as R2Bucket;

    const first = await reconcilePetCare(env(reconciliationBucket), NOW);
    const firstCursor = await db
      .prepare(
        "SELECT cursor FROM reconcile_state WHERE name = 'r2_clips_orphan_scan'",
      )
      .first<{ cursor: string | null }>();
    expect(first.orphanObjects).toBe(0);
    expect(first.staleMetadata).toBe(0);
    expect(firstCursor?.cursor).toEqual(expect.any(String));
    expect(
      await db
        .prepare(
          "SELECT cursor FROM reconcile_state WHERE name = 'd1_clips_stale_scan'",
        )
        .first(),
    ).toEqual({ cursor: expect.any(String) });
    expect(await clips.head("clips/100.mp4")).not.toBeNull();

    const second = await reconcilePetCare(env(reconciliationBucket), NOW);
    expect(second.orphanObjects).toBe(1);
    expect(second.staleMetadata).toBe(1);
    expect(
      await db
        .prepare("SELECT id FROM clips WHERE id = 'zz-stale-after-first-page'")
        .first(),
    ).toBeNull();
    expect(await clips.head("clips/100.mp4")).toBeNull();
    expect(
      await db
        .prepare(
          "SELECT cursor FROM reconcile_state WHERE name = 'r2_clips_orphan_scan'",
        )
        .first(),
    ).toEqual({ cursor: null });
  });

  it("clears an invalid R2 cursor without restarting the scan in the same run", async () => {
    await clips.put("clips/orphan.mp4", "mp4");
    await db
      .prepare(
        "INSERT INTO reconcile_state (name, cursor, updated_at) VALUES (?, ?, ?)",
      )
      .bind("r2_clips_orphan_scan", "invalid-cursor", NOW_ISO)
      .run();
    const list = vi.fn(async (options?: R2ListOptions) => {
      if (options?.cursor === "invalid-cursor") throw new Error("cursor-sentinel");
      return clips.list(options);
    });
    const wrapped = new Proxy(clips, {
      get(target, property, receiver) {
        if (property === "list") return list;
        const value = Reflect.get(target, property, receiver);
        return typeof value === "function" ? value.bind(target) : value;
      },
    });

    const failed = await reconcilePetCare(env(wrapped), NOW);

    expect(failed.retryableFailures).toBe(1);
    expect(list).toHaveBeenCalledTimes(1);
    expect(await clips.head("clips/orphan.mp4")).not.toBeNull();
    expect(
      await db
        .prepare(
          "SELECT cursor FROM reconcile_state WHERE name = 'r2_clips_orphan_scan'",
        )
        .first(),
    ).toEqual({ cursor: null });

    const retried = await reconcilePetCare(env(), NOW);
    expect(retried.orphanObjects).toBe(1);
    expect(await clips.head("clips/orphan.mp4")).toBeNull();
  });

  it("retains a nine-minute orphan and deletes it at the ten-minute boundary", async () => {
    const key = "clips/upload-in-progress.mp4";
    await clips.put(key, "mp4");
    const uploaded = await clips.head(key);
    expect(uploaded).not.toBeNull();

    const young = await reconcilePetCare(
      env(),
      new Date(uploaded!.uploaded.getTime() + 10 * 60 * 1000 - 1),
    );
    expect(young.orphanObjects).toBe(0);
    expect(await clips.head(key)).not.toBeNull();

    const oldEnough = await reconcilePetCare(
      env(),
      new Date(uploaded!.uploaded.getTime() + 10 * 60 * 1000),
    );
    expect(oldEnough.orphanObjects).toBe(1);
    expect(await clips.head(key)).toBeNull();
  });

  it("moves stale provisioning and activation rows into bounded cleanup", async () => {
    const activation = await seedHome("stale-activation");
    const provisioning = await seedHome("stale-provisioning");
    await db.batch([
      db
        .prepare(
          `INSERT INTO tunnel_routes
           (home_id, agent_id, activation_expires_at, status, created_at, updated_at)
           VALUES (?, ?, ?, 'activation_pending', ?, ?)`,
        )
        .bind(
          activation.homeId,
          activation.agentId,
          NOW_ISO,
          "2026-07-26T23:40:00.000Z",
          "2026-07-26T23:59:00.000Z",
        ),
      db
        .prepare(
          `INSERT INTO tunnel_routes
           (home_id, agent_id, status, created_at, updated_at)
           VALUES (?, ?, 'provisioning', ?, ?)`,
        )
        .bind(
          provisioning.homeId,
          provisioning.agentId,
          "2026-07-26T23:40:00.000Z",
          "2026-07-26T23:50:00.000Z",
        ),
    ]);

    const result = await reconcilePetCare(env(), NOW);

    expect(result.cleanedTunnels).toBe(2);
    expect(result.retryableFailures).toBe(0);
    expect(
      await db
        .prepare("SELECT status, activation_expires_at FROM tunnel_routes ORDER BY home_id")
        .all(),
    ).toMatchObject({
      results: [
        { status: "revoked", activation_expires_at: null },
        { status: "revoked", activation_expires_at: null },
      ],
    });
  });

  it("discovers and later removes a deterministic provider leak after double failure", async () => {
    const home = await seedHomeOnly("provider-leak");
    const leakedAgentId = "agent_11111111-1111-4111-8111-111111111111";
    const codeHash = "code-hash-provider-leak";
    await db.batch([
      db
        .prepare(
          "INSERT INTO enrollment_tokens (id, home_id, token_hash, expires_at) VALUES (?, ?, ?, ?)"
        )
        .bind("token-provider-leak", home.homeId, codeHash, "2026-07-28T00:00:00.000Z"),
      db
        .prepare(
          `INSERT INTO tunnel_routes
           (home_id, agent_id, status, created_at, updated_at)
           VALUES (?, ?, 'provisioning', ?, ?)`,
        )
        .bind(
          home.homeId,
          leakedAgentId,
          "2026-07-26T23:40:00.000Z",
          "2026-07-26T23:40:00.000Z",
        ),
    ]);
    const names = provisioningResourceNames(
      home.homeId,
      leakedAgentId,
      "agents.example.com",
    );
    const findTunnel = vi
      .spyOn(CloudflareClient.prototype, "findTunnelByName")
      .mockResolvedValue({ id: "discovered-tunnel" });
    const findDns = vi
      .spyOn(CloudflareClient.prototype, "findDnsRecordByHostname")
      .mockResolvedValue({ id: "discovered-dns" });
    const findApp = vi
      .spyOn(CloudflareClient.prototype, "findAccessAppByDomain")
      .mockResolvedValue({ id: "discovered-app", aud: "discovered-aud" });
    const findPolicy = vi
      .spyOn(CloudflareClient.prototype, "findAccessPolicyByName")
      .mockResolvedValue({ id: "discovered-policy" });
    const deletePolicy = vi
      .spyOn(CloudflareClient.prototype, "deleteAccessPolicy")
      .mockResolvedValue(undefined)
      .mockRejectedValueOnce(new Error("provider-delete-failed"));
    const deleteApp = vi
      .spyOn(CloudflareClient.prototype, "deleteAccessApp")
      .mockResolvedValue();
    const deleteDns = vi
      .spyOn(CloudflareClient.prototype, "deleteDnsRecord")
      .mockResolvedValue();
    const deleteTunnel = vi
      .spyOn(CloudflareClient.prototype, "deleteTunnel")
      .mockResolvedValue();
    const log = vi.spyOn(console, "error").mockImplementation(() => undefined);

    const failed = await reconcilePetCare(env(), NOW);
    expect(failed.retryableFailures).toBe(1);
    expect(await db.prepare("SELECT status FROM tunnel_routes").first()).toEqual({
      status: "cleanup_pending",
    });
    expect(deletePolicy).toHaveBeenCalledWith(
      "discovered-app",
      "discovered-policy",
    );
    expect(deleteApp).not.toHaveBeenCalled();
    expect(deleteDns).not.toHaveBeenCalled();
    expect(deleteTunnel).not.toHaveBeenCalled();

    const retried = await reconcilePetCare(
      env(),
      new Date("2026-07-27T01:00:00.000Z"),
    );
    expect(retried.cleanedTunnels).toBe(1);
    expect(retried.retryableFailures).toBe(0);
    expect(findTunnel).toHaveBeenCalledWith(names.tunnelName);
    expect(findDns).toHaveBeenCalledWith(
      names.hostname,
      "discovered-tunnel",
    );
    expect(findApp).toHaveBeenCalledWith(names.hostname, names.accessName);
    expect(findPolicy).toHaveBeenCalledWith("discovered-app");
    expect(deletePolicy).toHaveBeenCalledTimes(2);
    expect(deleteApp).toHaveBeenCalledWith("discovered-app");
    expect(deleteDns).toHaveBeenCalledWith("discovered-dns");
    expect(deleteTunnel).toHaveBeenCalledWith("discovered-tunnel");
    expect(await db.prepare("SELECT status FROM tunnel_routes").first()).toBeNull();
    await expect(
      new PetCareRepository(db).reserveTunnel(
        home.homeId,
        "agent_22222222-2222-4222-8222-222222222222",
        codeHash,
        "2026-07-27T01:00:01.000Z",
      ),
    ).resolves.toMatchObject({ status: "provisioning" });
    const output = JSON.stringify({ failed, retried });
    expect(output).not.toContain(names.hostname);
    expect(output).not.toContain(names.tunnelName);
    expect(output).not.toMatch(/discovered|provider/);
    expect(log).not.toHaveBeenCalled();
  });

  it("deletes remote resources in reverse order before completing a tenant", async () => {
    const home = await seedHome("cleanup", { deleted: true });
    const other = await seedHome("other-owner");
    const otherKey = await seedClip({ id: "other-clip", ...other });
    await clips.put(otherKey, "other-mp4");
    const queuedKey = "clips/tenant-cleanup.mp4";
    await clips.put(queuedKey, "mp4");
    await db.batch([
      db
        .prepare(
          `INSERT INTO tunnel_routes
           (home_id, agent_id, tunnel_id, tunnel_origin, access_app_id, access_policy_id, access_aud, dns_record_id,
            activation_expires_at, status, created_at, updated_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)`,
        )
        .bind(
          home.homeId,
          home.agentId,
          "tunnel-sentinel",
          "https://cleanup.invalid",
          "app-sentinel",
          "policy-sentinel",
          "aud-sentinel",
          "dns-sentinel",
          null,
          "revocation_pending",
          NOW_ISO,
          NOW_ISO,
        ),
      db
        .prepare(
          "INSERT INTO tenant_cleanup (owner_sub, home_id, status, started_at, updated_at) VALUES (?, ?, 'cleanup_pending', ?, ?)",
        )
        .bind(home.ownerSub, home.homeId, NOW_ISO, NOW_ISO),
      db
        .prepare(
          "INSERT INTO object_deletion_jobs (object_key, home_id, requested_at) VALUES (?, ?, ?)",
        )
        .bind(queuedKey, home.homeId, NOW_ISO),
      db
        .prepare(
          `INSERT INTO tunnel_routes
           (home_id, agent_id, tunnel_id, tunnel_origin, access_app_id, access_policy_id, access_aud, dns_record_id,
            status, created_at, updated_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'active', ?, ?)`,
        )
        .bind(
          other.homeId,
          other.agentId,
          "other-tunnel",
          "https://other.invalid",
          "other-app",
          "other-policy",
          "other-aud",
          "other-dns",
          NOW_ISO,
          NOW_ISO,
        ),
    ]);
    const otherBefore = await tenantSnapshot(other.homeId);
    const calls: string[] = [];
    const bucket = new Proxy(clips, {
      get(target, property, receiver) {
        if (property === "delete") {
          return async (keys: string | string[]) => {
            calls.push(`R2 ${String(keys)}`);
            return target.delete(keys);
          };
        }
        const value = Reflect.get(target, property, receiver);
        return typeof value === "function" ? value.bind(target) : value;
      },
    });
    vi.stubGlobal("fetch", async (input: RequestInfo | URL, init?: RequestInit) => {
      calls.push(`${init?.method} ${new URL(String(input)).pathname}`);
      return Response.json({ success: true, result: {} });
    });

    const result = await reconcilePetCare(env(bucket), NOW);

    expect(calls).toEqual([
      `R2 ${queuedKey}`,
      "DELETE /client/v4/accounts/account/access/apps/app-sentinel/policies/policy-sentinel",
      "DELETE /client/v4/accounts/account/access/apps/app-sentinel",
      "DELETE /client/v4/zones/zone/dns_records/dns-sentinel",
      "DELETE /client/v4/accounts/account/cfd_tunnel/tunnel-sentinel",
    ]);
    expect(result.cleanedTunnels).toBe(1);
    expect(result.cleanedTenants).toBe(1);
    expect(await count("tenant_cleanup")).toBe(0);
    expect(await count("tunnel_routes")).toBe(1);
    expect(await count("homes")).toBe(1);
    expect(
      await db
        .prepare(
          "SELECT home_id, tunnel_id, access_app_id, access_policy_id, dns_record_id, status FROM tunnel_routes",
        )
        .first(),
    ).toEqual({
      home_id: other.homeId,
      tunnel_id: "other-tunnel",
      access_app_id: "other-app",
      access_policy_id: "other-policy",
      dns_record_id: "other-dns",
      status: "active",
    });
    expect(
      await db.prepare("SELECT id FROM clips WHERE id = 'other-clip'").first(),
    ).toEqual({ id: "other-clip" });
    expect(await clips.get(otherKey).then((object) => object?.text())).toBe(
      "other-mp4",
    );
    expect(await tenantSnapshot(other.homeId)).toEqual(otherBefore);
    expect(JSON.stringify(result)).not.toMatch(
      /sentinel|api-token|access-secret|policy|tunnel|dns|app/,
    );
  });

  it("keeps pending state when a provider retry fails without exposing details", async () => {
    const home = await seedHome("failure", { deleted: true });
    await db.batch([
      db
        .prepare(
          `INSERT INTO tunnel_routes
           (home_id, agent_id, tunnel_id, tunnel_origin, access_app_id, access_policy_id, dns_record_id,
            status, created_at, updated_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, 'revocation_pending', ?, ?)`,
        )
        .bind(
          home.homeId,
          home.agentId,
          "tunnel-secret",
          "https://failure.invalid",
          "app-secret",
          "policy-secret",
          "dns-secret",
          NOW_ISO,
          NOW_ISO,
        ),
      db
        .prepare(
          "INSERT INTO tenant_cleanup (owner_sub, home_id, status, started_at, updated_at) VALUES (?, ?, 'cleanup_pending', ?, ?)",
        )
        .bind(home.ownerSub, home.homeId, NOW_ISO, NOW_ISO),
    ]);
    vi.stubGlobal("fetch", async () => {
      throw new Error("provider-body-secret");
    });
    const log = vi.spyOn(console, "error").mockImplementation(() => undefined);

    const result = await reconcilePetCare(env(), NOW);

    expect(result.retryableFailures).toBe(1);
    expect(result.cleanedTunnels).toBe(0);
    expect(result.cleanedTenants).toBe(0);
    expect(await db.prepare("SELECT status, access_policy_id FROM tunnel_routes").first()).toEqual({
      status: "revocation_pending",
      access_policy_id: "policy-secret",
    });
    expect(await count("tenant_cleanup")).toBe(1);
    expect(JSON.stringify(result)).not.toMatch(/secret|provider|policy/);
    expect(log).not.toHaveBeenCalled();
  });

  it("uses the supplied reconciliation clock for claim and renewal", async () => {
    const home = await seedHome("controlled-clock", { deleted: true });
    await db
      .prepare(
        `INSERT INTO tunnel_routes
         (home_id, agent_id, status, tunnel_id, dns_record_id, access_app_id,
          access_policy_id, created_at, updated_at)
         VALUES (?, ?, 'revocation_pending', ?, ?, ?, ?, ?, ?)`,
      )
      .bind(
        home.homeId,
        home.agentId,
        "clock-tunnel",
        "clock-dns",
        "clock-app",
        "clock-policy",
        NOW_ISO,
        NOW_ISO,
      )
      .run();
    const deletePolicy = vi
      .spyOn(CloudflareClient.prototype, "deleteAccessPolicy")
      .mockResolvedValue();
    const deleteApp = vi
      .spyOn(CloudflareClient.prototype, "deleteAccessApp")
      .mockResolvedValue();
    const deleteDns = vi
      .spyOn(CloudflareClient.prototype, "deleteDnsRecord")
      .mockResolvedValue();
    const deleteTunnel = vi
      .spyOn(CloudflareClient.prototype, "deleteTunnel")
      .mockResolvedValue();

    const result = await reconcilePetCare(
      env(),
      new Date("2020-01-01T00:00:00.000Z"),
    );

    expect(result.cleanedTunnels).toBe(1);
    expect(deletePolicy).toHaveBeenCalledOnce();
    expect(deleteApp).toHaveBeenCalledOnce();
    expect(deleteDns).toHaveBeenCalledOnce();
    expect(deleteTunnel).toHaveBeenCalledOnce();
    expect(await db.prepare("SELECT status FROM tunnel_routes").first()).toEqual({
      status: "revoked",
    });
  });

  it("fences concurrent tenant cleanup and later retries a failed discovered delete", async () => {
    const home = await seedHome("concurrent-cleanup", { deleted: true });
    await db.batch([
      db
        .prepare(
          `INSERT INTO tunnel_routes
           (home_id, agent_id, status, created_at, updated_at)
           VALUES (?, ?, 'cleanup_pending', ?, ?)`,
        )
        .bind(
          home.homeId,
          home.agentId,
          NOW_ISO,
          NOW_ISO,
        ),
      db
        .prepare(
          "INSERT INTO tenant_cleanup (owner_sub, home_id, status, started_at, updated_at) VALUES (?, ?, 'cleanup_pending', ?, ?)",
        )
        .bind(home.ownerSub, home.homeId, NOW_ISO, NOW_ISO),
    ]);
    let startDelete!: () => void;
    const started = new Promise<void>((resolve) => {
      startDelete = resolve;
    });
    let releaseDelete!: () => void;
    const released = new Promise<void>((resolve) => {
      releaseDelete = resolve;
    });
    vi.spyOn(CloudflareClient.prototype, "findTunnelByName").mockResolvedValue({
      id: "once-tunnel",
    });
    vi.spyOn(
      CloudflareClient.prototype,
      "findDnsRecordByHostname",
    ).mockResolvedValue({ id: "once-dns" });
    vi.spyOn(
      CloudflareClient.prototype,
      "findAccessAppByDomain",
    ).mockResolvedValue({ id: "once-app", aud: "once-aud" });
    vi.spyOn(
      CloudflareClient.prototype,
      "findAccessPolicyByName",
    ).mockResolvedValue({ id: "once-policy" });
    const deletePolicy = vi
      .spyOn(CloudflareClient.prototype, "deleteAccessPolicy")
      .mockImplementationOnce(async () => {
        startDelete();
        await released;
        throw new Error("provider-delete-failed");
      });
    const deleteApp = vi
      .spyOn(CloudflareClient.prototype, "deleteAccessApp")
      .mockResolvedValue();
    const deleteDns = vi
      .spyOn(CloudflareClient.prototype, "deleteDnsRecord")
      .mockResolvedValue();
    const deleteTunnel = vi
      .spyOn(CloudflareClient.prototype, "deleteTunnel")
      .mockResolvedValue();

    const first = reconcilePetCare(env(), NOW);
    await started;
    const second = await reconcilePetCare(env(), NOW);
    expect(second.cleanedTunnels).toBe(0);
    expect(second.cleanedTenants).toBe(0);
    expect(await db.prepare("SELECT id FROM homes").first()).toEqual({
      id: home.homeId,
    });
    expect(await count("tenant_cleanup")).toBe(1);
    releaseDelete();
    const firstResult = await first;

    expect(firstResult.cleanedTunnels).toBe(0);
    expect(firstResult.cleanedTenants).toBe(0);
    expect(await count("tenant_cleanup")).toBe(1);
    expect(await db.prepare("SELECT status FROM tunnel_routes").first()).toEqual({
      status: "cleanup_pending",
    });

    const retry = await reconcilePetCare(
      env(),
      new Date("2026-07-27T01:00:00.000Z"),
    );

    expect(deletePolicy).toHaveBeenCalledTimes(2);
    expect(deleteApp).toHaveBeenCalledTimes(1);
    expect(deleteDns).toHaveBeenCalledTimes(1);
    expect(deleteTunnel).toHaveBeenCalledTimes(1);
    expect(retry.cleanedTunnels).toBe(1);
    expect(retry.cleanedTenants).toBe(1);
    expect(await db.prepare("SELECT status FROM tunnel_routes").first()).toBeNull();
    expect(await count("tenant_cleanup")).toBe(0);
    expect(await count("homes")).toBe(0);
  });

  it("completes tenant cleanup when a revoked route lease has expired", async () => {
    const home = await seedHome("expired-cleanup-lease", { deleted: true });
    await db.batch([
      db
        .prepare(
          `INSERT INTO tunnel_routes
           (home_id, agent_id, status, lease_id, lease_expires_at, created_at, updated_at)
           VALUES (?, ?, 'revoked', ?, ?, ?, ?)`,
        )
        .bind(
          home.homeId,
          home.agentId,
          "expired-lease",
          "2026-07-26T23:59:59.999Z",
          NOW_ISO,
          NOW_ISO,
        ),
      db
        .prepare(
          "INSERT INTO tenant_cleanup (owner_sub, home_id, status, started_at, updated_at) VALUES (?, ?, 'cleanup_pending', ?, ?)",
        )
        .bind(home.ownerSub, home.homeId, NOW_ISO, NOW_ISO),
    ]);

    const result = await reconcilePetCare(env(), NOW);

    expect(result.cleanedTunnels).toBe(0);
    expect(result.cleanedTenants).toBe(1);
    expect(await count("tunnel_routes")).toBe(0);
    expect(await count("tenant_cleanup")).toBe(0);
    expect(await count("homes")).toBe(0);
  });

  it("does not revert a cleaned tunnel when tenant object cleanup is still pending", async () => {
    const home = await seedHome("tenant-pending", { deleted: true });
    await db.batch([
      db
        .prepare(
          `INSERT INTO tunnel_routes
           (home_id, agent_id, status, created_at, updated_at)
           VALUES (?, ?, 'revocation_pending', ?, ?)`,
        )
        .bind(home.homeId, home.agentId, NOW_ISO, NOW_ISO),
      db
        .prepare(
          "INSERT INTO tenant_cleanup (owner_sub, home_id, status, started_at, updated_at) VALUES (?, ?, 'cleanup_pending', ?, ?)",
        )
        .bind(home.ownerSub, home.homeId, NOW_ISO, NOW_ISO),
      db
        .prepare(
          "INSERT INTO object_deletion_jobs (object_key, home_id, requested_at) VALUES (?, ?, ?)",
        )
        .bind("clips/delete-retry.mp4", home.homeId, NOW_ISO),
    ]);
    const failingBucket = new Proxy(clips, {
      get(target, property, receiver) {
        if (property === "delete") return async () => { throw new Error("r2-failed"); };
        const value = Reflect.get(target, property, receiver);
        return typeof value === "function" ? value.bind(target) : value;
      },
    });

    const result = await reconcilePetCare(env(failingBucket), NOW);

    expect(result.cleanedTunnels).toBe(1);
    expect(result.cleanedTenants).toBe(0);
    expect(await db.prepare("SELECT status FROM tunnel_routes").first()).toEqual({
      status: "revoked",
    });
    expect(
      await db
        .prepare("SELECT last_error, updated_at FROM tenant_cleanup")
        .first(),
    ).toEqual({ last_error: "tenant_cleanup_failed", updated_at: NOW_ISO });
    expect(await count("object_deletion_jobs")).toBe(1);
  });

  it("rotates failed object jobs so the 101st job cannot starve", async () => {
    const home = await seedHome("job-fairness");
    const statements: D1PreparedStatement[] = [];
    for (let index = 0; index < 101; index += 1) {
      statements.push(
        db
          .prepare(
            "INSERT INTO object_deletion_jobs (object_key, home_id, requested_at) VALUES (?, ?, ?)",
          )
          .bind(
            `clips/job-${String(index).padStart(3, "0")}.mp4`,
            home.homeId,
            "2026-07-26T00:00:00.000Z",
          ),
      );
    }
    await db.batch(statements);
    const failingBucket = new Proxy(clips, {
      get(target, property, receiver) {
        if (property === "delete") return async () => { throw new Error("r2-failed"); };
        const value = Reflect.get(target, property, receiver);
        return typeof value === "function" ? value.bind(target) : value;
      },
    });

    await reconcilePetCare(env(failingBucket), NOW);
    expect(
      await db
        .prepare(
          "SELECT requested_at FROM object_deletion_jobs WHERE object_key = 'clips/job-000.mp4'",
        )
        .first(),
    ).toEqual({ requested_at: NOW_ISO });
    expect(
      await db
        .prepare(
          "SELECT requested_at FROM object_deletion_jobs WHERE object_key = 'clips/job-100.mp4'",
        )
        .first(),
    ).toEqual({ requested_at: "2026-07-26T00:00:00.000Z" });

    await reconcilePetCare(env(), new Date("2026-07-27T01:00:00.000Z"));
    expect(
      await db
        .prepare(
          "SELECT object_key FROM object_deletion_jobs WHERE object_key = 'clips/job-100.mp4'",
        )
        .first(),
    ).toBeNull();
  });

  it("returns secret-free counters when a D1 selector is temporarily unavailable", async () => {
    vi.spyOn(
      PetCareRepository.prototype,
      "listObjectDeletionJobs",
    ).mockRejectedValueOnce(new Error("d1-provider-secret"));
    const log = vi.spyOn(console, "error").mockImplementation(() => undefined);

    const result = await reconcilePetCare(env(), NOW);

    expect(result.retryableFailures).toBe(1);
    expect(JSON.stringify(result)).not.toMatch(/provider|secret|d1/);
    expect(log).not.toHaveBeenCalled();
  });

  it("enforces every hourly work budget", async () => {
    const home = await seedHome("budget");
    const statements: D1PreparedStatement[] = [];
    for (let index = 0; index < 101; index += 1) {
      const queuedKey = `queued/${String(index).padStart(3, "0")}.mp4`;
      await clips.put(queuedKey, "mp4");
      statements.push(
        db
          .prepare(
            "INSERT INTO object_deletion_jobs (object_key, home_id, requested_at) VALUES (?, ?, ?)",
          )
          .bind(queuedKey, home.homeId, NOW_ISO),
      );
      const expiredKey = await seedClip({
        id: `expired-${index}`,
        ...home,
        expiresAt: NOW_ISO,
      });
      await clips.put(expiredKey, "mp4");
    }
    for (let index = 0; index < 501; index += 1) {
      statements.push(
        db
          .prepare(
            "INSERT INTO upload_nonces (agent_id, nonce, used_at, expires_at) VALUES (?, ?, ?, ?)",
          )
          .bind(home.agentId, `nonce-${index}`, NOW_ISO, NOW_ISO),
      );
    }
    await db.batch(statements);
    for (let index = 0; index < 40; index += 1) {
      const routeHome = await seedHome(`route-${index}`, { deleted: true });
      await db
        .prepare(
          `INSERT INTO tunnel_routes
           (home_id, agent_id, status, created_at, updated_at)
           VALUES (?, ?, ?, ?, ?)`,
        )
        .bind(
          routeHome.homeId,
          routeHome.agentId,
          index < 20 ? "cleanup_pending" : "provisioning",
          "2026-07-26T23:40:00.000Z",
          "2026-07-26T23:40:00.000Z",
        )
        .run();
    }
    await db
      .prepare(
        "INSERT INTO tenant_cleanup (owner_sub, home_id, status, started_at, updated_at) VALUES (?, ?, 'cleanup_pending', ?, ?)",
      )
      .bind("owner-route-0", "home-route-0", NOW_ISO, NOW_ISO)
      .run();

    const result = await reconcilePetCare(env(), NOW);

    expect(result.expiredClips).toBe(100);
    expect(result.expiredNonces).toBe(500);
    expect(result.cleanedTunnels).toBe(25);
    expect(result.cleanedTenants).toBe(0);
    expect(await count("object_deletion_jobs")).toBe(101 + 100 - 100);
    expect(await count("clips")).toBe(1);
    expect(await count("upload_nonces")).toBe(1);
    expect(
      await db
        .prepare(
          "SELECT COUNT(*) AS count FROM tunnel_routes WHERE status = 'revoked'",
        )
        .first(),
    ).toEqual({ count: 25 });
    expect(
      await db
        .prepare(
          "SELECT COUNT(*) AS count FROM tunnel_routes WHERE status <> 'revoked'",
        )
        .first(),
    ).toEqual({ count: 15 });
    expect(await count("tenant_cleanup")).toBe(1);
  });
});
