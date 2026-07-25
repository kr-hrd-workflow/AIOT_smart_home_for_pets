export type ScrollWorldSection = {
  id: string;
  still: string;
  stillMobile?: string;
  clip: string;
  clipMobile?: string;
};

export type ScrollWorldConfig = {
  sections: ScrollWorldSection[];
  connectors: string[];
  connectorsMobile?: string[];
  diveScroll?: number;
  connectorScroll?: number;
};

export type ScrollWorldSegment = {
  kind: "dive" | "connector";
  sectionIndex: number;
  clip: string;
  still: string;
  endStill: string;
  weight: number;
};

export type MountScrollWorldOptions = {
  config: ScrollWorldConfig;
  root: HTMLElement;
  reducedMotion?: boolean;
  mobile?: boolean;
};

type SegmentRuntime = ScrollWorldSegment & {
  element: HTMLDivElement;
  image: HTMLImageElement;
  video?: HTMLVideoElement;
  playbackFrame?: number;
  reverseFromTime?: number;
  reverseStartedAt?: number;
  ready: boolean;
  targetTime: number;
};

const DESKTOP_FALLBACK = "/landing-apartment-photoreal-v3.webp";
const MOBILE_FALLBACK = "/landing-apartment-photoreal-mobile-v2.webp";
const SEAM_OVERLAP = 0.12;
const PLAYBACK_EPSILON_SECONDS = 0.04;
const SCROLL_PLAYBACK_RATE = 0.72;
const REVERSE_SEEK_DURATION_MS = 2200;
const CHAPTER_SCROLL_LOCK_MS = 720;
const clamp = (value: number) => Math.min(1, Math.max(0, value));

export const LANDING_COPY_SCENES = [
  "hero",
  "feeding",
  "rest",
  "events",
  "connect",
  "final",
] as const;

export type LandingCopyScene = (typeof LANDING_COPY_SCENES)[number];

export type LandingCopyLayer = {
  scene: LandingCopyScene;
  opacity: number;
  translateY: number;
};

export type LandingMotionFrame = {
  scale: number;
  x: number;
  y: number;
};

const LANDING_MOTION_STOPS = [
  { at: 0, scale: 1.025, x: 0, y: 0 },
  { at: 0.2, scale: 1.14, x: -3.2, y: 1.2 },
  { at: 0.4, scale: 1.08, x: 3, y: -1.4 },
  { at: 0.6, scale: 1.18, x: 0.8, y: 0.5 },
  { at: 0.8, scale: 1.07, x: -2.2, y: -0.8 },
  { at: 1, scale: 1.13, x: 0, y: 0 },
] as const;

export function getLandingMotionFrame(progress: number): LandingMotionFrame {
  const value = clamp(progress);
  const nextIndex = LANDING_MOTION_STOPS.findIndex((stop) => stop.at >= value);
  if (nextIndex <= 0) return LANDING_MOTION_STOPS[0];

  const next = LANDING_MOTION_STOPS[nextIndex];
  const previous = LANDING_MOTION_STOPS[nextIndex - 1];
  const mix = (value - previous.at) / (next.at - previous.at);
  const interpolate = (from: number, to: number) => from + (to - from) * mix;

  return {
    scale: interpolate(previous.scale, next.scale),
    x: interpolate(previous.x, next.x),
    y: interpolate(previous.y, next.y),
  };
}

export function getLandingCopyLayers(progress: number): LandingCopyLayer[] {
  const position = clamp(progress) * LANDING_COPY_SCENES.length;
  const activeIndex = Math.min(
    LANDING_COPY_SCENES.length - 1,
    Math.floor(position),
  );
  const localProgress = position - activeIndex;
  const blend =
    activeIndex < LANDING_COPY_SCENES.length - 1
      ? clamp((localProgress - 0.82) / 0.18)
      : 0;

  return LANDING_COPY_SCENES.map((scene, index) => {
    if (index === activeIndex) {
      return { scene, opacity: 1 - blend, translateY: -16 * blend };
    }
    if (index === activeIndex + 1) {
      return { scene, opacity: blend, translateY: 36 * (1 - blend) };
    }
    return { scene, opacity: 0, translateY: 36 };
  });
}

