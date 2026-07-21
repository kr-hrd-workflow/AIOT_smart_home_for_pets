// @vitest-environment node

import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("cloudflare:workers", () => ({ env: {} }));

const repository = vi.hoisted(() => ({
  requireActiveAgent: vi.fn(),
  markAgentSeen: vi.fn(),
  checkRateLimit: vi.fn(),
  consumeNonce: vi.fn(),
  publishClip: vi.fn(),
  queueObjectDeletion: vi.fn(),
}));

vi.mock("../lib/petcare/repository", () => ({
  PetCareRepository: class {
    requireActiveAgent = repository.requireActiveAgent;
    markAgentSeen = repository.markAgentSeen;
    checkRateLimit = repository.checkRateLimit;
    consumeNonce = repository.consumeNonce;
    publishClip = repository.publishClip;
    queueObjectDeletion = repository.queueObjectDeletion;
  },
}));

import {
  canonicalClipRequest,
  encodeBase64Url,
  parseSignedClipHeaders,
  verifyClipSignature,
} from "../lib/petcare/clip-signature";
import { uploadSignedClip } from "../lib/petcare/clip-upload";
import { errorResponse, PetCareError } from "../lib/petcare/errors";
import { FakeD1 } from "./helpers/petcare-fakes";

interface WireFixture {
  clip: {
    canonical: string;
    body_base64: string;
    headers: Record<string, string>;
    public_key: string;
  };
}

const fixture = JSON.parse(
  readFileSync(resolve(import.meta.dirname, "../../contracts/petcare-agent-wire-v1.json"), "utf8"),
) as WireFixture;

function fixtureRequest(overrides: Record<string, string> = {}): Request {
  return new Request("https://example.test/api/petcare/agent/clips", {
    method: "POST",
    headers: { ...fixture.clip.headers, ...overrides },
    body: new Uint8Array([1]),
  });
}

const fixtureBody = Uint8Array.from(atob(fixture.clip.body_base64), (character) =>
  character.charCodeAt(0),
);
const fixedNow = new Date("2026-07-20T04:00:21.000Z");

class FakeR2 {
  objects = new Map<string, Uint8Array>();
  deleted: string[] = [];
  failPut = false;
  failDelete = false;
  reportedSizeDelta = 0;

  async put(key: string, body: ReadableStream<Uint8Array>): Promise<{ key: string; size: number }> {
    if (this.failPut) throw new Error("secret-r2-failure");
    const chunks: Uint8Array[] = [];
    let size = 0;
    for await (const chunk of body as never as AsyncIterable<Uint8Array>) {
      chunks.push(chunk);
      size += chunk.byteLength;
    }
    const bytes = new Uint8Array(size);
    let offset = 0;
    for (const chunk of chunks) {
      bytes.set(chunk, offset);
      offset += chunk.byteLength;
    }
    this.objects.set(key, bytes);
    return { key, size: size + this.reportedSizeDelta };
  }

  async delete(key: string): Promise<void> {
    if (this.failDelete) throw new Error("secret-delete-failure");
    this.deleted.push(key);
    this.objects.delete(key);
  }
}

function uploadRequest(
  body: Uint8Array = fixtureBody,
  overrides: Record<string, string> = {},
): Request {
  return new Request("https://example.test/api/petcare/agent/clips", {
    method: "POST",
    headers: { ...fixture.clip.headers, ...overrides },
    body: Uint8Array.from(body).buffer,
  });
}

async function resignedRequest(
  overrides: Record<string, string>,
  body: Uint8Array = fixtureBody,
): Promise<{ request: Request; publicKey: string }> {
  const keys = await crypto.subtle.generateKey({ name: "Ed25519" }, true, ["sign", "verify"]);
  const unsigned = parseSignedClipHeaders(uploadRequest(body, overrides));
  const signature = new Uint8Array(
    await crypto.subtle.sign(
      { name: "Ed25519" },
      keys.privateKey,
      Uint8Array.from(canonicalClipRequest(unsigned)).buffer,
    ),
  );
  const publicKey = new Uint8Array(await crypto.subtle.exportKey("raw", keys.publicKey));
  return {
    request: uploadRequest(body, {
      ...overrides,
      "X-PetCare-Signature": encodeBase64Url(signature),
    }),
    publicKey: encodeBase64Url(publicKey),
  };
}

