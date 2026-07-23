import { afterEach, expect, it, vi } from "vitest";

import {
  detectSceneMode,
  subscribeSceneQuality,
} from "../../components/landing/scene-quality";

it.each([
  [{ reduced: true, saveData: false }, "reduced"],
  [{ reduced: false, saveData: true }, "fallback"],
  [{ reduced: false, saveData: false }, "animated"],
] as const)("selects a safe scene mode for %o", (capabilities, expected) => {
  expect(detectSceneMode(capabilities)).toBe(expected);
});

afterEach(() => {
  vi.unstubAllGlobals();
  Reflect.deleteProperty(navigator, "connection");
});

it("notifies and cleans up when motion, viewport, or data-saving changes", () => {
  const listeners = new Map<string, EventListener>();
  const media = new Map<string, EventListener>();
  vi.stubGlobal("matchMedia", undefined);
  window.matchMedia = vi.fn((query: string) => ({
    matches: false,
    media: query,
    onchange: null,
    addEventListener: (_event: string, listener: EventListener) => {
      media.set(query, listener);
    },
    removeEventListener: vi.fn(),
    dispatchEvent: vi.fn(),
  })) as typeof window.matchMedia;
  const connection = {
    saveData: false,
    addEventListener: (_event: string, listener: EventListener) => {
      listeners.set("connection", listener);
    },
    removeEventListener: vi.fn(),
  };
  Object.defineProperty(navigator, "connection", {
    configurable: true,
    value: connection,
  });
  const callback = vi.fn();

  const cleanup = subscribeSceneQuality(callback);
  media.get("(prefers-reduced-motion: reduce)")?.(new Event("change"));
  media.get("(max-width: 767px)")?.(new Event("change"));
  listeners.get("connection")?.(new Event("change"));

  expect(callback).toHaveBeenCalledTimes(3);
  cleanup();
  expect(connection.removeEventListener).toHaveBeenCalledWith(
    "change",
    callback,
  );
});

it("supports legacy MediaQueryList listeners", () => {
  const addListener = vi.fn();
  const removeListener = vi.fn();
  window.matchMedia = vi.fn(() => ({
    matches: false,
    media: "",
    onchange: null,
    addListener,
    removeListener,
    dispatchEvent: vi.fn(),
  })) as typeof window.matchMedia;
  const callback = vi.fn();

  const cleanup = subscribeSceneQuality(callback);

  expect(addListener).toHaveBeenCalledTimes(2);
  cleanup();
  expect(removeListener).toHaveBeenCalledTimes(2);
});