export function getLandingCopyScene(progress: number): LandingCopyScene {
  return getLandingCopyLayers(progress).reduce(
    (visible, layer) => (layer.opacity > visible.opacity ? layer : visible),
  ).scene;
}

export function getRootScrollProgress(
  root: HTMLElement,
  viewportHeight = window.innerHeight,
): number {
  const scrollRange = Math.max(1, root.scrollHeight - viewportHeight);
  return clamp(-root.getBoundingClientRect().top / scrollRange);
}

export function mapScrollWorldProgress(
  segments: ScrollWorldSegment[],
  progress: number,
): {
  activeIndex: number;
  layers: { index: number; opacity: number; progress: number }[];
} {
  if (!segments.length) return { activeIndex: -1, layers: [] };

  const totalWeight = segments.reduce((total, segment) => total + segment.weight, 0);
  const position = clamp(progress) * totalWeight;
  let offset = 0;
  let activeIndex = segments.length - 1;
  let localProgress = 1;

  for (let index = 0; index < segments.length; index += 1) {
    const segment = segments[index];
    const end = offset + segment.weight;
    if (position < end) {
      activeIndex = index;
      localProgress = clamp((position - offset) / segment.weight);
      break;
    }
    offset = end;
  }

  const blend =
    activeIndex < segments.length - 1
      ? clamp((localProgress - (1 - SEAM_OVERLAP)) / SEAM_OVERLAP)
      : 0;
  const layers = [
    { index: activeIndex, opacity: 1 - blend, progress: localProgress },
  ];
  if (blend > 0) {
    layers.push({ index: activeIndex + 1, opacity: blend, progress: 0 });
  }

  return { activeIndex, layers };
}

export function buildScrollWorldSegments(
  config: ScrollWorldConfig,
  mobile: boolean,
): ScrollWorldSegment[] {
  const segments: ScrollWorldSegment[] = [];

  config.sections.forEach((section, index) => {
    const still = mobile ? section.stillMobile || section.still : section.still;
    segments.push({
      kind: "dive",
      sectionIndex: index,
      clip: mobile ? section.clipMobile || section.clip : section.clip,
      still,
      endStill: still,
      weight: config.diveScroll || 1.3,
    });

    const connector = mobile
      ? config.connectorsMobile?.[index] || config.connectors[index]
      : config.connectors[index];
    const nextSection = config.sections[index + 1];
    if (connector && nextSection) {
      segments.push({
        kind: "connector",
        sectionIndex: index,
        clip: connector,
        still,
        endStill: mobile
          ? nextSection.stillMobile || nextSection.still
          : nextSection.still,
        weight: config.connectorScroll || 0.9,
      });
    }
  });

  return segments;
}

