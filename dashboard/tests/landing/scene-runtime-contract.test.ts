// @vitest-environment node

import { existsSync, readFileSync } from "node:fs";
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

it("uses the photorealistic apartment plate instead of low-poly room geometry", () => {
  const scene = readLandingSource("pet-home-scene.tsx");
  const fallback = readLandingSource("landing-fallback.tsx");

  expect(scene).toContain("/landing-apartment-photoreal-v3.webp");
  expect(scene).toContain("/landing-apartment-photoreal-mobile-v2.webp");
  expect(scene).toContain("useTexture");
  expect(scene).not.toContain("<boxGeometry");
  expect(fallback).toContain("/landing-apartment-photoreal-v3.webp");
  expect(fallback).toContain("/landing-apartment-photoreal-mobile-v2.webp");
  expect(
    existsSync(new URL("../../public/landing-apartment-photoreal-v3.webp", import.meta.url)),
  ).toBe(true);
  expect(
    existsSync(
      new URL(
        "../../public/landing-apartment-photoreal-mobile-v2.webp",
        import.meta.url,
      ),
    ),
  ).toBe(true);
});

it("selects the compact scene profile at the mobile breakpoint", () => {
  expect(readLandingSource("scene-quality.ts")).toContain(
    'matchMedia?.("(max-width: 767px)")',
  );
});

it("keeps Korean landing copy on word boundaries", () => {
  const styles = readFileSync(new URL("../../app/globals.css", import.meta.url), "utf8");

  expect(styles).toMatch(
    /\.landing-hero h1,\s*\.landing-chapter h2,\s*\.landing-final h2,\s*\.landing-lede,\s*\.landing-chapter-copy > p:last-child\s*\{[^}]*word-break: keep-all;/s,
  );
});

it("adds cinematic entry and scroll motion with a reduced-motion stop", () => {
  const styles = readFileSync(new URL("../../app/globals.css", import.meta.url), "utf8");

  expect(styles).toContain("@keyframes landing-cinematic-drift");
  expect(styles).toMatch(
    /\.pet-home-experience\s*\{[^}]*animation:\s*landing-cinematic-drift/s,
  );
  expect(styles).toContain("animation-timeline: view()");
  expect(styles).toMatch(
    /@media \(prefers-reduced-motion: reduce\)[\s\S]*\.pet-home-experience[\s\S]*animation:\s*none !important;/,
  );
});
