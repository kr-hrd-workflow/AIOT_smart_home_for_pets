import { env } from "cloudflare:workers";
import { NextRequest, NextResponse } from "next/server";
import { requireAuth, type AuthEnv } from "./lib/auth/require-auth";
import { createSupabaseSession } from "./lib/auth/session";

const PUBLIC_PAGES = new Set([
  "/login",
  "/signup",
  "/forgot-password",
  "/reset-password",
]);

export async function proxy(request: NextRequest) {
  if (request.nextUrl.pathname === "/demo") {
    return NextResponse.next({ request });
  }
  const authEnv = env as unknown as AuthEnv;
  const session = createSupabaseSession(request, authEnv);
  await session.supabase.auth.getClaims();
  let authenticated = true;
  try {
    await requireAuth(request, authEnv);
  } catch {
    authenticated = false;
  }
  if (!PUBLIC_PAGES.has(request.nextUrl.pathname) && !authenticated) {
    return session.applySessionCookies(
      NextResponse.redirect(new URL("/login", request.url)),
    );
  }
  return session.applySessionCookies(NextResponse.next({ request }));
}

export const config = {
  matcher: ["/((?!api/|auth/|_next/|favicon.svg|og.png).*)"],
};
