import "@testing-library/jest-dom/vitest";

import { afterEach, beforeEach, expect, it, vi } from "vitest";
import {
  buildScrollWorldSegments,
  getLandingCopyLayers,
  getLandingCopyScene,
  getRootScrollProgress,
  mapScrollWorldProgress,
  mountScrollWorld,
  type ScrollWorldConfig,
} from "../../components/landing/scene-director";

const config: ScrollWorldConfig = {
  sections: [
    {
      id: "arrival",
      still: "/arrival.webp",
      stillMobile: "/arrival-mobile.webp",
      clip: "/arrival.mp4",
      clipMobile: "/arrival-mobile.mp4",
    },
    {
      id: "presence",
      still: "/presence.webp",
      stillMobile: "/presence-mobile.webp",
      clip: "/presence.mp4",
      clipMobile: "/presence-mobile.mp4",
    },
  ],
  connectors: ["/connector.mp4"],
  connectorsMobile: ["/connector-mobile.mp4"],
};

const installFakeRaf = () => {
  let now = 0;
  let nextFrame = 1;
  const callbacks = new Map<number, FrameRequestCallback>();

  vi.spyOn(window, "requestAnimationFrame").mockImplementation((callback) => {
    const frame = nextFrame;
    nextFrame += 1;
    callbacks.set(frame, callback);
    return frame;
  });
  vi.spyOn(window, "cancelAnimationFrame").mockImplementation((frame) => {
    callbacks.delete(frame);
  });

  return {
    advance(milliseconds: number) {
      now += milliseconds;
      const scheduled = [...callbacks.entries()];
      callbacks.clear();
      scheduled.forEach(([, callback]) => callback(now));
    },
    pending: () => callbacks.size,
  };
};

afterEach(() => {
  vi.restoreAllMocks();
  document.body.replaceChildren();
});

beforeEach(() => {
  vi.spyOn(HTMLMediaElement.prototype, "pause").mockImplementation(() => {});
});

it("interleaves each dive with the rendered-frame connector that follows it", () => {
  expect(buildScrollWorldSegments(config, false)).toEqual([
    expect.objectContaining({
      kind: "dive",
      clip: "/arrival.mp4",
      still: "/arrival.webp",
    }),
    expect.objectContaining({
      kind: "connector",
      clip: "/connector.mp4",
      still: "/arrival.webp",
      endStill: "/presence.webp",
    }),
    expect.objectContaining({
      kind: "dive",
      clip: "/presence.mp4",
      still: "/presence.webp",
    }),
  ]);

  expect(buildScrollWorldSegments(config, true)[1]).toEqual(
    expect.objectContaining({
      clip: "/connector-mobile.mp4",
      still: "/arrival-mobile.webp",
      endStill: "/presence-mobile.webp",
    }),
  );
});

it("maps root-relative progress and scroll-linked seam overlap deterministically", () => {
  const root = document.createElement("main");
  Object.defineProperty(root, "scrollHeight", { value: 5000 });
  vi.spyOn(root, "getBoundingClientRect").mockReturnValue({
    top: -1000,
  } as DOMRect);

  expect(getRootScrollProgress(root, 1000)).toBe(0.25);

  const segments = buildScrollWorldSegments(config, false);
  const seam = mapScrollWorldProgress(segments, 1.3 / 3.5 - 0.01);

  expect(seam.activeIndex).toBe(0);
  expect(seam.layers).toHaveLength(2);
  expect(seam.layers[0].opacity).toBeLessThan(1);
  expect(seam.layers[1].opacity).toBeGreaterThan(0);
  expect(seam.layers[1].progress).toBe(0);
});

it("maps the same scroll progress into one fixed copy scene", () => {
  expect(getLandingCopyScene(0)).toBe("hero");
  expect(getLandingCopyScene(0.2)).toBe("feeding");
  expect(getLandingCopyScene(0.4)).toBe("rest");
  expect(getLandingCopyScene(0.6)).toBe("events");
  expect(getLandingCopyScene(0.8)).toBe("connect");
  expect(getLandingCopyScene(1)).toBe("final");
});

