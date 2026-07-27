import type { AuthUser } from "../auth/require-auth";
import type { PetCareEnv } from "./env";
import { PetCareError } from "./errors";
import { PetCareRepository } from "./repository";

type OwnedLiveStream = {
  bootId: string;
  initObjectKey: string;
  newestSequence: number;
  parts: { sequence: number; objectKey: string }[];
};

type LiveRepository = {
  getOwnedLiveStream(ownerSub: string, cameraId: string, now: string): Promise<OwnedLiveStream | null>;
};

const PRIVATE_HEADERS = {
  "Cache-Control": "private, no-store, no-transform",
  Pragma: "no-cache",
  "X-Content-Type-Options": "nosniff",
};

function mediaRoute(cameraId: string, bootId: string, part: string): string {
  return `/api/petcare/cameras/${cameraId}/live/${bootId}/${part}`;
}

export async function getLiveManifest(
  user: AuthUser,
  env: PetCareEnv,
  cameraId: string,
): Promise<Response> {
  const repository = new PetCareRepository(env.DB) as PetCareRepository & LiveRepository;
  const stream = await repository.getOwnedLiveStream(
    user.sub,
    cameraId,
    new Date().toISOString(),
  );
  if (!stream) throw new PetCareError(404, "not_found");

  return Response.json(
    {
      boot_id: stream.bootId,
      codec: "avc1.42E01E",
      newest_sequence: stream.newestSequence,
      target_latency_seconds: 6,
      init_url: mediaRoute(cameraId, stream.bootId, "init.mp4"),
      parts: stream.parts.map((part) => ({
        sequence: part.sequence,
        url: mediaRoute(cameraId, stream.bootId, `${part.sequence}.m4s`),
      })),
    },
    { headers: PRIVATE_HEADERS },
  );
}

export async function getLivePart(
  user: AuthUser,
  env: PetCareEnv,
  cameraId: string,
  bootId: string,
  sequence: number,
  kind: "init" | "segment",
): Promise<Response> {
  const stream = await new PetCareRepository(env.DB).getOwnedLiveStream(
    user.sub,
    cameraId,
    new Date().toISOString(),
  );
  if (!stream || stream.bootId !== bootId) throw new PetCareError(404, "not_found");
  const objectKey = kind === "init"
    ? stream.initObjectKey
    : stream.parts.find((part) => part.sequence === sequence)?.objectKey;
  if (!objectKey) throw new PetCareError(404, "not_found");
  const object = await env.CLIPS.get(objectKey);
  if (!object) throw new PetCareError(404, "not_found");
  return new Response(object.body, {
    headers: {
      ...PRIVATE_HEADERS,
      "Content-Type": kind === "init" ? "video/mp4" : "video/iso.segment",
    },
  });
}
