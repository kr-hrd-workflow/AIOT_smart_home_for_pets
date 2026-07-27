import { NextRequest, NextResponse } from "next/server";
import {
  createSupabaseSession,
  requireSameOrigin,
  runtimeAuthEnv,
} from "../../../lib/auth/session";

const MAX_RECOVERY_TOKEN_LENGTH = 16_384;

function readRecoveryToken(value: FormDataEntryValue | null): string | null {
  if (
    typeof value !== "string" ||
    !value ||
    value.length > MAX_RECOVERY_TOKEN_LENGTH ||
    value.trim() !== value
  ) {
    return null;
  }
  return value;
}

export async function POST(request: NextRequest) {
  try {
    requireSameOrigin(request);
  } catch {
    return Response.json({ error: "forbidden" }, { status: 403 });
  }
  const form = await request.formData();
  const password = form.get("password");
  const accessTokenEntry = form.get("access_token");
  const refreshTokenEntry = form.get("refresh_token");
  if (typeof password !== "string" || !password) {
    return Response.json({ error: "invalid_form" }, { status: 400 });
  }
  const hasRecoveryTokens =
    accessTokenEntry !== null || refreshTokenEntry !== null;
  const accessToken = readRecoveryToken(accessTokenEntry);
  const refreshToken = readRecoveryToken(refreshTokenEntry);
  if (hasRecoveryTokens && (!accessToken || !refreshToken)) {
    return NextResponse.redirect(
      new URL("/reset-password?error=invalid_session", request.url),
      303,
    );
  }
  let session;
  let error;
  try {
    session = createSupabaseSession(request, runtimeAuthEnv());
    if (accessToken && refreshToken) {
      const sessionResult = await session.supabase.auth.setSession({
        access_token: accessToken,
        refresh_token: refreshToken,
      });
      if (sessionResult.error) {
        return session.applySessionCookies(
          NextResponse.redirect(
            new URL("/reset-password?error=invalid_session", request.url),
            303,
          ),
        );
      }
    }
    ({ error } = await session.supabase.auth.updateUser({ password }));
  } catch {
    return NextResponse.redirect(
      new URL("/reset-password?error=unavailable", request.url),
      303,
    );
  }
  if (error?.status === 429) {
    return session.applySessionCookies(
      NextResponse.json({ error: "rate_limited" }, { status: 429 }),
    );
  }
  const destination = error
    ? "/reset-password?error=invalid_session"
    : "/login?reset=1";
  return session.applySessionCookies(
    NextResponse.redirect(new URL(destination, request.url), 303),
  );
}
