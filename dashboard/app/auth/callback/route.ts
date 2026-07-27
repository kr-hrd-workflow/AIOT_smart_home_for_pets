import { NextRequest, NextResponse } from "next/server";
import { getDb } from "../../../db";
import { requireAuth } from "../../../lib/auth/require-auth";
import {
  createSupabaseSession,
  runtimeAuthEnv,
} from "../../../lib/auth/session";
import { TenantRepository } from "../../../lib/tenancy/repository";

export async function GET(request: NextRequest) {
  const authEnv = runtimeAuthEnv();
  const session = createSupabaseSession(request, authEnv);
  const code = request.nextUrl.searchParams.get("code");
  const next =
    request.nextUrl.searchParams.get("next") === "/reset-password"
      ? "/reset-password"
      : "/dashboard";

  if (code) {
    const { error } = await session.supabase.auth.exchangeCodeForSession(code);
    if (!error) {
      try {
        const user = await requireAuth(request, authEnv);
        await new TenantRepository(getDb()).ensureHome(user.sub);
        return session.applySessionCookies(
          NextResponse.redirect(new URL(next, request.url), 303),
        );
      } catch {
        // Invalid claims and D1 failures share one generic callback response.
      }
    }
  }
  return session.applySessionCookies(
    NextResponse.redirect(new URL("/login?error=callback", request.url), 303),
  );
}
