// @vitest-environment node

import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { expect, it } from "vitest";

import { PETCARE_PROMO, sceneAtFrame } from "./petcare-promo";

it("defines the exact 15 second composition", () => {
  expect(PETCARE_PROMO).toEqual({
    id: "PetCarePromo",
    width: 1920,
    height: 1080,
    fps: 30,
    durationInFrames: 450,
  });
});

it.each([
  [0, "home"],
  [119, "home"],
  [120, "feeding"],
  [239, "feeding"],
  [240, "rest"],
  [359, "rest"],
  [360, "events"],
  [449, "events"],
] as const)("maps frame %i to %s", (frame, scene) => {
  expect(sceneAtFrame(frame)).toBe(scene);
});

it("keeps rendering deterministic and outside the application bundle", () => {
  const source = readFileSync(resolve("remotion/petcare-promo.tsx"), "utf8");
  expect(source).toContain("ThreeCanvas");
  expect(source).toContain("useCurrentFrame");
  expect(source).not.toMatch(/useFrame|animation:|transition:/);

  for (const path of [
    "app/page.tsx",
    "components/landing/landing-page.tsx",
    "proxy.ts",
  ]) {
    const routeSource = readFileSync(resolve(path), "utf8");
    expect(routeSource).not.toContain("remotion");
  }
});
