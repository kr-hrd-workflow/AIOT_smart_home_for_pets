// @vitest-environment node

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("../db", () => ({ getDb: vi.fn(() => ({ db: "test" })) }));
vi.mock("cloudflare:workers", () => ({ env: {} }));

import type { AuthUser } from "../lib/auth/require-auth";
import { TenantRepository } from "../lib/tenancy/repository";
import type { PetCareEnv } from "../lib/petcare/env";
import { proxyMjpeg, proxyStatus } from "../lib/petcare/live-proxy";
import {
  PetCareRepository,
  type ActiveRoute,
} from "../lib/petcare/repository";
import { demoDashboardData } from "../lib/demo-data";
import { FakeD1 } from "./helpers/petcare-fakes";

const user: AuthUser = { sub: "owner-a", email: "owner-a@example.com" };
const home = {
  id: "home-a",
  ownerSub: user.sub,
  createdAt: "2026-07-20T00:00:00.000Z",
  deletedAt: null,
};
const route: ActiveRoute = {
  homeId: home.id,
  agentId: "agent-a",
  cameraId: "camera-a",
  tunnelOrigin: "https://home-a.agents.example.com",
  publicKey: "public-key",
  lastSeenAt: "2026-07-20T00:59:00.000Z",
};
const now = new Date("2026-07-20T01:00:00.000Z");
const env = {
  DB: {} as PetCareEnv["DB"],
  CLIPS: {} as PetCareEnv["CLIPS"],
  SUPABASE_URL: "https://project.supabase.co",
  SUPABASE_PUBLISHABLE_KEY: "publishable",
  CF_ACCOUNT_ID: "account",
  CF_ZONE_ID: "zone",
  CF_ZONE_NAME: "agents.example.com",
  CF_ACCESS_TEAM_NAME: "team",
  CF_TUNNEL_API_TOKEN: "api-token-secret",
  CF_ACCESS_SERVICE_TOKEN_ID: "service-token",
  CF_ACCESS_CLIENT_ID: "access-client-id-secret",
  CF_ACCESS_CLIENT_SECRET: "access-client-secret",
} satisfies PetCareEnv;
const summary = Object.fromEntries(
  Object.entries(demoDashboardData).filter(
    ([key]) => key !== "zones" && key !== "calibration",
  ),
);

function ready(activeRoute = route, revoked = false) {
  return { state: "ready" as const, route: activeRoute, revoked };
}

function jsonUpstream(body: unknown = summary, status = 200) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json; charset=utf-8" },
  });
}

