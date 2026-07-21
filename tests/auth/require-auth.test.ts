// @vitest-environment node

import { NextRequest, NextResponse } from "next/server";
import { beforeEach, expect, it, vi } from "vitest";

const { createServerClient, getClaims } = vi.hoisted(() => ({
  createServerClient: vi.fn(),
  getClaims: vi.fn(),
}));

vi.mock("@supabase/ssr", () => ({ createServerClient }));
vi.mock("cloudflare:workers", () => ({ env: {} }));

import { requireAuth } from "../../lib/auth/require-auth";
import { requireAuthSession } from "../../lib/auth/session";

const env = {
  SUPABASE_URL: "https://project-ref.supabase.co",
  SUPABASE_PUBLISHABLE_KEY: "sb_publishable_test",
};

beforeEach(() => {
  vi.clearAllMocks();
  createServerClient.mockReturnValue({ auth: { getClaims } });
});

it("uses one client and one claims lookup while preserving refreshed response state", async () => {
  let setAll!: (
    cookies: Array<{
      name: string;
      value: string;
      options?: Record<string, unknown>;
    }>,
    headers?: Record<string, string>,
  ) => void;
  createServerClient.mockImplementation(
    (_url, _key, options: { cookies: { setAll: typeof setAll } }) => {
      setAll = options.cookies.setAll;
      return { auth: { getClaims } };
    },
  );
  getClaims.mockImplementation(async () => {
    setAll(
      [{ name: "sb-session", value: "rotated", options: { path: "/" } }],
      { "x-supabase-auth": "refreshed" },
    );
    return {
      data: {
        claims: {
          sub: "user-a",
          email: "a@example.com",
          iss: "https://project-ref.supabase.co/auth/v1",
          aud: "authenticated",
          exp: Math.floor(Date.now() / 1000) + 60,
        },
      },
      error: null,
    };
  });

  const session = await requireAuthSession(
    new NextRequest("https://app.test/api/petcare/test"),
    env,
  );
  const response = session.applySessionCookies(NextResponse.json({ ok: true }));

  expect(session.user).toEqual({ sub: "user-a", email: "a@example.com" });
  expect(createServerClient).toHaveBeenCalledTimes(1);
  expect(getClaims).toHaveBeenCalledTimes(1);
  expect(response.headers.get("set-cookie")).toContain("sb-session=rotated");
  expect(response.headers.get("set-cookie")).toMatch(/HttpOnly/i);
  expect(response.headers.get("x-supabase-auth")).toBe("refreshed");
  expect(response.headers.get("cache-control")).toBe("private, no-store");
});

it("fails closed on anonymous claims without exposing a response cookie", async () => {
  getClaims.mockResolvedValue({ data: null, error: new Error("anonymous") });
  const request = new NextRequest("https://app.test/api/petcare/test");

  await expect(requireAuthSession(request, env)).rejects.toMatchObject({
    status: 401,
    code: "unauthorized",
  });
  expect(createServerClient).toHaveBeenCalledTimes(1);
  expect(getClaims).toHaveBeenCalledTimes(1);
  expect(request.headers.get("set-cookie")).toBeNull();
  expect(request.cookies.getAll()).toEqual([]);
});

it("returns only sub and nullable email from verified JWKS claims", async () => {
  getClaims.mockResolvedValue({
    data: {
      claims: {
        sub: "user-a",
        email: "a@example.com",
        iss: "https://project-ref.supabase.co/auth/v1",
        aud: "authenticated",
        exp: Math.floor(Date.now() / 1000) + 60,
      },
    },
    error: null,
  });
  await expect(
    requireAuth(
      new Request("https://app.test", {
        headers: { cookie: "sb-test-auth-token=encoded" },
      }),
      env,
    ),
  ).resolves.toEqual({ sub: "user-a", email: "a@example.com" });
});

it("maps an absent or non-string email to null", async () => {
  getClaims.mockResolvedValue({
    data: {
      claims: {
        sub: "user-a",
        email: 42,
        iss: "https://project-ref.supabase.co/auth/v1",
        aud: ["authenticated"],
        exp: Math.floor(Date.now() / 1000) + 60,
      },
    },
    error: null,
  });
  await expect(requireAuth(new Request("https://app.test"), env)).resolves.toEqual({
    sub: "user-a",
    email: null,
  });
});

it.each([
  ["provider error", { data: null, error: new Error("expired") }],
  [
    "wrong issuer",
    {
      data: {
        claims: {
          sub: "user-a",
          iss: "https://evil.test",
          aud: "authenticated",
          exp: 4_102_444_800,
        },
      },
      error: null,
    },
  ],
  [
    "wrong audience",
    {
      data: {
        claims: {
          sub: "user-a",
          iss: "https://project-ref.supabase.co/auth/v1",
          aud: "anon",
          exp: 4_102_444_800,
        },
      },
      error: null,
    },
  ],
  [
    "malformed subject",
    {
      data: {
        claims: {
          sub: "",
          iss: "https://project-ref.supabase.co/auth/v1",
          aud: "authenticated",
          exp: 4_102_444_800,
        },
      },
      error: null,
    },
  ],
  [
    "expired claim",
    {
      data: {
        claims: {
          sub: "user-a",
          iss: "https://project-ref.supabase.co/auth/v1",
          aud: "authenticated",
          exp: 1,
        },
      },
      error: null,
    },
  ],
])("rejects %s", async (_name, result) => {
  getClaims.mockResolvedValue(result);
  await expect(requireAuth(new Request("https://app.test"), env)).rejects.toMatchObject({
    status: 401,
    code: "unauthorized",
  });
});
