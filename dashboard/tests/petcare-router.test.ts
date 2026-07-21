// @vitest-environment node

import { NextResponse } from "next/server";
import { beforeEach, describe, expect, it, vi } from "vitest";

const calls = vi.hoisted(() => ({
  account: vi.fn(),
  agentEnroll: vi.fn(),
  clipUpload: vi.fn(),
  deleteClip: vi.fn(),
  listClips: vi.fn(),
  mjpeg: vi.fn(),
  readClip: vi.fn(),
  session: vi.fn(),
  status: vi.fn(),
}));

vi.mock("../lib/auth/session", async (importOriginal) => {
  const original = await importOriginal<typeof import("../lib/auth/session")>();
  return { ...original, requireAuthSession: calls.session };
});
vi.mock("../lib/petcare/account-delete", () => ({
  deletePetCareAccountData: calls.account,
}));
vi.mock("../lib/petcare/agent-enroll", () => ({
  handleAgentEnroll: calls.agentEnroll,
}));
vi.mock("../lib/petcare/clip-upload", () => ({
  uploadSignedClip: calls.clipUpload,
}));
vi.mock("../lib/petcare/clips", () => ({
  deleteClip: calls.deleteClip,
  listClips: calls.listClips,
  readClip: calls.readClip,
}));
vi.mock("../lib/petcare/live-proxy", () => ({
  proxyMjpeg: calls.mjpeg,
  proxyStatus: calls.status,
}));

vi.mock("cloudflare:workers", () => ({ env: {} }));

import { AuthError } from "../lib/auth/require-auth";
import type { PetCareEnv } from "../lib/petcare/env";
import { routePetCare } from "../lib/petcare/router";

const user = { sub: "owner-a", email: "a@example.com" };
const env = {
  SUPABASE_URL: "https://project.supabase.co",
  SUPABASE_PUBLISHABLE_KEY: "publishable",
} as PetCareEnv;
const ctx = { waitUntil: vi.fn() };

function request(path: string, method = "GET", origin = true) {
  return new Request(`https://pets.example${path}`, {
    method,
    headers: origin ? { Origin: "https://pets.example" } : undefined,
  });
}

beforeEach(() => {
  vi.clearAllMocks();
  const ok = () => Promise.resolve(Response.json({ ok: true }));
  for (const handler of [
    calls.account,
    calls.agentEnroll,
    calls.clipUpload,
    calls.deleteClip,
    calls.listClips,
    calls.mjpeg,
    calls.readClip,
    calls.status,
  ]) {
    handler.mockImplementation(ok);
  }
  calls.session.mockResolvedValue({
    user,
    applySessionCookies(response: NextResponse) {
      response.headers.append(
        "Set-Cookie",
        "sb-refresh-token=rotated; Path=/; HttpOnly; Secure; SameSite=Lax",
      );
      return response;
    },
  });
});

