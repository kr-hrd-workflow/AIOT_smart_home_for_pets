import {
  decodeBase64Url,
  encodeBase64Url,
  verifyEd25519Signature,
} from "./clip-signature";
import type { PetCareEnv } from "./env";
import { PetCareError } from "./errors";
import { PetCareRepository, type SnapshotAgent } from "./repository";

export const SNAPSHOT_MAX_BYTES = 128 * 1024;
const SIGNATURE_WINDOW_SECONDS = 300;

export type AgentRequestContract<T> = {
  version: string;
  path: string;
  maxBytes: number;
  validateBody(body: string): T;
};

export type VerifiedAgentRequest<T> = {
  agent: SnapshotAgent;
  body: string;
  value: T;
};

function fail(status = 400, code = "invalid_snapshot_request"): never {
  throw new PetCareError(status, code);
}

function singleton(headers: Headers, name: string): string {
  const value = headers.get(name);
  if (!value || value.includes(",") || /[\u0000-\u001f\u007f]/.test(value)) fail();
  return value;
}

async function bodyBytes(request: Request, maxBytes: number, contentLength: number): Promise<Uint8Array> {
  if (!request.body) fail(400, "invalid_content_length");
  const reader = request.body.getReader();
  const chunks: Uint8Array[] = [];
  let size = 0;
  while (true) {
    const { done, value } = await reader.read();
    if (done) break;
    size += value.byteLength;
    if (size > maxBytes) fail(400, "invalid_content_length");
    chunks.push(value);
  }
  if (size !== contentLength) fail(400, "invalid_content_length");
  const body = new Uint8Array(size);
  let offset = 0;
  for (const chunk of chunks) {
    body.set(chunk, offset);
    offset += chunk.byteLength;
  }
  return body;
}

export async function verifySignedAgentRequest<T>(
  request: Request,
  env: PetCareEnv,
  contract: AgentRequestContract<T>,
  now = new Date(),
): Promise<VerifiedAgentRequest<T>> {
  const url = new URL(request.url);
  if (request.method !== "POST" || url.pathname !== contract.path || url.search) fail();
  if (singleton(request.headers, "Content-Type") !== "application/json") fail(400, "invalid_content_type");

  const contentLengthText = singleton(request.headers, "Content-Length");
  if (!/^(?:0|[1-9]\d*)$/.test(contentLengthText)) fail(400, "invalid_content_length");
  const contentLength = Number(contentLengthText);
  if (!Number.isSafeInteger(contentLength) || contentLength > contract.maxBytes) {
    fail(400, "invalid_content_length");
  }

  const agentId = singleton(request.headers, "X-PetCare-Agent-Id");
  const timestampText = singleton(request.headers, "X-PetCare-Timestamp");
  if (!/^(?:0|[1-9]\d*)$/.test(timestampText)) fail(401, "invalid_agent_signature");
  const timestamp = Number(timestampText);
  if (
    !Number.isSafeInteger(timestamp) ||
    Math.abs(timestamp - Math.floor(now.getTime() / 1_000)) > SIGNATURE_WINDOW_SECONDS
  ) {
    fail(401, "invalid_agent_signature");
  }

  const nonce = singleton(request.headers, "X-PetCare-Nonce");
  const digest = singleton(request.headers, "X-PetCare-Content-SHA256");
  const signature = singleton(request.headers, "X-PetCare-Signature");
  decodeBase64Url(nonce, 16);
  decodeBase64Url(digest, 32);
  const signatureBytes = decodeBase64Url(signature, 64);
  const bytes = await bodyBytes(request, contract.maxBytes, contentLength);
  let body: string;
  try {
    body = new TextDecoder("utf-8", { fatal: true }).decode(bytes);
  } catch {
    fail(400, "invalid_snapshot_body");
  }
  const actualDigest = encodeBase64Url(new Uint8Array(await crypto.subtle.digest("SHA-256", bytes)));
  if (actualDigest !== digest) fail(400, "digest_mismatch");

  const agent = await new PetCareRepository(env.DB).requireOutboundAgent(agentId);
  const canonical = new TextEncoder().encode(
    [contract.version, "POST", contract.path, agentId, timestampText, nonce, digest, ""].join("\n"),
  );
  await verifyEd25519Signature(signatureBytes, canonical, agent.publicKey);
  const value = contract.validateBody(body);
  await new PetCareRepository(env.DB).consumeNonce(agentId, nonce, now.toISOString());
  return { agent, body, value };
}
