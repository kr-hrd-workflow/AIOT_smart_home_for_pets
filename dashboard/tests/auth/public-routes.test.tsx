import "@testing-library/jest-dom/vitest";

import { cleanup, render, screen, waitFor } from "@testing-library/react";
import { NextRequest } from "next/server";
import { beforeEach, expect, it, vi } from "vitest";

const mocks = vi.hoisted(() => ({
  createSupabaseSession: vi.fn(),
  requireSameOrigin: vi.fn(),
  runtimeAuthEnv: vi.fn(() => ({
    SUPABASE_URL: "https://project-ref.supabase.co",
    SUPABASE_PUBLISHABLE_KEY: "sb_publishable_test",
  })),
  signInWithPassword: vi.fn(),
  signUp: vi.fn(),
  resetPasswordForEmail: vi.fn(),
  setSession: vi.fn(),
  updateUser: vi.fn(),
  requireAuth: vi.fn(),
  ensureHome: vi.fn(),
  getDb: vi.fn(() => ({ binding: "test" })),
}));

vi.mock("../../lib/auth/session", () => ({
  createSupabaseSession: mocks.createSupabaseSession,
  requireSameOrigin: mocks.requireSameOrigin,
  runtimeAuthEnv: mocks.runtimeAuthEnv,
}));
vi.mock("../../lib/auth/require-auth", () => ({
  requireAuth: mocks.requireAuth,
}));
vi.mock("../../db", () => ({ getDb: mocks.getDb }));
vi.mock("../../lib/tenancy/repository", () => ({
  TenantRepository: class {
    ensureHome = mocks.ensureHome;
  },
}));

import { POST as forgotPassword } from "../../app/auth/forgot-password/route";
import { POST as login } from "../../app/auth/login/route";
import { POST as resetPassword } from "../../app/auth/reset-password/route";
import { POST as signup } from "../../app/auth/signup/route";
import ForgotPasswordPage from "../../app/forgot-password/page";
import LoginPage from "../../app/login/page";
import ResetPasswordPage from "../../app/reset-password/page";
import SignupPage from "../../app/signup/page";

type Handler = (request: NextRequest) => Promise<Response>;

const handlers: Array<{
  name: string;
  path: string;
  fields: Record<string, string>;
  call: ReturnType<typeof vi.fn>;
  handler: Handler;
  errorLocation: string;
}> = [
  {
    name: "login",
    path: "/auth/login",
    fields: { email: "owner@example.com", password: "correct horse battery staple" },
    call: mocks.signInWithPassword,
    handler: login,
    errorLocation: "/login?error=credentials",
  },
  {
    name: "signup",
    path: "/auth/signup",
    fields: { email: "owner@example.com", password: "correct horse battery staple" },
    call: mocks.signUp,
    handler: signup,
    errorLocation: "/signup?error=signup",
  },
  {
    name: "forgot password",
    path: "/auth/forgot-password",
    fields: { email: "owner@example.com" },
    call: mocks.resetPasswordForEmail,
    handler: forgotPassword,
    errorLocation: "/forgot-password?error=unavailable",
  },
  {
    name: "reset password",
    path: "/auth/reset-password",
    fields: { password: "new correct horse battery staple" },
    call: mocks.updateUser,
    handler: resetPassword,
    errorLocation: "/reset-password?error=invalid_session",
  },
];

function request(
  path: string,
  fields: Record<string, string>,
  origin = "https://app.test",
) {
  return new NextRequest(`https://app.test${path}`, {
    method: "POST",
    headers: {
      "content-type": "application/x-www-form-urlencoded",
      origin,
    },
    body: new URLSearchParams(fields),
  });
}

beforeEach(() => {
  cleanup();
  vi.clearAllMocks();
  mocks.requireSameOrigin.mockImplementation((incoming: Request) => {
    if (incoming.headers.get("origin") !== new URL(incoming.url).origin) {
      throw new Error("Cross-origin request rejected");
    }
  });
  mocks.createSupabaseSession.mockReturnValue({
    supabase: {
      auth: {
        signInWithPassword: mocks.signInWithPassword,
        signUp: mocks.signUp,
        resetPasswordForEmail: mocks.resetPasswordForEmail,
        setSession: mocks.setSession,
        updateUser: mocks.updateUser,
      },
    },
    applySessionCookies(response: Response) {
      response.headers.set("Cache-Control", "private, no-store");
      return response;
    },
  });
  mocks.signInWithPassword.mockResolvedValue({ data: {}, error: null });
  mocks.signUp.mockResolvedValue({ data: {}, error: null });
  mocks.resetPasswordForEmail.mockResolvedValue({ data: {}, error: null });
  mocks.setSession.mockResolvedValue({ data: {}, error: null });
  mocks.updateUser.mockResolvedValue({ data: {}, error: null });
  mocks.requireAuth.mockResolvedValue({ sub: "user-a", email: "owner@example.com" });
  mocks.ensureHome.mockResolvedValue({ id: "home-a" });
});

