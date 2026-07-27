// @vitest-environment node

import { describe, expect, it, vi } from "vitest";

vi.mock("cloudflare:workers", () => ({ env: {} }));

const repository = vi.hoisted(() => ({
  getOwnedLiveStream: vi.fn(),
}));

vi.mock("../lib/petcare/repository", () => ({
  PetCareRepository: class {
    getOwnedLiveStream = repository.getOwnedLiveStream;
  },
}));

import {
  getLiveManifest,
  getLivePart,
  getLivePlaylist,
} from "../lib/petcare/live-manifest";

describe("getLiveManifest", () => {
  it("returns a fresh owned manifest with only same-origin media routes", async () => {
    repository.getOwnedLiveStream.mockResolvedValue({
      bootId: "boot-01",
      newestSequence: 9,
      parts: [
        { sequence: 2 },
        { sequence: 9 },
      ],
    });

    const response = await getLiveManifest(
      { sub: "owner-01" } as never,
      { DB: {} as never, CLIPS: {} as never } as never,
      "camera-01",
    );

    expect(response.status).toBe(200);
    expect(response.headers.get("Cache-Control")).toBe("private, no-store, no-transform");
    expect(await response.json()).toEqual({
      boot_id: "boot-01",
      codec: "avc1.42E01E",
      newest_sequence: 9,
      target_latency_seconds: 6,
      init_url: "/api/petcare/cameras/camera-01/live/boot-01/init.mp4",
      parts: [
        { sequence: 2, url: "/api/petcare/cameras/camera-01/live/boot-01/2.m4s" },
        { sequence: 9, url: "/api/petcare/cameras/camera-01/live/boot-01/9.m4s" },
      ],
    });
  });

  it("reads only the owned current boot object from private R2", async () => {
    repository.getOwnedLiveStream.mockResolvedValue({
      bootId: "boot-01",
      initObjectKey: "live/home-01/camera-01/boot-01/init.mp4",
      newestSequence: 9,
      parts: [{ sequence: 9, objectKey: "live/home-01/camera-01/boot-01/9.m4s" }],
    });
    const get = vi.fn(async () => ({ body: "media" }));
    const env = { DB: {} as never, CLIPS: { get } as never } as never;

    const response = await getLivePart(
      { sub: "owner-01" } as never,
      env,
      "camera-01",
      "boot-01",
      9,
      "segment",
    );

    expect(get).toHaveBeenCalledWith("live/home-01/camera-01/boot-01/9.m4s");
    expect(response.headers.get("Cache-Control")).toBe("private, no-store, no-transform");
    await expect(response.text()).resolves.toBe("media");
  });

  it("returns a native HLS playlist with accurate segment durations", async () => {
    repository.getOwnedLiveStream.mockResolvedValue({
      bootId: "boot-01",
      newestSequence: 9,
      parts: [
        { sequence: 8, durationMs: 1000 },
        { sequence: 9, durationMs: 3000 },
      ],
    });

    const response = await getLivePlaylist(
      { sub: "owner-01" } as never,
      { DB: {} as never, CLIPS: {} as never } as never,
      "camera-01",
    );

    expect(response.headers.get("Content-Type")).toBe("application/vnd.apple.mpegurl");
    expect(response.headers.get("Cache-Control")).toBe("private, no-store, no-transform");
    await expect(response.text()).resolves.toBe([
      "#EXTM3U",
      "#EXT-X-VERSION:7",
      "#EXT-X-TARGETDURATION:3",
      "#EXT-X-MEDIA-SEQUENCE:8",
      "#EXT-X-INDEPENDENT-SEGMENTS",
      '#EXT-X-MAP:URI="/api/petcare/cameras/camera-01/live/boot-01/init.mp4"',
      "#EXTINF:1.000,",
      "/api/petcare/cameras/camera-01/live/boot-01/8.m4s",
      "#EXTINF:3.000,",
      "/api/petcare/cameras/camera-01/live/boot-01/9.m4s",
      "",
    ].join("\n"));
  });

  it("returns 404 before R2 for a foreign boot or part", async () => {
    repository.getOwnedLiveStream.mockResolvedValue({
      bootId: "boot-01",
      initObjectKey: "live/home-01/camera-01/boot-01/init.mp4",
      newestSequence: 9,
      parts: [],
    });
    const get = vi.fn();
    const env = { DB: {} as never, CLIPS: { get } as never } as never;

    await expect(
      getLivePart(
        { sub: "owner-01" } as never,
        env,
        "camera-01",
        "boot-02",
        0,
        "init",
      ),
    ).rejects.toMatchObject({ status: 404 });
    expect(get).not.toHaveBeenCalled();
  });
});
