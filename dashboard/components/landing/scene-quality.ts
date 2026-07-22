export type SceneMode = "animated" | "reduced" | "fallback";

export type SceneCapabilities = {
  reduced: boolean;
  saveData: boolean;
  webgl: boolean;
};

export function detectSceneMode({
  reduced,
  saveData,
  webgl,
}: SceneCapabilities): SceneMode {
  if (saveData || !webgl) return "fallback";
  return reduced ? "reduced" : "animated";
}

function supportsWebGL(): boolean {
  if (typeof WebGLRenderingContext === "undefined") return false;
  try {
    const canvas = document.createElement("canvas");
    return Boolean(
      canvas.getContext("webgl2") ||
        canvas.getContext("webgl") ||
        canvas.getContext("experimental-webgl"),
    );
  } catch {
    return false;
  }
}

export function readSceneMode(): SceneMode {
  if (typeof window === "undefined" || typeof document === "undefined") {
    return "fallback";
  }
  const connection = navigator as Navigator & {
    connection?: { saveData?: boolean };
  };
  return detectSceneMode({
    reduced: window.matchMedia?.("(prefers-reduced-motion: reduce)").matches ?? false,
    saveData: connection.connection?.saveData === true,
    webgl: supportsWebGL(),
  });
}

export function readCompactScene(): boolean {
  if (typeof window === "undefined") return true;
  return window.matchMedia?.("(max-width: 767px)").matches ?? window.innerWidth < 768;
}