it("renders the four public auth forms with accessible native controls", async () => {
  render(await LoginPage({ searchParams: Promise.resolve({}) }));
  expect(screen.getByRole("heading", { name: "로그인" })).toBeInTheDocument();
  expect(screen.getByRole("main")).toHaveClass("auth-scene-shell");
  const form = document.querySelector("form");
  expect(form).toHaveAttribute("action", "/auth/login");
  expect(form).toHaveAttribute("method", "post");
  expect(screen.getByLabelText("이메일")).toHaveAttribute("name", "email");
  expect(screen.getByLabelText("이메일")).toHaveAttribute("autocomplete", "email");
  expect(screen.getByLabelText("비밀번호")).toHaveAttribute("name", "password");
  expect(screen.getByLabelText("비밀번호")).toHaveAttribute(
    "autocomplete",
    "current-password",
  );
  expect(screen.getByRole("button", { name: "로그인" })).toBeInTheDocument();

  cleanup();
  render(await SignupPage({ searchParams: Promise.resolve({}) }));
  expect(screen.getByRole("heading", { name: "계정 만들기" })).toBeInTheDocument();
  expect(screen.getByLabelText("비밀번호")).toHaveAttribute(
    "autocomplete",
    "new-password",
  );
  expect(screen.getByLabelText("비밀번호")).toHaveAttribute("minlength", "8");

  cleanup();
  render(await ForgotPasswordPage({ searchParams: Promise.resolve({}) }));
  expect(screen.getByRole("heading", { name: "비밀번호 재설정" })).toBeInTheDocument();

  cleanup();
  render(await ResetPasswordPage({ searchParams: Promise.resolve({}) }));
  expect(screen.getByLabelText("새 비밀번호")).toHaveAttribute(
    "autocomplete",
    "new-password",
  );
});

it("renders an unavailable account state without a broken login form", async () => {
  render(
    await LoginPage({
      searchParams: Promise.resolve({ error: "unavailable" }),
    }),
  );
  expect(screen.getByText(/실시간 연결 준비 중입니다/)).toHaveAttribute(
    "role",
    "status",
  );
  expect(screen.getByRole("link", { name: "데모 보기" })).toHaveAttribute(
    "href",
    "/demo",
  );
  expect(document.querySelector("form")).toBeNull();
});

it("calls only the assigned Supabase password method and redirects on success", async () => {
  const loginResponse = await login(request(handlers[0].path, handlers[0].fields));
  expect(mocks.signInWithPassword).toHaveBeenCalledWith({
    email: "owner@example.com",
    password: "correct horse battery staple",
  });
  expect(mocks.requireAuth).toHaveBeenCalled();
  expect(mocks.ensureHome).toHaveBeenCalledWith("user-a");
  expect(loginResponse.headers.get("location")).toBe("https://app.test/dashboard");

  const signupResponse = await signup(request(handlers[1].path, handlers[1].fields));
  expect(mocks.signUp).toHaveBeenCalledWith({
    email: "owner@example.com",
    password: "correct horse battery staple",
    options: { emailRedirectTo: "https://app.test/auth/callback" },
  });
  expect(signupResponse.headers.get("location")).toBe(
    "https://app.test/signup?sent=1",
  );

  const forgotResponse = await forgotPassword(
    request(handlers[2].path, handlers[2].fields),
  );
  expect(mocks.resetPasswordForEmail).toHaveBeenCalledWith(
    "owner@example.com",
    { redirectTo: "https://app.test/reset-password" },
  );
  expect(forgotResponse.headers.get("location")).toBe(
    "https://app.test/forgot-password?sent=1",
  );

  const resetResponse = await resetPassword(
    request(handlers[3].path, handlers[3].fields),
  );
  expect(mocks.updateUser).toHaveBeenCalledWith({
    password: "new correct horse battery staple",
  });
  expect(resetResponse.headers.get("location")).toBe(
    "https://app.test/login?reset=1",
  );
  expect(mocks.createSupabaseSession).toHaveBeenCalledTimes(4);
});

it("bridges the provider recovery fragment into the same-origin reset form", async () => {
  window.history.replaceState(
    null,
    "",
    "/reset-password#access_token=access-a&refresh_token=refresh-a&type=recovery",
  );

  render(await ResetPasswordPage({ searchParams: Promise.resolve({}) }));

  await waitFor(() => {
    expect(document.querySelector('input[name="access_token"]')).toHaveValue(
      "access-a",
    );
    expect(document.querySelector('input[name="refresh_token"]')).toHaveValue(
      "refresh-a",
    );
  });
  expect(window.location.hash).toBe("");
});

