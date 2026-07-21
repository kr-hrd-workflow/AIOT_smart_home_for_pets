// @vitest-environment node

import { beforeEach, expect, it, vi } from "vitest";

const mocks = vi.hoisted(() => ({
  enroll: vi.fn(),
}));

vi.mock("cloudflare:workers", () => ({ env: {} }));
vi.mock("../db", () => ({ getDb: vi.fn(() => ({ db: "test" })) }));
vi.mock("../lib/tenancy/repository", () => ({
  TenantRepository: class {},
}));
vi.mock("../lib/petcare/repository", () => ({
  PetCareRepository: class {},
}));
vi.mock("../lib/petcare/cloudflare", () => ({
  CloudflareClient: class {},
}));
vi.mock("../lib/petcare/env", () => ({ readPetCareConfig: vi.fn(() => ({})) }));
vi.mock("../lib/petcare/enrollment", () => ({
  EnrollmentProvisioningService: class {
    enroll = mocks.enroll;
  },
}));

import { handleAgentEnroll } from "../lib/petcare/agent-enroll";
import { PetCareError } from "../lib/petcare/errors";

const body = JSON.stringify({
  enrollment_code: "AQEBAQEBAQEBAQEBAQEBAQ",
  algorithm: "Ed25519",
  public_key: "INsaaxAGzWH6psMGL2Y-gJaRBGfO4on_-P_-e8Qiais",
  local_camera_id: "pc-webcam-01",
});

function request(
  overrides: { headers?: HeadersInit; body?: BodyInit | null; method?: string } = {},
) {
  return new Request("https://app.test/api/petcare/agent/enroll", {
    method: overrides.method ?? "POST",
    headers: {
      "Content-Type": "application/json",
      "Content-Length": String(new TextEncoder().encode(body).byteLength),
      "CF-Connecting-IP": "203.0.113.10",
      ...overrides.headers,
    },
    body: overrides.body === undefined ? body : overrides.body,
    duplex: "half",
  } as RequestInit);
}

beforeEach(() => {
  vi.clearAllMocks();
  mocks.enroll.mockResolvedValue({
    agentId: "agent_01",
    cameraId: "camera_01",
    connectorToken: "connector-secret",
  });
});

it("passes the platform IP and returns only the one-time connector response", async () => {
  const response = await handleAgentEnroll(request(), {} as never, new Date());
  expect(response.status).toBe(201);
  expect(response.headers.get("cache-control")).toBe("private, no-store");
  await expect(response.json()).resolves.toEqual({
    agent_id: "agent_01",
    camera_id: "camera_01",
    connector_token: "connector-secret",
  });
  expect(mocks.enroll).toHaveBeenCalledWith({
    code: "AQEBAQEBAQEBAQEBAQEBAQ",
    publicKey: "INsaaxAGzWH6psMGL2Y-gJaRBGfO4on_-P_-e8Qiais",
    localCameraId: "pc-webcam-01",
    connectingIp: "203.0.113.10",
  });
});

it.each([
  ["missing IP", { "CF-Connecting-IP": "" }],
  ["duplicate IP", { "CF-Connecting-IP": "203.0.113.10, 203.0.113.11" }],
  ["malformed IP", { "CF-Connecting-IP": "999.0.0.1" }],
  ["non JSON", { "Content-Type": "text/plain" }],
  ["duplicate content type", { "Content-Type": "application/json, application/json" }],
  ["missing length", { "Content-Length": "" }],
  ["duplicate length", { "Content-Length": "100, 100" }],
  ["invalid length", { "Content-Length": "1e2" }],
  ["oversized length", { "Content-Length": "4097" }],
])("rejects %s before provisioning", async (_name, headers) => {
  const response = await handleAgentEnroll(
    request({ headers }),
    {} as never,
    new Date(),
  );
  expect(response.status).toBe(400);
  await expect(response.json()).resolves.toEqual({ error: "invalid_request" });
  expect(mocks.enroll).not.toHaveBeenCalled();
});

