import "@testing-library/jest-dom/vitest";

import { act, cleanup, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { LiveMediaPlayer } from "../components/live-media-player";
import type {
  PetCareLiveClient,
  PetCareLiveManifest,
} from "../lib/petcare-remote";

class FakeSourceBuffer extends EventTarget {
  readonly appended: number[] = [];
  readonly removed: Array<[number, number]> = [];
  updating = false;
  mode: AppendMode = "segments";

  get buffered(): TimeRanges {
    const end = this.appended.filter((value) => value > 0).length;
    return {
      length: end ? 1 : 0,
      start: () => 0,
      end: () => end,
    };
  }

  appendBuffer(bytes: BufferSource) {
    const view = bytes instanceof ArrayBuffer
      ? new Uint8Array(bytes)
      : new Uint8Array(bytes.buffer, bytes.byteOffset, bytes.byteLength);
    this.updating = true;
    this.appended.push(view[0] ?? -1);
    queueMicrotask(() => {
      this.updating = false;
      this.dispatchEvent(new Event("updateend"));
    });
  }

  remove(start: number, end: number) {
    this.updating = true;
    this.removed.push([start, end]);
    queueMicrotask(() => {
      this.updating = false;
      this.dispatchEvent(new Event("updateend"));
    });
  }

  abort() {
    this.updating = false;
  }
}

class FakeMediaSource extends EventTarget {
  static readonly instances: FakeMediaSource[] = [];
  static isTypeSupported = vi.fn(() => true);

  readonly sourceBuffer = new FakeSourceBuffer();
  readyState: ReadyState = "closed";

  constructor() {
    super();
    FakeMediaSource.instances.push(this);
    queueMicrotask(() => {
      this.readyState = "open";
      this.dispatchEvent(new Event("sourceopen"));
    });
  }

  addSourceBuffer() {
    return this.sourceBuffer as unknown as SourceBuffer;
  }

  endOfStream() {
    this.readyState = "ended";
  }
}

function manifest(
  bootId: string,
  newestSequence: number,
): PetCareLiveManifest {
  const first = Math.max(1, newestSequence - 7);
  return {
    boot_id: bootId,
    codec: "avc1.42E01E",
    newest_sequence: newestSequence,
    target_latency_seconds: 6,
    init_url: `/api/petcare/cameras/camera-1/live/${bootId}/init.mp4`,
    parts: Array.from(
      { length: Math.max(0, newestSequence - first + 1) },
      (_, index) => {
        const sequence = first + index;
        return {
          sequence,
          url: `/api/petcare/cameras/camera-1/live/${bootId}/${sequence}.m4s`,
        };
      },
    ),
  };
}

function client(
  liveManifest: PetCareLiveClient["liveManifest"] = vi
    .fn()
    .mockResolvedValue(manifest("boot-a", 3)),
): PetCareLiveClient {
  return {
    liveManifest,
    livePart: vi.fn(async (url) => {
      const match = url.match(/\/(\d+)\.m4s$/);
      return new Uint8Array([match ? Number(match[1]) : 0]).buffer;
    }),
  };
}

beforeEach(() => {
  FakeMediaSource.instances.length = 0;
  vi.stubGlobal("MediaSource", FakeMediaSource);
  vi.spyOn(URL, "createObjectURL").mockReturnValue("blob:petcare-live");
  vi.spyOn(URL, "revokeObjectURL").mockImplementation(() => undefined);
  vi.spyOn(HTMLMediaElement.prototype, "play").mockResolvedValue();
});

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

describe("LiveMediaPlayer", () => {
  it("appends init before ordered parts and starts with a six-second buffer", async () => {
    const liveClient = client();
    render(
      <LiveMediaPlayer
        cameraId="camera-1"
        client={liveClient}
        alt="실시간 반려동물 카메라"
      />,
    );

    await waitFor(() =>
      expect(FakeMediaSource.instances[0]?.sourceBuffer.appended).toEqual([
        0, 1, 2, 3,
      ]),
    );
    const video = screen.getByLabelText("실시간 반려동물 카메라");
    expect(video).toHaveAttribute("autoplay");
    expect(video).toHaveAttribute("playsinline");
    expect(video).toHaveProperty("muted", true);
    expect(video).toHaveAttribute("data-live-state", "live");
  });

  it("resets the MediaSource and init part when the stream boot changes", async () => {
    vi.spyOn(HTMLCanvasElement.prototype, "getContext").mockReturnValue({
      drawImage: vi.fn(),
    } as unknown as CanvasRenderingContext2D);
    vi.spyOn(HTMLCanvasElement.prototype, "toDataURL").mockReturnValue(
      "data:image/jpeg;base64,frame",
    );
    vi.spyOn(HTMLMediaElement.prototype, "play").mockImplementation(function () {
      this.dispatchEvent(new Event("playing"));
      return Promise.resolve();
    });
    const liveManifest = vi
      .fn()
      .mockResolvedValueOnce(manifest("boot-a", 1))
      .mockResolvedValue(manifest("boot-b", 1));
    const liveClient = client(liveManifest);
    render(
      <LiveMediaPlayer
        cameraId="camera-1"
        client={liveClient}
        alt="실시간 반려동물 카메라"
        pollIntervalMs={10}
      />,
    );

    await waitFor(() => expect(FakeMediaSource.instances).toHaveLength(2));
    await waitFor(() =>
      expect(screen.getByLabelText("실시간 반려동물 카메라")).not.toHaveAttribute("poster"),
    );
    expect(liveClient.livePart).toHaveBeenCalledWith(
      expect.stringContaining("/boot-a/init.mp4"),
      expect.any(AbortSignal),
    );
    expect(liveClient.livePart).toHaveBeenCalledWith(
      expect.stringContaining("/boot-b/init.mp4"),
      expect.any(AbortSignal),
    );
    expect(URL.revokeObjectURL).toHaveBeenCalled();
  });

  it("shows offline after three seconds without a fresh part", async () => {
    render(
      <LiveMediaPlayer
        cameraId="camera-1"
        client={client()}
        alt="실시간 반려동물 카메라"
        pollIntervalMs={10}
        offlineAfterMs={30}
      />,
    );

    expect(await screen.findByRole("alert")).toHaveTextContent(
      "라이브 영상 연결이 지연되고 있습니다.",
    );
  });

  it("aborts an in-flight manifest request on unmount", async () => {
    let signal: AbortSignal | undefined;
    const pending = new Promise<PetCareLiveManifest>(() => undefined);
    const liveClient = client(
      vi.fn((_cameraId, nextSignal) => {
        signal = nextSignal;
        return pending;
      }),
    );
    const view = render(
      <LiveMediaPlayer
        cameraId="camera-1"
        client={liveClient}
        alt="실시간 반려동물 카메라"
      />,
    );
    await act(async () => Promise.resolve());

    view.unmount();
    expect(signal?.aborted).toBe(true);
  });
});