it("crossfades adjacent copy scenes from scroll progress instead of elapsed time", () => {
  const layers = getLandingCopyLayers(0.16);
  const hero = layers.find((layer) => layer.scene === "hero");
  const feeding = layers.find((layer) => layer.scene === "feeding");

  expect(hero?.opacity).toBeGreaterThan(0);
  expect(hero?.opacity).toBeLessThan(1);
  expect(hero?.translateY).toBeLessThan(0);
  expect(feeding?.opacity).toBeGreaterThan(0);
  expect(feeding?.opacity).toBeLessThan(1);
  expect(feeding?.translateY).toBeGreaterThan(0);
});

it("does not load video assets in data saver mode", () => {
  const fetchSpy = vi.spyOn(globalThis, "fetch");
  const root = document.createElement("main");
  const stage = document.createElement("div");
  Object.defineProperty(navigator, "connection", {
    configurable: true,
    value: { saveData: true },
  });
  document.body.append(root, stage);

  const cleanup = mountScrollWorld(stage, {
    config,
    root,
    reducedMotion: false,
    mobile: false,
  });

  expect(stage.querySelectorAll("img")).toHaveLength(3);
  expect(stage.querySelectorAll('img[src=""]')).toHaveLength(0);
  expect(stage.querySelectorAll("img[src]")).toHaveLength(2);
  expect(stage.querySelector("video")).toBeNull();
  expect(fetchSpy).not.toHaveBeenCalled();

  cleanup();
  expect(stage.childElementCount).toBe(0);
});

it("keeps reduced-motion mode on static posters", () => {
  const play = vi.spyOn(HTMLMediaElement.prototype, "play").mockResolvedValue();
  const root = document.createElement("main");
  const stage = document.createElement("div");
  Object.defineProperty(root, "scrollHeight", { value: 6000 });
  vi.spyOn(root, "getBoundingClientRect").mockReturnValue({
    top: 0,
  } as DOMRect);
  Object.defineProperty(navigator, "connection", {
    configurable: true,
    value: { saveData: false },
  });
  document.body.append(root, stage);

  const cleanup = mountScrollWorld(stage, {
    config,
    root,
    reducedMotion: true,
    mobile: false,
  });

  expect(stage.querySelector("video")).toBeNull();
  expect(play).not.toHaveBeenCalled();
  cleanup();
});

it("leaves wheel scrolling native instead of snapping story chapters", () => {
  const root = document.createElement("main");
  const stage = document.createElement("div");
  const scrollTo = vi.spyOn(window, "scrollTo").mockImplementation(() => {});
  Object.defineProperty(root, "scrollHeight", { value: 6000 });
  vi.spyOn(root, "getBoundingClientRect").mockReturnValue({
    top: 0,
  } as DOMRect);
  Object.defineProperty(navigator, "connection", {
    configurable: true,
    value: { saveData: false },
  });
  document.body.append(root, stage);

  const cleanup = mountScrollWorld(stage, {
    config,
    root,
    reducedMotion: false,
    mobile: false,
  });
  window.dispatchEvent(
    new WheelEvent("wheel", { cancelable: true, deltaY: 120 }),
  );
  window.dispatchEvent(
    new WheelEvent("wheel", { cancelable: true, deltaY: 120 }),
  );

  expect(scrollTo).not.toHaveBeenCalled();
  cleanup();
});

