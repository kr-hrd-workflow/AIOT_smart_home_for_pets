// @vitest-environment node

import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { describe, expect, it } from "vitest";

describe("integrated PetCare Worker", () => {
  const workerSource = readFileSync(resolve("worker/index.ts"), "utf8");
  const hosting = JSON.parse(
    readFileSync(resolve(".openai/hosting.json"), "utf8"),
  );

  it("declares the shared bindings and persisted Sites project", () => {
    expect(Object.keys(hosting).sort()).toEqual(["d1", "project_id", "r2"]);
    expect(hosting).toMatchObject({ d1: "DB", r2: "CLIPS" });
    expect(hosting.project_id).toEqual(expect.any(String));
    expect(hosting.project_id).not.toHaveLength(0);
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
