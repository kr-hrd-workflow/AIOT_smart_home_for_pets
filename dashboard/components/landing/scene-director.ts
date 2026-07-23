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
  ready: boolean;
  targetTime: number;
};

const DESKTOP_FALLBACK = "/landing-apartment-photoreal-v3.webp";
const MOBILE_FALLBACK = "/landing-apartment-photoreal-mobile-v2.webp";
const SEAM_OVERLAP = 0.12;
const clamp = (value: number) => Math.min(1, Math.max(0, value));

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
  const staticMode = reducedMotion || saveData;
  const segments = buildScrollWorldSegments(options.config, mobile);
  const layer = document.createElement("div");
  let closed = false;
  let frame = 0;

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

  const seek = (runtime: SegmentRuntime) => {
    const video = runtime.video;
    if (!runtime.ready || !video || isAtTarget(runtime)) return;
    runtime.element.classList.remove("has-video-frame");
    if (!video.seeking) video.currentTime = targetTime(runtime);
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
      seek(runtime);
    });
    video.addEventListener("loadeddata", () => {
      if (isAtTarget(runtime)) revealPaintedFrame(runtime);
      else seek(runtime);
    });
    video.addEventListener("seeked", () => {
      seek(runtime);
      if (isAtTarget(runtime)) revealPaintedFrame(runtime);
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

    const state = mapScrollWorldProgress(
      runtimes,
      getRootScrollProgress(options.root),
    );

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
      seek(runtime);
    });

    const nextRuntime = runtimes[state.activeIndex + 1];
    if (nextRuntime) loadClip(nextRuntime);
  };

  const scheduleUpdate = () => {
    if (!frame) frame = window.requestAnimationFrame(update);
  };

  window.addEventListener("scroll", scheduleUpdate, { passive: true });
  window.addEventListener("resize", scheduleUpdate);
  update();

  return () => {
    if (closed) return;
    closed = true;
    window.removeEventListener("scroll", scheduleUpdate);
    window.removeEventListener("resize", scheduleUpdate);
    if (frame) window.cancelAnimationFrame(frame);
    layer.remove();
  };
}
