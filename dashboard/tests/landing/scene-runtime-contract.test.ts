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

it("loads the scroll world lazily without pulling R3F into the public route", () => {
  const experience = readLandingSource("pet-home-experience.tsx");
  const canvas = readLandingSource("pet-home-canvas.tsx");

  expect(experience).toContain('lazy(() => import("./pet-home-canvas")');
  expect(experience).not.toContain("@react-three/fiber");
  expect(canvas).toContain("mountScrollWorld");
  expect(canvas).not.toContain("@react-three/fiber");
  expect(canvas).not.toContain("<Canvas");
  expect(readLandingSource("pet-home-scene.tsx")).toBe("");
});

it("uses the photorealistic apartment plate instead of low-poly room geometry", () => {
  const config = readLandingSource("scroll-world-config.ts");
  const fallback = readLandingSource("landing-fallback.tsx");

  expect(config).not.toContain("low-poly");
  expect(config).not.toContain("boxGeometry");
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

it("scrubs one continuous photoreal journey instead of stitching scene fragments", () => {
  const canvas = readLandingSource("pet-home-canvas.tsx");
  const config = readLandingSource("scroll-world-config.ts");
  const overlay = readLandingSource("landing-overlay.tsx");
  const globals = readFileSync(
    new URL("../../app/globals.css", import.meta.url),
    "utf8",
  );
  const clip = new URL(
    "../../public/landing/scroll-world/desktop/scene-01-arrival.mp4",
    import.meta.url,
  );
  const poster = new URL(
    "../../public/landing/scroll-world/source/scene-01-arrival.png",
    import.meta.url,
  );

  expect(canvas).toContain("SCROLL_WORLD_CONFIG");
  expect(config.match(/id: "journey"/g)).toHaveLength(1);
  expect(config).toContain("connectors: []");
  expect(config).toContain(
    "/landing/scroll-world/desktop/scene-01-arrival.mp4",
  );
  expect(config).toContain(
    "/landing/scroll-world/source/scene-01-arrival.png",
  );
  expect(existsSync(clip)).toBe(true);
  expect(existsSync(poster)).toBe(true);
  expect(canvas).not.toContain("autoPlay");
  expect(canvas).not.toContain("loop");
  expect(globals).not.toContain("animation: landing-cinematic-drift");
  expect(overlay).toContain('className="landing-copy-track"');
  expect(globals).toContain(
    '.landing-page[data-scroll-world-active="true"] .landing-copy-track',
  );
  expect(globals).toContain('[data-landing-scene="hero"]');
  expect(globals).toContain("--landing-copy-hero-opacity");
  expect(globals).toMatch(
    /\.landing-page\[data-scroll-world-active="true"\] \.landing-header\s*\{[^}]*left:\s*0;[^}]*right:\s*0;/s,
  );
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

it("keeps entry and chapter motion without an idle scene drift", () => {
  const styles = readFileSync(new URL("../../app/globals.css", import.meta.url), "utf8");

  expect(styles).not.toContain("@keyframes landing-cinematic-drift");
  expect(styles).toContain("animation-timeline: view()");
  expect(styles).toMatch(
    /@media \(prefers-reduced-motion: reduce\)[\s\S]*\.pet-home-experience[\s\S]*animation:\s*none !important;/,
  );
});

it("keeps mobile chapter copy cinematic instead of covering the footage with a card", () => {
  const styles = readFileSync(new URL("../../app/globals.css", import.meta.url), "utf8");
  const reducedMotionStart = styles.indexOf(
    "@media (prefers-reduced-motion: reduce)",
  );
  const mobileStyles = styles.slice(
    styles.lastIndexOf("@media (max-width: 600px)", reducedMotionStart),
    reducedMotionStart,
  );

  expect(mobileStyles).toMatch(
    /\.landing-chapter,\s*\.landing-chapter:nth-child\(even\)\s*\{[^}]*align-items:\s*flex-end;/s,
  );
  expect(mobileStyles).toMatch(
    /\.landing-chapter-copy,[\s\S]*?\.landing-chapter:last-child \.landing-chapter-copy\s*\{[^}]*background:\s*transparent;/s,
  );
  expect(mobileStyles).not.toContain("background: var(--landing-surface);");
});
