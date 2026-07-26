import type { DashboardSummary } from "../types";
import { verifySignedAgentRequest, SNAPSHOT_MAX_BYTES } from "./agent-signature";
import type { PetCareEnv } from "./env";
import { PetCareError } from "./errors";
import { isDashboardSummary } from "./live-proxy";
import { PetCareRepository } from "./repository";

function parseSnapshot(body: string): DashboardSummary {
  let value: unknown;
  try {
    value = JSON.parse(body);
  } catch {
    throw new PetCareError(400, "invalid_snapshot_body");
  }
  if (!isDashboardSummary(value)) throw new PetCareError(400, "invalid_snapshot_body");
  return value;
}

const contract = {
  version: "PETCARE-SNAPSHOT-V1",
  path: "/api/petcare/agent/snapshot",
  maxBytes: SNAPSHOT_MAX_BYTES,
  validateBody: parseSnapshot,
};

export async function handleSnapshotUpload(
  request: Request,
  env: PetCareEnv,
  now: Date,
): Promise<Response> {
  const verified = await verifySignedAgentRequest(request, env, contract, now);
  await new PetCareRepository(env.DB).putAgentSnapshot(
    verified.agent.agentId,
    verified.body,
    verified.value.generated_at,
    now.toISOString(),
  );
  return new Response(null, { status: 204, headers: { "Cache-Control": "private, no-store" } });
}
