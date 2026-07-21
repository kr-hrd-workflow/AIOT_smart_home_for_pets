// @vitest-environment node

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("cloudflare:workers", () => ({ env: {} }));
import {
  PetCareRepository,
  type PublishClipInput,
} from "../lib/petcare/repository";
import { FakeD1 } from "./helpers/petcare-fakes";

const now = "2026-07-20T00:00:00.000Z";
let fake: FakeD1;
let db: D1Database;
let repo: PetCareRepository;

async function run(sql: string, ...values: unknown[]) {
  await db.prepare(sql).bind(...values).run();
}

async function seedHome(suffix: "a" | "b", status = "active") {
  const homeId = `home-${suffix}`;
  const agentId = `agent-${suffix}`;
  const cameraId = `camera-${suffix}`;
  await run(
    "INSERT INTO homes (id, owner_sub, created_at) VALUES (?, ?, ?)",
    homeId,
    `owner-${suffix}`,
    now,
  );
  await run(
    "INSERT INTO agents (id, home_id, public_key, tunnel_origin) VALUES (?, ?, ?, ?)",
    agentId,
    homeId,
    `public-${suffix}`,
    `https://${suffix}.example.test`,
  );
  await run(
    "INSERT INTO cameras (id, home_id, agent_id, local_camera_id, created_at) VALUES (?, ?, ?, ?, ?)",
    cameraId,
    homeId,
    agentId,
    "pc-webcam-01",
    now,
  );
  await run(
    "INSERT INTO tunnel_routes (home_id, agent_id, tunnel_origin, status, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?)",
    homeId,
    agentId,
    `https://${suffix}.example.test`,
    status,
    now,
    now,
  );
  return { homeId, agentId, cameraId };
}

function clip(overrides: Partial<PublishClipInput> = {}): PublishClipInput {
  return {
    id: "clip-a",
    homeId: "home-a",
    agentId: "agent-a",
    cameraId: "camera-a",
    objectKey: "clips/random-a.mp4",
    sha256: "digest-a",
    sizeBytes: 123,
    startedAt: "2026-07-19T23:59:30.000Z",
    endedAt: now,
    createdAt: now,
    expiresAt: "2026-07-27T00:00:00.000Z",
    events: [{ eventType: "eating", eventId: "event-a" }],
    ...overrides,
  };
}

beforeEach(() => {
  fake = new FakeD1();
  db = fake as unknown as D1Database;
  repo = new PetCareRepository(db);
});

afterEach(() => fake.dispose());

