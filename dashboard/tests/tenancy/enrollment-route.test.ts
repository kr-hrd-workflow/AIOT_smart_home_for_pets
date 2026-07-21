// @vitest-environment node

import { beforeEach, expect, it, vi } from "vitest";

const mocks = vi.hoisted(() => ({
  requireAuth: vi.fn(),
  issueEnrollment: vi.fn(),
}));

vi.mock("cloudflare:workers", () => ({
  env: {
    SUPABASE_URL: "https://project-ref.supabase.co",
    SUPABASE_PUBLISHABLE_KEY: "sb_publishable_test",
  },
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

beforeEach(() => vi.clearAllMocks());

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
