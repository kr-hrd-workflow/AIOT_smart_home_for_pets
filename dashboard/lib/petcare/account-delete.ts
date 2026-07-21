import type { AuthUser } from "../auth/require-auth";
import { requireRecentPassword } from "../auth/recent-reauth";
import { requireSameOrigin } from "../auth/session";
import type { PetCareEnv } from "./env";
import { PetCareError } from "./errors";
import { PetCareRepository } from "./repository";

const PRIVATE_HEADERS = { "Cache-Control": "private, no-store" };

export async function deletePetCareAccountData(
  request: Request,
  env: PetCareEnv,
  now: Date,
  user: AuthUser,
): Promise<Response> {
  try {
    requireSameOrigin(request);
  } catch {
    throw new PetCareError(403, "csrf");
  }

  await requireRecentPassword(request, env, user);
  const state = await new PetCareRepository(env.DB).beginTenantCleanup(
    user.sub,
    now.toISOString(),
  );
  if (state.status === "absent") {
    return new Response(null, { status: 204, headers: PRIVATE_HEADERS });
  }
  return Response.json(
    { status: "cleanup_pending" },
    { status: 202, headers: PRIVATE_HEADERS },
  );
}