it("smoothly retargets the paused video from its visible time in both directions", () => {
  const raf = installFakeRaf();
  const root = document.createElement("main");
  const stage = document.createElement("div");
  let top = 0;
  Object.defineProperty(root, "scrollHeight", { value: 6000 });
  vi.spyOn(root, "getBoundingClientRect").mockImplementation(
    () => ({ top }) as DOMRect,
  );
  Object.defineProperty(navigator, "connection", {
    configurable: true,
    value: { saveData: false },
  });
  document.body.append(root, stage);

  const cleanup = mountScrollWorld(stage, {
    config,
    root,
    reducedMotion: false,
    mobile: false,
  });
  const video = stage.querySelector<HTMLVideoElement>("video");
  if (!video) throw new Error("expected landing video");
  Object.defineProperty(video, "duration", { value: 10 });
  video.dispatchEvent(new Event("loadedmetadata"));
  const scrollRange = root.scrollHeight - window.innerHeight;
  const scrollToProgress = (progress: number) => {
    top = -(scrollRange * progress);
    window.dispatchEvent(new Event("scroll"));
    raf.advance(0);
  };

  scrollToProgress(0.17);
  expect(raf.pending()).toBe(1);
  raf.advance(0);
  raf.advance(500);
  const firstIntermediate = video.currentTime;
  expect(firstIntermediate).toBeGreaterThan(0);
  expect(firstIntermediate).toBeLessThan(10 * (0.17 * 3.5 / 1.3));

  scrollToProgress(0.32);
  expect(video.currentTime).toBe(firstIntermediate);
  raf.advance(0);
  raf.advance(500);
  const forwardIntermediate = video.currentTime;
  expect(forwardIntermediate).toBeGreaterThan(firstIntermediate);

  scrollToProgress(0.1);
  expect(video.currentTime).toBe(forwardIntermediate);
  raf.advance(0);
  raf.advance(500);
  expect(video.currentTime).toBeLessThan(forwardIntermediate);
  expect(video.currentTime).toBeGreaterThan(10 * (0.1 * 3.5 / 1.3));
  raf.advance(500);
  expect(video.currentTime).toBeCloseTo(10 * (0.1 * 3.5 / 1.3), 10);
  expect(raf.pending()).toBe(0);
  const settled = video.currentTime;
  raf.advance(1_000);
  expect(video.currentTime).toBe(settled);

  cleanup();
});

it("keeps advancing through continuous forward and reverse scroll retargeting", () => {
  const raf = installFakeRaf();
  const root = document.createElement("main");
  const stage = document.createElement("div");
  let top = 0;
  Object.defineProperty(root, "scrollHeight", { value: 6000 });
  vi.spyOn(root, "getBoundingClientRect").mockImplementation(
    () => ({ top }) as DOMRect,
  );
  Object.defineProperty(navigator, "connection", {
    configurable: true,
    value: { saveData: false },
  });
  document.body.append(root, stage);

  const cleanup = mountScrollWorld(stage, {
    config,
    root,
    reducedMotion: false,
    mobile: false,
  });
  const video = stage.querySelector<HTMLVideoElement>("video");
  if (!video) throw new Error("expected landing video");
  Object.defineProperty(video, "duration", { value: 10 });
  video.dispatchEvent(new Event("loadedmetadata"));
  const scrollRange = root.scrollHeight - window.innerHeight;
  const retarget = (progress: number) => {
    top = -(scrollRange * progress);
    window.dispatchEvent(new Event("scroll"));
    raf.advance(16);
    expect(video.paused).toBe(true);
    expect(raf.pending()).toBe(1);
  };

  [0.03, 0.06, 0.09, 0.12, 0.15, 0.18].forEach(retarget);
  expect(video.currentTime).toBeGreaterThan(0);
  expect(video.currentTime).toBeLessThan(4.846153846153846);
  raf.advance(0);
  raf.advance(1_000);
  expect(video.currentTime).toBeCloseTo(4.846153846153846, 10);
  expect(raf.pending()).toBe(0);

  [0.15, 0.12, 0.09, 0.06].forEach(retarget);
  expect(video.currentTime).toBeLessThan(4.846153846153846);
  expect(video.currentTime).toBeGreaterThan(1.6153846153846154);
  raf.advance(0);
  raf.advance(1_000);
  expect(video.currentTime).toBeCloseTo(1.6153846153846154, 10);
  expect(raf.pending()).toBe(0);

  cleanup();
});

it("interpolates a target change larger than its sub-frame threshold", () => {
  const raf = installFakeRaf();
  const root = document.createElement("main");
  const stage = document.createElement("div");
  let top = 0;
  Object.defineProperty(root, "scrollHeight", { value: 6000 });
  vi.spyOn(root, "getBoundingClientRect").mockImplementation(
    () => ({ top }) as DOMRect,
  );
  Object.defineProperty(navigator, "connection", {
    configurable: true,
    value: { saveData: false },
  });
  document.body.append(root, stage);

  const cleanup = mountScrollWorld(stage, {
    config,
    root,
    reducedMotion: false,
    mobile: false,
  });
  const video = stage.querySelector<HTMLVideoElement>("video");
  if (!video) throw new Error("expected landing video");
  Object.defineProperty(video, "duration", { value: 10 });
  video.dispatchEvent(new Event("loadedmetadata"));
  video.currentTime = 1;
  top = -((root.scrollHeight - window.innerHeight) * ((1.004 / 10) * 1.3 / 3.5));
  window.dispatchEvent(new Event("scroll"));
  raf.advance(0);

  const pending = raf.pending();
  cleanup();
  expect(pending).toBe(1);
});

