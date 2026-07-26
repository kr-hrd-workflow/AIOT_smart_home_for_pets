// @vitest-environment node

import { beforeEach, expect, it, vi } from "vitest";

const mocks = vi.hoisted(() => ({
  requireAuth: vi.fn(),
  issueEnrollment: vi.fn(),
}));

const runtimeEnv = vi.hoisted(() => ({
  SUPABASE_URL: "https://project-ref.supabase.co",
  SUPABASE_PUBLISHABLE_KEY: "sb_publishable_test",
  CF_ACCOUNT_ID: "account",
  CF_ZONE_ID: "zone",
  CF_ZONE_NAME: "pets.example",
  CF_ACCESS_TEAM_NAME: "petcare",
  CF_TUNNEL_API_TOKEN: "api-token",
  CF_ACCESS_SERVICE_TOKEN_ID: "service-token",
  CF_ACCESS_CLIENT_ID: "access-client",
  CF_ACCESS_CLIENT_SECRET: "access-secret",
}));

vi.mock("cloudflare:workers", () => ({
  env: runtimeEnv,
}));
vi.mock("../../lib/auth/require-auth", () => ({
  AuthError: class AuthError extends Error {
    readonly status = 401;
    readonly code = "unauthorized";
  },
  requireAuth: mocks.requireAuth,
}));
vi.mock("../../lib/tenancy/enrollment", () => ({
  issueEnrollment: mocks.issueEnrollment,
}));
vi.mock("../../lib/tenancy/repository", () => ({
  TenantNotFoundError: class TenantNotFoundError extends Error {
    readonly status = 404;
    readonly code = "home_not_found";
  },
}));

import { POST } from "../../app/api/petcare/enrollment/route";
import { AuthError } from "../../lib/auth/require-auth";
import { TenantNotFoundError } from "../../lib/tenancy/repository";

beforeEach(() => {
  vi.clearAllMocks();
  Object.assign(runtimeEnv, {
    CF_ACCOUNT_ID: "account",
    CF_ZONE_ID: "zone",
    CF_ZONE_NAME: "pets.example",
    CF_ACCESS_TEAM_NAME: "petcare",
    CF_TUNNEL_API_TOKEN: "api-token",
    CF_ACCESS_SERVICE_TOKEN_ID: "service-token",
    CF_ACCESS_CLIENT_ID: "access-client",
    CF_ACCESS_CLIENT_SECRET: "access-secret",
  });
});

it.each(["owner-a", "owner-b"])(
  "issues only for verified subject %s",
  async (ownerSub) => {
    mocks.requireAuth.mockResolvedValue({
      sub: ownerSub,
      email: `${ownerSub}@example.com`,
    });
    mocks.issueEnrollment.mockResolvedValue({
      code: `code-${ownerSub}`,
      expiresAt: "2026-07-20T03:10:00.000Z",
    });

    const response = await POST(
      new Request("https://app.test/api/petcare/enrollment", {
        method: "POST",
        headers: {
          origin: "https://app.test",
          "content-type": "application/json",
        },
        body: JSON.stringify({
          owner_sub: "attacker",
          home_id: "foreign-home",
        }),
      }),
    );

    expect(response.status).toBe(201);
    await expect(response.json()).resolves.toEqual({
      code: `code-${ownerSub}`,
      expiresAt: "2026-07-20T03:10:00.000Z",
    });
    expect(mocks.requireAuth).toHaveBeenCalledTimes(1);
    expect(mocks.issueEnrollment).toHaveBeenCalledTimes(1);
    expect(mocks.issueEnrollment).toHaveBeenCalledWith(ownerSub);
    expect(mocks.issueEnrollment).not.toHaveBeenCalledWith("attacker");
    expect(response.headers.get("cache-control")).toBe("private, no-store");
  },
);

it("returns 401 before issuing for an anonymous request", async () => {
  mocks.requireAuth.mockRejectedValue(new AuthError("Authentication required"));

  const response = await POST(
    new Request("https://app.test/api/petcare/enrollment", {
      method: "POST",
      headers: { origin: "https://app.test" },
    }),
  );

  expect(response.status).toBe(401);
  await expect(response.json()).resolves.toEqual({ error: "unauthorized" });
  expect(mocks.issueEnrollment).not.toHaveBeenCalled();
});

it("returns 404 for a subject without an active home", async () => {
  mocks.requireAuth.mockResolvedValue({ sub: "owner-a", email: null });
  mocks.issueEnrollment.mockRejectedValue(
    new TenantNotFoundError("Active home not found"),
  );

  const response = await POST(
    new Request("https://app.test/api/petcare/enrollment", {
      method: "POST",
      headers: { origin: "https://app.test" },
    }),
  );

  expect(response.status).toBe(404);
  await expect(response.json()).resolves.toEqual({ error: "not_found" });
});

it("fails before issuing a code when the managed tunnel runtime is unavailable", async () => {
  mocks.requireAuth.mockResolvedValue({ sub: "owner-a", email: null });
  runtimeEnv.CF_ZONE_ID = "";

  const response = await POST(
    new Request("https://app.test/api/petcare/enrollment", {
      method: "POST",
      headers: { origin: "https://app.test" },
    }),
  );

  expect(response.status).toBe(503);
  await expect(response.json()).resolves.toEqual({
    error: "enrollment_unavailable",
  });
  expect(mocks.issueEnrollment).not.toHaveBeenCalled();
});

it("rejects cross-origin issuance before auth or D1", async () => {
  const response = await POST(
    new Request("https://app.test/api/petcare/enrollment", {
      method: "POST",
      headers: { origin: "https://evil.test" },
    }),
  );

  expect(response.status).toBe(403);
  expect(mocks.requireAuth).not.toHaveBeenCalled();
  expect(mocks.issueEnrollment).not.toHaveBeenCalled();
});
