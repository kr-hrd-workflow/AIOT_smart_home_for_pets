// @vitest-environment node

import { existsSync, readFileSync } from "node:fs";
import { resolve } from "node:path";
import { expect, it } from "vitest";

import {
  PETCARE_HERO_LOOPS,
  heroLoopState,
} from "./petcare-hero-loop";

it("defines deterministic 12 second desktop and mobile loops", () => {
  expect(PETCARE_HERO_LOOPS).toEqual({
    desktop: {
      id: "PetCareHeroLoopDesktop",
      width: 1600,
      height: 900,
      fps: 24,
      durationInFrames: 288,
    },
    mobile: {
      id: "PetCareHeroLoopMobile",
      width: 720,
      height: 1280,
      fps: 24,
      durationInFrames: 288,
    },
  });

  const start = heroLoopState(0, 288);
  const end = heroLoopState(288, 288);
  const middle = heroLoopState(144, 288);

  expect(end).toEqual(start);
  expect(start.blueOpacity).toBe(0);
  expect(middle.blueOpacity).toBeCloseTo(1, 6);
  expect(middle.scale).toBeGreaterThan(start.scale);
});

it("uses the aligned photoreal keyframes without generated text", () => {
  const source = readFileSync(resolve("remotion/petcare-hero-loop.tsx"), "utf8");

  for (const asset of [
    "landing-apartment-photoreal-v3.webp",
    "landing-apartment-photoreal-v3-blue.webp",
    "landing-apartment-photoreal-mobile-v2.webp",
    "landing-apartment-photoreal-mobile-v2-blue.webp",
  ]) {
    expect(source).toContain(`staticFile(\"${asset}\")`);
    expect(existsSync(resolve("public", asset))).toBe(true);
  }

  expect(source).toContain("useCurrentFrame");
  expect(source).not.toContain("PetCare</");
  expect(source).not.toContain("<ThreeCanvas");
});
