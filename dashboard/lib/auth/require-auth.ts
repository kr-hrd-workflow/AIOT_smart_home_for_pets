import { createServerClient } from "@supabase/ssr";

export type AuthEnv = {
  SUPABASE_URL: string;
  SUPABASE_PUBLISHABLE_KEY: string;
};

export type AuthUser = { sub: string; email: string | null };

export class AuthError extends Error {
  readonly status = 401;
  readonly code = "unauthorized";
}

function requestCookies(request: Request) {
  return (request.headers.get("cookie") ?? "").split(";").flatMap((part) => {
    const separator = part.indexOf("=");
    if (separator < 1) return [];
    return [
      {
        name: part.slice(0, separator).trim(),
        value: part.slice(separator + 1).trim(),
      },
    ];
  });
}

function hasAudience(aud: unknown): boolean {
  return (
    aud === "authenticated" ||
    (Array.isArray(aud) && aud.includes("authenticated"))
  );
}

type ClaimsResult = {
  data?: { claims?: unknown } | null;
  error?: unknown;
};

export function validateAuthClaims(
  result: ClaimsResult,
  env: AuthEnv,
): AuthUser {
  const claims = result.data?.claims;
  const now = Math.floor(Date.now() / 1000);
  if (
    result.error ||
    !claims ||
    typeof claims !== "object" ||
    !("sub" in claims) ||
    typeof claims.sub !== "string" ||
    claims.sub.length === 0 ||
    !("iss" in claims) ||
    claims.iss !== `${env.SUPABASE_URL.replace(/\/$/, "")}/auth/v1` ||
    !("aud" in claims) ||
    !hasAudience(claims.aud) ||
    !("exp" in claims) ||
    typeof claims.exp !== "number" ||
    claims.exp <= now
  ) {
    throw new AuthError("Authentication required");
  }
  return {
    sub: claims.sub,
    email:
      "email" in claims && typeof claims.email === "string"
        ? claims.email
        : null,
  };
}

export async function requireAuth(
  request: Request,
  env: AuthEnv,
): Promise<AuthUser> {
  const supabase = createServerClient(
    env.SUPABASE_URL,
    env.SUPABASE_PUBLISHABLE_KEY,
    {
      cookies: {
        getAll: () => requestCookies(request),
        setAll: () => undefined,
      },
    },
  );
  return validateAuthClaims(await supabase.auth.getClaims(), env);
}
