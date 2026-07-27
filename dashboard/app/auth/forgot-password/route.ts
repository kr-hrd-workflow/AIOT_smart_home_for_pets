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
  let session;
  let error;
  try {
    session = createSupabaseSession(request, runtimeAuthEnv());
    ({ error } = await session.supabase.auth.resetPasswordForEmail(
      email.trim(),
      {
        redirectTo: new URL(
          "/reset-password",
          request.url,
        ).toString(),
      },
    ));
  } catch {
    return NextResponse.redirect(
      new URL("/forgot-password?error=unavailable", request.url),
      303,
    );
  }
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