function env(r2: FakeR2) {
  return { DB: {} as never, CLIPS: r2 as never } as never;
}

async function activationPendingDb(): Promise<FakeD1> {
  const db = new FakeD1();
  await db
    .prepare("INSERT INTO homes (id, owner_sub, created_at) VALUES (?, ?, ?)")
    .bind("home_01", "owner_01", "2026-07-20T03:50:00.000Z")
    .run();
  await db
    .prepare(
      "INSERT INTO agents (id, home_id, public_key, tunnel_origin) VALUES (?, ?, ?, ?)",
    )
    .bind("agent_01", "home_01", fixture.clip.public_key, "https://agent.example.test/")
    .run();
  await db
    .prepare(
      "INSERT INTO cameras (id, home_id, agent_id, local_camera_id, created_at) VALUES (?, ?, ?, ?, ?)",
    )
    .bind("camera_01", "home_01", "agent_01", "pc-webcam-01", "2026-07-20T03:50:00.000Z")
    .run();
  await db
    .prepare(`
      INSERT INTO tunnel_routes (
        home_id, agent_id, tunnel_origin, activation_expires_at, status, created_at, updated_at
      ) VALUES (?, ?, ?, ?, 'activation_pending', ?, ?)
    `)
    .bind(
      "home_01",
      "agent_01",
      "https://agent.example.test/",
      "2099-01-01T00:00:00.000Z",
      "2026-07-20T03:50:00.000Z",
      "2026-07-20T03:50:00.000Z",
    )
    .run();
  return db;
}

beforeEach(() => {
  vi.clearAllMocks();
  repository.requireActiveAgent.mockResolvedValue({
    homeId: "home_01",
    agentId: "agent_01",
    cameraId: "camera_01",
    publicKey: fixture.clip.public_key,
  });
  repository.checkRateLimit.mockResolvedValue(undefined);
  repository.consumeNonce.mockResolvedValue(undefined);
  repository.markAgentSeen.mockResolvedValue(undefined);
  repository.publishClip.mockResolvedValue(undefined);
  repository.queueObjectDeletion.mockResolvedValue(undefined);
});

