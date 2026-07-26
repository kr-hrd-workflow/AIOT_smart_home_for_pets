// @vitest-environment node

import { beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("cloudflare:workers", () => ({ env: {} }));

const repository = vi.hoisted(() => ({
  acknowledgeActivityCleanup: vi.fn(),
  checkRateLimit: vi.fn(),
  consumeNonce: vi.fn(),
  findActiveActivityCleanupAgent: vi.fn(),
  requireActivityCleanupAgent: vi.fn(),
}));

vi.mock("../lib/petcare/repository", () => ({
  PetCareRepository: class {
    acknowledgeActivityCleanup = repository.acknowledgeActivityCleanup;
    checkRateLimit = repository.checkRateLimit;
    consumeNonce = repository.consumeNonce;
    findActiveActivityCleanupAgent = repository.findActiveActivityCleanupAgent;
    requireActivityCleanupAgent = repository.requireActivityCleanupAgent;
  },
}));

import { encodeBase64Url } from "../lib/petcare/clip-signature";
import {
  canonicalCleanupRequest,
  handleAgentActivityCleanup,
  parseSignedCleanupRequest,
} from "../lib/petcare/agent-cleanup";
import { PetCareError } from "../lib/petcare/errors";

const NOW = new Date("2026-07-26T04:00:00.000Z");
const AGENT_ID = "agent_01";
const COMMAND_ID = "clc_0123456789abcdef0123456789abcdef";

let privateKey: CryptoKey;
let publicKey: string;

async function signedRequest(
  body = '{"action":"poll"}',
  overrides: Record<string, string> = {},
): Promise<Request> {
  const digest = encodeBase64Url(new Uint8Array(
    await crypto.subtle.digest("SHA-256", new TextEncoder().encode(body)),
  ));
  const unsigned = new Request("https://pets.example/api/petcare/agent/cleanup", {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      "X-PetCare-Agent-Id": AGENT_ID,
      "X-PetCare-Timestamp": String(Math.floor(NOW.getTime() / 1000)),
      "X-PetCare-Nonce": "MDEyMzQ1Njc4OWFiY2RlZg",
      "X-PetCare-Content-SHA256": digest,
      "X-PetCare-Signature": encodeBase64Url(new Uint8Array(64)),
      ...overrides,
    },
    body,
  });
  const parsed = await parseSignedCleanupRequest(unsigned.clone());
  const signature = new Uint8Array(await crypto.subtle.sign(
    { name: "Ed25519" },
    privateKey,
    Uint8Array.from(canonicalCleanupRequest(parsed)).buffer,
  ));
  const headers = new Headers(unsigned.headers);
  headers.set("X-PetCare-Signature", encodeBase64Url(signature));
  return new Request("https://pets.example/api/petcare/agent/cleanup", {
    method: "POST",
    headers,
    body,
  });
}

function oversizedRequest(body: ReadableStream<Uint8Array>, contentLength?: string): Request {
  const headers = new Headers({
    "Content-Type": "application/json",
    "X-PetCare-Agent-Id": AGENT_ID,
    "X-PetCare-Timestamp": "0",
    "X-PetCare-Nonce": encodeBase64Url(new Uint8Array(16)),
    "X-PetCare-Content-SHA256": encodeBase64Url(new Uint8Array(32)),
    "X-PetCare-Signature": encodeBase64Url(new Uint8Array(64)),
  });
  if (contentLength) headers.set("Content-Length", contentLength);
  return new Request("https://pets.example/api/petcare/agent/cleanup", {
    method: "POST",
    headers,
    body,
    duplex: "half",
  });
}

beforeEach(async () => {
  vi.clearAllMocks();
  const keys = await crypto.subtle.generateKey({ name: "Ed25519" }, true, ["sign", "verify"]);
  privateKey = keys.privateKey;
  publicKey = encodeBase64Url(new Uint8Array(await crypto.subtle.exportKey("raw", keys.publicKey)));
  repository.requireActivityCleanupAgent.mockResolvedValue({
    commandId: COMMAND_ID,
    homeId: "home_01",
    publicKey,
    type: "delete_activity_observations",
  });
  repository.checkRateLimit.mockResolvedValue(undefined);
  repository.consumeNonce.mockResolvedValue(undefined);
  repository.acknowledgeActivityCleanup.mockResolvedValue(undefined);
  repository.findActiveActivityCleanupAgent.mockResolvedValue(null);
});