describe("routePetCare", () => {
  it.each(["/", "/demo", "/demo-camera.webp", "/_vinext/image"])(
    "leaves %s network-free",
    async (path) => {
      await expect(routePetCare(request(path), env, ctx)).resolves.toBeNull();
      expect(calls.session).not.toHaveBeenCalled();
      expect(calls.status).not.toHaveBeenCalled();
    },
  );

  it("delegates the auth-owned enrollment route without authenticating", async () => {
    await expect(
      routePetCare(request("/api/petcare/enrollment", "POST"), env, ctx),
    ).resolves.toBeNull();
    expect(calls.session).not.toHaveBeenCalled();
  });

  it.each([
    ["/api/petcare/agent/enroll", calls.agentEnroll],
    ["/api/petcare/agent/clips", calls.clipUpload],
  ])("keeps public agent route %s Supabase-independent", async (path, handler) => {
    const response = await routePetCare(request(path, "POST"), env, ctx);
    expect(response?.status).toBe(200);
    expect(handler).toHaveBeenCalledOnce();
    expect(calls.session).not.toHaveBeenCalled();
  });

  it("authenticates a browser route once and propagates refreshed cookies", async () => {
    const raw = request("/api/petcare/status");
    const response = await routePetCare(raw, env, ctx);

    expect(calls.session).toHaveBeenCalledOnce();
    expect(calls.status).toHaveBeenCalledWith(user, env, expect.any(Date));
    expect(response?.headers.get("Set-Cookie")).toContain("sb-refresh-token=rotated");
  });

  it.each([
    ["/api/petcare/cameras/camera-a/stream.mjpeg", calls.mjpeg],
    ["/api/petcare/clips", calls.listClips],
    ["/api/petcare/clips/clip-a.mp4", calls.readClip],
    ["/api/petcare/clips/clip-a", calls.deleteClip, "DELETE"],
    ["/api/petcare/account", calls.account, "DELETE"],
  ])("passes one verified user to %s", async (path, handler, method = "GET") => {
    const response = await routePetCare(request(path, method), env, ctx);
    expect(response?.status).toBe(200);
    expect(calls.session).toHaveBeenCalledOnce();
    expect(handler).toHaveBeenCalledOnce();
    expect(handler.mock.calls[0]).toContain(user);
  });

  it("rejects cross-origin mutation before JWT validation", async () => {
    const response = await routePetCare(
      request("/api/petcare/account", "DELETE", false),
      env,
      ctx,
    );
    expect(response?.status).toBe(403);
    await expect(response?.json()).resolves.toEqual({ error: "csrf" });
    expect(calls.session).not.toHaveBeenCalled();
    expect(calls.account).not.toHaveBeenCalled();
  });

  it("returns generic 401 without refresh cookies when authentication fails", async () => {
    const log = vi.spyOn(console, "error").mockImplementation(() => undefined);
    calls.session.mockRejectedValue(new AuthError("secret-cookie"));
    const response = await routePetCare(request("/api/petcare/clips"), env, ctx);
    expect(response?.status).toBe(401);
    await expect(response?.json()).resolves.toEqual({ error: "unauthorized" });
    expect(response?.headers.get("Set-Cookie")).toBeNull();
    expect(JSON.stringify(log.mock.calls)).not.toContain("secret-cookie");
  });

  it("applies refreshed cookies to a safe authenticated handler error", async () => {
    const log = vi.spyOn(console, "error").mockImplementation(() => undefined);
    calls.listClips.mockRejectedValue(
      new Error("cookie signature nonce digest tunnel-origin clips/private.mp4"),
    );
    const response = await routePetCare(request("/api/petcare/clips"), env, ctx);
    const text = await response!.text();

    expect(response?.status).toBe(500);
    expect(text).toBe('{"error":"internal_error"}');
    expect(response?.headers.get("Set-Cookie")).toContain("sb-refresh-token=rotated");
    expect(JSON.stringify(log.mock.calls)).not.toMatch(
      /cookie signature nonce digest tunnel-origin|clips\/private\.mp4/,
    );
  });

  it.each([
    ["/api/petcare/status", "POST", "GET"],
    ["/api/petcare/clips", "POST", "GET"],
    ["/api/petcare/account", "GET", "DELETE"],
    ["/api/petcare/agent/enroll", "GET", "POST"],
  ])("closes method %s %s", async (path, method, allow) => {
    const response = await routePetCare(request(path, method), env, ctx);
    expect(response?.status).toBe(405);
    expect(response?.headers.get("Allow")).toBe(allow);
    expect(calls.session).not.toHaveBeenCalled();
  });

  it.each([
    "/api/petcare/cameras/a%2Fb/stream.mjpeg",
    "/api/petcare/cameras/../camera-a/stream.mjpeg",
    `/api/petcare/clips/${"a".repeat(65)}.mp4`,
    "/api/petcare/clips/a%00.mp4",
    "/api/petcare/clips/a%2Fb",
  ])("rejects malformed selector %s before auth or I/O", async (path) => {
    const response = await routePetCare(request(path), env, ctx);
    expect(response?.status).toBe(404);
    expect(calls.session).not.toHaveBeenCalled();
  });
});
