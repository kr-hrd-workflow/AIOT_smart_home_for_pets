// @vitest-environment node

import { beforeEach, expect, it, vi } from "vitest";

const { createSupabaseSession, signInWithPassword } = vi.hoisted(() => ({
  createSupabaseSession: vi.fn(),
  signInWithPassword: vi.fn(),
}));

vi.mock("../../lib/auth/session", () => ({ createSupabaseSession }));

import { requireRecentPassword } from "../../lib/auth/recent-reauth";

const env = {
  SUPABASE_URL: "https://project-ref.supabase.co",
  SUPABASE_PUBLISHABLE_KEY: "sb_publishable_test",
};
const user = { sub: "user-a", email: "owner@example.com" };

beforeEach(() => {
  vi.clearAllMocks();
  createSupabaseSession.mockReturnValue({
    supabase: { auth: { signInWithPassword } },
  });
});

it("verifies the current password only against the verified JWT email", async () => {
  signInWithPassword.mockResolvedValue({
    data: { user: { id: "user-a" } },
    error: null,
  });
  const log = vi.spyOn(console, "log").mockImplementation(() => undefined);
  const errorLog = vi.spyOn(console, "error").mockImplementation(() => undefined);
  const request = new Request("https://app.test/api/petcare/account", {
    method: "DELETE",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({
      currentPassword: "correct horse battery staple",
      email: "attacker@example.com",
    }),
  });

  await expect(requireRecentPassword(request, env, user)).resolves.toBeUndefined();
  expect(signInWithPassword).toHaveBeenCalledWith({
    email: "owner@example.com",
    password: "correct horse battery staple",
  });
  expect(log).not.toHaveBeenCalled();
  expect(errorLog).not.toHaveBeenCalled();
  log.mockRestore();
  errorLog.mockRestore();
});

it.each([
  [429, 429, "rate_limited"],
  [400, 401, "reauthentication_failed"],
  [503, 503, "auth_unavailable"],
])(
  "maps provider status %i to generic %i",
  async (providerStatus, status, code) => {
    signInWithPassword.mockResolvedValue({
      data: { user: null },
      error: Object.assign(new Error("provider detail"), {
        status: providerStatus,
      }),
    });
    const request = new Request("https://app.test/api/petcare/account", {
      method: "DELETE",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ currentPassword: "not logged or returned" }),
    });
    const failure = requireRecentPassword(request, env, user);
    await expect(failure).rejects.toMatchObject({ status, code });
    await expect(failure).rejects.not.toThrow(
      /provider detail|not logged or returned/,
    );
  },
);

it("rejects a successful provider response for a different subject", async () => {
  signInWithPassword.mockResolvedValue({
    data: { user: { id: "user-b" } },
    error: null,
  });
  const request = new Request("https://app.test/api/petcare/account", {
    method: "DELETE",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ currentPassword: "not retained" }),
  });

  await expect(requireRecentPassword(request, env, user)).rejects.toMatchObject({
    status: 401,
    code: "reauthentication_failed",
  });
});

it("rejects a missing verified email or password before calling Supabase", async () => {
  const request = new Request("https://app.test/api/petcare/account", {
    method: "DELETE",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ currentPassword: "" }),
  });
  await expect(
    requireRecentPassword(request, env, { sub: "user-a", email: null }),
  ).rejects.toMatchObject({
    status: 401,
    code: "reauthentication_failed",
  });
  expect(signInWithPassword).not.toHaveBeenCalled();
});
