import { getDb } from "../../db";
import type { AuthUser } from "../auth/require-auth";
import { TenantRepository } from "../tenancy/repository";
import type { PetCareEnv } from "./env";
import { PetCareRepository, type OwnedClip } from "./repository";

const cacheControl = "private, no-store";
const eventTypes = [
  "eating",
  "resting",
  "bed_sensor_mismatch",
] as const;

async function requireHome(env: PetCareEnv, user: AuthUser) {
  return new TenantRepository(getDb(env.DB)).requireHome(user.sub);
}

function browserClip(clip: OwnedClip) {
  const present = new Set(clip.events.map((event) => event.eventType));
  return {
    id: clip.id,
    camera_id: clip.cameraId,
    event_types: eventTypes.filter((eventType) => present.has(eventType)),
    started_at: clip.startedAt,
    ended_at: clip.endedAt,
    expires_at: clip.expiresAt,
  };
}

export async function listClips(
  _request: Request,
  env: PetCareEnv,
  now: Date,
  user: AuthUser,
): Promise<Response> {
  const home = await requireHome(env, user);
  const clips = await new PetCareRepository(env.DB).listOwnedClips(
    home.id,
    now.toISOString(),
  );
  return Response.json(
    { clips: clips.map(browserClip) },
    { headers: { "Cache-Control": cacheControl } },
  );
}

export async function readClip(
  _request: Request,
  env: PetCareEnv,
  clipId: string,
  now: Date,
  user: AuthUser,
): Promise<Response> {
  const home = await requireHome(env, user);
  const repository = new PetCareRepository(env.DB);
  const clip = await repository.requireOwnedClip(
    home.id,
    clipId,
    now.toISOString(),
  );
  const object = await env.CLIPS.get(clip.objectKey);
  if (!object) {
    const { objectKey } = await repository.deleteClipAndQueueObject(
      home.id,
      clipId,
      now.toISOString(),
    );
    try {
      await repository.completeObjectDeletion(home.id, objectKey);
    } catch {
      // The durable job remains for reconciliation.
    }
    return Response.json(
      { error: "not_found" },
      { status: 404, headers: { "Cache-Control": cacheControl } },
    );
  }
  return new Response(object.body, {
    headers: {
      "Cache-Control": `${cacheControl}, no-transform`,
      "Content-Type": "video/mp4",
    },
  });
}

export async function deleteClip(
  _request: Request,
  env: PetCareEnv,
  clipId: string,
  now: Date,
  user: AuthUser,
): Promise<Response> {
  const home = await requireHome(env, user);
  const repository = new PetCareRepository(env.DB);
  const { objectKey } = await repository.deleteClipAndQueueObject(
    home.id,
    clipId,
    now.toISOString(),
  );
  try {
    await env.CLIPS.delete(objectKey);
    await repository.completeObjectDeletion(home.id, objectKey);
  } catch {
    // Logical access is already gone; reconciliation retries physical cleanup.
  }
  return new Response(null, {
    status: 204,
    headers: { "Cache-Control": cacheControl },
  });
}