it("cancels a pending landing scrub when the scroll world unmounts", () => {
  const raf = installFakeRaf();
  const root = document.createElement("main");
  const stage = document.createElement("div");
  Object.defineProperty(root, "scrollHeight", { value: 6000 });
  vi.spyOn(root, "getBoundingClientRect").mockReturnValue({ top: 0 } as DOMRect);
  Object.defineProperty(navigator, "connection", {
    configurable: true,
    value: { saveData: false },
  });
  document.body.append(root, stage);

  const cleanup = mountScrollWorld(stage, {
    config,
    root,
    reducedMotion: false,
    mobile: false,
  });
  const video = stage.querySelector<HTMLVideoElement>("video");
  if (!video) throw new Error("expected landing video");
  Object.defineProperty(video, "duration", { value: 10 });
  video.dispatchEvent(new Event("loadedmetadata"));
  vi.spyOn(root, "getBoundingClientRect").mockReturnValue({
    top: -(root.scrollHeight - window.innerHeight) * 0.17,
  } as DOMRect);
  window.dispatchEvent(new Event("scroll"));
  raf.advance(0);
  const pending = raf.pending();

  cleanup();
  expect(pending).toBe(1);
  expect(raf.pending()).toBe(0);
});

it("streams nearby clips directly, keeps posters until the requested frame paints, and cleans up", async () => {
  const playSpy = vi
    .spyOn(HTMLMediaElement.prototype, "play")
    .mockResolvedValue();
  vi.spyOn(HTMLMediaElement.prototype, "pause").mockImplementation(() => {});
  const fetchSpy = vi.spyOn(globalThis, "fetch");
  const createObjectURL = vi.spyOn(URL, "createObjectURL");
  const revokeObjectURL = vi.spyOn(URL, "revokeObjectURL");
  const root = document.createElement("main");
  const stage = document.createElement("div");
  Object.defineProperty(root, "scrollHeight", { value: 6000 });
  let top = 0;
  vi.spyOn(root, "getBoundingClientRect").mockImplementation(
    () => ({ top }) as DOMRect,
  );
  Object.defineProperty(navigator, "connection", {
    configurable: true,
    value: { saveData: false },
  });
  document.body.append(root, stage);

  const cleanup = mountScrollWorld(stage, {
    config,
    root,
    reducedMotion: false,
    mobile: false,
  });
  expect(stage.querySelectorAll("video")).toHaveLength(2);

  expect(fetchSpy).not.toHaveBeenCalled();
  expect(createObjectURL).not.toHaveBeenCalled();
  for (const video of stage.querySelectorAll("video")) {
    expect(video.autoplay).toBe(false);
    expect(video.loop).toBe(false);
    expect(video.muted).toBe(true);
    expect(video.playsInline).toBe(true);
  }

  const firstScene = stage.querySelector<HTMLElement>(".scroll-world-scene");
  const firstVideo = firstScene?.querySelector("video");
  expect(firstVideo).toHaveAttribute("src", "/arrival.mp4");
  expect(firstScene).not.toHaveClass("has-video-frame");
  Object.defineProperty(firstVideo, "duration", { value: 10 });
  Object.defineProperty(firstVideo, "requestVideoFrameCallback", {
    configurable: true,
    value: vi.fn(),
  });
  firstVideo?.dispatchEvent(new Event("loadedmetadata"));
  expect(firstVideo?.currentTime).toBe(0);
  firstVideo?.dispatchEvent(new Event("seeked"));
  expect(firstScene).not.toHaveClass("has-video-frame");
  await new Promise((resolve) => window.setTimeout(resolve, 100));
  expect(firstScene).toHaveClass("has-video-frame");
  expect(firstVideo?.paused).toBe(true);
  top = -5;
  window.dispatchEvent(new Event("scroll"));
  await new Promise<void>((resolve) => requestAnimationFrame(() => resolve()));
  await new Promise((resolve) => window.setTimeout(resolve, 120));
  expect(firstVideo?.currentTime).toBeGreaterThan(0);
  expect(firstVideo?.currentTime).toBeLessThan(0.04);
  firstVideo?.dispatchEvent(new Event("seeked"));
  await new Promise((resolve) => window.setTimeout(resolve, 100));
  expect(firstScene).toHaveClass("has-video-frame");
  top = -1000;
  window.dispatchEvent(new Event("scroll"));
  await new Promise<void>((resolve) => requestAnimationFrame(() => resolve()));
  expect(root.dataset.landingScene).toBe("feeding");
  expect(playSpy).not.toHaveBeenCalled();
  expect(firstVideo?.currentTime).toBeGreaterThan(0);
  expect(firstScene).toHaveClass("has-video-frame");
  top = -((root.scrollHeight - window.innerHeight) * 0.16);
  window.dispatchEvent(new Event("scroll"));
  await new Promise<void>((resolve) => requestAnimationFrame(() => resolve()));
  expect(root.dataset.landingScene).toBe("feeding");
  expect(root.dataset.landingCopyPeer).toBe("hero");
  expect(Number(root.style.getPropertyValue("--landing-copy-hero-opacity"))).toBeGreaterThan(0);
  expect(Number(root.style.getPropertyValue("--landing-copy-feeding-opacity"))).toBeGreaterThan(0);
  firstVideo?.dispatchEvent(new Event("error"));
  expect(firstScene).toHaveAttribute("data-video-error", "true");
  expect(firstScene).not.toHaveClass("has-video-frame");

  cleanup();
  expect(revokeObjectURL).not.toHaveBeenCalled();
});

