import { getDb } from "../../db";
import { TenantRepository } from "../tenancy/repository";
import { CloudflareClient } from "./cloudflare";
import { EnrollmentProvisioningService } from "./enrollment";
import { readPetCareConfig, type PetCareEnv } from "./env";
import { errorResponse, PetCareError } from "./errors";
import { PetCareRepository } from "./repository";

export type AgentEnrollWireRequest = {
  enrollment_code: string;
  algorithm: "Ed25519";
  public_key: string;
  local_camera_id: "pc-webcam-01";
};

export type AgentEnrollWireResponse = {
  agent_id: string;
  camera_id: string;
  connector_token: string;
};

const MAX_BODY_BYTES = 4096;
const EXPECTED_KEYS = [
  "algorithm",
  "enrollment_code",
  "local_camera_id",
  "public_key",
];

function invalid(): never {
  throw new PetCareError(400, "invalid_request");
}

function isCanonicalBase64Url(value: unknown, length: number, bytes: number) {
  if (
    typeof value !== "string" ||
    value.length !== length ||
    !/^[A-Za-z0-9_-]+$/.test(value)
  ) {
    return false;
  }
  try {
    const standard = value.replaceAll("-", "+").replaceAll("_", "/");
    const decoded = atob(standard.padEnd(Math.ceil(length / 4) * 4, "="));
    return (
      decoded.length === bytes &&
      btoa(decoded)
        .replaceAll("+", "-")
        .replaceAll("/", "_")
        .replace(/=+$/, "") === value
    );
  } catch {
    return false;
  }
}

function isCanonicalIp(value: string | null): value is string {
  if (!value || value.includes(",") || value.trim() !== value) return false;
  if (value.includes(":")) {
    try {
      const normalized = new URL(`http://[${value}]/`).hostname.slice(1, -1);
      return normalized === value.toLowerCase() && value === value.toLowerCase();
    } catch {
      return false;
    }
  }
  const parts = value.split(".");
  return (
    parts.length === 4 &&
    parts.every(
      (part) =>
        /^(0|[1-9]\d{0,2})$/.test(part) && Number(part) >= 0 && Number(part) <= 255,
    )
  );
}

async function boundedBody(request: Request, declaredLength: number) {
  if (!request.body) invalid();
  const reader = request.body!.getReader();
  const chunks: Uint8Array[] = [];
  let size = 0;
  while (true) {
    const { done, value } = await reader.read();
    if (done) break;
    size += value.byteLength;
    if (size > MAX_BODY_BYTES) {
      await reader.cancel();
      invalid();
    }
    chunks.push(value);
  }
  if (size !== declaredLength) invalid();
  const bytes = new Uint8Array(size);
  let offset = 0;
  for (const chunk of chunks) {
    bytes.set(chunk, offset);
    offset += chunk.byteLength;
  }
  try {
    const text = new TextDecoder("utf-8", { fatal: true }).decode(bytes);
    const roundTrip = new TextEncoder().encode(text);
    if (
      roundTrip.length !== bytes.length ||
      roundTrip.some((byte, index) => byte !== bytes[index])
    ) {
      invalid();
    }
    return text;
  } catch {
    invalid();
  }
}

function parsePayload(text: string): AgentEnrollWireRequest {
  let found: string[];
  try {
    found = [...text.matchAll(/"((?:\\.|[^"\\])*)"\s*:/g)].map(
      (match) => JSON.parse(`"${match[1]}"`) as string,
    );
  } catch {
    invalid();
  }
  if (new Set(found).size !== found.length) invalid();

  let value: unknown;
  try {
    value = JSON.parse(text);
  } catch {
    invalid();
  }
  if (!value || Array.isArray(value) || typeof value !== "object") invalid();
  const payload = value as Record<string, unknown>;
  if (
    JSON.stringify(Object.keys(payload).sort()) !== JSON.stringify(EXPECTED_KEYS) ||
    !isCanonicalBase64Url(payload.enrollment_code, 22, 16) ||
    payload.algorithm !== "Ed25519" ||
    !isCanonicalBase64Url(payload.public_key, 43, 32) ||
    payload.local_camera_id !== "pc-webcam-01"
  ) {
    invalid();
  }
  return payload as AgentEnrollWireRequest;
}

export async function handleAgentEnroll(
  request: Request,
  env: PetCareEnv,
  now: Date,
): Promise<Response> {
  try {
    if (request.method !== "POST") {
      return Response.json(
        { error: "method_not_allowed" },
        { status: 405, headers: { Allow: "POST" } },
      );
    }
    const contentType = request.headers.get("Content-Type");
    if (!/^application\/json(?:;\s*charset=utf-8)?$/i.test(contentType ?? "")) {
      invalid();
    }
    const lengthText = request.headers.get("Content-Length");
    if (!/^[1-9]\d*$/.test(lengthText ?? "")) invalid();
    const declaredLength = Number(lengthText);
    if (declaredLength > MAX_BODY_BYTES) invalid();
    const connectingIp = request.headers.get("CF-Connecting-IP");
    if (!isCanonicalIp(connectingIp)) invalid();

    const payload = parsePayload(await boundedBody(request, declaredLength));
    const db = getDb(env.DB);
    const service = new EnrollmentProvisioningService(
      new TenantRepository(db),
      new PetCareRepository(env.DB),
      new CloudflareClient(readPetCareConfig(env)),
      () => now,
    );
    const result = await service.enroll({
      code: payload.enrollment_code,
      publicKey: payload.public_key,
      localCameraId: payload.local_camera_id,
      connectingIp,
    });
    const response: AgentEnrollWireResponse = {
      agent_id: result.agentId,
      camera_id: result.cameraId,
      connector_token: result.connectorToken,
    };
    return Response.json(response, {
      status: 201,
      headers: { "Cache-Control": "private, no-store" },
    });
  } catch (error) {
    return errorResponse(error);
  }
}
