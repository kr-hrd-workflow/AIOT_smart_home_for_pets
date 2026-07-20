// @vitest-environment node

import { NextRequest } from "next/server";
import { beforeEach, expect, it, vi } from "vitest";

const mocks = vi.hoisted(() => ({
  createServerClient: vi.fn(),
  getClaims: vi.fn(),
  exchangeCodeForSession: vi.fn(),
  signOut: vi.fn(),
  requireAuth: vi.fn(),
  ensureHome: vi.fn(),
  getDb: vi.fn(() => ({ binding: "test" })),
}));

vi.mock("cloudflare:workers", () => ({
  env: {
    SUPABASE_URL: "https://project-ref.supabase.co",
    SUPABASE_PUBLISHABLE_KEY: "sb_publishable_test",
  },
}));
vi.mock("@supabase/ssr", () => ({ createServerClient: mocks.createServerClient }));
vi.mock("../../lib/auth/require-auth", () => ({
  AuthError: class AuthError extends Error {
    readonly status = 401;
    readonly code = "unauthorized";
  },
  requireAuth: mocks.requireAuth,
}));
vi.mock("../../db", () => ({ getDb: mocks.getDb }));
vi.mock("../../lib/tenancy/repository", () => ({
  TenantRepository: class {
    ensureHome = mocks.ensureHome;
  },
}));

import { GET } from "../../app/auth/callback/route";
import { POST } from "../../app/auth/logout/route";
import { AuthError } from "../../lib/auth/require-auth";
import { proxy } from "../../proxy";

const validClaims = {
  sub: "user-a",
  email: "a@example.com",
  iss: "https://project-ref.supabase.co/auth/v1",
  aud: "authenticated",
  exp: 4_102_444_800,
};

let setAll: (
  cookies: Array<{
    name: string;
    value: string;
    options?: Record<string, unknown>;
  }>,
  headers?: Record<string, string>,
) => void;

beforeEach(() => {
  vi.clearAllMocks();
  mocks.createServerClient.mockImplementation(
    (_url: string, _key: string, options: { cookies: { setAll: typeof setAll } }) => {
      setAll = options.cookies.setAll;
      return {
        auth: {
          getClaims: mocks.getClaims,
          exchangeCodeForSession: mocks.exchangeCodeForSession,
          signOut: mocks.signOut,
        },
      };
    },
  );
  mocks.getClaims.mockResolvedValue({ data: { claims: validClaims }, error: null });
  mocks.requireAuth.mockResolvedValue({ sub: "user-a", email: "a@example.com" });
  mocks.exchangeCodeForSession.mockResolvedValue({ data: {}, error: null });
  mocks.signOut.mockResolvedValue({ error: null });
  mocks.ensureHome.mockResolvedValue({ id: "home-a" });
});

it.each(["/login", "/signup", "/forgot-password", "/reset-password"])(
  "keeps %s public without verified claims",
  async (pathname) => {
    mocks.getClaims.mockResolvedValue({
      data: null,
      error: new Error("anonymous"),
    });
    mocks.requireAuth.mockRejectedValue(new AuthError("Authentication required"));
    const response = await proxy(new NextRequest(`https://app.test${pathname}`));
    expect(response.status).toBe(200);
    expect(response.headers.get("location")).toBeNull();
  },
);

it("keeps /demo public without constructing a Supabase client", async () => {
  const response = await proxy(new NextRequest("https://app.test/demo"));
  expect(response.status).toBe(200);
  expect(response.headers.get("location")).toBeNull();
  expect(mocks.createServerClient).not.toHaveBeenCalled();
  expect(mocks.getClaims).not.toHaveBeenCalled();
  expect(mocks.requireAuth).not.toHaveBeenCalled();
});

it("redirects an anonymous dashboard request to login", async () => {
  mocks.getClaims.mockResolvedValue({ data: null, error: new Error("anonymous") });
  mocks.requireAuth.mockRejectedValue(new AuthError("Authentication required"));
  const response = await proxy(new NextRequest("https://app.test/"));
  expect(response.status).toBe(307);
  expect(response.headers.get("location")).toBe("https://app.test/login");
});

