// @vitest-environment node

import { beforeEach, describe, expect, it, vi } from "vitest";
import { FakeD1, FakeR2 } from "./helpers/petcare-fakes";

vi.mock("cloudflare:workers", () => ({ env: {} }));

const mocks = vi.hoisted(() => ({
  getDb: vi.fn(() => ({ kind: "db" })),
  requireAuth: vi.fn(),
  requireHome: vi.fn(),
  listOwnedClips: vi.fn(),
  requireOwnedClip: vi.fn(),
  deleteClipAndQueueObject: vi.fn(),
  completeObjectDeletion: vi.fn(),
}));

vi.mock("../db", () => ({ getDb: mocks.getDb }));
vi.mock("../lib/auth/require-auth", () => ({
  requireAuth: mocks.requireAuth,
}));
vi.mock("../lib/tenancy/repository", () => ({
  TenantRepository: class {
    requireHome = mocks.requireHome;
  },
}));
vi.mock("../lib/petcare/repository", () => ({
  PetCareRepository: class {
    listOwnedClips = mocks.listOwnedClips;
    requireOwnedClip = mocks.requireOwnedClip;
    deleteClipAndQueueObject = mocks.deleteClipAndQueueObject;
    completeObjectDeletion = mocks.completeObjectDeletion;
  },
}));

import { deleteClip, listClips, readClip } from "../lib/petcare/clips";

const now = new Date("2026-07-27T00:00:00.000Z");
const request = new Request("https://app.test/api/petcare/clips", {
  headers: { cookie: "session=private" },
});
const userA = { sub: "owner-a", email: null };
const userB = { sub: "owner-b", email: null };

function makeEnv() {
  return {
    SUPABASE_URL: "https://auth.test",
    SUPABASE_PUBLISHABLE_KEY: "publishable",
    DB: { kind: "d1" },
    CLIPS: {
      get: vi.fn(),
      delete: vi.fn(),
    },
  };
}

function ownedClip(overrides: Record<string, unknown> = {}) {
  return {
    id: "clip-a",
    homeId: "home-a",
    cameraId: "camera-a",
    objectKey: "clips/opaque-a.mp4",
    sha256: "server-only-digest",
    startedAt: "2026-07-19T23:59:40.000Z",
    endedAt: "2026-07-20T00:00:10.000Z",
    createdAt: "2026-07-20T00:00:00.000Z",
    expiresAt: "2026-07-27T00:00:00.001Z",
    events: [
      { eventType: "resting", eventId: "event-secret-b" },
      { eventType: "eating", eventId: "event-secret-a" },
      { eventType: "resting", eventId: "event-secret-c" },
    ],
    ...overrides,
  };
}

beforeEach(() => {
  vi.clearAllMocks();
  mocks.requireHome.mockResolvedValue({ id: "home-a" });
  mocks.listOwnedClips.mockResolvedValue([]);
  mocks.completeObjectDeletion.mockResolvedValue(undefined);
});