describe("signed clip request", () => {
  it("verifies the shared PETCARE-CLIP-V1 Ed25519 fixture over exact canonical bytes", async () => {
    const headers = parseSignedClipHeaders(fixtureRequest());
    expect(new TextDecoder().decode(canonicalClipRequest(headers))).toBe(fixture.clip.canonical);
    await expect(verifyClipSignature(headers, fixture.clip.public_key)).resolves.toBeUndefined();
  });

  it("rejects a wrong key, wrong signature, and case-folded signed values", async () => {
    const headers = parseSignedClipHeaders(fixtureRequest());
    const wrongKey = await crypto.subtle.generateKey({ name: "Ed25519" }, true, ["sign", "verify"]);
    const wrongRaw = new Uint8Array(await crypto.subtle.exportKey("raw", wrongKey.publicKey));
    await expect(verifyClipSignature(headers, encodeBase64Url(wrongRaw))).rejects.toMatchObject({
      status: 401,
      code: "invalid_agent_signature",
    });

    const wrongSignature = parseSignedClipHeaders(
      fixtureRequest({ "X-PetCare-Signature": `A${fixture.clip.headers["X-PetCare-Signature"].slice(1)}` }),
    );
    await expect(verifyClipSignature(wrongSignature, fixture.clip.public_key)).rejects.toMatchObject({
      status: 401,
      code: "invalid_agent_signature",
    });

    const foldedNonce = parseSignedClipHeaders(
      fixtureRequest({
        "X-PetCare-Nonce": `a${fixture.clip.headers["X-PetCare-Nonce"].slice(1)}`,
      }),
    );
    await expect(verifyClipSignature(foldedNonce, fixture.clip.public_key)).rejects.toMatchObject({
      status: 401,
      code: "invalid_agent_signature",
    });

    await expect(verifyClipSignature(headers, "not-a-public-key")).rejects.toMatchObject({
      status: 401,
      code: "invalid_agent_signature",
    });
  });

  it("verifies an independently generated Ed25519 signature", async () => {
    const keys = await crypto.subtle.generateKey({ name: "Ed25519" }, true, ["sign", "verify"]);
    const unsigned = parseSignedClipHeaders(fixtureRequest());
    const signature = new Uint8Array(
      await crypto.subtle.sign(
        { name: "Ed25519" },
        keys.privateKey,
        Uint8Array.from(canonicalClipRequest(unsigned)).buffer,
      ),
    );
    const publicKey = new Uint8Array(await crypto.subtle.exportKey("raw", keys.publicKey));
    await expect(
      verifyClipSignature({ ...unsigned, signature }, encodeBase64Url(publicKey)),
    ).resolves.toBeUndefined();
  });

  it.each([
    ["Content-Type", "video/webm", "invalid_content_type"],
    ["Content-Length", "0", "invalid_content_length"],
    ["Content-Length", "52428801", "invalid_content_length"],
    ["X-PetCare-Nonce", "not+base64url============", "invalid_clip_request"],
    ["X-PetCare-Started-At", "2026-07-20T04:00:20.000000Z", "invalid_clip_duration"],
    ["X-PetCare-Ended-At", "2026-07-20T04:02:00.000001Z", "invalid_clip_duration"],
    ["X-PetCare-Events", "eating:41,bed_sensor_mismatch:7", "invalid_clip_events"],
    ["X-PetCare-Events", "eating:41,eating:41", "invalid_clip_events"],
    ["X-PetCare-Events", "no_meal_12h:1", "invalid_clip_events"],
  ])("rejects invalid %s", (name, value, code) => {
    expect(() => parseSignedClipHeaders(fixtureRequest({ [name]: value }))).toThrowError(
      expect.objectContaining({ status: 400, code }),
    );
  });

  it("rejects coalesced duplicate singleton headers", () => {
    const request = fixtureRequest();
    request.headers.append("X-PetCare-Agent-Id", "agent_02");
    expect(() => parseSignedClipHeaders(request)).toThrowError(
      expect.objectContaining({ status: 400, code: "invalid_clip_request" }),
    );
  });

  it("rejects coalesced duplicate event headers", () => {
    const request = fixtureRequest();
    request.headers.append("X-PetCare-Events", "resting:999");
    expect(() => parseSignedClipHeaders(request)).toThrowError(
      expect.objectContaining({ status: 400, code: "invalid_clip_request" }),
    );
  });

  it("rejects an absent Content-Length", () => {
    const request = fixtureRequest();
    request.headers.delete("Content-Length");
    expect(() => parseSignedClipHeaders(request)).toThrowError(
      expect.objectContaining({ status: 400, code: "invalid_clip_request" }),
    );
  });

  it("accepts an exact 120-second duration and rejects the wrong route", () => {
    expect(() =>
      parseSignedClipHeaders(
        fixtureRequest({ "X-PetCare-Ended-At": "2026-07-20T04:01:50.000000Z" }),
      ),
    ).not.toThrow();
    const request = new Request("https://example.test/api/petcare/agent/clips?debug=1", {
      method: "POST",
      headers: fixture.clip.headers,
      body: new Uint8Array([1]),
    });
    expect(() => parseSignedClipHeaders(request)).toThrowError(
      expect.objectContaining({ status: 400, code: "invalid_clip_request" }),
    );
  });
});