describe("tenant-scoped live proxy", () => {
  beforeEach(() => {
    vi.spyOn(TenantRepository.prototype, "requireHome").mockResolvedValue(home);
    vi.spyOn(PetCareRepository.prototype, "getHomeConnection").mockResolvedValue(
      ready(),
    );
    vi.spyOn(PetCareRepository.prototype, "requireActiveRoute").mockResolvedValue(
      route,
    );
    vi.spyOn(
      PetCareRepository.prototype,
      "requireActivationRoute",
    ).mockResolvedValue(route);
    vi.spyOn(PetCareRepository.prototype, "markAgentSeen").mockResolvedValue();
  });

  afterEach(() => {
    vi.useRealTimers();
    vi.restoreAllMocks();
    vi.unstubAllGlobals();
  });

  it("keeps the exact eight-key Task 12 dashboard summary contract", () => {
    expect(Object.keys(summary)).toEqual([
      "generated_at",
      "health",
      "devices",
      "latest_sensors",
      "camera",
      "bed",
      "behaviors",
      "anomalies",
    ]);
  });

  it("resolves only the authenticated owner's home and allowlisted status URL", async () => {
    const fetchMock = vi.fn().mockResolvedValue(jsonUpstream());
    vi.stubGlobal("fetch", fetchMock);

    const response = await proxyStatus(user, env, now);

    expect(TenantRepository.prototype.requireHome).toHaveBeenCalledWith("owner-a");
    expect(
      PetCareRepository.prototype.requireActivationRoute,
    ).toHaveBeenCalledWith("home-a", now.toISOString());
    expect(PetCareRepository.prototype.requireActiveRoute).not.toHaveBeenCalled();
    expect(fetchMock).toHaveBeenCalledOnce();
    const [url, init] = fetchMock.mock.calls[0] as [URL, RequestInit];
    expect(url.href).toBe(
      "https://home-a.agents.example.com/api/dashboard/summary",
    );
    expect(init.method).toBe("GET");
    expect([...new Headers(init.headers).entries()]).toEqual([
      ["cf-access-client-id", "access-client-id-secret"],
      ["cf-access-client-secret", "access-client-secret"],
    ]);
    expect(await response.json()).toEqual({
      home: { id: "home-a", state: "ready" },
      agent: { id: "agent-a", state: "online", last_seen_at: now.toISOString() },
      camera: {
        id: "camera-a",
        state: "online",
        last_seen_at: now.toISOString(),
      },
      dashboard: summary,
    });
    expect(PetCareRepository.prototype.markAgentSeen).toHaveBeenCalledWith(
      "agent-a",
      now.toISOString(),
    );
    expect(response.headers.get("Cache-Control")).toBe(
      "private, no-store, no-transform",
    );
    expect(response.headers.get("Pragma")).toBe("no-cache");
    expect(response.headers.get("X-Content-Type-Options")).toBe("nosniff");
  });

  it("returns needs_enrollment without an upstream request", async () => {
    vi.mocked(PetCareRepository.prototype.getHomeConnection).mockResolvedValue({
      state: "needs_enrollment",
    });
    const fetchMock = vi.fn();
    vi.stubGlobal("fetch", fetchMock);

    const response = await proxyStatus(user, env, now);

    expect(response.status).toBe(200);
    expect(await response.json()).toEqual({
      home: { id: "home-a", state: "needs_enrollment" },
      agent: null,
      camera: null,
      dashboard: null,
    });
    expect(fetchMock).not.toHaveBeenCalled();
    expect(PetCareRepository.prototype.requireActiveRoute).not.toHaveBeenCalled();
    expect(
      PetCareRepository.prototype.requireActivationRoute,
    ).not.toHaveBeenCalled();
  });

  it.each([
    ["non-200", () => jsonUpstream({ secret: "upstream" }, 401)],
    [
      "wrong content type",
      () => new Response(JSON.stringify(summary), { headers: { "Content-Type": "text/plain" } }),
    ],
    [
      "JSON prefix content type",
      () => new Response(JSON.stringify(summary), { headers: { "Content-Type": "application/jsonp" } }),
    ],
    [
      "malformed JSON",
      () => new Response("{", { headers: { "Content-Type": "application/json" } }),
    ],
    ["wrong summary shape", () => jsonUpstream({ ...summary, extra: true })],
  ])("maps %s status responses to redacted offline state", async (_name, upstream) => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(upstream()));

    const response = await proxyStatus(user, env, now);
    const text = await response.text();

    expect(response.status).toBe(503);
    expect(JSON.parse(text)).toEqual({
      code: "agent_offline",
      agent_id: "agent-a",
      camera_id: "camera-a",
      last_seen_at: route.lastSeenAt,
    });
    expect(PetCareRepository.prototype.markAgentSeen).not.toHaveBeenCalled();
    for (const secret of [
      route.tunnelOrigin,
      env.CF_ACCESS_CLIENT_ID,
      env.CF_ACCESS_CLIENT_SECRET,
    ]) {
      expect(text).not.toContain(secret);
      expect([...response.headers]).not.toContainEqual(expect.arrayContaining([expect.stringContaining(secret)]));
    }
  });

  it("uses a 2000ms status deadline and redacts fetch failures", async () => {
    const controller = new AbortController();
    const timeout = vi
      .spyOn(AbortSignal, "timeout")
      .mockReturnValue(controller.signal);
    vi.stubGlobal(
      "fetch",
      vi.fn((_url: URL, init: RequestInit) =>
        new Promise<Response>((_resolve, reject) => {
          init.signal?.addEventListener("abort", () => reject(new Error("origin secret")));
        }),
      ),
    );

    const pending = proxyStatus(user, env, now);
    await vi.waitFor(() => expect(timeout).toHaveBeenCalledWith(2_000));
    controller.abort();
    const response = await pending;

    expect(response.status).toBe(503);
    expect(await response.json()).toEqual({
      code: "agent_offline",
      agent_id: "agent-a",
      camera_id: "camera-a",
      last_seen_at: route.lastSeenAt,
    });
    expect(PetCareRepository.prototype.markAgentSeen).not.toHaveBeenCalled();
  });

  it("returns revoked or malformed routes as offline without leaking them", async () => {
    vi.mocked(PetCareRepository.prototype.getHomeConnection).mockResolvedValue(
      ready(route, true),
    );
    const fetchMock = vi.fn();
    vi.stubGlobal("fetch", fetchMock);

    const revoked = await proxyStatus(user, env, now);
    expect(revoked.status).toBe(503);
    expect(fetchMock).not.toHaveBeenCalled();

    vi.mocked(PetCareRepository.prototype.getHomeConnection).mockResolvedValue(
      ready({ ...route, tunnelOrigin: "http://127.0.0.1:8000" }),
    );
    vi.mocked(PetCareRepository.prototype.requireActivationRoute).mockResolvedValue({
      ...route,
      tunnelOrigin: "http://127.0.0.1:8000",
    });
    const malformed = await proxyStatus(user, env, now);
    const text = await malformed.text();
    expect(malformed.status).toBe(503);
    expect(text).not.toContain("127.0.0.1");
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("rejects a foreign camera before any upstream request", async () => {
    const fetchMock = vi.fn();
    vi.stubGlobal("fetch", fetchMock);

    await expect(proxyMjpeg(user, env, "camera-b")).rejects.toMatchObject({
      status: 404,
      code: "not_found",
    });
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("rechecks the camera after active-route resolution", async () => {
    vi.mocked(PetCareRepository.prototype.requireActiveRoute).mockResolvedValue({
      ...route,
      cameraId: "camera-b",
    });
    const fetchMock = vi.fn();
    vi.stubGlobal("fetch", fetchMock);

    await expect(proxyMjpeg(user, env, "camera-a")).rejects.toMatchObject({
      status: 404,
      code: "not_found",
    });
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("streams only the allowlisted MJPEG URL, clears its header timeout, and cancels upstream", async () => {
    vi.useFakeTimers();
    const headerTimeout = vi.spyOn(globalThis, "setTimeout");
    const cancel = vi.fn();
    const upstreamBody = new ReadableStream<Uint8Array>({
      start(controller) {
        controller.enqueue(new TextEncoder().encode("--frame\r\n"));
      },
      cancel,
    });
    let signal: AbortSignal | null | undefined;
    const fetchMock = vi.fn((_url: URL, init: RequestInit) => {
      signal = init.signal;
      return Promise.resolve(
        new Response(upstreamBody, {
          headers: {
            "Content-Type": "multipart/x-mixed-replace; boundary=frame",
            "Cache-Control": "public, max-age=3600",
            "X-Upstream-Origin": route.tunnelOrigin,
          },
        }),
      );
    });
    vi.stubGlobal("fetch", fetchMock);

    const response = await proxyMjpeg(user, env, "camera-a");
    const [url, init] = fetchMock.mock.calls[0] as [URL, RequestInit];
    expect(url.href).toBe("https://home-a.agents.example.com/api/video_feed");
    expect([...new Headers(init.headers).entries()]).toEqual([
      ["cf-access-client-id", "access-client-id-secret"],
      ["cf-access-client-secret", "access-client-secret"],
    ]);
    expect(
      PetCareRepository.prototype.requireActivationRoute,
    ).not.toHaveBeenCalled();
    expect(headerTimeout).toHaveBeenCalledWith(expect.any(Function), 2_000);
    await vi.advanceTimersByTimeAsync(5_000);
    expect(signal?.aborted).toBe(false);
    expect(response.headers.get("Content-Type")).toBe(
      "multipart/x-mixed-replace; boundary=frame",
    );
    expect(response.headers.get("Cache-Control")).toBe(
      "private, no-store, no-transform",
    );
    expect(response.headers.get("X-Upstream-Origin")).toBeNull();
    await response.body?.cancel("browser closed");
    expect(cancel).toHaveBeenCalledWith("browser closed");
  });

  it("starts the MJPEG header deadline after tenant route resolution", async () => {
    vi.useFakeTimers();
    let resolveRoute!: (value: ActiveRoute) => void;
    vi.mocked(PetCareRepository.prototype.requireActiveRoute).mockReturnValue(
      new Promise((resolve) => {
        resolveRoute = resolve;
      }),
    );
    vi.stubGlobal(
      "fetch",
      vi.fn((_url: URL, init: RequestInit) => {
        if (init.signal?.aborted) return Promise.reject(new Error("premature timeout"));
        return Promise.resolve(
          new Response("--frame\r\n", {
            headers: {
              "Content-Type": "multipart/x-mixed-replace; boundary=frame",
            },
          }),
        );
      }),
    );

    const pending = proxyMjpeg(user, env, "camera-a");
    for (let tick = 0; tick < 5; tick += 1) await Promise.resolve();
    expect(PetCareRepository.prototype.requireActiveRoute).toHaveBeenCalledOnce();
    await vi.advanceTimersByTimeAsync(2_500);
    resolveRoute(route);

    expect((await pending).status).toBe(200);
  });

  it("aborts MJPEG header establishment at exactly 2000ms", async () => {
    vi.useFakeTimers();
    let signal: AbortSignal | null | undefined;
    vi.stubGlobal(
      "fetch",
      vi.fn((_url: URL, init: RequestInit) => {
        signal = init.signal;
        return new Promise<Response>((_resolve, reject) => {
          init.signal?.addEventListener("abort", () => reject(new Error("timed out")));
        });
      }),
    );

    const pending = proxyMjpeg(user, env, "camera-a");
    for (let tick = 0; tick < 5; tick += 1) await Promise.resolve();
    expect(signal?.aborted).toBe(false);
    await vi.advanceTimersByTimeAsync(1_999);
    expect(signal?.aborted).toBe(false);
    await vi.advanceTimersByTimeAsync(1);
    expect(signal?.aborted).toBe(true);
    expect((await pending).status).toBe(503);
  });

  it.each([
    [401, "multipart/x-mixed-replace; boundary=frame"],
    [200, "application/json"],
    [200, "multipart/x-mixed-replaceevil; boundary=frame"],
    [200, "multipart/x-mixed-replace; boundary=frame; token=secret-sentinel"],
  ])("maps bad MJPEG status/content type to offline", async (status, contentType) => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        new Response("untrusted", {
          status,
          headers: { "Content-Type": contentType },
        }),
      ),
    );

    const response = await proxyMjpeg(user, env, "camera-a");
    expect(response.status).toBe(503);
    expect(await response.json()).toEqual({
      code: "agent_offline",
      agent_id: "agent-a",
      camera_id: "camera-a",
      last_seen_at: route.lastSeenAt,
    });
  });

  it("cancels rejected status and MJPEG upstream bodies", async () => {
    const statusCancel = vi.fn();
    const mjpegCancel = vi.fn();
    vi.stubGlobal(
      "fetch",
      vi.fn()
        .mockResolvedValueOnce(
          new Response(new ReadableStream({ cancel: statusCancel }), {
            status: 401,
            headers: { "Content-Type": "application/json" },
          }),
        )
        .mockResolvedValueOnce(
          new Response(new ReadableStream({ cancel: mjpegCancel }), {
            headers: { "Content-Type": "application/json" },
          }),
        ),
    );

    expect((await proxyStatus(user, env, now)).status).toBe(503);
    expect(statusCancel).toHaveBeenCalledOnce();
    expect((await proxyMjpeg(user, env, "camera-a")).status).toBe(503);
    expect(mjpegCancel).toHaveBeenCalledOnce();
  });

  it("promotes a live activation-pending route after a valid summary", async () => {
    vi.mocked(PetCareRepository.prototype.getHomeConnection).mockRestore();
    vi.mocked(PetCareRepository.prototype.requireActivationRoute).mockRestore();
    vi.mocked(PetCareRepository.prototype.markAgentSeen).mockRestore();
    const fake = new FakeD1();
    const db = fake as unknown as PetCareEnv["DB"];
    const run = (sql: string, ...values: unknown[]) =>
      db.prepare(sql).bind(...values).run();
    try {
      await run(
        "INSERT INTO homes (id, owner_sub, created_at) VALUES (?, ?, ?)",
        home.id,
        user.sub,
        now.toISOString(),
      );
      await run(
        "INSERT INTO agents (id, home_id, public_key, tunnel_origin) VALUES (?, ?, ?, ?)",
        route.agentId,
        home.id,
        route.publicKey,
        route.tunnelOrigin,
      );
      await run(
        "INSERT INTO cameras (id, home_id, agent_id, local_camera_id, created_at) VALUES (?, ?, ?, ?, ?)",
        route.cameraId,
        home.id,
        route.agentId,
        "pc-webcam-01",
        now.toISOString(),
      );
      await run(
        "INSERT INTO tunnel_routes (home_id, agent_id, tunnel_origin, status, activation_expires_at, created_at, updated_at) VALUES (?, ?, ?, 'activation_pending', ?, ?, ?)",
        home.id,
        route.agentId,
        route.tunnelOrigin,
        "2099-07-20T01:10:00.000Z",
        now.toISOString(),
        now.toISOString(),
      );
      vi.stubGlobal("fetch", vi.fn().mockResolvedValue(jsonUpstream()));

      const response = await proxyStatus(user, { ...env, DB: db }, now);

      expect(response.status).toBe(200);
      expect(fake.rows.tunnel_routes[0]).toMatchObject({
        status: "active",
        activation_expires_at: null,
      });
      expect(fake.rows.agents[0]).toMatchObject({
        last_seen_at: now.toISOString(),
      });
    } finally {
      fake.dispose();
    }
  });

  it("keeps an expired activation-pending route offline and unpromoted", async () => {
    vi.mocked(PetCareRepository.prototype.getHomeConnection).mockRestore();
    vi.mocked(PetCareRepository.prototype.requireActivationRoute).mockRestore();
    vi.mocked(PetCareRepository.prototype.markAgentSeen).mockRestore();
    const fake = new FakeD1();
    const db = fake as unknown as PetCareEnv["DB"];
    const run = (sql: string, ...values: unknown[]) =>
      db.prepare(sql).bind(...values).run();
    const fetchMock = vi.fn();
    try {
      await run(
        "INSERT INTO homes (id, owner_sub, created_at) VALUES (?, ?, ?)",
        home.id,
        user.sub,
        now.toISOString(),
      );
      await run(
        "INSERT INTO agents (id, home_id, public_key, tunnel_origin) VALUES (?, ?, ?, ?)",
        route.agentId,
        home.id,
        route.publicKey,
        route.tunnelOrigin,
      );
      await run(
        "INSERT INTO cameras (id, home_id, agent_id, local_camera_id, created_at) VALUES (?, ?, ?, ?, ?)",
        route.cameraId,
        home.id,
        route.agentId,
        "pc-webcam-01",
        now.toISOString(),
      );
      await run(
        "INSERT INTO tunnel_routes (home_id, agent_id, tunnel_origin, status, activation_expires_at, created_at, updated_at) VALUES (?, ?, ?, 'activation_pending', ?, ?, ?)",
        home.id,
        route.agentId,
        route.tunnelOrigin,
        "2000-01-01T00:00:00.000Z",
        now.toISOString(),
        now.toISOString(),
      );
      vi.stubGlobal("fetch", fetchMock);

      const response = await proxyStatus(user, { ...env, DB: db }, now);

      expect(response.status).toBe(503);
      expect(fetchMock).not.toHaveBeenCalled();
      expect(fake.rows.tunnel_routes[0]).toMatchObject({
        status: "activation_pending",
        activation_expires_at: "2000-01-01T00:00:00.000Z",
      });
      expect(fake.rows.agents[0]).toMatchObject({ last_seen_at: null });
    } finally {
      fake.dispose();
    }
  });

  it("maps MJPEG fetch failure to offline without exposing the error", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockRejectedValue(new Error("https://secret-origin.invalid credential")),
    );

    const response = await proxyMjpeg(user, env, "camera-a");
    const text = await response.text();
    expect(response.status).toBe(503);
    expect(text).not.toContain("secret-origin");
    expect(JSON.parse(text)).toEqual({
      code: "agent_offline",
      agent_id: "agent-a",
      camera_id: "camera-a",
      last_seen_at: route.lastSeenAt,
    });
  });
});
