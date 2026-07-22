// @vitest-environment node

import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { Miniflare } from "miniflare";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("cloudflare:workers", () => ({ env: {} }));

import { getDb } from "../../db";
import { TenantRepository } from "../../lib/tenancy/repository";
import { miniflarePort } from "../helpers/miniflare";

let mf: Miniflare;
let db: D1Database;

beforeEach(async () => {
  mf = new Miniflare({
    modules: true,
    port: miniflarePort(1),
    script: "export default { fetch() { return new Response('ok') } }",
    d1Databases: ["DB"],
  });
  db = await mf.getD1Database("DB");
  for (const migrationName of [
    "0000_petcare_tenancy.sql",
    "0001_petcare_tunnels_clips.sql",
  ]) {
    const migration = readFileSync(
      resolve(import.meta.dirname, `../../drizzle/${migrationName}`),
      "utf8",
    );
    await db.batch(
      migration
        .split("--> statement-breakpoint")
        .map((statement) => statement.trim())
        .filter(Boolean)
        .map((statement) => db.prepare(statement)),
    );
  }
});

afterEach(async () => mf.dispose());

describe("TenantRepository", () => {
  async function reserveRoute(
    homeId: string,
    agentId: string,
    status = "provisioning",
  ) {
    await db
      .prepare(
        "INSERT INTO tunnel_routes (home_id, agent_id, status, created_at, updated_at) VALUES (?, ?, ?, ?, ?)",
      )
      .bind(
        homeId,
        agentId,
        status,
        "2026-07-20T03:05:00.000Z",
        "2026-07-20T03:05:00.000Z",
      )
      .run();
  }

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
    await db
      .prepare("UPDATE homes SET deleted_at = ? WHERE id = ?")
      .bind("2026-07-20T03:00:00.000Z", home.id)
      .run();

    await expect(repository.ensureHome("owner-a")).rejects.toMatchObject({
      status: 410,
      code: "account_deleted",
    });
    expect(
      await db
        .prepare("SELECT COUNT(*) AS count FROM homes WHERE owner_sub = ?")
        .bind("owner-a")
        .first(),
    ).toEqual({ count: 1 });
  });

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
      agent: {
        id: "agent-a",
        publicKey: "public-a",
        tunnelOrigin: "https://a.invalid",
      },
      camera: { id: "camera-a", localCameraId: "usb-0" },
    };
    await reserveRoute(home.id, input.agent.id);

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

  it("rejects a valid token without its reserved provisioning route", async () => {
    const repository = new TenantRepository(getDb(db));
    const home = await repository.ensureHome("owner-a");
    await repository.replaceEnrollmentToken(
      home.id,
      "unreserved-hash",
      "2026-07-20T03:10:00.000Z",
    );

    await expect(
      repository.consumeEnrollment({
        codeHash: "unreserved-hash",
        consumedAt: "2026-07-20T03:05:00.000Z",
        agent: {
          id: "agent-a",
          publicKey: "public-a",
          tunnelOrigin: "https://a.invalid",
        },
        camera: { id: "camera-a", localCameraId: "usb-0" },
      }),
    ).rejects.toMatchObject({ code: "enrollment_rejected" });

    expect(
      await db
        .prepare(
          "SELECT consumed_at FROM enrollment_tokens WHERE token_hash = ?",
        )
        .bind("unreserved-hash")
        .first(),
    ).toEqual({ consumed_at: null });
    expect(
      await db.prepare("SELECT COUNT(*) AS count FROM agents").first(),
    ).toEqual({ count: 0 });
    expect(
      await db.prepare("SELECT COUNT(*) AS count FROM cameras").first(),
    ).toEqual({ count: 0 });
  });

  it.each([
    ["agent", "owner-a", "agent-other", "provisioning"],
    ["home", "owner-b", "agent-a", "provisioning"],
    ["status", "owner-a", "agent-a", "activation_pending"],
  ])(
    "rejects a valid token when the reserved route has mismatched %s",
    async (_field, routeOwner, routeAgentId, routeStatus) => {
      const repository = new TenantRepository(getDb(db));
      const home = await repository.ensureHome("owner-a");
      const routeHome =
        routeOwner === "owner-a"
          ? home
          : await repository.ensureHome(routeOwner);
      await repository.replaceEnrollmentToken(
        home.id,
        "mismatched-route-hash",
        "2026-07-20T03:10:00.000Z",
      );
      await reserveRoute(routeHome.id, routeAgentId, routeStatus);

      await expect(
        repository.consumeEnrollment({
          codeHash: "mismatched-route-hash",
          consumedAt: "2026-07-20T03:05:00.000Z",
          agent: {
            id: "agent-a",
            publicKey: "public-a",
            tunnelOrigin: "https://a.invalid",
          },
          camera: { id: "camera-a", localCameraId: "usb-0" },
        }),
      ).rejects.toMatchObject({ code: "enrollment_rejected" });

      expect(
        await db
          .prepare(
            "SELECT consumed_at FROM enrollment_tokens WHERE token_hash = ?",
          )
          .bind("mismatched-route-hash")
          .first(),
      ).toEqual({ consumed_at: null });
      expect(
        await db.prepare("SELECT COUNT(*) AS count FROM agents").first(),
      ).toEqual({ count: 0 });
      expect(
        await db.prepare("SELECT COUNT(*) AS count FROM cameras").first(),
      ).toEqual({ count: 0 });
    },
  );

  it("rejects expiry without consuming or binding", async () => {
    const repository = new TenantRepository(getDb(db));
    const home = await repository.ensureHome("owner-a");
    await repository.replaceEnrollmentToken(
      home.id,
      "expired-hash",
      "2026-07-20T03:00:00.000Z",
    );

    await expect(
      repository.consumeEnrollment({
        codeHash: "expired-hash",
        consumedAt: "2026-07-20T03:00:00.001Z",
        agent: {
          id: "agent-a",
          publicKey: "public-a",
          tunnelOrigin: "https://a.invalid",
        },
        camera: { id: "camera-a", localCameraId: "usb-0" },
      }),
    ).rejects.toMatchObject({ code: "enrollment_rejected" });

    const token = await db
      .prepare(
        "SELECT consumed_at FROM enrollment_tokens WHERE token_hash = ?",
      )
      .bind("expired-hash")
      .first<{ consumed_at: string | null }>();
    expect(token?.consumed_at).toBeNull();
    expect(
      await db
        .prepare("SELECT COUNT(*) AS count FROM agents")
        .first<{ count: number }>(),
    ).toEqual({ count: 0 });
  });

  it("rejects an unknown code without creating a binding", async () => {
    const repository = new TenantRepository(getDb(db));
    await expect(
      repository.consumeEnrollment({
        codeHash: "unknown-hash",
        consumedAt: "2026-07-20T03:05:00.000Z",
        agent: {
          id: "agent-a",
          publicKey: "public-a",
          tunnelOrigin: "https://a.invalid",
        },
        camera: { id: "camera-a", localCameraId: "usb-0" },
      }),
    ).rejects.toMatchObject({ status: 409, code: "enrollment_rejected" });
    expect(
      await db
        .prepare("SELECT COUNT(*) AS count FROM agents")
        .first<{ count: number }>(),
    ).toEqual({ count: 0 });
    expect(
      await db
        .prepare("SELECT COUNT(*) AS count FROM cameras")
        .first<{ count: number }>(),
    ).toEqual({ count: 0 });
  });

  it("rejects a second active agent and leaves the fresh token unused", async () => {
    const repository = new TenantRepository(getDb(db));
    const home = await repository.ensureHome("owner-a");
    await db
      .prepare(
        "INSERT INTO agents (id, home_id, public_key, tunnel_origin) VALUES (?, ?, ?, ?)",
      )
      .bind(
        "agent-existing",
        home.id,
        "key-existing",
        "https://existing.invalid",
      )
      .run();
    await repository.replaceEnrollmentToken(
      home.id,
      "second-agent-hash",
      "2026-07-20T03:10:00.000Z",
    );
    await reserveRoute(home.id, "agent-new");

    await expect(
      repository.consumeEnrollment({
        codeHash: "second-agent-hash",
        consumedAt: "2026-07-20T03:05:00.000Z",
        agent: {
          id: "agent-new",
          publicKey: "key-new",
          tunnelOrigin: "https://new.invalid",
        },
        camera: { id: "camera-new", localCameraId: "usb-1" },
      }),
    ).rejects.toMatchObject({ code: "enrollment_rejected" });

    expect(
      await db
        .prepare("SELECT id FROM agents WHERE id = ?")
        .bind("agent-new")
        .first(),
    ).toBeNull();
    expect(
      await db
        .prepare(
          "SELECT consumed_at FROM enrollment_tokens WHERE token_hash = ?",
        )
        .bind("second-agent-hash")
        .first(),
    ).toEqual({ consumed_at: null });
  });

  it("rolls back a new agent when an active camera blocks binding", async () => {
    const repository = new TenantRepository(getDb(db));
    const home = await repository.ensureHome("owner-a");
    await db.batch([
      db
        .prepare(
          "INSERT INTO agents (id, home_id, public_key, tunnel_origin, revoked_at) VALUES (?, ?, ?, ?, ?)",
        )
        .bind(
          "agent-old",
          home.id,
          "key-old",
          "https://old.invalid",
          "2026-07-20T03:00:00.000Z",
        ),
      db
        .prepare(
          "INSERT INTO cameras (id, home_id, agent_id, local_camera_id, created_at) VALUES (?, ?, ?, ?, ?)",
        )
        .bind(
          "camera-existing",
          home.id,
          "agent-old",
          "usb-0",
          "2026-07-20T02:00:00.000Z",
        ),
    ]);
    await repository.replaceEnrollmentToken(
      home.id,
      "camera-conflict-hash",
      "2026-07-20T03:10:00.000Z",
    );
    await reserveRoute(home.id, "agent-new");

    await expect(
      repository.consumeEnrollment({
        codeHash: "camera-conflict-hash",
        consumedAt: "2026-07-20T03:05:00.000Z",
        agent: {
          id: "agent-new",
          publicKey: "key-new",
          tunnelOrigin: "https://new.invalid",
        },
        camera: { id: "camera-new", localCameraId: "usb-1" },
      }),
    ).rejects.toMatchObject({ code: "enrollment_rejected" });

    expect(
      await db
        .prepare("SELECT id FROM agents WHERE id = ?")
        .bind("agent-new")
        .first(),
    ).toBeNull();
    expect(
      await db
        .prepare(
          "SELECT consumed_at FROM enrollment_tokens WHERE token_hash = ?",
        )
        .bind("camera-conflict-hash")
        .first(),
    ).toEqual({ consumed_at: null });
  });

  it("preserves an unexpected D1 failure as a secret-safe 503", async () => {
    const client = {
      prepare: vi.fn(() => ({ bind: vi.fn(() => ({})) })),
      batch: vi
        .fn()
        .mockRejectedValue(
          new Error("D1_ERROR: connection reset at internal-host"),
        ),
    };
    const repository = new TenantRepository({ $client: client } as never);

    const failure = repository.consumeEnrollment({
      codeHash: "valid-hash",
      consumedAt: "2026-07-20T03:05:00.000Z",
      agent: {
        id: "agent-a",
        publicKey: "public-a",
        tunnelOrigin: "https://a.invalid",
      },
      camera: { id: "camera-a", localCameraId: "usb-0" },
    });
    await expect(failure).rejects.toMatchObject({
      status: 503,
      code: "tenancy_unavailable",
      message: "Tenancy unavailable",
    });
    await expect(failure).rejects.not.toThrow(/connection reset|internal-host/);
  });
});
