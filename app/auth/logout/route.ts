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
  const session = createSupabaseSession(request, runtimeAuthEnv());
  const { error } = await session.supabase.auth.signOut({ scope: "local" });
  if (error) {
    return Response.json({ error: "logout_failed" }, { status: 503 });
  }
  return session.applySessionCookies(
    NextResponse.redirect(new URL("/login", request.url), 303),
  );
}
