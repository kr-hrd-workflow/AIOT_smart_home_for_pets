import { NextRequest, NextResponse } from "next/server";

import { AuthError, type AuthUser } from "../auth/require-auth";
import { RecentReauthError } from "../auth/recent-reauth";
import {
  requireAuthSession,
  requireSameOrigin,
  type AuthSessionHandle,
} from "../auth/session";
import { deletePetCareAccountData } from "./account-delete";
import { handleAgentEnroll } from "./agent-enroll";
import { uploadSignedClip } from "./clip-upload";
import { deleteClip, listClips, readClip } from "./clips";
import type { PetCareEnv } from "./env";
import { errorResponse, PetCareError } from "./errors";
import { proxyMjpeg, proxyStatus } from "./live-proxy";

export type PetCareExecutionContext = {
  waitUntil(promise: Promise<unknown>): void;
};

const CAMERA =
  /^\/api\/petcare\/cameras\/([A-Za-z0-9_-]{1,64})\/stream\.mjpeg$/;
const CLIP_MEDIA =
  /^\/api\/petcare\/clips\/([A-Za-z0-9_-]{1,64})\.mp4$/;
const CLIP_DELETE = /^\/api\/petcare\/clips\/([A-Za-z0-9_-]{1,64})$/;
const PRIVATE_HEADERS = { "Cache-Control": "private, no-store" };

function methodNotAllowed(allow: string): Response {
  return Response.json(
    { error: "method_not_allowed" },
    { status: 405, headers: { ...PRIVATE_HEADERS, Allow: allow } },
  );
}

function notFound(): Response {
  return Response.json(
    { error: "not_found" },
    { status: 404, headers: PRIVATE_HEADERS },
  );
}

function nextResponse(response: Response): NextResponse {
  return new NextResponse(response.body, response);
}

function safeCode(error: unknown): string {
  return error instanceof AuthError ||
    error instanceof RecentReauthError ||
    error instanceof PetCareError
    ? error.code
    : "internal_error";
}

export async function routePetCare(
  request: Request,
  env: PetCareEnv,
  ctx: PetCareExecutionContext,
): Promise<Response | null> {
  void ctx;
  const url = new URL(request.url);
  if (!url.pathname.startsWith("/api/petcare/")) return null;
  if (
    url.pathname === "/api/petcare/enrollment" &&
    request.method === "POST"
  ) {
    return null;
  }

  let auth: AuthSessionHandle | undefined;
  let routeName = "unknown";
  const now = new Date();
  try {
    if (url.pathname === "/api/petcare/enrollment") {
      routeName = "enrollment";
      return methodNotAllowed("POST");
    }
    if (url.pathname === "/api/petcare/agent/enroll") {
      routeName = "agent_enroll";
      return request.method === "POST"
        ? await handleAgentEnroll(request, env, now)
        : methodNotAllowed("POST");
    }
    if (url.pathname === "/api/petcare/agent/clips") {
      routeName = "agent_clip_upload";
      return request.method === "POST"
        ? await uploadSignedClip(request, env, now)
        : methodNotAllowed("POST");
    }

    let invoke: ((next: NextRequest, user: AuthUser) => Promise<Response>) | null =
      null;
    let mutation = false;
    if (url.pathname === "/api/petcare/status") {
      routeName = "status";
      if (request.method !== "GET") return methodNotAllowed("GET");
      invoke = (_next, user) => proxyStatus(user, env, now);
    } else if (url.pathname === "/api/petcare/clips") {
      routeName = "clips";
      if (request.method !== "GET") return methodNotAllowed("GET");
      invoke = (next, user) => listClips(next, env, now, user);
    } else if (url.pathname === "/api/petcare/account") {
      routeName = "account_delete";
      if (request.method !== "DELETE") return methodNotAllowed("DELETE");
      mutation = true;
      invoke = (next, user) =>
        deletePetCareAccountData(next, env, now, user);
    } else {
      const camera = CAMERA.exec(url.pathname);
      const clipMedia = CLIP_MEDIA.exec(url.pathname);
      const clipDelete = CLIP_DELETE.exec(url.pathname);
      if (camera) {
        routeName = "camera_stream";
        if (request.method !== "GET") return methodNotAllowed("GET");
        invoke = (_next, user) => proxyMjpeg(user, env, camera[1]);
      } else if (clipMedia) {
        routeName = "clip_read";
        if (request.method !== "GET") return methodNotAllowed("GET");
        invoke = (next, user) =>
          readClip(next, env, clipMedia[1], now, user);
      } else if (clipDelete) {
        routeName = "clip_delete";
        if (request.method !== "DELETE") return methodNotAllowed("DELETE");
        mutation = true;
        invoke = (next, user) =>
          deleteClip(next, env, clipDelete[1], now, user);
      }
    }
    if (!invoke) return notFound();

    if (mutation) {
      try {
        requireSameOrigin(request);
      } catch {
        throw new PetCareError(403, "csrf");
      }
    }
    const next = new NextRequest(request);
    auth = await requireAuthSession(next, env);
    const response = await invoke(next, auth.user);
    return auth.applySessionCookies(nextResponse(response));
  } catch (error) {
    console.error("petcare_request_failed", {
      code: safeCode(error),
      method: request.method,
      requestId: crypto.randomUUID(),
      routeName,
    });
    const response = nextResponse(errorResponse(error));
    return auth ? auth.applySessionCookies(response) : response;
  }
}
