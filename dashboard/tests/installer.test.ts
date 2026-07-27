// @vitest-environment node

import { createHash } from "node:crypto";
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { beforeEach, describe, expect, it, vi } from "vitest";

import type { PetCareEnv } from "../lib/petcare/env";
import {
  downloadInstaller,
  INSTALLER_FILE_NAME,
  INSTALLER_OBJECT_KEY,
  INSTALLER_SHA256,
  INSTALLER_SIZE_BYTES,
  uploadInstaller,
} from "../lib/petcare/installer";

const installerPath = resolve(
  import.meta.dirname,
  "../../packaging/windows/release/PetCare-Home-Agent-Setup.exe",
);
const installerBytes = readFileSync(installerPath);

function makeEnv() {
  let stored: Uint8Array | null = null;
  let customMetadata: Record<string, string> | undefined;
  return {
    env: {
      PETCARE_INSTALLER_UPLOAD_TOKEN: "operator-secret",
      CLIPS: {
        get: vi.fn(async () =>
          stored
            ? {
                body: stored,
                size: stored.byteLength,
                customMetadata,
              }
            : null,
        ),
        put: vi.fn(
          async (
            _key: string,
            value: ArrayBuffer,
            options: { customMetadata?: Record<string, string> },
          ) => {
            stored = new Uint8Array(value).slice();
            customMetadata = options.customMetadata;
          },
        ),
      },
    } as unknown as PetCareEnv,
    clearMetadata() {
      customMetadata = undefined;
    },
  };
}

function uploadRequest(bytes: Uint8Array, token = "operator-secret") {
  return new Request("https://app.test/api/petcare/operator/installer", {
    method: "PUT",
    headers: {
      "content-length": String(bytes.byteLength),
      "x-petcare-installer-upload-token": token,
    },
    body: bytes,
  });
}

describe("private installer release", () => {
  beforeEach(() => vi.clearAllMocks());

  it("pins the tracked Windows release identity", () => {
    expect(INSTALLER_FILE_NAME).toBe("PetCare-Home-Agent-Setup.exe");
    expect(installerBytes.byteLength).toBe(INSTALLER_SIZE_BYTES);
    expect(createHash("sha256").update(installerBytes).digest("hex").toUpperCase()).toBe(
      INSTALLER_SHA256,
    );
    expect(INSTALLER_OBJECT_KEY).toContain(INSTALLER_SHA256.toLowerCase());
  });

  it("hides the operator upload route without the exact temporary secret", async () => {
    const { env } = makeEnv();
    for (const token of ["", "wrong-secret"]) {
      const request = uploadRequest(installerBytes, token);
      const response = await uploadInstaller(request, env);
      expect(response.status).toBe(404);
      expect(await response.json()).toEqual({ error: "not_found" });
    }
    expect(env.CLIPS.put).not.toHaveBeenCalled();
  });

  it("keeps the operator upload route closed after the temporary secret is removed", async () => {
    const { env } = makeEnv();
    delete env.PETCARE_INSTALLER_UPLOAD_TOKEN;

    const response = await uploadInstaller(uploadRequest(installerBytes), env);

    expect(response.status).toBe(404);
    expect(await response.json()).toEqual({ error: "not_found" });
    expect(env.CLIPS.put).not.toHaveBeenCalled();
  });

  it("rejects altered installer bytes", async () => {
    const { env } = makeEnv();
    const altered = Uint8Array.from(installerBytes);
    altered[0] ^= 0xff;

    const rejected = await uploadInstaller(uploadRequest(altered), env);
    expect(rejected.status).toBe(422);
    expect(await rejected.json()).toEqual({ error: "invalid_digest" });
    expect(env.CLIPS.put).not.toHaveBeenCalled();
  });

  it("serves only the pinned R2 object", async () => {
    const { env, clearMetadata } = makeEnv();

    const uploaded = await uploadInstaller(uploadRequest(installerBytes), env);
    expect(uploaded.status).toBe(204);
    expect(env.CLIPS.put).toHaveBeenCalledWith(
      INSTALLER_OBJECT_KEY,
      expect.any(ArrayBuffer),
      expect.objectContaining({ customMetadata: { sha256: INSTALLER_SHA256 } }),
    );

    const downloaded = await downloadInstaller(env);
    expect(downloaded.status).toBe(200);
    expect(downloaded.headers.get("Cache-Control")).toBe(
      "private, no-store, no-transform",
    );
    expect(downloaded.headers.get("Content-Disposition")).toContain(
      INSTALLER_FILE_NAME,
    );
    expect(downloaded.headers.get("Content-Length")).toBe(
      String(INSTALLER_SIZE_BYTES),
    );
    expect(downloaded.headers.get("X-Content-Type-Options")).toBe("nosniff");
    expect(new Uint8Array(await downloaded.arrayBuffer())).toEqual(
      Uint8Array.from(installerBytes),
    );

    clearMetadata();
    const metadataMismatch = await downloadInstaller(env);
    expect(metadataMismatch.status).toBe(503);
    await expect(metadataMismatch.json()).resolves.toEqual({
      error: "installer_unavailable",
    });
  }, 10_000);

  it("requires a declared exact upload length", async () => {
    const { env } = makeEnv();
    const missing = new Request(
      "https://app.test/api/petcare/operator/installer",
      {
        method: "PUT",
        headers: { "x-petcare-installer-upload-token": "operator-secret" },
        body: installerBytes,
      },
    );
    missing.headers.delete("content-length");
    const response = await uploadInstaller(missing, env);
    expect(response.status).toBe(411);
    expect(env.CLIPS.put).not.toHaveBeenCalled();
  });
});
