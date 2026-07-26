// @vitest-environment node

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("cloudflare:workers", () => ({ env: {} }));

const repository = vi.hoisted(() => ({
  consumeNonce: vi.fn(),
  putAgentSnapshot: vi.fn(),
  requireOutboundAgent: vi.fn(),
}));

vi.mock("../lib/petcare/repository", () => ({
  PetCareRepository: class {
    consumeNonce = repository.consumeNonce;
    putAgentSnapshot = repository.putAgentSnapshot;
    requireOutboundAgent = repository.requireOutboundAgent;
  },
}));

import { encodeBase64Url } from "../lib/petcare/clip-signature";
import { routePetCare } from "../lib/petcare/router";
import { demoDashboardData } from "../lib/demo-data";

const now = new Date("2026-07-27T00:00:00.000Z");
const summary = Object.fromEntries(
  Object.entries(demoDashboardData).filter(([key]) => key !== "zones" && key !== "calibration"),
);

async function signedRequest(
  privateKey: CryptoKey,
  publicKey: string,
  body = JSON.stringify(summary),
  overrides: Record<string, string> = {},
): Promise<Request> {
  const timestamp = String(Math.floor(now.getTime() / 1_000));
  const nonce = "MDEyMzQ1Njc4OWFiY2RlZg";
  const digest = encodeBase64Url(
    new Uint8Array(await crypto.subtle.digest("SHA-256", new TextEncoder().encode(body))),
  );
  const canonical = [
    "PETCARE-SNAPSHOT-V1",
    "POST",
    "/api/petcare/agent/snapshot",
    "agent_01",
    timestamp,
    nonce,
    digest,
    "",
  ].join("\n");
  const signature = encodeBase64Url(
    new Uint8Array(
      await crypto.subtle.sign("Ed25519", privateKey, new TextEncoder().encode(canonical)),
    ),
  );
  return new Request("https://pets.example/api/petcare/agent/snapshot", {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      "Content-Length": String(new TextEncoder().encode(body).byteLength),
      "X-PetCare-Agent-Id": "agent_01",
      "X-PetCare-Timestamp": timestamp,
      "X-PetCare-Nonce": nonce,
      "X-PetCare-Content-SHA256": digest,
      "X-PetCare-Signature": signature,
      ...overrides,
    },
    body,
  });
}

describe("signed snapshot request", () => {
  let privateKey: CryptoKey;
  let publicKey: string;

  beforeEach(async () => {
    vi.clearAllMocks();
    vi.useFakeTimers();
    vi.setSystemTime(now);
    const keys = await crypto.subtle.generateKey("Ed25519", true, ["sign", "verify"]);
    privateKey = keys.privateKey;
    publicKey = encodeBase64Url(new Uint8Array(await crypto.subtle.exportKey("raw", keys.publicKey)));
    repository.requireOutboundAgent.mockResolvedValue({
      homeId: "home_01",
      agentId: "agent_01",
      cameraId: "camera_01",
      publicKey,
    });
    repository.consumeNonce.mockResolvedValue(undefined);
    repository.putAgentSnapshot.mockResolvedValue(undefined);
  });

  afterEach(() => {
    vi.useRealTimers();
    vi.restoreAllMocks();
  });

  it("accepts only the exact PETCARE-SNAPSHOT-V1 canonical body and stores it once", async () => {
    const request = await signedRequest(privateKey, publicKey);

    const response = await routePetCare(request, { DB: {} as never, CLIPS: {} as never } as never, {
      waitUntil: vi.fn(),
    });

    expect(response?.status).toBe(204);
    expect(response?.headers.get("Cache-Control")).toBe("private, no-store");
    expect(repository.consumeNonce).toHaveBeenCalledWith(
      "agent_01",
      "MDEyMzQ1Njc4OWFiY2RlZg",
      now.toISOString(),
    );
    expect(repository.putAgentSnapshot).toHaveBeenCalledWith(
      "agent_01",
      JSON.stringify(summary),
      summary.generated_at,
      now.toISOString(),
    );
  });

  it("rejects a strict-summary violation before nonce or snapshot mutation", async () => {
    const body = JSON.stringify({ ...summary, extra: true });
    const request = await signedRequest(privateKey, publicKey, body);

    const response = await routePetCare(request, { DB: {} as never, CLIPS: {} as never } as never, {
      waitUntil: vi.fn(),
    });

    expect(response?.status).toBe(400);
    expect(repository.consumeNonce).not.toHaveBeenCalled();
    expect(repository.putAgentSnapshot).not.toHaveBeenCalled();
  });
});
