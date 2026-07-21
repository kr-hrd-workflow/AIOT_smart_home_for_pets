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
  const email = form.get("email");
  if (typeof email !== "string" || !email.trim()) {
    return Response.json({ error: "invalid_form" }, { status: 400 });
  }
  const session = createSupabaseSession(request, runtimeAuthEnv());
  const { error } = await session.supabase.auth.resetPasswordForEmail(
    email.trim(),
    {
      redirectTo: new URL(
        "/auth/callback?next=/reset-password",
        request.url,
      ).toString(),
    },
  );
  if (error?.status === 429) {
    return session.applySessionCookies(
      NextResponse.json({ error: "rate_limited" }, { status: 429 }),
    );
  }
  const destination = error
    ? "/forgot-password?error=unavailable"
    : "/forgot-password?sent=1";
  return session.applySessionCookies(
    NextResponse.redirect(new URL(destination, request.url), 303),
  );
}
