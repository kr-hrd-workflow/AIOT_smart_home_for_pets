import { env } from "cloudflare:workers";
import { createServerClient, type CookieOptions } from "@supabase/ssr";
import type { SupabaseClient } from "@supabase/supabase-js";
import { NextRequest, NextResponse } from "next/server";
import {
  validateAuthClaims,
  type AuthEnv,
  type AuthUser,
} from "./require-auth";

type PendingCookie = {
  name: string;
  value: string;
  options: CookieOptions;
};

export type SessionHandle = {
  supabase: SupabaseClient;
  applySessionCookies(response: NextResponse): NextResponse;
};

export type AuthSessionHandle = Pick<
  SessionHandle,
  "applySessionCookies"
> & {
  user: AuthUser;
};

export function createSupabaseSession(
  request: NextRequest,
  authEnv: AuthEnv,
): SessionHandle {
  const pendingCookies: PendingCookie[] = [];
  const pendingHeaders = new Headers();
  const supabase = createServerClient(
    authEnv.SUPABASE_URL,
    authEnv.SUPABASE_PUBLISHABLE_KEY,
    {
      cookies: {
        getAll: () => request.cookies.getAll(),
        setAll(cookiesToSet, headersToSet) {
          for (const cookie of cookiesToSet) {
            request.cookies.set(cookie.name, cookie.value);
            pendingCookies.push({
              ...cookie,
              options: {
                ...cookie.options,
                httpOnly: true,
                secure: true,
                sameSite: "lax",
                path: "/",
              },
            });
          }
          for (const [name, value] of Object.entries(headersToSet ?? {})) {
            pendingHeaders.set(name, value);
          }
        },
      },
    },
  );
  return {
    supabase,
    applySessionCookies(response) {
      for (const cookie of pendingCookies) {
        response.cookies.set(cookie.name, cookie.value, cookie.options);
      }
      pendingHeaders.forEach((value, name) => response.headers.set(name, value));
      response.headers.set("Cache-Control", "private, no-store");
      return response;
    },
  };
}

export async function requireAuthSession(
  request: NextRequest,
  authEnv: AuthEnv,
): Promise<AuthSessionHandle> {
  const session = createSupabaseSession(request, authEnv);
  return {
    user: validateAuthClaims(await session.supabase.auth.getClaims(), authEnv),
    applySessionCookies: session.applySessionCookies,
  };
}

export function runtimeAuthEnv(): AuthEnv {
  const runtime = env as unknown as Partial<AuthEnv>;
  if (!runtime.SUPABASE_URL || !runtime.SUPABASE_PUBLISHABLE_KEY) {
    throw new Error("Supabase runtime configuration is unavailable");
  }
  return {
    SUPABASE_URL: runtime.SUPABASE_URL,
    SUPABASE_PUBLISHABLE_KEY: runtime.SUPABASE_PUBLISHABLE_KEY,
  };
}

export function requireSameOrigin(request: Request): void {
  const origin = request.headers.get("origin");
  if (!origin || origin !== new URL(request.url).origin) {
    throw Object.assign(new Error("Cross-origin request rejected"), {
      status: 403,
    });
  }
}
