export type SceneMode = "animated" | "reduced" | "fallback";

export type SceneCapabilities = {
  reduced: boolean;
  saveData: boolean;
};

export function detectSceneMode({
  reduced,
  saveData,
}: SceneCapabilities): SceneMode {
  if (saveData) return "fallback";
  return reduced ? "reduced" : "animated";
}

export function readSceneMode(): SceneMode {
  if (typeof window === "undefined" || typeof navigator === "undefined") {
    return "fallback";
  }
  const connection = navigator as Navigator & {
    connection?: { saveData?: boolean };
  };
  return detectSceneMode({
    reduced: window.matchMedia?.("(prefers-reduced-motion: reduce)").matches ?? false,
    saveData: connection.connection?.saveData === true,
  });
}

export function readCompactScene(): boolean {
  if (typeof window === "undefined") return true;
  return window.matchMedia?.("(max-width: 767px)").matches ?? window.innerWidth < 768;
}

type SceneChangeSource = {
  addEventListener?: (event: "change", callback: EventListener) => void;
  removeEventListener?: (event: "change", callback: EventListener) => void;
  addListener?: (callback: EventListener) => void;
  removeListener?: (callback: EventListener) => void;
};

export function subscribeSceneQuality(callback: () => void): () => void {
  if (typeof window === "undefined") return () => undefined;
  const connection =
    typeof navigator === "undefined"
      ? undefined
      : (navigator as Navigator & { connection?: SceneChangeSource }).connection;
  const sources = [
    window.matchMedia?.("(prefers-reduced-motion: reduce)"),
    window.matchMedia?.("(max-width: 767px)"),
    connection,
  ] as Array<SceneChangeSource | undefined>;

  for (const source of sources) {
    if (source?.addEventListener) source.addEventListener("change", callback);
    else source?.addListener?.(callback);
  }

  return () => {
    for (const source of sources) {
      if (source?.removeEventListener) {
        source.removeEventListener("change", callback);
      } else {
        source?.removeListener?.(callback);
      }
    }
  };
}