describe("clip upload", () => {
  it("stores a private opaque object and publishes the exact seven-day receipt", async () => {
    const r2 = new FakeR2();
    const response = await uploadSignedClip(uploadRequest(), env(r2), fixedNow);

    expect(response.status).toBe(201);
    expect(response.headers.get("Cache-Control")).toBe("private, no-store");
    const receipt = await response.json();
    expect(Object.keys(receipt)).toEqual(["id", "createdAt", "expiresAt"]);
    expect(receipt).toEqual({
      id: expect.any(String),
      createdAt: "2026-07-20T04:00:21.000Z",
      expiresAt: "2026-07-27T04:00:21.000Z",
    });

    const [[published]] = repository.publishClip.mock.calls;
    expect(published).toMatchObject({
      id: receipt.id,
      homeId: "home_01",
      agentId: "agent_01",
      cameraId: "camera_01",
      sizeBytes: 9,
      sha256: fixture.clip.headers["X-PetCare-Content-SHA256"],
      events: [
        { eventType: "bed_sensor_mismatch", eventId: "7" },
        { eventType: "eating", eventId: "41" },
        { eventType: "resting", eventId: "105" },
      ],
    });
    expect(published.objectKey).toMatch(/^clips\/[0-9a-f-]{36}\.mp4$/);
    expect(published.objectKey).not.toContain("agent_01");
    expect(published.objectKey).not.toContain("camera_01");
    expect(r2.objects.get(published.objectKey)).toEqual(fixtureBody);
    expect(repository.checkRateLimit).toHaveBeenCalledWith("agent_01", "clip-upload", 30, 60, fixedNow);
    expect(repository.checkRateLimit).toHaveBeenCalledBefore(repository.consumeNonce);
    expect(repository.consumeNonce).toHaveBeenCalledBefore(repository.publishClip);
    expect(repository.markAgentSeen).toHaveBeenCalledOnce();
    expect(repository.markAgentSeen).toHaveBeenCalledWith("agent_01", "2026-07-20T04:00:21.000Z");
    expect(repository.markAgentSeen).toHaveBeenCalledBefore(repository.publishClip);
  });

  it.each([-300, 300])("accepts the signed timestamp boundary at %+d seconds", async (delta) => {
    const timestamp = Math.floor(fixedNow.getTime() / 1000) + delta;
    const signed = await resignedRequest({ "X-PetCare-Timestamp": String(timestamp) });
    repository.requireActiveAgent.mockResolvedValue({
      homeId: "home_01",
      agentId: "agent_01",
      cameraId: "camera_01",
      publicKey: signed.publicKey,
    });
    await expect(uploadSignedClip(signed.request, env(new FakeR2()), fixedNow)).resolves.toMatchObject({
      status: 201,
    });
  });

  it.each([-301, 301])("rejects the signed timestamp outside the window at %+d seconds", async (delta) => {
    const timestamp = Math.floor(fixedNow.getTime() / 1000) + delta;
    const signed = await resignedRequest({ "X-PetCare-Timestamp": String(timestamp) });
    repository.requireActiveAgent.mockResolvedValue({
      homeId: "home_01",
      agentId: "agent_01",
      cameraId: "camera_01",
      publicKey: signed.publicKey,
    });
    await expect(uploadSignedClip(signed.request, env(new FakeR2()), fixedNow)).rejects.toMatchObject({
      status: 401,
      code: "invalid_agent_signature",
    });
    expect(repository.consumeNonce).not.toHaveBeenCalled();
  });

  it("rejects replay before reading or storing the body", async () => {
    repository.consumeNonce.mockRejectedValue(new PetCareError(409, "replay"));
    const r2 = new FakeR2();
    await expect(uploadSignedClip(uploadRequest(), env(r2), fixedNow)).rejects.toMatchObject({
      status: 409,
      code: "replay",
    });
    expect(r2.objects.size).toBe(0);
  });

  it("rejects a revoked agent or camera before consuming a nonce", async () => {
    repository.requireActiveAgent.mockRejectedValue(new PetCareError(503, "agent_offline"));
    const body = new ReadableStream<Uint8Array>({
      pull(controller) {
        controller.enqueue(fixtureBody);
        controller.close();
      },
    });
    const request = new Request("https://example.test/api/petcare/agent/clips", {
      method: "POST",
      headers: fixture.clip.headers,
      body,
      duplex: "half",
    } as RequestInit & { duplex: "half" });
    await expect(uploadSignedClip(request, env(new FakeR2()), fixedNow)).rejects.toMatchObject({
      status: 503,
      code: "agent_offline",
    });
    expect(repository.checkRateLimit).not.toHaveBeenCalled();
    expect(repository.consumeNonce).not.toHaveBeenCalled();
    expect(repository.markAgentSeen).not.toHaveBeenCalled();
    expect(request.bodyUsed).toBe(false);
  });

  it("rejects a wrong registered key before rate limit, nonce, or body read", async () => {
    const wrongKey = await crypto.subtle.generateKey({ name: "Ed25519" }, true, ["sign", "verify"]);
    const wrongRaw = new Uint8Array(await crypto.subtle.exportKey("raw", wrongKey.publicKey));
    repository.requireActiveAgent.mockResolvedValue({
      homeId: "home_01",
      agentId: "agent_01",
      cameraId: "camera_01",
      publicKey: encodeBase64Url(wrongRaw),
    });
    const body = new ReadableStream<Uint8Array>({
      pull(controller) {
        controller.enqueue(fixtureBody);
        controller.close();
      },
    });
    const request = new Request("https://example.test/api/petcare/agent/clips", {
      method: "POST",
      headers: fixture.clip.headers,
      body,
      duplex: "half",
    } as RequestInit & { duplex: "half" });
    await expect(uploadSignedClip(request, env(new FakeR2()), fixedNow)).rejects.toMatchObject({
      status: 401,
      code: "invalid_agent_signature",
    });
    expect(repository.checkRateLimit).not.toHaveBeenCalled();
    expect(repository.consumeNonce).not.toHaveBeenCalled();
    expect(repository.markAgentSeen).not.toHaveBeenCalled();
    expect(request.bodyUsed).toBe(false);
  });

  it("rejects the 31st request before consuming a nonce", async () => {
    repository.checkRateLimit.mockRejectedValue(new PetCareError(429, "rate_limited"));
    await expect(uploadSignedClip(uploadRequest(), env(new FakeR2()), fixedNow)).rejects.toMatchObject({
      status: 429,
      code: "rate_limited",
    });
    expect(repository.consumeNonce).not.toHaveBeenCalled();
  });

  it("keeps the nonce spent when R2 fails", async () => {
    const r2 = new FakeR2();
    r2.failPut = true;
    await expect(uploadSignedClip(uploadRequest(), env(r2), fixedNow)).rejects.toMatchObject({
      status: 503,
      code: "upload_retryable",
    });
    expect(repository.consumeNonce).toHaveBeenCalledOnce();
  });

  it("stops a streamed body that exceeds 50 MiB even when the claim is in range", async () => {
    const signed = await resignedRequest({ "Content-Length": String(50 * 1024 * 1024) });
    repository.requireActiveAgent.mockResolvedValue({
      homeId: "home_01",
      agentId: "agent_01",
      cameraId: "camera_01",
      publicKey: signed.publicKey,
    });
    const chunk = new Uint8Array(1024 * 1024);
    let sent = 0;
    let cancelled = false;
    const body = new ReadableStream<Uint8Array>({
      pull(controller) {
        if (sent < 50) controller.enqueue(chunk);
        else controller.enqueue(new Uint8Array([0]));
        sent += 1;
      },
      cancel() {
        cancelled = true;
      },
    });
    const request = new Request("https://example.test/api/petcare/agent/clips", {
      method: "POST",
      headers: signed.request.headers,
      body,
      duplex: "half",
    } as RequestInit & { duplex: "half" });
    await expect(uploadSignedClip(request, env(new FakeR2()), fixedNow)).rejects.toMatchObject({
      status: 400,
      code: "invalid_content_length",
    });
    expect(repository.consumeNonce).toHaveBeenCalledOnce();
    expect(cancelled).toBe(true);
  });

  it("rejects an actual zero-byte stream and removes the unpublished object", async () => {
    const r2 = new FakeR2();
    await expect(uploadSignedClip(uploadRequest(new Uint8Array()), env(r2), fixedNow)).rejects.toMatchObject({
      status: 400,
      code: "invalid_content_length",
    });
    expect(repository.consumeNonce).toHaveBeenCalledOnce();
    expect(r2.deleted).toHaveLength(1);
  });

  it("deletes an unpublished object when the streamed length or digest differs", async () => {
    const lengthR2 = new FakeR2();
    await expect(
      uploadSignedClip(uploadRequest(fixtureBody.subarray(0, 8)), env(lengthR2), fixedNow),
    ).rejects.toMatchObject({ status: 400, code: "invalid_content_length" });
    expect(lengthR2.deleted).toHaveLength(1);

    const digestR2 = new FakeR2();
    const changed = Uint8Array.from(fixtureBody);
    changed[0] ^= 1;
    await expect(uploadSignedClip(uploadRequest(changed), env(digestR2), fixedNow)).rejects.toMatchObject({
      status: 400,
      code: "digest_mismatch",
    });
    expect(digestR2.deleted).toHaveLength(1);
    expect(repository.consumeNonce).toHaveBeenCalledTimes(2);
    expect(repository.markAgentSeen).not.toHaveBeenCalled();
  });

  it("rejects an R2 object size that differs from the streamed count", async () => {
    const r2 = new FakeR2();
    r2.reportedSizeDelta = 1;
    await expect(uploadSignedClip(uploadRequest(), env(r2), fixedNow)).rejects.toMatchObject({
      status: 400,
      code: "invalid_content_length",
    });
    expect(r2.deleted).toHaveLength(1);
  });

  it("rejects a signed but false claimed digest after upload", async () => {
    const signed = await resignedRequest({
      "X-PetCare-Content-SHA256": encodeBase64Url(new Uint8Array(32)),
    });
    repository.requireActiveAgent.mockResolvedValue({
      homeId: "home_01",
      agentId: "agent_01",
      cameraId: "camera_01",
      publicKey: signed.publicKey,
    });
    const r2 = new FakeR2();
    await expect(uploadSignedClip(signed.request, env(r2), fixedNow)).rejects.toMatchObject({
      status: 400,
      code: "digest_mismatch",
    });
    expect(r2.deleted).toHaveLength(1);
  });

  it("deletes the object on D1 failure and preserves account deletion", async () => {
    const r2 = new FakeR2();
    repository.publishClip.mockRejectedValueOnce(new Error("secret-d1-failure"));
    await expect(uploadSignedClip(uploadRequest(), env(r2), fixedNow)).rejects.toMatchObject({
      status: 503,
      code: "upload_retryable",
    });
    expect(r2.deleted).toHaveLength(1);

    repository.publishClip.mockRejectedValueOnce(new PetCareError(410, "account_deleted"));
    await expect(uploadSignedClip(uploadRequest(), env(r2), fixedNow)).rejects.toMatchObject({
      status: 410,
      code: "account_deleted",
    });
    expect(r2.deleted).toHaveLength(2);
  });

  it("fails closed and rolls back when cleanup or revocation wins the activation race", async () => {
    const r2 = new FakeR2();
    repository.markAgentSeen.mockRejectedValue(new PetCareError(503, "agent_offline"));
    await expect(uploadSignedClip(uploadRequest(), env(r2), fixedNow)).rejects.toMatchObject({
      status: 503,
      code: "upload_retryable",
    });
    expect(repository.markAgentSeen).toHaveBeenCalledOnce();
    expect(repository.publishClip).not.toHaveBeenCalled();
    expect(r2.deleted).toHaveLength(1);
  });

  it("queues cleanup when rollback deletion fails", async () => {
    const r2 = new FakeR2();
    r2.failDelete = true;
    repository.publishClip.mockRejectedValue(new Error("secret-d1-failure"));
    await expect(uploadSignedClip(uploadRequest(), env(r2), fixedNow)).rejects.toMatchObject({
      status: 503,
      code: "upload_retryable",
    });
    expect(repository.queueObjectDeletion).toHaveBeenCalledWith(
      "home_01",
      expect.stringMatching(/^clips\//),
      "2026-07-20T04:00:21.000Z",
    );
  });

  it("redacts keys, signatures, nonces, digests, object names, and backend failures", async () => {
    const logs = (["debug", "error", "info", "log", "warn"] as const).map((method) =>
      vi.spyOn(console, method).mockImplementation(() => undefined),
    );
    const objectUuid = "11111111-1111-4111-8111-111111111111";
    const clipUuid = "22222222-2222-4222-8222-222222222222";
    const uuid = vi.spyOn(crypto, "randomUUID")
      .mockReturnValueOnce(objectUuid)
      .mockReturnValueOnce(clipUuid);
    const r2 = new FakeR2();
    repository.publishClip.mockRejectedValue(new Error("secret-d1-failure"));
    let thrown: unknown;
    try {
      await uploadSignedClip(uploadRequest(), env(r2), fixedNow);
    } catch (error) {
      thrown = error;
    }
    const response = errorResponse(thrown);
    expect(await response.json()).toEqual({ error: "upload_retryable" });
    const publicSurface = `${String(thrown)} ${JSON.stringify([...response.headers])}`;
    for (const secret of [
      fixture.clip.public_key,
      fixture.clip.headers["X-PetCare-Signature"],
      fixture.clip.headers["X-PetCare-Nonce"],
      fixture.clip.headers["X-PetCare-Content-SHA256"],
      objectUuid,
      clipUuid,
      "secret-d1-failure",
    ]) {
      expect(publicSurface).not.toContain(secret);
    }
    for (const log of logs) expect(log).not.toHaveBeenCalled();
    uuid.mockRestore();
    for (const log of logs) log.mockRestore();
  });
});

describe("clip upload activation handshake", () => {
  it("promotes only a fully valid activation-pending upload in real D1", async () => {
    vi.resetModules();
    vi.doUnmock("../lib/petcare/repository");
    const { uploadSignedClip: uploadWithRealRepository } = await import(
      "../lib/petcare/clip-upload"
    );

    const invalidSignatureDb = await activationPendingDb();
    const digestMismatchDb = await activationPendingDb();
    const validDb = await activationPendingDb();
    try {
      await expect(
        uploadWithRealRepository(
          uploadRequest(fixtureBody, {
            "X-PetCare-Signature": `A${fixture.clip.headers["X-PetCare-Signature"].slice(1)}`,
          }),
          { DB: invalidSignatureDb, CLIPS: new FakeR2() } as never,
          fixedNow,
        ),
      ).rejects.toMatchObject({ code: "invalid_agent_signature" });
      expect(invalidSignatureDb.rows.tunnel_routes[0]).toMatchObject({
        status: "activation_pending",
        activation_expires_at: "2099-01-01T00:00:00.000Z",
      });

      const changed = Uint8Array.from(fixtureBody);
      changed[0] ^= 1;
      await expect(
        uploadWithRealRepository(
          uploadRequest(changed),
          { DB: digestMismatchDb, CLIPS: new FakeR2() } as never,
          fixedNow,
        ),
      ).rejects.toMatchObject({ code: "digest_mismatch" });
      expect(digestMismatchDb.rows.tunnel_routes[0]).toMatchObject({
        status: "activation_pending",
        activation_expires_at: "2099-01-01T00:00:00.000Z",
      });

      const response = await uploadWithRealRepository(
        uploadRequest(),
        { DB: validDb, CLIPS: new FakeR2() } as never,
        fixedNow,
      );
      expect(response.status).toBe(201);
      expect(validDb.rows.tunnel_routes[0]).toMatchObject({
        status: "active",
        activation_expires_at: null,
        updated_at: "2026-07-20T04:00:21.000Z",
      });
      expect(validDb.rows.agents[0]).toMatchObject({
        last_seen_at: "2026-07-20T04:00:21.000Z",
      });
    } finally {
      invalidSignatureDb.dispose();
      digestMismatchDb.dispose();
      validDb.dispose();
    }
  });
});
