import {
  type AgentSignatureHeaders,
  verifyBoundedAgentRequest,
} from "./agent-signature";
import type { PetCareEnv } from "./env";
import { PetCareError } from "./errors";
import { PetCareRepository } from "./repository";

export const LIVE_INIT_MAX_BYTES = 256 * 1024;
export const LIVE_SEGMENT_MAX_BYTES = 1024 * 1024;
const LIVE_PATH = "/api/petcare/agent/live";
const IDENTIFIER = /^[A-Za-z0-9_-]{1,64}$/;
const UTC = /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,9})?Z$/;

type LiveHeaders = {
  cameraId: string;
  bootId: string;
  kind: "init" | "segment";
  sequence: number;
  startedAt: string;
  durationMs: number;
};

function fail(code = "invalid_live_request"): never {
  throw new PetCareError(400, code);
}

function header(request: Request, name: string): string {
  const value = request.headers.get(name);
  if (!value || value.includes(",") || /[\u0000-\u001f\u007f]/.test(value)) fail();
  return value;
}

function parseLiveHeaders(request: Request, signed: AgentSignatureHeaders): LiveHeaders {
  const cameraId = header(request, "X-PetCare-Camera-Id");
  const bootId = header(request, "X-PetCare-Boot-Id");
  const kind = header(request, "X-PetCare-Live-Kind");
  const sequenceText = header(request, "X-PetCare-Live-Sequence");
  const startedAt = header(request, "X-PetCare-Started-At");
  const durationText = header(request, "X-PetCare-Duration-Ms");
  if (!IDENTIFIER.test(cameraId) || !IDENTIFIER.test(bootId) || (kind !== "init" && kind !== "segment")) fail();
  if (!/^(?:0|[1-9]\d*)$/.test(sequenceText) || !/^(?:0|[1-9]\d*)$/.test(durationText)) fail();
  if (!UTC.test(startedAt) || Number.isNaN(Date.parse(startedAt))) fail();
  const sequence = Number(sequenceText);
  const durationMs = Number(durationText);
  if (!Number.isSafeInteger(sequence) || !Number.isSafeInteger(durationMs) || signed.contentLength === 0) fail();
  if ((kind === "init" && (sequence !== 0 || durationMs !== 0)) ||
      (kind === "segment" && (sequence < 1 || durationMs !== 1000))) fail();
  return { cameraId, bootId, kind, sequence, startedAt, durationMs };
}

function canonicalLiveRequest(signed: AgentSignatureHeaders, live: LiveHeaders): Uint8Array {
  return new TextEncoder().encode([
    "PETCARE-LIVE-V1",
    "POST",
    LIVE_PATH,
    signed.agentId,
    live.cameraId,
    live.bootId,
    live.kind,
    String(live.sequence),
    live.startedAt,
    String(live.durationMs),
    String(signed.contentLength),
    signed.digest,
    "",
  ].join("\n"));
}

function objectKey(homeId: string, live: LiveHeaders): string {
  const base = `live/${homeId}/${live.cameraId}/${live.bootId}`;
  return live.kind === "init" ? `${base}/init.mp4` : `${base}/${live.sequence}.m4s`;
}

async function deleteOrQueue(
  env: Pick<PetCareEnv, "CLIPS">,
  repository: PetCareRepository,
  homeId: string,
  key: string,
  now: string,
): Promise<void> {
  try {
    await env.CLIPS.delete(key);
  } catch {
    try {
      await repository.queueObjectDeletion(homeId, key, now);
    } catch {
      // Reconciliation discovers an unreferenced private object.
    }
  }
}

export async function handleLiveUpload(
  request: Request,
  env: PetCareEnv,
  now: Date,
): Promise<Response> {
  const contract = {
    path: LIVE_PATH,
    maxBytes: LIVE_SEGMENT_MAX_BYTES,
    contentType: "video/mp4",
    canonical: (signed: AgentSignatureHeaders) => canonicalLiveRequest(signed, parseLiveHeaders(request, signed)),
  };
  const verified = await verifyBoundedAgentRequest(request, env, contract, now);
  const live = parseLiveHeaders(request, verified.headers);
  if (live.kind === "init" && verified.bytes.byteLength > LIVE_INIT_MAX_BYTES) {
    fail("invalid_content_length");
  }
  if (live.cameraId !== verified.agent.cameraId) fail("invalid_agent_signature");

  const repository = new PetCareRepository(env.DB);
  const createdAt = now.toISOString();
  await repository.consumeNonce(verified.agent.agentId, verified.headers.nonce, createdAt);

  const key = objectKey(verified.agent.homeId, live);
  let stored: R2Object | null;
  try {
    stored = await env.CLIPS.put(key, verified.bytes, {
      onlyIf: { etagDoesNotMatch: "*" },
      httpMetadata: { contentType: "video/mp4" },
    });
  } catch {
    throw new PetCareError(503, "upload_retryable");
  }
  if (!stored) throw new PetCareError(409, "live_conflict");
  if (stored.size !== verified.bytes.byteLength) {
    await deleteOrQueue(env, repository, verified.agent.homeId, key, createdAt);
    fail("invalid_content_length");
  }

  const expiresAt = new Date(now.getTime() + 60_000).toISOString();
  let trimmed: string[];
  try {
    trimmed = await repository.publishLiveUpload({
      homeId: verified.agent.homeId,
      agentId: verified.agent.agentId,
      cameraId: verified.agent.cameraId,
      bootId: live.bootId,
      kind: live.kind,
      sequence: live.sequence,
      objectKey: key,
      sha256: verified.headers.digest,
      sizeBytes: verified.bytes.byteLength,
      startedAt: live.startedAt,
      durationMs: live.durationMs,
      createdAt,
      expiresAt,
    });
  } catch (error) {
    await deleteOrQueue(env, repository, verified.agent.homeId, key, createdAt);
    if (error instanceof PetCareError) throw error;
    throw new PetCareError(503, "upload_retryable");
  }
  await Promise.all(trimmed.map((staleKey) =>
    deleteOrQueue(env, repository, verified.agent.homeId, staleKey, createdAt),
  ));
  return new Response(null, { status: 204, headers: { "Cache-Control": "private, no-store" } });
}
