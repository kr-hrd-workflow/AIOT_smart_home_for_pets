"use client";

import { useEffect, useRef, useState } from "react";

import type { PetCareLiveClient } from "../lib/petcare-remote";

const CODEC = 'video/mp4; codecs="avc1.42E01E"';
const HLS_MIME = "application/vnd.apple.mpegurl";
const DEFAULT_POLL_MS = 1_000;
const DEFAULT_OFFLINE_MS = 3_000;
const MAX_BUFFER_SECONDS = 18;

type LiveState = "connecting" | "live" | "offline" | "unsupported";

function aborted(): DOMException {
  return new DOMException("The operation was aborted.", "AbortError");
}

function isAbort(error: unknown): boolean {
  return error instanceof DOMException && error.name === "AbortError";
}

export function LiveMediaPlayer({
  cameraId,
  client,
  alt,
  pollIntervalMs = DEFAULT_POLL_MS,
  offlineAfterMs = DEFAULT_OFFLINE_MS,
}: {
  cameraId: string;
  client: PetCareLiveClient;
  alt: string;
  pollIntervalMs?: number;
  offlineAfterMs?: number;
}) {
  const videoRef = useRef<HTMLVideoElement>(null);
  const [state, setState] = useState<LiveState>("connecting");

  useEffect(() => {
    const video = videoRef.current;
    if (!video) return;

    const supportsMediaSource =
      typeof MediaSource !== "undefined" && MediaSource.isTypeSupported(CODEC);
    if (!supportsMediaSource) {
      const supportsNativeHls = Boolean(
        video.canPlayType(HLS_MIME) || video.canPlayType("application/x-mpegURL"),
      );
      if (!supportsNativeHls) {
        setState("unsupported");
        return;
      }

      const handlePlaying = () => setState("live");
      const handleError = () => setState("offline");
      video.addEventListener("playing", handlePlaying);
      video.addEventListener("error", handleError);
      video.src = `/api/petcare/cameras/${encodeURIComponent(cameraId)}/live/index.m3u8`;
      setState("connecting");
      void video.play().catch(() => undefined);

      return () => {
        video.removeEventListener("playing", handlePlaying);
        video.removeEventListener("error", handleError);
        video.removeAttribute("src");
      };
    }

    let active = true;
    let timer: number | undefined;
    let requestController: AbortController | undefined;
    let mediaSource: MediaSource | undefined;
    let sourceBuffer: SourceBuffer | undefined;
    let objectUrl: string | undefined;
    let openReject: ((reason: unknown) => void) | undefined;
    let bufferQueue = Promise.resolve();
    let bootId: string | undefined;
    let initAppended = false;
    let appendedSequence = 0;
    let newestSequence = -1;
    let lastFreshAt = Date.now();

    const preserveVisibleFrame = () => {
      if (!video.videoWidth || !video.videoHeight) return;
      try {
        const canvas = document.createElement("canvas");
        canvas.width = video.videoWidth;
        canvas.height = video.videoHeight;
        canvas.getContext("2d")?.drawImage(video, 0, 0);
        video.poster = canvas.toDataURL("image/jpeg", 0.85);
      } catch {
        // The current frame remains visible when capture is unavailable.
      }
    };

    const closePipeline = () => {
      openReject?.(aborted());
      openReject = undefined;
      try {
        sourceBuffer?.abort();
      } catch {
        // A closed MediaSource needs no further cleanup.
      }
      try {
        if (mediaSource?.readyState === "open") mediaSource.endOfStream();
      } catch {
        // The browser can close the source while a request is being aborted.
      }
      sourceBuffer = undefined;
      mediaSource = undefined;
      if (objectUrl) URL.revokeObjectURL(objectUrl);
      objectUrl = undefined;
      video.removeAttribute("src");
    };

    const openPipeline = async () => {
      await bufferQueue.catch(() => undefined);
      closePipeline();
      bufferQueue = Promise.resolve();

      const next = new MediaSource();
      mediaSource = next;
      objectUrl = URL.createObjectURL(next);
      video.src = objectUrl;

      await new Promise<void>((resolve, reject) => {
        openReject = reject;
        const opened = () => {
          openReject = undefined;
          if (!active) {
            reject(aborted());
            return;
          }
          try {
            sourceBuffer = next.addSourceBuffer(CODEC);
            sourceBuffer.mode = "segments";
            resolve();
          } catch (error) {
            reject(error);
          }
        };
        next.addEventListener("sourceopen", opened, { once: true });
      });
    };

    const queueBufferOperation = (
      operation: (buffer: SourceBuffer) => void,
    ): Promise<void> => {
      bufferQueue = bufferQueue.catch(() => undefined).then(
        () =>
          new Promise<void>((resolve, reject) => {
            const buffer = sourceBuffer;
            if (!active || !buffer) {
              reject(aborted());
              return;
            }
            const done = () => {
              buffer.removeEventListener("error", failed);
              resolve();
            };
            const failed = () => {
              buffer.removeEventListener("updateend", done);
              reject(new Error("live_buffer_error"));
            };
            buffer.addEventListener("updateend", done, { once: true });
            buffer.addEventListener("error", failed, { once: true });
            try {
              operation(buffer);
            } catch (error) {
              buffer.removeEventListener("updateend", done);
              buffer.removeEventListener("error", failed);
              reject(error);
            }
          }),
      );
      return bufferQueue;
    };

    const append = async (bytes: ArrayBuffer) => {
      await queueBufferOperation((buffer) => buffer.appendBuffer(bytes));
    };

    const synchronizePlayback = async (targetLatency: number) => {
      const ranges = sourceBuffer?.buffered;
      if (!ranges?.length) return;
      const start = ranges.start(0);
      const liveEdge = ranges.end(ranges.length - 1);
      const target = Math.max(start, liveEdge - targetLatency);
      const lag = liveEdge - video.currentTime;
      if (
        video.currentTime < start ||
        lag > targetLatency + 1
      ) {
        video.currentTime = target;
      }
      const removeBefore = liveEdge - MAX_BUFFER_SECONDS;
      if (removeBefore > start) {
        await queueBufferOperation((buffer) =>
          buffer.remove(start, removeBefore),
        );
      }
    };

    const poll = async () => {
      requestController = new AbortController();
      const signal = requestController.signal;
      try {
        const manifest = await client.liveManifest(cameraId, signal);
        const changedBoot = manifest.boot_id !== bootId;
        if (changedBoot) {
          if (bootId !== undefined) preserveVisibleFrame();
          await openPipeline();
          bootId = manifest.boot_id;
          initAppended = false;
          appendedSequence = 0;
          newestSequence = -1;
        }

        if (!initAppended) {
          await append(await client.livePart(manifest.init_url, signal));
          initAppended = true;
        }

        for (const part of manifest.parts) {
          if (part.sequence <= appendedSequence) continue;
          await append(await client.livePart(part.url, signal));
          appendedSequence = part.sequence;
        }

        if (changedBoot || manifest.newest_sequence > newestSequence) {
          newestSequence = manifest.newest_sequence;
          lastFreshAt = Date.now();
        }
        if (changedBoot && video.poster) {
          video.addEventListener(
            "playing",
            () => video.removeAttribute("poster"),
            { once: true },
          );
        }
        await synchronizePlayback(manifest.target_latency_seconds);
        if (video.paused) void video.play().catch(() => undefined);
        if (active) {
          setState(
            Date.now() - lastFreshAt >= offlineAfterMs ? "offline" : "live",
          );
        }
      } catch (error) {
        if (active && !isAbort(error) && Date.now() - lastFreshAt >= offlineAfterMs) {
          setState("offline");
        }
      } finally {
        requestController = undefined;
        if (active) timer = window.setTimeout(() => void poll(), pollIntervalMs);
      }
    };

    void poll();
    return () => {
      active = false;
      requestController?.abort();
      if (timer !== undefined) window.clearTimeout(timer);
      closePipeline();
    };
  }, [alt, cameraId, client, offlineAfterMs, pollIntervalMs]);

  const message =
    state === "offline"
      ? "라이브 영상 연결이 지연되고 있습니다."
      : state === "unsupported"
        ? "이 브라우저에서는 라이브 영상을 재생할 수 없습니다."
        : "라이브 영상을 연결하고 있습니다.";

  return (
    <div className="live-media-player">
      <video
        ref={videoRef}
        aria-label={alt}
        data-live-state={state}
        width="640"
        height="480"
        autoPlay
        muted
        playsInline
      />
      {state !== "live" && (
        <p
          className="camera-unavailable"
          role={state === "offline" ? "alert" : "status"}
        >
          {message}
        </p>
      )}
    </div>
  );
}
