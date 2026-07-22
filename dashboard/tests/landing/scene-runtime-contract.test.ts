// @vitest-environment node

import { readFileSync } from "node:fs";
import { expect, it } from "vitest";

const readLandingSource = (name: string) => {
  try {
    return readFileSync(new URL(`../../components/landing/${name}`, import.meta.url), "utf8");
  } catch {
    return "";
  }
};

it("loads the heavy R3F canvas lazily and renders it only on demand", () => {
  const experience = readLandingSource("pet-home-experience.tsx");
  const canvas = readLandingSource("pet-home-canvas.tsx");

  expect(experience).toContain('lazy(() => import("./pet-home-canvas")');
  expect(experience).not.toContain("@react-three/fiber");
  expect(canvas).toContain('frameloop="demand"');
  expect(canvas).not.toContain('frameloop="always"');
});

it("selects the compact scene profile at the mobile breakpoint", () => {
  expect(readLandingSource("scene-quality.ts")).toContain(
    'matchMedia?.("(max-width: 767px)")',
  );
});
