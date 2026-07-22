// @vitest-environment node

import { existsSync, readFileSync } from "node:fs";
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
  const packageJson = JSON.parse(readFileSync(resolve("package.json"), "utf8")) as {
    devDependencies: Record<string, string>;
  };
  const source = readFileSync(resolve("remotion/petcare-promo.tsx"), "utf8");
  expect(packageJson.devDependencies["@remotion/fonts"]).toBe("4.0.496");
  expect(source).toContain("ThreeCanvas");
  expect(source).toContain("useCurrentFrame");
  expect(source).toContain("loadFont");
  expect(source).not.toContain("await loadFont");
  expect(source).toContain('staticFile("og.png")');
  expect(source).toContain("<Img");
  expect(source).toContain('staticFile("fonts/Pretendard-Bold.woff2")');
  expect(source).toContain('wordBreak: "keep-all"');
  expect(source).toContain("premountFor={PETCARE_PROMO.fps}");
  expect(source).toContain("SensorAccents");
  expect(source).not.toContain("KoreanApartment");
  expect(source).not.toMatch(/useFrame|animation:|transition:/);

  expect(existsSync(resolve("public/fonts/Pretendard-Bold.woff2"))).toBe(true);
  expect(existsSync(resolve("public/fonts/OFL.txt"))).toBe(true);

  for (const path of [
    "app/page.tsx",
    "components/landing/landing-page.tsx",
    "proxy.ts",
  ]) {
    const routeSource = readFileSync(resolve(path), "utf8");
    expect(routeSource).not.toContain("remotion");
  }
});