it("uses scroll position for seam opacity and restores the correct connector poster in reverse", async () => {
  vi.spyOn(globalThis, "fetch").mockResolvedValue({
    ok: true,
    blob: async () => new Blob(["video"], { type: "video/mp4" }),
  } as Response);
  vi.spyOn(URL, "createObjectURL")
    .mockReturnValueOnce("blob:arrival")
    .mockReturnValueOnce("blob:connector")
    .mockReturnValueOnce("blob:presence");
  vi.spyOn(URL, "revokeObjectURL").mockImplementation(() => {});
  const root = document.createElement("main");
  const stage = document.createElement("div");
  let top = 0;
  Object.defineProperty(root, "scrollHeight", { value: 4500 });
  vi.spyOn(root, "getBoundingClientRect").mockImplementation(
    () => ({ top }) as DOMRect,
  );
  Object.defineProperty(navigator, "connection", {
    configurable: true,
    value: { saveData: false },
  });
  document.body.append(root, stage);

  const cleanup = mountScrollWorld(stage, {
    config,
    root,
    reducedMotion: false,
    mobile: false,
  });
  await vi.waitFor(() => expect(stage.querySelectorAll("video")).toHaveLength(2));

  const scrollRange = root.scrollHeight - window.innerHeight;
  top = -(scrollRange * (2.15 / 3.5));
  window.dispatchEvent(new Event("scroll"));
  await new Promise<void>((resolve) => requestAnimationFrame(() => resolve()));

  const scenes = stage.querySelectorAll<HTMLElement>(".scroll-world-scene");
  expect(Number(scenes[1].style.getPropertyValue("--scroll-world-opacity"))).toBeGreaterThan(0);
  expect(Number(scenes[2].style.getPropertyValue("--scroll-world-opacity"))).toBeGreaterThan(0);
  expect(scenes[1].querySelector("img")).toHaveAttribute("src", "/presence.webp");

  top = -(scrollRange * (1.57 / 3.5));
  window.dispatchEvent(new Event("scroll"));
  await new Promise<void>((resolve) => requestAnimationFrame(() => resolve()));

  expect(scenes[1].querySelector("img")).toHaveAttribute("src", "/arrival.webp");
  expect(scenes[2].style.getPropertyValue("--scroll-world-opacity")).toBe("0");

  cleanup();
});
