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
const ROOT_AUTH_HEADER = "x-petcare-authenticated";

function nextWithAuth(request: NextRequest, authenticated: boolean) {
  const headers = new Headers(request.headers);
  headers.set(ROOT_AUTH_HEADER, authenticated ? "1" : "0");
  return NextResponse.next({ request: { headers } });
}

export async function proxy(request: NextRequest) {
  if (request.nextUrl.pathname === "/demo") {
    return nextWithAuth(request, false);
  }
  const authEnv = env as unknown as AuthEnv;
  const session = createSupabaseSession(request, authEnv);
  let authenticated = false;
  try {
    await session.supabase.auth.getClaims();
    await requireAuth(request, authEnv);
    authenticated = true;
  } catch {
    // Provider failures and invalid claims both degrade to an anonymous request.
  }
  if (request.nextUrl.pathname === "/") {
    return session.applySessionCookies(nextWithAuth(request, authenticated));
  }
  if (!PUBLIC_PAGES.has(request.nextUrl.pathname) && !authenticated) {
    return session.applySessionCookies(
      NextResponse.redirect(new URL("/login", request.url)),
    );
  }
  return session.applySessionCookies(nextWithAuth(request, authenticated));
}

export const config = {
  matcher: ["/((?!api/|auth/|_next/|favicon.svg|og.png).*)"],
};