describe("private tenant clip handlers", () => {
  it("enforces actual D1 tenant, expiry, ordering, and durable R2-failure semantics", async () => {
    const fake = new FakeD1();
    const r2 = new FakeR2();
    const db = fake as unknown as D1Database;
    const run = async (sql: string, ...values: unknown[]) => {
      await db.prepare(sql).bind(...values).run();
    };
    try {
      for (const suffix of ["a", "b"]) {
        await run(
          "INSERT INTO homes (id, owner_sub, created_at) VALUES (?, ?, ?)",
          `home-${suffix}`,
          `owner-${suffix}`,
          "2026-07-20T00:00:00.000Z",
        );
        await run(
          "INSERT INTO agents (id, home_id, public_key, tunnel_origin) VALUES (?, ?, ?, ?)",
          `agent-${suffix}`,
          `home-${suffix}`,
          `public-${suffix}`,
          `https://${suffix}.example.test`,
        );
        await run(
          "INSERT INTO cameras (id, home_id, agent_id, local_camera_id, created_at) VALUES (?, ?, ?, ?, ?)",
          `camera-${suffix}`,
          `home-${suffix}`,
          `agent-${suffix}`,
          "pc-webcam-01",
          "2026-07-20T00:00:00.000Z",
        );
      }
      for (const [id, home, camera, created, expires] of [
        ["clip-a-new", "home-a", "camera-a", "2026-07-20T00:00:02.000Z", "2026-07-27T00:00:01.000Z"],
        ["clip-a-old", "home-a", "camera-a", "2026-07-20T00:00:01.000Z", "2026-07-27T00:00:01.000Z"],
        ["clip-a-expired", "home-a", "camera-a", "2026-07-20T00:00:03.000Z", now.toISOString()],
        ["clip-b", "home-b", "camera-b", "2026-07-20T00:00:04.000Z", "2026-07-27T00:00:01.000Z"],
      ]) {
        await run(
          "INSERT INTO clips (id, home_id, camera_id, object_key, sha256, size_bytes, started_at, ended_at, created_at, expires_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
          id,
          home,
          camera,
          `clips/${id}.mp4`,
          `digest-${id}`,
          1,
          "2026-07-19T23:59:30.000Z",
          "2026-07-20T00:00:00.000Z",
          created,
          expires,
        );
        await r2.put(`clips/${id}.mp4`, id);
      }

      let failDelete = false;
      const r2Binding = {
        get: vi.fn(async (key: string) => {
          const object = await r2.get(key);
          return object
            ? { body: new Uint8Array(await object.arrayBuffer()) }
            : null;
        }),
        delete: vi.fn(async (key: string) => {
          if (failDelete) throw new Error("synthetic R2 delete failure");
          await r2.delete(key);
        }),
      };
      vi.resetModules();
      vi.doUnmock("../db");
      vi.doUnmock("../lib/tenancy/repository");
      vi.doUnmock("../lib/petcare/repository");
      const actual = await import("../lib/petcare/clips");
      const env = {
        SUPABASE_URL: "https://auth.test",
        SUPABASE_PUBLISHABLE_KEY: "publishable",
        DB: db,
        CLIPS: r2Binding,
      };

      const listResponse = await actual.listClips(
        request,
        env as never,
        now,
        userA,
      );
      expect(await listResponse.json()).toEqual({
        clips: [
          expect.objectContaining({ id: "clip-a-new" }),
          expect.objectContaining({ id: "clip-a-old" }),
        ],
      });

      await expect(
        actual.readClip(request, env as never, "clip-b", now, userA),
      ).rejects.toMatchObject({ status: 404, code: "not_found" });
      expect(r2Binding.get).not.toHaveBeenCalled();

      failDelete = true;
      await expect(
        actual.deleteClip(request, env as never, "clip-a-new", now, userA),
      ).resolves.toMatchObject({ status: 204 });

      expect(fake.rows.clips.map((row) => row.id)).not.toContain("clip-a-new");
      expect(fake.rows.object_deletion_jobs).toEqual([
        expect.objectContaining({
          home_id: "home-a",
          object_key: "clips/clip-a-new.mp4",
        }),
      ]);
      expect(r2.objects.has("clips/clip-a-new.mp4")).toBe(true);
      await expect(
        actual.readClip(request, env as never, "clip-a-new", now, userA),
      ).rejects.toMatchObject({ status: 404, code: "not_found" });

      failDelete = false;
      fake.failOnce(/DELETE FROM clips/);
      await expect(
        actual.deleteClip(request, env as never, "clip-a-old", now, userA),
      ).rejects.toThrow("synthetic failure");
      expect(fake.rows.clips.map((row) => row.id)).toContain("clip-a-old");
      expect(
        fake.rows.object_deletion_jobs.map((row) => row.object_key),
      ).not.toContain("clips/clip-a-old.mp4");
      expect(r2.objects.has("clips/clip-a-old.mp4")).toBe(true);

      await expect(
        actual.deleteClip(request, env as never, "clip-a-old", now, userA),
      ).resolves.toMatchObject({ status: 204 });
      expect(
        fake.rows.object_deletion_jobs.map((row) => row.object_key),
      ).not.toContain("clips/clip-a-old.mp4");
      expect(r2.objects.has("clips/clip-a-old.mp4")).toBe(false);
    } finally {
      fake.dispose();
    }
  }, 15_000);

  it("authenticates and lists only the resolved home's non-secret clip DTOs", async () => {
    const env = makeEnv();
    mocks.listOwnedClips.mockResolvedValue([ownedClip()]);

    const response = await listClips(request, env as never, now, userA);

    expect(mocks.requireAuth).not.toHaveBeenCalled();
    expect(mocks.requireHome).toHaveBeenCalledWith("owner-a");
    expect(mocks.listOwnedClips).toHaveBeenCalledWith(
      "home-a",
      now.toISOString(),
    );
    expect(response.status).toBe(200);
    expect(response.headers.get("Cache-Control")).toBe("private, no-store");
    expect(await response.json()).toEqual({
      clips: [
        {
          id: "clip-a",
          camera_id: "camera-a",
          event_types: ["eating", "resting"],
          started_at: "2026-07-19T23:59:40.000Z",
          ended_at: "2026-07-20T00:00:10.000Z",
          expires_at: "2026-07-27T00:00:00.001Z",
        },
      ],
    });
  });

  it("uses the router-authenticated user without a second auth lookup", async () => {
    const env = makeEnv();

    await listClips(request, env as never, now, userA);

    expect(mocks.requireAuth).not.toHaveBeenCalled();
    expect(mocks.requireHome).toHaveBeenCalledWith("owner-a");
    expect(mocks.listOwnedClips).toHaveBeenCalledWith(
      "home-a",
      now.toISOString(),
    );
  });

  it("rejects another tenant's or exactly expired clip before R2 access", async () => {
    const env = makeEnv();
    const notFound = Object.assign(new Error("not found"), {
      status: 404,
      code: "not_found",
    });
    mocks.requireOwnedClip.mockRejectedValue(notFound);

    await expect(
      readClip(request, env as never, "clip-b", now, userA),
    ).rejects.toBe(notFound);
    await expect(
      readClip(request, env as never, "clip-expired", now, userA),
    ).rejects.toBe(notFound);

    expect(mocks.requireOwnedClip).toHaveBeenNthCalledWith(
      1,
      "home-a",
      "clip-b",
      now.toISOString(),
    );
    expect(mocks.requireOwnedClip).toHaveBeenNthCalledWith(
      2,
      "home-a",
      "clip-expired",
      now.toISOString(),
    );
    expect(env.CLIPS.get).not.toHaveBeenCalled();
  });

  it("serves owned private MP4 bytes without exposing the R2 object key", async () => {
    const env = makeEnv();
    mocks.requireOwnedClip.mockResolvedValue(ownedClip());
    env.CLIPS.get.mockResolvedValue({ body: "private-video" });

    const response = await readClip(
      request,
      env as never,
      "clip-a",
      now,
      userA,
    );

    expect(env.CLIPS.get).toHaveBeenCalledWith("clips/opaque-a.mp4");
    expect(response.status).toBe(200);
    expect(response.headers.get("Content-Type")).toBe("video/mp4");
    expect(response.headers.get("Cache-Control")).toBe(
      "private, no-store, no-transform",
    );
    expect([...response.headers.values()].join(" ")).not.toContain(
      "clips/opaque-a.mp4",
    );
    expect(await response.text()).toBe("private-video");
  });

  it("removes stale metadata when an owned R2 object is missing", async () => {
    const env = makeEnv();
    mocks.requireOwnedClip.mockResolvedValue(ownedClip());
    mocks.deleteClipAndQueueObject.mockResolvedValue({
      objectKey: "clips/opaque-a.mp4",
    });
    env.CLIPS.get.mockResolvedValue(null);

    const response = await readClip(
      request,
      env as never,
      "clip-a",
      now,
      userA,
    );

    expect(response.status).toBe(404);
    expect(response.headers.get("Cache-Control")).toBe("private, no-store");
    expect(mocks.deleteClipAndQueueObject).toHaveBeenCalledWith(
      "home-a",
      "clip-a",
      now.toISOString(),
    );
    expect(mocks.completeObjectDeletion).toHaveBeenCalledWith(
      "home-a",
      "clips/opaque-a.mp4",
    );
  });

  it("deletes D1 metadata before R2 and clears the queued job on success", async () => {
    const env = makeEnv();
    mocks.deleteClipAndQueueObject.mockResolvedValue({
      objectKey: "clips/opaque-a.mp4",
    });
    env.CLIPS.delete.mockResolvedValue(undefined);

    const response = await deleteClip(
      request,
      env as never,
      "clip-a",
      now,
      userA,
    );

    expect(response.status).toBe(204);
    expect(response.headers.get("Cache-Control")).toBe("private, no-store");
    expect(mocks.deleteClipAndQueueObject).toHaveBeenCalledWith(
      "home-a",
      "clip-a",
      now.toISOString(),
    );
    expect(
      mocks.deleteClipAndQueueObject.mock.invocationCallOrder[0],
    ).toBeLessThan(env.CLIPS.delete.mock.invocationCallOrder[0]);
    expect(mocks.completeObjectDeletion).toHaveBeenCalledWith(
      "home-a",
      "clips/opaque-a.mp4",
    );
  });

  it("keeps logical denial and the deletion job when R2 deletion fails", async () => {
    const env = makeEnv();
    const notFound = Object.assign(new Error("not found"), {
      status: 404,
      code: "not_found",
    });
    mocks.deleteClipAndQueueObject.mockResolvedValue({
      objectKey: "clips/opaque-a.mp4",
    });
    env.CLIPS.delete.mockRejectedValue(new Error("private-r2-failure"));

    const response = await deleteClip(
      request,
      env as never,
      "clip-a",
      now,
      userA,
    );
    expect(response.status).toBe(204);
    expect(mocks.completeObjectDeletion).not.toHaveBeenCalled();

    mocks.requireOwnedClip.mockRejectedValue(notFound);
    await expect(
      readClip(request, env as never, "clip-a", now, userA),
    ).rejects.toBe(notFound);
    expect(env.CLIPS.get).not.toHaveBeenCalled();
  });

  it("never lets owner A delete owner B's clip and leaves B readable", async () => {
    const env = makeEnv();
    const notFound = Object.assign(new Error("not found"), {
      status: 404,
      code: "not_found",
    });
    mocks.deleteClipAndQueueObject.mockRejectedValueOnce(notFound);

    await expect(
      deleteClip(request, env as never, "clip-b", now, userA),
    ).rejects.toBe(notFound);
    expect(env.CLIPS.delete).not.toHaveBeenCalled();

    mocks.requireHome.mockResolvedValue({ id: "home-b" });
    mocks.requireOwnedClip.mockResolvedValue(
      ownedClip({
        id: "clip-b",
        homeId: "home-b",
        cameraId: "camera-b",
        objectKey: "clips/opaque-b.mp4",
      }),
    );
    env.CLIPS.get.mockResolvedValue({ body: "owner-b-video" });

    const response = await readClip(
      request,
      env as never,
      "clip-b",
      now,
      userB,
    );
    expect(response.status).toBe(200);
    expect(mocks.requireOwnedClip).toHaveBeenCalledWith(
      "home-b",
      "clip-b",
      now.toISOString(),
    );
    expect(await response.text()).toBe("owner-b-video");
  });
});
