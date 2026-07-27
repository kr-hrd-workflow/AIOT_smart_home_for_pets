// @vitest-environment node

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { webcrypto } from "node:crypto";

import { handleLiveUpload } from "../lib/petcare/live-upload";
import { encodeBase64Url } from "../lib/petcare/clip-signature";
import { FakeD1 } from "./helpers/petcare-fakes";

vi.mock("cloudflare:workers", () => ({ env: {} }));

const now = new Date("2026-07-27T00:00:00.000Z");
let fake: FakeD1;
let db: D1Database;
let r2: ConditionalR2;
let privateKey: CryptoKey;

class ConditionalR2 {
  readonly objects = new Map<string, Uint8Array>();
  failPut = false;

  async put(
    key: string,
    value: ArrayBufferView,
    options?: { onlyIf?: { etagDoesNotMatch?: string } },
  ): Promise<{ size: number } | null> {
    if (this.failPut) throw new Error("synthetic R2 failure");
    if (options?.onlyIf?.etagDoesNotMatch === "*" && this.objects.has(key)) {
      return null;
    }
    const bytes = new Uint8Array(value.buffer, value.byteOffset, value.byteLength).slice();
    this.objects.set(key, bytes);
    return { size: bytes.byteLength };
  }

  async delete(key: string): Promise<void> {
    this.objects.delete(key);
  }
}

function b64url(value: Uint8Array): string {
  return encodeBase64Url(value);
}

async function signed(
  kind: "init" | "segment",
  sequence: number,
  body: Uint8Array,
  nonceByte: number,
  segmentDuration = "3000",
): Promise<Request> {
  const bootId = "boot-a";
  const cameraId = "camera-a";
  const startedAt = "2026-07-27T00:00:00.000Z";
  const duration = kind === "init" ? "0" : segmentDuration;
  const digest = b64url(
    new Uint8Array(await webcrypto.subtle.digest("SHA-256", body)),
  );
  const timestamp = String(Math.floor(now.getTime() / 1000));
  const nonce = b64url(new Uint8Array(16).fill(nonceByte));
  const canonical = new TextEncoder().encode([
    "PETCARE-LIVE-V1",
    "POST",
    "/api/petcare/agent/live",
    "agent-a",
    cameraId,
    bootId,
    kind,
    String(sequence),
    startedAt,
    duration,
    String(body.byteLength),
    digest,
    "",
  ].join("\n"));
  const signature = new Uint8Array(
    await webcrypto.subtle.sign("Ed25519", privateKey, canonical),
  );
  return new Request("https://pets.example/api/petcare/agent/live", {
    method: "POST",
    headers: {
      "Content-Type": "video/mp4",
      "Content-Length": String(body.byteLength),
      "X-PetCare-Agent-Id": "agent-a",
      "X-PetCare-Camera-Id": cameraId,
      "X-PetCare-Boot-Id": bootId,
      "X-PetCare-Live-Kind": kind,
      "X-PetCare-Live-Sequence": String(sequence),
      "X-PetCare-Started-At": startedAt,
      "X-PetCare-Duration-Ms": duration,
      "X-PetCare-Timestamp": timestamp,
      "X-PetCare-Nonce": nonce,
      "X-PetCare-Content-SHA256": digest,
      "X-PetCare-Signature": b64url(signature),
    },
    body,
  });
}

async function run(sql: string, ...values: unknown[]) {
  await db.prepare(sql).bind(...values).run();
}

beforeEach(async () => {
  fake = new FakeD1();
  db = fake as unknown as D1Database;
  const pair = await webcrypto.subtle.generateKey(
    "Ed25519",
    true,
    ["sign", "verify"],
  );
  privateKey = pair.privateKey;
  const publicKey = new Uint8Array(
    await webcrypto.subtle.exportKey("raw", pair.publicKey),
  );
  await run(
    "INSERT INTO homes (id, owner_sub, created_at) VALUES (?, ?, ?)",
    "home-a",
    "owner-a",
    now.toISOString(),
  );
  await run(
    "INSERT INTO agents (id, home_id, public_key, tunnel_origin, connection_mode) VALUES (?, ?, ?, NULL, 'outbound')",
    "agent-a",
    "home-a",
    b64url(publicKey),
  );
  await run(
    "INSERT INTO cameras (id, home_id, agent_id, local_camera_id, created_at) VALUES (?, ?, ?, ?, ?)",
    "camera-a",
    "home-a",
    "agent-a",
    "pc-webcam-01",
    now.toISOString(),
  );
  r2 = new ConditionalR2();
});

