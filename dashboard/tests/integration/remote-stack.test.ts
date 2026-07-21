// @vitest-environment node

import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { describe, expect, it } from "vitest";

describe("integrated PetCare Worker", () => {
  const workerSource = readFileSync(resolve("worker/index.ts"), "utf8");
  const hosting = JSON.parse(
    readFileSync(resolve(".openai/hosting.json"), "utf8"),
  );

  it("declares only the shared D1/R2 binding names", () => {
    expect(hosting).toEqual({ d1: "DB", r2: "CLIPS" });
  });

  it("orders image handling, the PetCare router, then Vinext", () => {
    const image = workerSource.indexOf('url.pathname === "/_vinext/image"');
    const petcare = workerSource.indexOf("routePetCare(request, env, ctx)");
    const vinext = workerSource.indexOf("handler.fetch(request, env, ctx)");
    expect(image).toBeGreaterThan(-1);
    expect(petcare).toBeGreaterThan(image);
    expect(vinext).toBeGreaterThan(petcare);
    expect(workerSource.match(/routePetCare\(request, env, ctx\)/g)).toHaveLength(
      1,
    );
  });

  it("runs the sibling reconciliation job once per scheduled event", () => {
    expect(
      workerSource.match(
        /reconcilePetCare\(env, new Date\(controller\.scheduledTime\)\)/g,
      ),
    ).toHaveLength(1);
  });
});
