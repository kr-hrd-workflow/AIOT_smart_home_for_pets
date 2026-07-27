import type { PetCareEnv } from "./env";

export const INSTALLER_FILE_NAME = "PetCare-Home-Agent-Setup.exe";
export const INSTALLER_SHA256 =
  "58860E707B86A9688882E465E52A2C2C3B4DF9A847B933AEC96F8077DB411887";
export const INSTALLER_SIZE_BYTES = 986_624;
export const INSTALLER_OBJECT_KEY =
  `system/installers/${INSTALLER_FILE_NAME}-${INSTALLER_SHA256.toLowerCase()}`;

const PRIVATE_HEADERS = { "Cache-Control": "private, no-store" };
const DOWNLOAD_CACHE_CONTROL = "private, no-store, no-transform";
const UPLOAD_TOKEN_HEADER = "x-petcare-installer-upload-token";

function jsonError(error: string, status: number): Response {
  return Response.json(
    { error },
    { status, headers: PRIVATE_HEADERS },
  );
}

function hex(bytes: ArrayBuffer): string {
  return [...new Uint8Array(bytes)]
    .map((value) => value.toString(16).padStart(2, "0"))
    .join("")
    .toUpperCase();
}

async function secretMatches(actual: string | null, expected?: string) {
  if (!actual || !expected) return false;
  const encoder = new TextEncoder();
  const [actualHash, expectedHash] = await Promise.all([
    crypto.subtle.digest("SHA-256", encoder.encode(actual)),
    crypto.subtle.digest("SHA-256", encoder.encode(expected)),
  ]);
  const left = new Uint8Array(actualHash);
  const right = new Uint8Array(expectedHash);
  let difference = left.length ^ right.length;
  for (let index = 0; index < Math.min(left.length, right.length); index += 1) {
    difference |= left[index] ^ right[index];
  }
  return difference === 0;
}

export async function downloadInstaller(env: PetCareEnv): Promise<Response> {
  const object = await env.CLIPS.get(INSTALLER_OBJECT_KEY);
  if (
    !object ||
    object.size !== INSTALLER_SIZE_BYTES ||
    object.customMetadata?.sha256 !== INSTALLER_SHA256
  ) {
    return jsonError("installer_unavailable", 503);
  }
  return new Response(object.body, {
    headers: {
      "Cache-Control": DOWNLOAD_CACHE_CONTROL,
      "Content-Disposition": `attachment; filename="${INSTALLER_FILE_NAME}"`,
      "Content-Length": String(INSTALLER_SIZE_BYTES),
      "Content-Type": "application/vnd.microsoft.portable-executable",
      "X-Content-Type-Options": "nosniff",
    },
  });
}

export async function uploadInstaller(
  request: Request,
  env: PetCareEnv,
): Promise<Response> {
  if (
    !(await secretMatches(
      request.headers.get(UPLOAD_TOKEN_HEADER),
      env.PETCARE_INSTALLER_UPLOAD_TOKEN,
    ))
  ) {
    return jsonError("not_found", 404);
  }
  const declaredLength = request.headers.get("content-length");
  if (!declaredLength) return jsonError("length_required", 411);
  if (declaredLength !== String(INSTALLER_SIZE_BYTES)) {
    return jsonError("invalid_size", 413);
  }

  const bytes = await request.arrayBuffer();
  if (bytes.byteLength !== INSTALLER_SIZE_BYTES) {
    return jsonError("invalid_size", 413);
  }
  const digest = hex(await crypto.subtle.digest("SHA-256", bytes));
  if (digest !== INSTALLER_SHA256) {
    return jsonError("invalid_digest", 422);
  }

  await env.CLIPS.put(INSTALLER_OBJECT_KEY, bytes, {
    customMetadata: { sha256: INSTALLER_SHA256 },
    httpMetadata: {
      contentType: "application/vnd.microsoft.portable-executable",
    },
  });
  return new Response(null, { status: 204, headers: PRIVATE_HEADERS });
}
