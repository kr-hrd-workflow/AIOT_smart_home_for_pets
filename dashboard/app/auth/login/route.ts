import { NextRequest, NextResponse } from "next/server";
import { getDb } from "../../../db";
import { requireAuth } from "../../../lib/auth/require-auth";
import {
  createSupabaseSession,
  requireSameOrigin,
  runtimeAuthEnv,
} from "../../../lib/auth/session";
import { TenantRepository } from "../../../lib/tenancy/repository";

export async function POST(request: NextRequest) {
  try {
    requireSameOrigin(request);
  } catch {
    return Response.json({ error: "forbidden" }, { status: 403 });
  }
  const form = await request.formData();
  const email = form.get("email");
  const password = form.get("password");
  if (
    typeof email !== "string" ||
    !email.trim() ||
    typeof password !== "string" ||
    !password
  ) {
    return Response.json({ error: "invalid_form" }, { status: 400 });
  }
  const authEnv = runtimeAuthEnv();
  const session = createSupabaseSession(request, authEnv);
  const { error } = await session.supabase.auth.signInWithPassword({
    email: email.trim(),
    password,
  });
  if (error?.status === 429) {
    return session.applySessionCookies(
      NextResponse.json({ error: "rate_limited" }, { status: 429 }),
    );
  }
  let destination = error ? "/login?error=credentials" : "/";
  if (!error) {
    try {
      const user = await requireAuth(request, authEnv);
      await new TenantRepository(getDb()).ensureHome(user.sub);
    } catch {
      destination = "/login?error=unavailable";
    }
  }
  return session.applySessionCookies(
    NextResponse.redirect(new URL(destination, request.url), 303),
  );
}