describe("PetCareRepository", () => {
  it("never resolves another home's inactive tunnel", async () => {
    await seedHome("a");
    await seedHome("b", "revoked");

    await expect(repo.requireActiveRoute("home-a")).resolves.toMatchObject({
      homeId: "home-a",
      agentId: "agent-a",
      cameraId: "camera-a",
    });
    await expect(repo.requireActiveRoute("home-b")).rejects.toMatchObject({
      status: 503,
      code: "agent_offline",
    });
  });

  it("rejects a malformed or non-HTTPS active tunnel origin", async () => {
    await seedHome("a");
    await run("UPDATE tunnel_routes SET tunnel_origin = ? WHERE home_id = ?", "http://a.example.test", "home-a");

    await expect(repo.requireActiveRoute("home-a")).rejects.toMatchObject({
      status: 503,
      code: "agent_offline",
    });
  });

  it("allows one unexpired activation-pending route to prove liveness", async () => {
    await seedHome("a", "activation_pending");
    await run(
      "UPDATE tunnel_routes SET activation_expires_at = ? WHERE home_id = ?",
      "2099-07-20T00:10:00.000Z",
      "home-a",
    );

    await expect(repo.getHomeConnection("home-a")).resolves.toMatchObject({
      state: "ready",
      revoked: false,
    });
    await expect(repo.requireActiveRoute("home-a")).rejects.toMatchObject({
      status: 503,
      code: "agent_offline",
    });
    await expect(repo.requireActivationRoute("home-a", now)).resolves.toMatchObject({
      agentId: "agent-a",
    });

    await run(
      "UPDATE tunnel_routes SET activation_expires_at = ? WHERE home_id = ?",
      now,
      "home-a",
    );
    await expect(repo.requireActivationRoute("home-a", now)).rejects.toMatchObject({
      status: 503,
      code: "agent_offline",
    });
  });

  it("does not activate a route for a disabled camera", async () => {
    await seedHome("a", "activation_pending");
    await run(
      "UPDATE tunnel_routes SET activation_expires_at = ? WHERE home_id = ?",
      "2026-07-20T00:10:00.000Z",
      "home-a",
    );
    await run("UPDATE cameras SET disabled_at = ? WHERE id = ?", now, "camera-a");

    await expect(repo.markAgentSeen("agent-a", now)).rejects.toMatchObject({
      status: 503,
      code: "agent_offline",
    });
    expect(fake.rows.tunnel_routes).toEqual([
      expect.objectContaining({ home_id: "home-a", status: "activation_pending" }),
    ]);
  });

  it("consumes an agent nonce once", async () => {
    await expect(repo.consumeNonce("agent-a", "nonce-1234567890123456", now)).resolves.toBeUndefined();
    await expect(repo.consumeNonce("agent-a", "nonce-1234567890123456", now)).rejects.toMatchObject({
      status: 409,
      code: "replay",
    });
  });

  it("enforces a fixed-window request limit atomically", async () => {
    for (let attempt = 0; attempt < 5; attempt += 1) {
      await repo.checkRateLimit("code-hash", "enroll-code", 5, 600, new Date(now));
    }
    await expect(
      repo.checkRateLimit("code-hash", "enroll-code", 5, 600, new Date(now)),
    ).rejects.toMatchObject({ status: 429, code: "rate_limited" });
  });

  it("denies a clip at the exact expiry instant", async () => {
    await seedHome("a");
    await repo.publishClip(clip());

    await expect(
      repo.requireOwnedClip("home-a", "clip-a", "2026-07-27T00:00:00.000Z"),
    ).rejects.toMatchObject({ status: 404, code: "not_found" });
  });

  it("cannot publish after tenant cleanup starts and preserves the other tenant", async () => {
    await seedHome("a");
    await seedHome("b");
    await repo.publishClip(clip({ id: "clip-b", homeId: "home-b", agentId: "agent-b", cameraId: "camera-b", objectKey: "clips/random-b.mp4" }));

    await expect(repo.beginTenantCleanup("owner-a", now)).resolves.toEqual({
      homeId: "home-a",
      status: "cleanup_pending",
    });
    await expect(repo.publishClip(clip())).rejects.toMatchObject({
      status: 410,
      code: "account_deleted",
    });
    await expect(repo.requireOwnedClip("home-b", "clip-b", now)).resolves.toMatchObject({
      id: "clip-b",
      homeId: "home-b",
    });
  });

  it("cannot attach events to another tenant's existing clip after a guarded insert rejects", async () => {
    await seedHome("a");
    await seedHome("b");
    await repo.publishClip(
      clip({
        id: "shared-id",
        homeId: "home-b",
        agentId: "agent-b",
        cameraId: "camera-b",
        objectKey: "clips/random-b.mp4",
        events: [{ eventType: "resting", eventId: "event-b" }],
      }),
    );
    await repo.beginTenantCleanup("owner-a", now);

    await expect(
      repo.publishClip(
        clip({
          id: "shared-id",
          events: [{ eventType: "eating", eventId: "foreign-write" }],
        }),
      ),
    ).rejects.toMatchObject({ status: 410, code: "account_deleted" });
    expect(fake.rows.clip_events).toEqual([
      expect.objectContaining({
        clip_id: "shared-id",
        event_type: "resting",
        event_id: "event-b",
      }),
    ]);
  });

  it("deletes clip metadata and queues its private object atomically", async () => {
    await seedHome("a");
    await repo.publishClip(clip());

    await expect(repo.deleteClipAndQueueObject("home-a", "clip-a", now)).resolves.toEqual({
      objectKey: "clips/random-a.mp4",
    });
    await expect(repo.requireOwnedClip("home-a", "clip-a", now)).rejects.toMatchObject({
      status: 404,
      code: "not_found",
    });
    expect(fake.rows.object_deletion_jobs).toEqual([
      expect.objectContaining({ home_id: "home-a", object_key: "clips/random-a.mp4" }),
    ]);
  });

  it("paginates unexpired clip metadata without restarting at the first row", async () => {
    await seedHome("a");
    await repo.publishClip(clip({ id: "clip-001", objectKey: "clips/001.mp4" }));
    await repo.publishClip(clip({ id: "clip-002", objectKey: "clips/002.mp4" }));

    await expect(repo.listUnexpiredClipObjects(now, null, 1)).resolves.toEqual([
      { id: "clip-001", objectKey: "clips/001.mp4" },
    ]);
    await expect(repo.listUnexpiredClipObjects(now, "clip-001", 1)).resolves.toEqual([
      { id: "clip-002", objectKey: "clips/002.mp4" },
    ]);
  });

  it("cannot complete another owner's cleanup by pairing their subject with a guessed home", async () => {
    await seedHome("a");
    await seedHome("b");
    await repo.beginTenantCleanup("owner-a", now);
    await repo.beginTenantCleanup("owner-b", now);
    const before = JSON.stringify(fake.rows);

    await expect(repo.completeTenantCleanup("owner-a", "home-b", now)).rejects.toMatchObject({
      status: 503,
      code: "cleanup_retryable",
    });
    expect(JSON.stringify(fake.rows)).toBe(before);
  });

  it("completes cleanup once and treats concurrent or lost-response retries as success", async () => {
    await seedHome("a");
    await repo.beginTenantCleanup("owner-a", now);
    await run(
      "UPDATE tunnel_routes SET status = 'cleanup_pending', lease_id = ?, lease_expires_at = ? WHERE home_id = ?",
      "cleanup-lease",
      "2026-07-20T00:02:00.000Z",
      "home-a",
    );

    await expect(repo.completeTenantCleanup("owner-a", "home-a", now)).rejects.toMatchObject({
      status: 503,
      code: "cleanup_retryable",
    });
    expect(fake.rows.homes).toEqual([expect.objectContaining({ id: "home-a" })]);
    await run(
      "UPDATE tunnel_routes SET status = 'revoked', lease_id = NULL WHERE home_id = ?",
      "home-a",
    );
    await expect(repo.completeTenantCleanup("owner-a", "home-a", now)).rejects.toMatchObject({
      status: 503,
      code: "cleanup_retryable",
    });
    await run(
      "UPDATE tunnel_routes SET lease_id = ?, lease_expires_at = ? WHERE home_id = ?",
      "expired-lease",
      "2026-07-19T23:59:00.000Z",
      "home-a",
    );

    await expect(
      Promise.all([
        repo.completeTenantCleanup("owner-a", "home-a", now),
        repo.completeTenantCleanup("owner-a", "home-a", now),
      ]),
    ).resolves.toEqual([undefined, undefined]);
    await expect(repo.completeTenantCleanup("owner-a", "home-a", now)).resolves.toBeUndefined();
    expect(fake.rows.homes).toEqual([]);
    expect(fake.rows.tenant_cleanup).toEqual([]);
    expect(fake.rows.agents).toEqual([]);
    expect(fake.rows.cameras).toEqual([]);
    expect(fake.rows.tunnel_routes).toEqual([]);
  });

  it("retains cleanup with a fixed error until queued objects are gone", async () => {
    await seedHome("a");
    await repo.publishClip(clip());
    await repo.beginTenantCleanup("owner-a", now);

    await expect(repo.completeTenantCleanup("owner-a", "home-a", now)).rejects.toMatchObject({
      status: 503,
      code: "cleanup_retryable",
    });
    expect(fake.rows.tenant_cleanup).toEqual([
      expect.objectContaining({
        owner_sub: "owner-a",
        home_id: "home-a",
        last_error: "tenant_cleanup_failed",
      }),
    ]);

    await repo.recordObjectDeletionFailure(
      "home-a",
      "clips/random-a.mp4",
      "2026-07-20T00:01:00.000Z",
    );
    expect(fake.rows.object_deletion_jobs).toEqual([
      expect.objectContaining({
        requested_at: "2026-07-20T00:01:00.000Z",
        last_error: "object_delete_failed",
      }),
    ]);
  });

  it("cannot overwrite another route ledger while recording cleanup", async () => {
    await seedHome("a");
    await run(
      `UPDATE tunnel_routes SET status = 'cleanup_pending', lease_id = ?, lease_expires_at = ?
       WHERE home_id = ?`,
      "lease-a",
      "2026-07-20T00:02:00.000Z",
      "home-a",
    );
    const before = JSON.stringify(fake.rows.tunnel_routes);

    await expect(
      repo.recordCleanupPending(
        "home-a",
        "agent-b",
        { tunnelId: "foreign-tunnel" },
        "resource_state_write_failed",
        "lease-a",
        now,
      ),
    ).rejects.toMatchObject({ status: 503, code: "cleanup_retryable" });
    expect(JSON.stringify(fake.rows.tunnel_routes)).toBe(before);
  });

  it("rolls back the logical deletion batch when one guarded write fails", async () => {
    await seedHome("a");
    const before = JSON.stringify(fake.rows);
    fake.failOnce(/UPDATE cameras SET disabled_at/);

    await expect(repo.beginTenantCleanup("owner-a", now)).rejects.toThrow("synthetic failure");
    expect(JSON.stringify(fake.rows)).toBe(before);
  });

  it("leases provisioning so concurrent retries cannot create duplicate remote resources", async () => {
    await run(
      "INSERT INTO homes (id, owner_sub, created_at) VALUES (?, ?, ?)",
      "home-a",
      "owner-a",
      now,
    );
    await run(
      "INSERT INTO enrollment_tokens (id, home_id, token_hash, expires_at) VALUES (?, ?, ?, ?)",
      "token-a",
      "home-a",
      "hash-a",
      "2026-07-20T00:10:00.000Z",
    );
    await expect(
      repo.reserveTunnel("home-a", "agent-a", "wrong-hash", now),
    ).rejects.toMatchObject({ status: 409, code: "enrollment_rejected" });
    await repo.reserveTunnel("home-a", "agent-a", "hash-a", now);
    await expect(
      repo.reserveTunnel("home-a", "agent-a", "wrong-hash", now),
    ).rejects.toMatchObject({ status: 409, code: "enrollment_rejected" });
    await expect(
      repo.reserveTunnel(
        "home-a",
        "agent-a",
        "hash-a",
        "2026-07-20T00:10:00.000Z",
      ),
    ).rejects.toMatchObject({ status: 409, code: "enrollment_rejected" });

    await expect(
      repo.claimTunnelProvisioning(
        "home-a",
        "agent-a",
        "short-lease",
        now,
        "2026-07-20T00:00:59.000Z",
      ),
    ).rejects.toMatchObject({ status: 503, code: "enrollment_retryable" });
    await expect(
      repo.claimTunnelProvisioning(
        "home-a",
        "agent-a",
        "long-lease",
        now,
        "2026-07-20T00:02:01.000Z",
      ),
    ).rejects.toMatchObject({ status: 503, code: "enrollment_retryable" });

    await expect(
      repo.claimTunnelProvisioning(
        "home-a",
        "agent-a",
        "lease-a",
        now,
        "2026-07-20T00:02:00.000Z",
      ),
    ).resolves.toBeUndefined();
    await expect(
      repo.claimTunnelProvisioning(
        "home-a",
        "agent-a",
        "lease-b",
        "2026-07-20T00:01:00.000Z",
        "2026-07-20T00:03:00.000Z",
      ),
    ).rejects.toMatchObject({ status: 503, code: "enrollment_retryable" });
    await expect(
      repo.updateTunnelResource(
        "home-a",
        "agent-a",
        { tunnelId: "tunnel-a" },
        "2026-07-20T00:01:00.000Z",
        "lease-b",
      ),
    ).rejects.toMatchObject({ status: 503, code: "enrollment_retryable" });

    await expect(
      repo.claimTunnelProvisioning(
        "home-a",
        "agent-a",
        "lease-b",
        "2026-07-20T00:02:00.000Z",
        "2026-07-20T00:04:00.000Z",
      ),
    ).resolves.toBeUndefined();
    await expect(
      repo.updateTunnelResource(
        "home-a",
        "agent-a",
        { tunnelId: "stale-writer" },
        "2026-07-20T00:02:01.000Z",
        "lease-a",
      ),
    ).rejects.toMatchObject({ status: 503, code: "enrollment_retryable" });
    await expect(
      repo.renewTunnelLease(
        "home-a",
        "agent-a",
        "lease-b",
        "2026-07-20T00:03:00.000Z",
        "2026-07-20T00:05:00.000Z",
      ),
    ).resolves.toBeUndefined();
    await expect(
      repo.renewTunnelLease(
        "home-a",
        "agent-a",
        "lease-b",
        "2026-07-20T00:03:01.000Z",
        "2026-07-20T00:05:02.000Z",
      ),
    ).rejects.toMatchObject({ status: 503, code: "cleanup_retryable" });
    await expect(
      repo.claimTunnelProvisioning(
        "home-a",
        "agent-a",
        "lease-c",
        "2026-07-20T00:04:00.000Z",
        "2026-07-20T00:06:00.000Z",
      ),
    ).rejects.toMatchObject({ status: 503, code: "enrollment_retryable" });
  });

  it("keeps revocation changes atomic when route lease fencing fails", async () => {
    await seedHome("a");
    await expect(
      repo.requestRevocation(
        "home-a",
        "agent-a",
        "long-lease",
        now,
        "2026-07-20T00:02:01.000Z",
      ),
    ).rejects.toMatchObject({ status: 503, code: "revocation_retryable" });
    await run(
      "UPDATE tunnel_routes SET lease_id = ?, lease_expires_at = ? WHERE home_id = ?",
      "other-lease",
      "2026-07-20T00:02:00.000Z",
      "home-a",
    );

    await expect(
      repo.requestRevocation(
        "home-a",
        "agent-a",
        "lease-a",
        now,
        "2026-07-20T00:02:00.000Z",
      ),
    ).rejects.toMatchObject({ status: 503, code: "revocation_retryable" });
    expect(fake.rows.agents).toEqual([
      expect.objectContaining({ id: "agent-a", revoked_at: null }),
    ]);
    expect(fake.rows.cameras).toEqual([
      expect.objectContaining({ id: "camera-a", disabled_at: null }),
    ]);
    expect(fake.rows.tunnel_routes).toEqual([
      expect.objectContaining({ home_id: "home-a", status: "active", lease_id: "other-lease" }),
    ]);
  });

  it("fences cleanup mutations and permits takeover at exact lease expiry", async () => {
    await seedHome("a");
    await run("UPDATE tunnel_routes SET status = 'cleanup_pending' WHERE home_id = ?", "home-a");

    await expect(
      repo.claimPendingTunnelCleanup(
        now,
        now,
        "short-lease",
        "2026-07-20T00:00:59.000Z",
        25,
      ),
    ).rejects.toMatchObject({ status: 503, code: "cleanup_retryable" });
    await expect(
      repo.claimPendingTunnelCleanup(
        now,
        now,
        "lease-a",
        "2026-07-20T00:02:00.000Z",
        25,
      ),
    ).resolves.toEqual([
      expect.objectContaining({ homeId: "home-a", agentId: "agent-a", bound: true }),
    ]);
    await expect(
      repo.claimPendingTunnelCleanup(
        "2026-07-20T00:01:00.000Z",
        now,
        "lease-b",
        "2026-07-20T00:03:00.000Z",
        25,
      ),
    ).resolves.toEqual([]);
    await expect(
      repo.clearTunnelResource(
        "home-a",
        "agent-a",
        "lease-b",
        "tunnelOrigin",
        "2026-07-20T00:01:00.000Z",
      ),
    ).rejects.toMatchObject({ status: 503, code: "cleanup_retryable" });
    await expect(
      repo.markTunnelState(
        "home-a",
        "agent-a",
        "lease-b",
        "cleanup_pending",
        "revoked",
        "2026-07-20T00:01:00.000Z",
      ),
    ).rejects.toMatchObject({ status: 503, code: "cleanup_retryable" });
    await expect(
      repo.deleteTunnelRoute(
        "home-a",
        "agent-a",
        "lease-b",
        "2026-07-20T00:01:00.000Z",
      ),
    ).rejects.toMatchObject({ status: 503, code: "cleanup_retryable" });

    await expect(
      repo.claimPendingTunnelCleanup(
        "2026-07-20T00:02:00.000Z",
        now,
        "lease-b",
        "2026-07-20T00:04:00.000Z",
        25,
      ),
    ).resolves.toEqual([
      expect.objectContaining({ homeId: "home-a", leaseId: "lease-b" }),
    ]);
    await expect(
      repo.clearTunnelResource(
        "home-a",
        "agent-a",
        "lease-a",
        "tunnelOrigin",
        "2026-07-20T00:02:01.000Z",
      ),
    ).rejects.toMatchObject({ status: 503, code: "cleanup_retryable" });
  });

  it("claims at most 25 pending tunnel cleanups in one fenced workset", async () => {
    for (let index = 0; index < 26; index += 1) {
      const suffix = String(index).padStart(2, "0");
      await run(
        "INSERT INTO homes (id, owner_sub, created_at) VALUES (?, ?, ?)",
        `home-${suffix}`,
        `owner-${suffix}`,
        now,
      );
      await run(
        `INSERT INTO tunnel_routes (home_id, agent_id, status, created_at, updated_at)
         VALUES (?, ?, 'cleanup_pending', ?, ?)`,
        `home-${suffix}`,
        `agent-${suffix}`,
        now,
        now,
      );
    }

    const claimed = await repo.claimPendingTunnelCleanup(
      now,
      now,
      "lease-batch",
      "2026-07-20T00:02:00.000Z",
      100,
    );
    expect(claimed).toHaveLength(25);
    expect(claimed.every((route) => route.leaseId === "lease-batch")).toBe(true);
    await expect(
      repo.claimPendingTunnelCleanup(
        "2026-07-20T00:01:00.000Z",
        now,
        "lease-other",
        "2026-07-20T00:03:00.000Z",
        25,
      ),
    ).resolves.toHaveLength(1);
  });
});
