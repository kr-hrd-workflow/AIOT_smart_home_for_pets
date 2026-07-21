import { NextRequest, NextResponse } from "next/server";
import {
  createSupabaseSession,
  requireSameOrigin,
  runtimeAuthEnv,
} from "../../../lib/auth/session";

export async function POST(request: NextRequest) {
  try {
    requireSameOrigin(request);
  } catch {
    return Response.json({ error: "forbidden" }, { status: 403 });
  }
  const form = await request.formData();
  const password = form.get("password");
  if (typeof password !== "string" || !password) {
    return Response.json({ error: "invalid_form" }, { status: 400 });
  }
  const session = createSupabaseSession(request, runtimeAuthEnv());
  const { error } = await session.supabase.auth.updateUser({ password });
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
