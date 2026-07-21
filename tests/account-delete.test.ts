// @vitest-environment node

import { beforeEach, describe, expect, it, vi } from "vitest";
import { RecentReauthError } from "../lib/auth/recent-reauth";
import { errorResponse } from "../lib/petcare/errors";
import { deletePetCareAccountData } from "../lib/petcare/account-delete";
import type { PetCareEnv } from "../lib/petcare/env";

const mocks = vi.hoisted(() => ({
  order: [] as string[],
  beginResult: { homeId: "home-a", status: "cleanup_pending" } as
    | { homeId: string; status: "cleanup_pending" }
    | { status: "absent" },
  requireSameOrigin: vi.fn(),
  requireRecentPassword: vi.fn(),
  beginTenantCleanup: vi.fn(),
}));

vi.mock("cloudflare:workers", () => ({ env: {} }));

vi.mock("../lib/auth/session", async (importOriginal) => ({
  ...(await importOriginal<typeof import("../lib/auth/session")>()),
  requireSameOrigin: mocks.requireSameOrigin,
}));

vi.mock("../lib/auth/recent-reauth", async (importOriginal) => ({
  ...(await importOriginal<typeof import("../lib/auth/recent-reauth")>()),
  requireRecentPassword: mocks.requireRecentPassword,
}));

vi.mock("../lib/petcare/repository", () => ({
  PetCareRepository: class {
    beginTenantCleanup(ownerSub: string, now: string) {
      return mocks.beginTenantCleanup(ownerSub, now);
    }
  },
}));

const NOW = new Date("2026-07-20T09:00:00.000Z");
const USER = { sub: "owner-a", email: "owner@example.com" };
const PASSWORD = "password-sentinel";
const COOKIE = "cookie-sentinel";
const PROVIDER_ERROR = "provider-error-sentinel";

const env = {
  DB: {},
  CLIPS: {
    delete: vi.fn(() => {
      throw new Error("R2 must not be called inline");
    }),
  },
  SUPABASE_URL: "https://example.supabase.co",
  SUPABASE_PUBLISHABLE_KEY: "publishable",
  CF_ACCOUNT_ID: "account",
  CF_ZONE_ID: "zone",
  CF_ZONE_NAME: "agents.example.com",
  CF_ACCESS_TEAM_NAME: "team",
  CF_TUNNEL_API_TOKEN: "cloudflare-token-sentinel",
  CF_ACCESS_SERVICE_TOKEN_ID: "service-token",
  CF_ACCESS_CLIENT_ID: "access-client",
  CF_ACCESS_CLIENT_SECRET: "access-secret-sentinel",
} as unknown as PetCareEnv;

function request(origin = "https://pets.example.com") {
  return new Request("https://pets.example.com/api/petcare/account", {
    method: "DELETE",
    headers: {
      "Content-Type": "application/json",
      Cookie: `sb-session=${COOKIE}`,
      Origin: origin,
    },
    body: JSON.stringify({ currentPassword: PASSWORD, owner_sub: "owner-b" }),
  });
}

async function routedDelete(input: Request) {
  try {
    return await deletePetCareAccountData(input, env, NOW, USER);
  } catch (error) {
    return errorResponse(error);
  }
}

beforeEach(() => {
  vi.clearAllMocks();
  mocks.order.length = 0;
  mocks.beginResult = { homeId: "home-a", status: "cleanup_pending" };
  mocks.requireSameOrigin.mockImplementation(() => {
    mocks.order.push("csrf");
  });
  mocks.requireRecentPassword.mockImplementation(async (input, authEnv, user) => {
    mocks.order.push("reauth");
    expect(authEnv).toBe(env);
    expect(user).toEqual(USER);
    expect(await input.clone().json()).toEqual({
      currentPassword: PASSWORD,
      owner_sub: "owner-b",
    });
  });
  mocks.beginTenantCleanup.mockImplementation(
    async (ownerSub: string, now: string) => {
      mocks.order.push("logical-delete");
      expect(ownerSub).toBe("owner-a");
      expect(now).toBe(NOW.toISOString());
      return mocks.beginResult;
    },
  );
});

describe("deletePetCareAccountData", () => {
  it("runs CSRF, password reauthentication, then authenticated-owner cleanup", async () => {
    const response = await routedDelete(request());

    expect(mocks.order).toEqual(["csrf", "reauth", "logical-delete"]);
    expect(response.status).toBe(202);
    expect(response.headers.get("Cache-Control")).toBe("private, no-store");
    await expect(response.json()).resolves.toEqual({ status: "cleanup_pending" });
    expect(JSON.stringify(mocks.beginTenantCleanup.mock.calls)).not.toContain(
      PASSWORD,
    );
    expect(env.CLIPS.delete).not.toHaveBeenCalled();
  });

  it("rejects CSRF before password parsing or mutation", async () => {
    mocks.requireSameOrigin.mockImplementationOnce(() => {
      mocks.order.push("csrf");
      throw new Error(`bad origin ${PROVIDER_ERROR}`);
    });

    const response = await routedDelete(request("https://evil.example"));

    expect(response.status).toBe(403);
    await expect(response.json()).resolves.toEqual({ error: "csrf" });
    expect(mocks.order).toEqual(["csrf"]);
    expect(mocks.requireRecentPassword).not.toHaveBeenCalled();
    expect(mocks.beginTenantCleanup).not.toHaveBeenCalled();
  });

  it.each([
    [401, "reauthentication_failed"],
    [429, "rate_limited"],
    [503, "auth_unavailable"],
  ] as const)(
    "maps recent-password failure %i to generic %s",
    async (status, code) => {
      mocks.requireRecentPassword.mockImplementationOnce(async () => {
        mocks.order.push("reauth");
        const error = new RecentReauthError(status, code);
        error.message = `${code}:${PASSWORD}:${PROVIDER_ERROR}`;
        throw error;
      });

      const response = await routedDelete(request());
      const body = await response.text();

      expect(response.status).toBe(status);
      expect(body).toBe(JSON.stringify({ error: code }));
      expect(body).not.toContain(PASSWORD);
      expect(body).not.toContain(PROVIDER_ERROR);
      expect(mocks.order).toEqual(["csrf", "reauth"]);
      expect(mocks.beginTenantCleanup).not.toHaveBeenCalled();
    },
  );

  it("keeps pending retries idempotent at the handler boundary", async () => {
    const first = await routedDelete(request());
    const retry = await routedDelete(request());

    expect(first.status).toBe(202);
    expect(retry.status).toBe(202);
    expect(mocks.beginTenantCleanup).toHaveBeenCalledTimes(2);
    expect(env.CLIPS.delete).not.toHaveBeenCalled();
  });

  it("returns an empty 204 after cleanup has completed", async () => {
    mocks.beginResult = { status: "absent" };

    const response = await routedDelete(request());

    expect(response.status).toBe(204);
    expect(response.headers.get("Cache-Control")).toBe("private, no-store");
    expect(await response.text()).toBe("");
  });

  it("redacts unexpected repository failures", async () => {
    mocks.beginTenantCleanup.mockRejectedValueOnce(
      new Error(`${PASSWORD}:${COOKIE}:${PROVIDER_ERROR}`),
    );

    const response = await routedDelete(request());
    const body = await response.text();

    expect(response.status).toBe(500);
    expect(body).toBe(JSON.stringify({ error: "internal_error" }));
    expect(body).not.toContain(PASSWORD);
    expect(body).not.toContain(COOKIE);
    expect(body).not.toContain(PROVIDER_ERROR);
  });
});