it("copies refreshed cookies and private no-store headers", async () => {
  mocks.getClaims.mockImplementation(async () => {
    setAll(
      [{ name: "sb-session", value: "rotated", options: { path: "/" } }],
      { "cache-control": "private, no-store" },
    );
    return { data: { claims: validClaims }, error: null };
  });
  const response = await proxy(new NextRequest("https://app.test/"));
  expect(response.headers.get("set-cookie")).toContain("sb-session=rotated");
  expect(response.headers.get("set-cookie")).toMatch(/HttpOnly/i);
  expect(response.headers.get("set-cookie")).toMatch(/Secure/i);
  expect(response.headers.get("set-cookie")).toMatch(/SameSite=Lax/i);
  expect(response.headers.get("set-cookie")).toMatch(/Path=\//i);
  expect(response.headers.get("cache-control")).toBe("private, no-store");
});

it("always marks provider cookies Secure at the public boundary", async () => {
  mocks.getClaims.mockImplementation(async () => {
    setAll([{ name: "sb-session", value: "rotated", options: {} }]);
    return { data: { claims: validClaims }, error: null };
  });
  const response = await proxy(new NextRequest("http://app.test/"));
  expect(response.headers.get("set-cookie")).toMatch(/Secure/i);
});

it("exchanges a PKCE code, creates the subject home, and redirects safely", async () => {
  const response = await GET(
    new NextRequest(
      "https://app.test/auth/callback?code=pkce-code&next=/reset-password",
    ),
  );
  expect(mocks.exchangeCodeForSession).toHaveBeenCalledWith("pkce-code");
  expect(mocks.ensureHome).toHaveBeenCalledWith("user-a");
  expect(response.headers.get("location")).toBe(
    "https://app.test/reset-password",
  );
});

it("uses a safe callback destination and maps callback failures generically", async () => {
  const safe = await GET(
    new NextRequest("https://app.test/auth/callback?code=pkce-code&next=https://evil.test"),
  );
  expect(safe.headers.get("location")).toBe("https://app.test/");

  mocks.exchangeCodeForSession.mockResolvedValue({
    data: null,
    error: new Error("provider detail"),
  });
  const failed = await GET(
    new NextRequest("https://app.test/auth/callback?code=bad-code"),
  );
  expect(failed.headers.get("location")).toBe(
    "https://app.test/login?error=callback",
  );
});

it("rejects cross-origin logout before creating a Supabase session", async () => {
  const response = await POST(
    new NextRequest("https://app.test/auth/logout", {
      method: "POST",
      headers: { origin: "https://evil.test" },
    }),
  );
  expect(response.status).toBe(403);
  expect(mocks.signOut).not.toHaveBeenCalled();
});

it("logs out only the local session and redirects to login", async () => {
  mocks.signOut.mockImplementation(async () => {
    setAll([
      {
        name: "sb-session",
        value: "",
        options: { maxAge: 0, path: "/" },
      },
    ]);
    return { error: null };
  });
  const response = await POST(
    new NextRequest("https://app.test/auth/logout", {
      method: "POST",
      headers: { origin: "https://app.test" },
    }),
  );
  expect(mocks.signOut).toHaveBeenCalledWith({ scope: "local" });
  expect(response.status).toBe(303);
  expect(response.headers.get("location")).toBe("https://app.test/login");
  expect(response.headers.get("set-cookie")).toContain("sb-session=");
  expect(response.headers.get("set-cookie")).toMatch(/Max-Age=0/i);
  expect(response.headers.get("cache-control")).toBe("private, no-store");
});

it("maps provider logout failures without exposing details", async () => {
  mocks.signOut.mockResolvedValue({ error: new Error("provider detail") });
  const response = await POST(
    new NextRequest("https://app.test/auth/logout", {
      method: "POST",
      headers: { origin: "https://app.test" },
    }),
  );
  expect(response.status).toBe(503);
  await expect(response.json()).resolves.toEqual({ error: "logout_failed" });
});
