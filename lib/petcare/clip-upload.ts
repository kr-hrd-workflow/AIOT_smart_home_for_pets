import { createHash, timingSafeEqual } from "node:crypto";

import { PetCareError } from "./errors";
import type { PetCareEnv } from "./env";
import {
  type ClipReceipt,
  type ExactClipInput,
  PetCareRepository,
} from "./repository";
import {
  CLIP_MAX_BYTES,
  CLIP_SIGNATURE_WINDOW_SECONDS,
  decodeBase64Url,
  encodeBase64Url,
  parseSignedClipHeaders,
  verifyClipSignature,
} from "./clip-signature";

const CLIP_TTL_MS = 7 * 24 * 60 * 60 * 1000;

async function removePendingObject(
  env: Pick<PetCareEnv, "CLIPS">,
  repository: PetCareRepository,
  homeId: string,
  objectKey: string,
  now: string,
): Promise<void> {
  try {
    await env.CLIPS.delete(objectKey);
  } catch {
    try {
      await repository.queueObjectDeletion(homeId, objectKey, now);
    } catch {
      // The unreferenced-object reconciler is the final cleanup fallback.
    }
  }
}

function clipIdentity(
  homeId: string,
  agentId: string,
  cameraId: string,
  digest: string,
  startedAt: string,
  endedAt: string,
  events: ExactClipInput["events"],
): string {
  return createHash("sha256")
    .update(JSON.stringify([
      "PETCARE-CLIP-ID-V1",
      homeId,
      agentId,
      cameraId,
      digest,
      startedAt,
      endedAt,
      events.map(({ eventType, eventId }) => `${eventType}:${eventId}`),
    ]))
    .digest("hex");
}

function receiptResponse(receipt: ClipReceipt): Response {
  return Response.json(receipt, {
    status: 201,
    headers: { "Cache-Control": "private, no-store" },
  });
}

export async function uploadSignedClip(
  request: Request,
  env: PetCareEnv,
  now: Date,
): Promise<Response> {
  const headers = parseSignedClipHeaders(request);
  const repository = new PetCareRepository(env.DB);
  const route = await repository.requireActiveAgent(headers.agentId, headers.cameraId);

  await verifyClipSignature(headers, route.publicKey);
  const nowSeconds = Math.floor(now.getTime() / 1000);
  if (Math.abs(headers.timestamp - nowSeconds) > CLIP_SIGNATURE_WINDOW_SECONDS) {
    throw new PetCareError(401, "invalid_agent_signature");
  }

  await repository.checkRateLimit(headers.agentId, "clip-upload", 30, 60, now);
  const createdAt = now.toISOString();
  await repository.consumeNonce(headers.agentId, headers.nonce, createdAt);
  const contentLength = Number(request.headers.get("Content-Length"));

  const id = clipIdentity(
    route.homeId,
    headers.agentId,
    headers.cameraId,
    headers.digest,
    headers.startedAt,
    headers.endedAt,
    headers.events,
  );
  const objectKey = `clips/${id}.mp4`;
  const exactInput: ExactClipInput = {
    id,
    homeId: route.homeId,
    agentId: headers.agentId,
    cameraId: headers.cameraId,
    objectKey,
    sha256: headers.digest,
    sizeBytes: contentLength,
    startedAt: headers.startedAt,
    endedAt: headers.endedAt,
    events: headers.events,
  };
  let existing: ClipReceipt | null;
  try {
    existing = await repository.findExactClip(exactInput, createdAt);
  } catch {
    throw new PetCareError(503, "upload_retryable");
  }
  if (existing) {
    await repository.markAgentSeen(headers.agentId, createdAt);
    return receiptResponse(existing);
  }

  if (!request.body) throw new PetCareError(400, "invalid_content_length");

  const pendingKey = `pending/${crypto.randomUUID()}.mp4`;
  const hash = createHash("sha256");
  let sizeBytes = 0;
  let bodyError: PetCareError | undefined;
  const monitored = request.body.pipeThrough(
    new TransformStream<Uint8Array, Uint8Array>({
      transform(chunk, controller) {
        sizeBytes += chunk.byteLength;
        if (sizeBytes > CLIP_MAX_BYTES) {
          bodyError = new PetCareError(400, "invalid_content_length");
          throw bodyError;
        }
        hash.update(chunk);
        controller.enqueue(chunk);
      },
    }),
  );

  let stored: { size: number };
  try {
    stored = await env.CLIPS.put(pendingKey, monitored, {
      httpMetadata: { contentType: "video/mp4" },
    });
  } catch {
    if (bodyError) throw bodyError;
    throw new PetCareError(503, "upload_retryable");
  }

  if (sizeBytes !== contentLength || stored.size !== sizeBytes) {
    await removePendingObject(env, repository, route.homeId, pendingKey, createdAt);
    throw new PetCareError(400, "invalid_content_length");
  }

  const actualDigest = hash.digest();
  const claimedDigest = decodeBase64Url(headers.digest, 32);
  if (!timingSafeEqual(actualDigest, claimedDigest)) {
    await removePendingObject(env, repository, route.homeId, pendingKey, createdAt);
    throw new PetCareError(400, "digest_mismatch");
  }

  const pending = await env.CLIPS.get(pendingKey);
  if (!pending) {
    await removePendingObject(env, repository, route.homeId, pendingKey, createdAt);
    throw new PetCareError(503, "upload_retryable");
  }
  try {
    const promoted = await env.CLIPS.put(objectKey, pending.body, {
      httpMetadata: { contentType: "video/mp4" },
    });
    if (promoted.size !== sizeBytes) throw new Error("promote_size_mismatch");
  } catch {
    await removePendingObject(env, repository, route.homeId, pendingKey, createdAt);
    throw new PetCareError(503, "upload_retryable");
  }

  const expiresAt = new Date(now.getTime() + CLIP_TTL_MS).toISOString();
  try {
    await repository.markAgentSeen(headers.agentId, createdAt);
    await repository.publishClip({
      id,
      homeId: route.homeId,
      agentId: headers.agentId,
      cameraId: headers.cameraId,
      objectKey,
      sha256: encodeBase64Url(actualDigest),
      sizeBytes,
      startedAt: headers.startedAt,
      endedAt: headers.endedAt,
      createdAt,
      expiresAt,
      events: headers.events,
    });
  } catch (error) {
    let concurrent: ClipReceipt | null = null;
    try {
      concurrent = await repository.findExactClip(exactInput, createdAt);
    } catch {
      // The original write error remains the safe public result.
    }
    await removePendingObject(env, repository, route.homeId, pendingKey, createdAt);
    if (concurrent) return receiptResponse(concurrent);
    if (error instanceof PetCareError && error.code === "account_deleted") throw error;
    throw new PetCareError(503, "upload_retryable");
  }

  await removePendingObject(env, repository, route.homeId, pendingKey, createdAt);
  return receiptResponse({ id, createdAt, expiresAt });
}