afterEach(() => fake.dispose());

describe("signed rolling live upload", () => {
  it("accepts a signed legacy one-second segment during rollout", async () => {
    const env = { DB: db, CLIPS: r2 } as unknown as Parameters<typeof handleLiveUpload>[1];
    await handleLiveUpload(await signed("init", 0, new Uint8Array([1]), 1), env, now);
    await expect(
      handleLiveUpload(
        await signed("segment", 1, new Uint8Array([2]), 2, "1000"),
        env,
        now,
      ),
    ).resolves.toMatchObject({ status: 204 });
  });

  it("keeps one init and only the newest eight increasing one-second segments", async () => {
    const env = { DB: db, CLIPS: r2 } as unknown as Parameters<typeof handleLiveUpload>[1];
    await expect(
      handleLiveUpload(await signed("init", 0, new Uint8Array([1, 2]), 1), env, now),
    ).resolves.toMatchObject({ status: 204 });

    for (let sequence = 1; sequence <= 10; sequence += 1) {
      await expect(
        handleLiveUpload(
          await signed("segment", sequence, new Uint8Array([sequence]), sequence + 1),
          env,
          new Date(now.getTime() + sequence * 1_000),
        ),
      ).resolves.toMatchObject({ status: 204 });
    }

    expect(
      (await db.prepare(
        "SELECT sequence FROM live_parts WHERE home_id = ? ORDER BY sequence",
      ).bind("home-a").all()).results,
    ).toEqual(Array.from({ length: 8 }, (_, index) => ({ sequence: index + 3 })));
    expect([...r2.objects.keys()].sort()).toEqual([
      "live/home-a/camera-a/boot-a/10.m4s",
      "live/home-a/camera-a/boot-a/3.m4s",
      "live/home-a/camera-a/boot-a/4.m4s",
      "live/home-a/camera-a/boot-a/5.m4s",
      "live/home-a/camera-a/boot-a/6.m4s",
      "live/home-a/camera-a/boot-a/7.m4s",
      "live/home-a/camera-a/boot-a/8.m4s",
      "live/home-a/camera-a/boot-a/9.m4s",
      "live/home-a/camera-a/boot-a/init.mp4",
    ]);
  });

  it("does not overwrite an accepted part when a duplicate sequence is retried", async () => {
    const env = { DB: db, CLIPS: r2 } as unknown as Parameters<typeof handleLiveUpload>[1];
    await handleLiveUpload(await signed("init", 0, new Uint8Array([1]), 1), env, now);
    await handleLiveUpload(await signed("segment", 1, new Uint8Array([2]), 2), env, now);
    await expect(
      handleLiveUpload(await signed("segment", 1, new Uint8Array([9]), 3), env, now),
    ).rejects.toMatchObject({ status: 409, code: "live_conflict" });
    expect(r2.objects.get("live/home-a/camera-a/boot-a/1.m4s")).toEqual(
      new Uint8Array([2]),
    );
  });

  it("accepts an identical retry after the success response is lost", async () => {
    const env = { DB: db, CLIPS: r2 } as unknown as Parameters<typeof handleLiveUpload>[1];
    await handleLiveUpload(await signed("init", 0, new Uint8Array([1]), 1), env, now);
    await handleLiveUpload(await signed("segment", 1, new Uint8Array([2]), 2), env, now);
    await expect(
      handleLiveUpload(await signed("segment", 1, new Uint8Array([2]), 3), env, now),
    ).resolves.toMatchObject({ status: 204 });
  });

  it("rolls back the private object when D1 publication fails", async () => {
    const env = { DB: db, CLIPS: r2 } as unknown as Parameters<typeof handleLiveUpload>[1];
    await handleLiveUpload(await signed("init", 0, new Uint8Array([1]), 1), env, now);
    fake.failOnce(/INSERT INTO live_parts/);
    await expect(
      handleLiveUpload(await signed("segment", 1, new Uint8Array([2]), 2), env, now),
    ).rejects.toMatchObject({ status: 503, code: "upload_retryable" });
    expect(r2.objects.has("live/home-a/camera-a/boot-a/1.m4s")).toBe(false);
  });
});