describe("agent activity cleanup", () => {
  it("returns only the pending command identity and type", async () => {
    const response = await handleAgentActivityCleanup(await signedRequest(), { DB: {} as never }, NOW);

    expect(response.status).toBe(200);
    expect(response.headers.get("Cache-Control")).toBe("private, no-store");
    await expect(response.json()).resolves.toEqual({
      commandId: COMMAND_ID,
      type: "delete_activity_observations",
    });
    expect(repository.requireActivityCleanupAgent).toHaveBeenCalledWith(AGENT_ID, undefined, false);
    expect(repository.checkRateLimit).toHaveBeenCalledWith(AGENT_ID, "activity-cleanup", 30, 60, NOW);
    expect(repository.consumeNonce).toHaveBeenCalledWith(AGENT_ID, "MDEyMzQ1Njc4OWFiY2RlZg", NOW.toISOString());
  });

  it("returns an empty 204 for a signed active agent with no pending cleanup", async () => {
    repository.requireActivityCleanupAgent.mockResolvedValueOnce(null);
    repository.findActiveActivityCleanupAgent.mockResolvedValueOnce({ publicKey });

    const response = await handleAgentActivityCleanup(await signedRequest(), { DB: {} as never }, NOW);

    expect(response.status).toBe(204);
    expect(response.headers.get("Cache-Control")).toBe("private, no-store");
    expect(repository.findActiveActivityCleanupAgent).toHaveBeenCalledWith(AGENT_ID);
    expect(repository.consumeNonce).toHaveBeenCalledWith(AGENT_ID, "MDEyMzQ1Njc4OWFiY2RlZg", NOW.toISOString());
  });

  it("rejects a wrong, stale, or replayed signed request", async () => {
    const wrong = await signedRequest();
    const wrongHeaders = new Headers(wrong.headers);
    wrongHeaders.set("X-PetCare-Signature", encodeBase64Url(new Uint8Array(64).fill(1)));
    const wrongSignature = new Request(wrong, {
      headers: wrongHeaders,
    });
    await expect(handleAgentActivityCleanup(wrongSignature, { DB: {} as never }, NOW)).rejects.toMatchObject({
      status: 401,
      code: "invalid_agent_signature",
    });

    const stale = await signedRequest(undefined, {
      "X-PetCare-Timestamp": String(Math.floor(NOW.getTime() / 1000) - 301),
    });
    await expect(handleAgentActivityCleanup(stale, { DB: {} as never }, NOW)).rejects.toMatchObject({
      status: 401,
      code: "invalid_agent_signature",
    });

    repository.consumeNonce.mockRejectedValueOnce(new PetCareError(409, "replay"));
    await expect(handleAgentActivityCleanup(await signedRequest(), { DB: {} as never }, NOW)).rejects.toMatchObject({
      status: 409,
      code: "replay",
    });
  });

  it("rejects an invalid signature from an otherwise active agent", async () => {
    repository.requireActivityCleanupAgent.mockResolvedValueOnce(null);
    repository.findActiveActivityCleanupAgent.mockResolvedValueOnce({ publicKey });
    const signed = await signedRequest();
    const headers = new Headers(signed.headers);
    headers.set("X-PetCare-Signature", encodeBase64Url(new Uint8Array(64).fill(1)));

    await expect(handleAgentActivityCleanup(new Request(signed, { headers }), { DB: {} as never }, NOW)).rejects.toMatchObject({
      status: 401,
      code: "invalid_agent_signature",
    });
    expect(repository.consumeNonce).not.toHaveBeenCalled();
  });

  it("rejects a non-cleanup or foreign agent before nonce consumption", async () => {
    repository.requireActivityCleanupAgent.mockResolvedValueOnce(null);

    await expect(handleAgentActivityCleanup(await signedRequest(), { DB: {} as never }, NOW)).rejects.toMatchObject({
      status: 401,
      code: "invalid_agent_signature",
    });
    expect(repository.consumeNonce).not.toHaveBeenCalled();
  });

  it("acknowledges only its exact command and allows an idempotent retry", async () => {
    const body = `{"action":"ack","commandId":"${COMMAND_ID}"}`;
    const first = await handleAgentActivityCleanup(await signedRequest(body), { DB: {} as never }, NOW);
    const retry = await handleAgentActivityCleanup(await signedRequest(body, {
      "X-PetCare-Nonce": "ZmVkY2JhOTg3NjU0MzIxMA",
    }), { DB: {} as never }, NOW);

    expect(first.status).toBe(204);
    expect(retry.status).toBe(204);
    expect(repository.requireActivityCleanupAgent).toHaveBeenCalledWith(AGENT_ID, COMMAND_ID, true);
    expect(repository.acknowledgeActivityCleanup).toHaveBeenCalledWith(AGENT_ID, COMMAND_ID, NOW.toISOString());
  });

  it("rejects non-canonical JSON without consulting D1", async () => {
    const request = new Request("https://pets.example/api/petcare/agent/cleanup", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: '{ "action": "poll" }',
    });

    await expect(parseSignedCleanupRequest(request)).rejects.toMatchObject({
      status: 400,
      code: "invalid_cleanup_request",
    });
    expect(repository.requireActivityCleanupAgent).not.toHaveBeenCalled();
  });

  it("rejects declared cleanup bodies over 128 bytes before reading them", async () => {
    let cancelled = false;
    const request = oversizedRequest(new ReadableStream({
      pull() {
        throw new Error("body must not be read");
      },
      cancel() {
        cancelled = true;
      },
    }), "129");

    await expect(parseSignedCleanupRequest(request)).rejects.toMatchObject({
      status: 400,
      code: "invalid_cleanup_request",
    });
    expect(cancelled).toBe(true);
  });

  it("cancels an oversized cleanup stream before buffering its next chunk", async () => {
    let cancelled = false;
    let pulls = 0;
    const request = oversizedRequest(new ReadableStream({
      pull(controller) {
        if (pulls++ === 0) controller.enqueue(new Uint8Array(1024));
        else controller.close();
      },
      cancel() {
        cancelled = true;
      },
    }));

    await expect(parseSignedCleanupRequest(request)).rejects.toMatchObject({
      status: 400,
      code: "invalid_cleanup_request",
    });
    expect(cancelled).toBe(true);
  });
});
