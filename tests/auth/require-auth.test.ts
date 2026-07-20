import { beforeEach, expect, it, vi } from "vitest";

const { createServerClient, getClaims } = vi.hoisted(() => ({
  createServerClient: vi.fn(),
  getClaims: vi.fn(),
}));

vi.mock("@supabase/ssr", () => ({ createServerClient }));

import { requireAuth } from "../../lib/auth/require-auth";

const env = {
  SUPABASE_URL: "https://project-ref.supabase.co",
  SUPABASE_PUBLISHABLE_KEY: "sb_publishable_test",
};

beforeEach(() => {
  vi.clearAllMocks();
  createServerClient.mockReturnValue({ auth: { getClaims } });
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
