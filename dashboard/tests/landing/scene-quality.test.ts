import { expect, it } from "vitest";

import { detectSceneMode } from "../../components/landing/scene-quality";

it.each([
  [{ reduced: true, saveData: false, webgl: true }, "reduced"],
  [{ reduced: false, saveData: true, webgl: true }, "fallback"],
  [{ reduced: false, saveData: false, webgl: false }, "fallback"],
  [{ reduced: false, saveData: false, webgl: true }, "animated"],
] as const)("selects a safe scene mode for %o", (capabilities, expected) => {
  expect(detectSceneMode(capabilities)).toBe(expected);
});
