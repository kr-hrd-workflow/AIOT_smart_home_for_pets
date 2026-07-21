import { AuthError } from "../auth/require-auth";
import { RecentReauthError } from "../auth/recent-reauth";

export class PetCareError extends Error {
  constructor(
    readonly status: number,
    readonly code: string,
  ) {
    super(code);
  }
}

export function errorResponse(error: unknown): Response {
  const safe =
    error instanceof AuthError ||
    error instanceof RecentReauthError ||
    error instanceof PetCareError
      ? error
      : new PetCareError(500, "internal_error");
  return Response.json({ error: safe.code }, {
    status: safe.status,
    headers: { "Cache-Control": "private, no-store" },
  });
}
