import { NextRequest } from "next/server";
import type { AuthEnv, AuthUser } from "./require-auth";
import { createSupabaseSession } from "./session";

type RecentReauthCode =
  | "reauthentication_failed"
  | "rate_limited"
  | "auth_unavailable";

export class RecentReauthError extends Error {
  constructor(
    readonly status: 401 | 429 | 503,
    readonly code: RecentReauthCode,
  ) {
    super(code);
  }
}

export async function requireRecentPassword(
  request: Request,
  env: AuthEnv,
  user: AuthUser,
): Promise<void> {
  if (!user.email) {
    throw new RecentReauthError(401, "reauthentication_failed");
  }

  let payload: unknown;
  try {
    payload = await request.json();
  } catch {
    throw new RecentReauthError(401, "reauthentication_failed");
  }
  const password = (payload as { currentPassword?: unknown }).currentPassword;
  if (typeof password !== "string" || password.length === 0) {
    throw new RecentReauthError(401, "reauthentication_failed");
  }

  const sessionRequest = new NextRequest(request.url, {
    method: request.method,
    headers: request.headers,
  });
  const session = createSupabaseSession(sessionRequest, env);
  const { data, error } = await session.supabase.auth.signInWithPassword({
    email: user.email,
    password,
  });
  if (!error && data.user?.id === user.sub) return;
  if (!error) {
    throw new RecentReauthError(401, "reauthentication_failed");
  }
  if (error.status === 429) {
    throw new RecentReauthError(429, "rate_limited");
  }
  if (error.status === 400 || error.status === 401) {
    throw new RecentReauthError(401, "reauthentication_failed");
  }
  throw new RecentReauthError(503, "auth_unavailable");
}
