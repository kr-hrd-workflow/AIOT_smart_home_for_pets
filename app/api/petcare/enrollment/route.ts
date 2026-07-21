import { AuthError, requireAuth } from "../../../../lib/auth/require-auth";
import {
  requireSameOrigin,
  runtimeAuthEnv,
} from "../../../../lib/auth/session";
import { issueEnrollment } from "../../../../lib/tenancy/enrollment";
import { TenantNotFoundError } from "../../../../lib/tenancy/repository";

export async function POST(request: Request) {
  try {
    requireSameOrigin(request);
    const user = await requireAuth(request, runtimeAuthEnv());
    const result = await issueEnrollment(user.sub);
    return Response.json(result, {
      status: 201,
      headers: { "Cache-Control": "private, no-store" },
    });
  } catch (error) {
    const status =
      error instanceof AuthError
        ? 401
        : error instanceof TenantNotFoundError
          ? 404
          : Number((error as { status?: number }).status) === 403
            ? 403
            : 503;
    const code =
      status === 401
        ? "unauthorized"
        : status === 404
          ? "not_found"
          : status === 403
            ? "forbidden"
            : "enrollment_unavailable";
    return Response.json(
      { error: code },
      {
        status,
        headers: { "Cache-Control": "private, no-store" },
      },
    );
  }
}