it.each([
  String.raw`{"\x":1}`,
  String.raw`{"\uZZZZ":1}`,
  String.raw`{"ok":"\x"}`,
  String.raw`{"unterminated:1}`,
  String.raw`{"a":1,}`,
  "{",
])("maps malformed JSON corpus to invalid_request", async (invalidBody) => {
  const response = await handleAgentEnroll(
    request({
      body: invalidBody,
      headers: {
        "Content-Length": String(new TextEncoder().encode(invalidBody).length),
      },
    }),
    {} as never,
    new Date(),
  );
  expect(response.status).toBe(400);
  await expect(response.json()).resolves.toEqual({ error: "invalid_request" });
  expect(mocks.enroll).not.toHaveBeenCalled();
});

it("stops a streamed body at byte 4097 despite a smaller declaration", async () => {
  const stream = new ReadableStream({
    start(controller) {
      controller.enqueue(new Uint8Array(4097));
      controller.close();
    },
  });
  const response = await handleAgentEnroll(
    request({ body: stream, headers: { "Content-Length": "1" } }),
    {} as never,
    new Date(),
  );
  expect(response.status).toBe(400);
  expect(mocks.enroll).not.toHaveBeenCalled();
});

it("rejects UTF-8 that does not round-trip byte-for-byte", async () => {
  const payload = new TextEncoder().encode(body);
  const withBom = new Uint8Array(payload.length + 3);
  withBom.set([0xef, 0xbb, 0xbf]);
  withBom.set(payload, 3);
  const response = await handleAgentEnroll(
    request({
      body: withBom,
      headers: { "Content-Length": String(withBom.length) },
    }),
    {} as never,
    new Date(),
  );
  expect(response.status).toBe(400);
  expect(mocks.enroll).not.toHaveBeenCalled();
});

it.each([
  ["extra key", body.slice(0, -1) + ',"owner_sub":"attacker"}'],
  ["duplicate key", body.replace('"algorithm":"Ed25519"', '"algorithm":"Ed25519","algorithm":"Ed25519"')],
  ["wrong algorithm", body.replace("Ed25519", "ed25519")],
  ["wrong camera", body.replace("pc-webcam-01", "webcam")],
  ["padded code", body.replace("AQEBAQEBAQEBAQEBAQEBAQ", "AQEBAQEBAQEBAQEBAQEBAQ==")],
  ["trailing JSON", `${body} {}`],
])("rejects %s", async (_name, invalidBody) => {
  const response = await handleAgentEnroll(
    request({
      body: invalidBody,
      headers: {
        "Content-Length": String(new TextEncoder().encode(invalidBody).byteLength),
      },
    }),
    {} as never,
    new Date(),
  );
  expect(response.status).toBe(400);
  expect(mocks.enroll).not.toHaveBeenCalled();
});

it("ignores cookies and forwarded headers", async () => {
  await handleAgentEnroll(
    request({
      headers: {
        Cookie: "session=browser-secret",
        Forwarded: "for=198.51.100.1",
        "X-Forwarded-For": "198.51.100.2",
      },
    }),
    {} as never,
    new Date(),
  );
  expect(mocks.enroll).toHaveBeenCalledWith(
    expect.objectContaining({ connectingIp: "203.0.113.10" }),
  );
});

it.each([
  [409, "enrollment_rejected"],
  [429, "rate_limited"],
  [503, "enrollment_retryable"],
] as const)("returns a redacted %s %s failure", async (status, code) => {
  mocks.enroll.mockRejectedValueOnce(new PetCareError(status, code));
  const response = await handleAgentEnroll(
    request({ headers: { Cookie: "session=browser-secret" } }),
    {} as never,
    new Date(),
  );
  expect(response.status).toBe(status);
  const text = await response.text();
  expect(JSON.parse(text)).toEqual({ error: code });
  for (const secret of [
    "AQEBAQEBAQEBAQEBAQEBAQ",
    "INsaaxAGzWH6psMGL2Y-gJaRBGfO4on_-P_-e8Qiais",
    "browser-secret",
    "203.0.113.10",
  ]) {
    expect(text).not.toContain(secret);
  }
});
