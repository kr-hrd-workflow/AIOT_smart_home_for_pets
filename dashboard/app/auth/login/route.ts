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
  let authEnv;
  let session;
  let error;
  try {
    authEnv = runtimeAuthEnv();
    session = createSupabaseSession(request, authEnv);
    ({ error } = await session.supabase.auth.signInWithPassword({
      email: email.trim(),
      password,
    }));
  } catch {
    return NextResponse.redirect(
      new URL("/login?error=unavailable", request.url),
      303,
    );
  }
  if (error?.status === 429) {
    return session.applySessionCookies(
      NextResponse.json({ error: "rate_limited" }, { status: 429 }),
    );
  }
  let destination =
    error?.code === "email_not_confirmed"
      ? "/login?error=email_not_confirmed"
      : error
        ? "/login?error=credentials"
        : "/dashboard";
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