export function mountScrollWorld(
  stage: HTMLElement,
  options: MountScrollWorldOptions,
): () => void {
  const mobile =
    options.mobile ??
    window.matchMedia?.("(max-width: 767px)").matches ??
    false;
  const reducedMotion =
    options.reducedMotion ??
    window.matchMedia?.("(prefers-reduced-motion: reduce)").matches ??
    false;
  const saveData =
    (
      navigator as Navigator & {
        connection?: { saveData?: boolean };
      }
    ).connection?.saveData === true;
  const staticMode = saveData;
  const segments = buildScrollWorldSegments(options.config, mobile);
  const layer = document.createElement("div");
  let closed = false;
  let frame = 0;
  let wheelLock = 0;

  if (!staticMode) {
    options.root.dataset.scrollWorldActive = "true";
    options.root.dataset.landingScene = "hero";
  }

  layer.className = "scroll-world-layer";
  stage.appendChild(layer);

  const runtimes: SegmentRuntime[] = segments.map((segment) => {
    const element = document.createElement("div");
    const image = document.createElement("img");
    const fallback = mobile ? MOBILE_FALLBACK : DESKTOP_FALLBACK;

    element.className = "scroll-world-scene";
    image.className = "scroll-world-poster";
    image.alt = "";
    image.decoding = "async";
    image.loading = "lazy";
    image.addEventListener(
      "error",
      () => {
        if (!image.src.endsWith(fallback)) image.src = fallback;
      },
      { once: true },
    );
    element.appendChild(image);
    layer.appendChild(element);

    return {
      ...segment,
      element,
      image,
      ready: false,
      targetTime: 0,
    };
  });

  const setPoster = (runtime: SegmentRuntime, progress: number) => {
    const source =
      runtime.kind === "connector" && progress >= 0.5
        ? runtime.endStill
        : runtime.still;
    if (runtime.image.getAttribute("src") !== source) {
      runtime.image.setAttribute("src", source);
    }
  };

  const targetTime = (runtime: SegmentRuntime) => {
    const video = runtime.video;
    const duration =
      video && Number.isFinite(video.duration) ? video.duration : 0;
    return duration
      ? Math.min(duration * 0.999, Math.max(0, runtime.targetTime * duration))
      : 0;
  };

  const isAtTarget = (runtime: SegmentRuntime) => {
    const video = runtime.video;
    return Boolean(
      video &&
        Math.abs(video.currentTime - targetTime(runtime)) <
          (mobile ? 0.02 : 0.008),
    );
  };

  const revealPaintedFrame = (runtime: SegmentRuntime) => {
    if (closed || !isAtTarget(runtime)) return;
    const video = runtime.video as
      | (HTMLVideoElement & {
          requestVideoFrameCallback?: (callback: () => void) => number;
        })
      | undefined;
    const reveal = () => {
      if (!closed && isAtTarget(runtime)) {
        runtime.element.classList.add("has-video-frame");
      }
    };
    if (video?.requestVideoFrameCallback) {
      video.requestVideoFrameCallback(reveal);
      window.setTimeout(reveal, 80);
    } else {
      reveal();
    }
  };

  const cancelPlaybackFrame = (runtime: SegmentRuntime) => {
    if (runtime.playbackFrame) {
      window.cancelAnimationFrame(runtime.playbackFrame);
      runtime.playbackFrame = undefined;
    }
  };

  const resetReverseSeek = (runtime: SegmentRuntime) => {
    runtime.reverseFromTime = undefined;
    runtime.reverseStartedAt = undefined;
  };

  const pauseAtTarget = (runtime: SegmentRuntime) => {
    const video = runtime.video;
    if (!video) return;
    video.pause();
    cancelPlaybackFrame(runtime);
    resetReverseSeek(runtime);
    const target = targetTime(runtime);
    if (!video.seeking && Math.abs(video.currentTime - target) > 0.008) {
      video.currentTime = target;
    } else {
      revealPaintedFrame(runtime);
    }
  };

  const monitorPlayback = (runtime: SegmentRuntime) => {
    cancelPlaybackFrame(runtime);
    const tick = () => {
      runtime.playbackFrame = undefined;
      const video = runtime.video;
      if (closed || !runtime.ready || !video) return;
      const remaining = targetTime(runtime) - video.currentTime;
      if (remaining <= PLAYBACK_EPSILON_SECONDS) {
        pauseAtTarget(runtime);
        return;
      }
      video.playbackRate = SCROLL_PLAYBACK_RATE;
      runtime.playbackFrame = window.requestAnimationFrame(tick);
    };
    runtime.playbackFrame = window.requestAnimationFrame(tick);
  };

  const seekBackward = (runtime: SegmentRuntime) => {
    const video = runtime.video;
    if (!video) return;
    video.pause();
    if (
      runtime.reverseStartedAt === undefined ||
      runtime.reverseFromTime === undefined
    ) {
      cancelPlaybackFrame(runtime);
      runtime.reverseStartedAt = performance.now();
      runtime.reverseFromTime = video.currentTime;
    } else if (runtime.playbackFrame) {
      return;
    }
    const tick = (now: number) => {
      runtime.playbackFrame = undefined;
      const activeVideo = runtime.video;
      if (closed || !runtime.ready || !activeVideo) return;
      const target = targetTime(runtime);
      const startedAt = runtime.reverseStartedAt;
      const fromTime = runtime.reverseFromTime;
      if (startedAt === undefined || fromTime === undefined) return;
      const progress = clamp((now - startedAt) / REVERSE_SEEK_DURATION_MS);
      const nextTime = fromTime + (target - fromTime) * progress;
      if (
        progress >= 1 ||
        activeVideo.currentTime - target <= PLAYBACK_EPSILON_SECONDS
      ) {
        if (activeVideo.seeking) {
          runtime.playbackFrame = window.requestAnimationFrame(tick);
          return;
        }
        pauseAtTarget(runtime);
        return;
      }
      if (!activeVideo.seeking) {
        activeVideo.currentTime = Math.max(target, nextTime);
      }
      runtime.playbackFrame = window.requestAnimationFrame(tick);
    };
    runtime.playbackFrame = window.requestAnimationFrame(tick);
  };

  const driveToTarget = (runtime: SegmentRuntime) => {
    const video = runtime.video;
    if (!runtime.ready || !video) return;
    const difference = targetTime(runtime) - video.currentTime;
    if (reducedMotion) {
      pauseAtTarget(runtime);
      return;
    }
    if (Math.abs(difference) <= PLAYBACK_EPSILON_SECONDS) {
      pauseAtTarget(runtime);
      return;
    }
    if (difference < 0) {
      seekBackward(runtime);
      return;
    }
    if (runtime.reverseStartedAt !== undefined) {
      cancelPlaybackFrame(runtime);
    }
    resetReverseSeek(runtime);
    video.playbackRate = SCROLL_PLAYBACK_RATE;
    void video.play().then(
      () => monitorPlayback(runtime),
      () => pauseAtTarget(runtime),
    );
  };

  const loadClip = (runtime: SegmentRuntime) => {
    if (staticMode || closed || runtime.video || !runtime.clip) {
      return;
    }

    const video = document.createElement("video");
    video.className = "scroll-world-video";
    video.autoplay = false;
    video.loop = false;
    video.muted = true;
    video.playsInline = true;
    video.preload = "auto";
    video.setAttribute("muted", "");
    video.setAttribute("playsinline", "");
    video.src = runtime.clip;
    video.addEventListener("loadedmetadata", () => {
      runtime.ready = true;
      driveToTarget(runtime);
    });
    video.addEventListener("loadeddata", () => {
      if (isAtTarget(runtime)) revealPaintedFrame(runtime);
      else driveToTarget(runtime);
    });
    video.addEventListener("seeked", () => {
      if (isAtTarget(runtime)) revealPaintedFrame(runtime);
      else if (!runtime.playbackFrame) driveToTarget(runtime);
    });
    video.addEventListener(
      "error",
      () => {
        runtime.ready = false;
        runtime.element.classList.remove("has-video-frame");
        runtime.element.dataset.videoError = "true";
      },
      { once: true },
    );
    runtime.video = video;
    runtime.element.appendChild(video);
  };

  const update = () => {
    frame = 0;
    if (closed || !runtimes.length) return;

    const progress = getRootScrollProgress(options.root);
    const state = mapScrollWorldProgress(runtimes, progress);
    const motion = getLandingMotionFrame(progress);
    options.root.style.setProperty("--landing-story-progress", String(progress));
    options.root.style.setProperty("--landing-media-scale", String(motion.scale));
    options.root.style.setProperty("--landing-media-x", `${motion.x}%`);
    options.root.style.setProperty("--landing-media-y", `${motion.y}%`);

    if (!staticMode) {
      const copyLayers = getLandingCopyLayers(progress);
      const activeCopyScene = getLandingCopyScene(progress);
      const peer = copyLayers.find(
        (layer) => layer.scene !== activeCopyScene && layer.opacity > 0,
      );

      options.root.dataset.landingScene = activeCopyScene;
      options.root
        .querySelectorAll<HTMLElement>("[data-landing-target]")
        .forEach((item) => {
          if (item.dataset.landingTarget === activeCopyScene) {
            item.setAttribute("aria-current", "step");
          } else {
            item.removeAttribute("aria-current");
          }
        });
      if (peer) options.root.dataset.landingCopyPeer = peer.scene;
      else delete options.root.dataset.landingCopyPeer;
      copyLayers.forEach((layer) => {
        options.root.style.setProperty(
          `--landing-copy-${layer.scene}-opacity`,
          String(layer.opacity),
        );
        options.root.style.setProperty(
          `--landing-copy-${layer.scene}-translate`,
          `${layer.translateY}px`,
        );
      });
    }

    runtimes.forEach((runtime) => {
      runtime.element.classList.remove("is-active");
      runtime.element.style.setProperty("--scroll-world-opacity", "0");
    });

    for (
      let index = Math.max(0, state.activeIndex - 1);
      index <= Math.min(runtimes.length - 1, state.activeIndex + 1);
      index += 1
    ) {
      setPoster(runtimes[index], index < state.activeIndex ? 1 : 0);
    }

    state.layers.forEach((layerState) => {
      const runtime = runtimes[layerState.index];
      runtime.targetTime = layerState.progress;
      setPoster(runtime, layerState.progress);
      runtime.element.classList.add("is-active");
      runtime.element.style.setProperty(
        "--scroll-world-opacity",
        String(layerState.opacity),
      );
      loadClip(runtime);
      driveToTarget(runtime);
    });

    const nextRuntime = runtimes[state.activeIndex + 1];
    if (nextRuntime) loadClip(nextRuntime);
    runtimes.forEach((runtime, index) => {
      if (
        runtime.video &&
        index !== state.activeIndex &&
        !state.layers.some((layerState) => layerState.index === index)
      ) {
        runtime.video.pause();
        cancelPlaybackFrame(runtime);
      }
    });
  };

  const scheduleUpdate = () => {
    if (!frame) frame = window.requestAnimationFrame(update);
  };

  const armWheelLock = () => {
    if (wheelLock) window.clearTimeout(wheelLock);
    wheelLock = window.setTimeout(() => {
      wheelLock = 0;
    }, CHAPTER_SCROLL_LOCK_MS);
  };

  const scrollToChapter = (event: WheelEvent) => {
    if (
      mobile ||
      staticMode ||
      event.ctrlKey ||
      Math.abs(event.deltaY) < Math.abs(event.deltaX) ||
      Math.abs(event.deltaY) < 8
    ) {
      return;
    }

    const rootTop = options.root.getBoundingClientRect().top;
    const rootBottom = rootTop + options.root.scrollHeight;
    if (rootBottom <= 0 || rootTop >= window.innerHeight) return;

    const progress = getRootScrollProgress(options.root);
    const lastIndex = LANDING_COPY_SCENES.length - 1;
    const currentIndex = Math.round(progress * lastIndex);
    const direction = Math.sign(event.deltaY);
    const nextIndex = Math.min(lastIndex, Math.max(0, currentIndex + direction));
    if (nextIndex === currentIndex) return;

    event.preventDefault();
    if (wheelLock) {
      armWheelLock();
      return;
    }
    const rootStart = window.scrollY + rootTop;
    const scrollRange = Math.max(1, options.root.scrollHeight - window.innerHeight);
    armWheelLock();
    window.scrollTo({
      top: rootStart + scrollRange * (nextIndex / lastIndex),
      behavior: reducedMotion ? "auto" : "smooth",
    });
  };

  window.addEventListener("scroll", scheduleUpdate, { passive: true });
  window.addEventListener("resize", scheduleUpdate);
  window.addEventListener("wheel", scrollToChapter, { passive: false });
  update();

  return () => {
    if (closed) return;
    closed = true;
    window.removeEventListener("scroll", scheduleUpdate);
    window.removeEventListener("resize", scheduleUpdate);
    window.removeEventListener("wheel", scrollToChapter);
    if (frame) window.cancelAnimationFrame(frame);
    if (wheelLock) window.clearTimeout(wheelLock);
    runtimes.forEach((runtime) => {
      if (runtime.video && !runtime.video.paused) runtime.video.pause();
      cancelPlaybackFrame(runtime);
    });
    delete options.root.dataset.scrollWorldActive;
    delete options.root.dataset.landingScene;
    delete options.root.dataset.landingCopyPeer;
    LANDING_COPY_SCENES.forEach((scene) => {
      options.root.style.removeProperty(`--landing-copy-${scene}-opacity`);
      options.root.style.removeProperty(`--landing-copy-${scene}-translate`);
    });
    options.root.style.removeProperty("--landing-story-progress");
    options.root.style.removeProperty("--landing-media-scale");
    options.root.style.removeProperty("--landing-media-x");
    options.root.style.removeProperty("--landing-media-y");
    layer.remove();
  };
}