it("forwards and removes an incomplete recovery fragment for server rejection", async () => {
  window.history.replaceState(
    null,
    "",
    "/reset-password#access_token=access-a&type=recovery",
  );

  render(await ResetPasswordPage({ searchParams: Promise.resolve({}) }));

  await waitFor(() => {
    expect(document.querySelector('input[name="access_token"]')).toHaveValue(
      "access-a",
    );
    expect(document.querySelector('input[name="refresh_token"]')).toHaveValue(
      "",
    );
  });
  expect(window.location.hash).toBe("");
});

it("establishes the recovery session before changing the password", async () => {
  const response = await resetPassword(
    request("/auth/reset-password", {
      password: "new correct horse battery staple",
      access_token: "access-a",
      refresh_token: "refresh-a",
    }),
  );

  expect(mocks.setSession).toHaveBeenCalledWith({
    access_token: "access-a",
    refresh_token: "refresh-a",
  });
  expect(mocks.updateUser).toHaveBeenCalledWith({
    password: "new correct horse battery staple",
  });
  expect(response.headers.get("location")).toBe(
    "https://app.test/login?reset=1",
  );
});

it("explains confirmation, repeat signup, and password recovery states", async () => {
  render(
    await SignupPage({
      searchParams: Promise.resolve({ sent: "1" }),
    }),
  );
  expect(screen.getByRole("status")).toHaveTextContent(
    "새 계정이라면 확인 메일이 도착합니다",
  );
  expect(
    screen.getByRole("link", { name: "비밀번호를 재설정해 주세요." }),
  ).toHaveAttribute("href", "/forgot-password");

  cleanup();
  render(
    await SignupPage({
      searchParams: Promise.resolve({ error: "weak_password" }),
    }),
  );
  expect(screen.getByRole("alert")).toHaveTextContent("8자 이상");

  cleanup();
  render(
    await LoginPage({
      searchParams: Promise.resolve({ error: "email_not_confirmed" }),
    }),
  );
  expect(screen.getByRole("alert")).toHaveTextContent("가입 확인 메일");
});

it("maps provider rate limits and actionable auth errors back to the forms", async () => {
  mocks.signInWithPassword.mockResolvedValueOnce({
    data: {},
    error: { status: 400, code: "email_not_confirmed" },
  });
  const unconfirmed = await login(
    request(handlers[0].path, handlers[0].fields),
  );
  expect(unconfirmed.headers.get("location")).toBe(
    "https://app.test/login?error=email_not_confirmed",
  );

  mocks.signUp.mockResolvedValueOnce({
    data: {},
    error: { status: 422, code: "weak_password" },
  });
  const weakPassword = await signup(
    request(handlers[1].path, handlers[1].fields),
  );
  expect(weakPassword.headers.get("location")).toBe(
    "https://app.test/signup?error=weak_password",
  );

  mocks.signInWithPassword.mockResolvedValueOnce({
    data: {},
    error: { status: 429, code: "over_request_rate_limit" },
  });
  const rateLimitedLogin = await login(
    request(handlers[0].path, handlers[0].fields),
  );
  expect(rateLimitedLogin.status).toBe(429);
  await expect(rateLimitedLogin.json()).resolves.toEqual({
    error: "rate_limited",
  });
});

it.each(handlers)("maps $name provider throttling without caching", async (entry) => {
  entry.call.mockResolvedValueOnce({ error: { status: 429, message: "provider token" } });
  const response = await entry.handler(request(entry.path, entry.fields));
  expect(response.status).toBe(429);
  await expect(response.json()).resolves.toEqual({ error: "rate_limited" });
  expect(response.headers.get("cache-control")).toBe("private, no-store");
});

it.each(handlers)("rejects cross-origin $name before reading credentials", async (entry) => {
  const response = await entry.handler(
    request(entry.path, entry.fields, "https://evil.test"),
  );
  expect(response.status).toBe(403);
  await expect(response.json()).resolves.toEqual({ error: "forbidden" });
  expect(entry.call).not.toHaveBeenCalled();
});

it.each(handlers)("rejects malformed $name form data", async (entry) => {
  const response = await entry.handler(request(entry.path, {}));
  expect(response.status).toBe(400);
  await expect(response.json()).resolves.toEqual({ error: "invalid_form" });
  expect(entry.call).not.toHaveBeenCalled();
});

it.each(handlers)("keeps $name provider errors generic", async (entry) => {
  entry.call.mockResolvedValueOnce({
    error: { status: 500, message: "provider token and raw detail" },
  });
  const response = await entry.handler(request(entry.path, entry.fields));
  expect(response.status).toBe(303);
  expect(response.headers.get("location")).toBe(
    `https://app.test${entry.errorLocation}`,
  );
  expect(await response.text()).not.toContain("provider token");
  expect(response.headers.get("cache-control")).toBe("private, no-store");
});
