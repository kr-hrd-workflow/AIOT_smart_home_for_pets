import {
  decodeBase64Url,
  encodeBase64Url,
  verifyEd25519Signature,
} from "./clip-signature";
import type { PetCareEnv } from "./env";
import { PetCareError } from "./errors";
import { PetCareRepository } from "./repository";

const BASE64URL = /^[A-Za-z0-9_-]+$/;
const COMMAND_ID = /^clc_[0-9a-f]{32}$/;
const PRIVATE_HEADERS = { "Cache-Control": "private, no-store" };
const CLEANUP_SIGNATURE_WINDOW_SECONDS = 300;

type CleanupAction =
  | { action: "poll" }
  | { action: "ack"; commandId: string };

export type SignedCleanupRequest = CleanupAction & {
  agentId: string;
  timestamp: number;
  nonce: string;
  digest: string;
  signature: Uint8Array;
};

function fail(code = "invalid_cleanup_request"): never {
  throw new PetCareError(400, code);
}

function singleton(headers: Headers, name: string): string {
  const value = headers.get(name);
  if (!value || value.includes(",") || /[\u0000-\u001f\u007f]/.test(value)) fail();
  return value;
}

function decoded(value: string, bytes: number): Uint8Array {
  try {
    return decodeBase64Url(value, bytes);
  } catch {
    return fail();
  }
}

function action(body: string): CleanupAction {
  if (body === '{"action":"poll"}') return { action: "poll" };
  const match = /^\{"action":"ack","commandId":"([^"]+)"\}$/.exec(body);
  if (!match || !COMMAND_ID.test(match[1])) fail();
  return { action: "ack", commandId: match[1] };
}

export async function parseSignedCleanupRequest(request: Request): Promise<SignedCleanupRequest> {
  const url = new URL(request.url);
  if (request.method !== "POST" || url.pathname !== "/api/petcare/agent/cleanup" || url.search) fail();
  if (singleton(request.headers, "Content-Type") !== "application/json") fail("invalid_content_type");

  const agentId = singleton(request.headers, "X-PetCare-Agent-Id");
  const timestampText = singleton(request.headers, "X-PetCare-Timestamp");
  if (!/^(?:0|[1-9]\d*)$/.test(timestampText)) fail();
  const timestamp = Number(timestampText);
  if (!Number.isSafeInteger(timestamp)) fail();

  const nonce = singleton(request.headers, "X-PetCare-Nonce");
  const digest = singleton(request.headers, "X-PetCare-Content-SHA256");
  const signatureText = singleton(request.headers, "X-PetCare-Signature");
  if (!BASE64URL.test(nonce) || !BASE64URL.test(digest) || !BASE64URL.test(signatureText)) fail();
  decoded(nonce, 16);
  decoded(digest, 32);
  const signature = decoded(signatureText, 64);

  const bytes = new Uint8Array(await request.arrayBuffer());
  if (bytes.byteLength > 128) fail();
  let body: string;
  try {
    body = new TextDecoder("utf-8", { fatal: true }).decode(bytes);
  } catch {
    fail();
  }
  const parsedAction = action(body);
  const actualDigest = encodeBase64Url(new Uint8Array(
    await crypto.subtle.digest("SHA-256", Uint8Array.from(bytes).buffer),
  ));
  if (actualDigest !== digest) fail("digest_mismatch");

  return { agentId, timestamp, nonce, digest, signature, ...parsedAction };
}

export function canonicalCleanupRequest(headers: SignedCleanupRequest): Uint8Array {
  return new TextEncoder().encode([
    "PETCARE-CLEANUP-V1",
    "POST",
    "/api/petcare/agent/cleanup",
    headers.agentId,
    String(headers.timestamp),
    headers.nonce,
    headers.digest,
    "",
  ].join("\n"));
}

export async function handleAgentActivityCleanup(
  request: Request,
  env: Pick<PetCareEnv, "DB">,
  now: Date,
): Promise<Response> {
  const headers = await parseSignedCleanupRequest(request);
  const repository = new PetCareRepository(env.DB);
  const commandId = headers.action === "ack" ? headers.commandId : undefined;
  const binding = await repository.requireActivityCleanupAgent(
    headers.agentId,
    commandId,
    headers.action === "ack",
  );
  if (!binding || binding.type !== "delete_activity_observations") {
    throw new PetCareError(401, "invalid_agent_signature");
  }
  await verifyEd25519Signature(
    headers.signature,
    canonicalCleanupRequest(headers),
    binding.publicKey,
  );
  if (Math.abs(headers.timestamp - Math.floor(now.getTime() / 1000)) > CLEANUP_SIGNATURE_WINDOW_SECONDS) {
    throw new PetCareError(401, "invalid_agent_signature");
  }
  await repository.checkRateLimit(headers.agentId, "activity-cleanup", 30, 60, now);
  const nowIso = now.toISOString();
  await repository.consumeNonce(headers.agentId, headers.nonce, nowIso);

  if (headers.action === "poll") {
    return Response.json({ commandId: binding.commandId, type: binding.type }, {
      status: 200,
      headers: PRIVATE_HEADERS,
    });
  }
  await repository.acknowledgeActivityCleanup(headers.agentId, headers.commandId, nowIso);
  return new Response(null, { status: 204, headers: PRIVATE_HEADERS });
}
