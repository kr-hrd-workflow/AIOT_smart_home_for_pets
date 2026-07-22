// @vitest-environment node

import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { Miniflare } from "miniflare";
import { afterEach, beforeEach, describe, expect, it } from "vitest";

import { miniflarePort } from "../helpers/miniflare";

let mf: Miniflare;
let db: D1Database;

beforeEach(async () => {
  mf = new Miniflare({
    modules: true,
    port: miniflarePort(0),
    script: "export default { fetch() { return new Response('ok') } }",
    d1Databases: ["DB"],
  });
  db = await mf.getD1Database("DB");
  const migration = readFileSync(
    resolve(import.meta.dirname, "../../drizzle/0000_petcare_tenancy.sql"),
    "utf8",
  );
  await db.batch(
    migration
      .split("--> statement-breakpoint")
      .map((statement) => statement.trim())
      .filter(Boolean)
      .map((statement) => db.prepare(statement)),
  );
});

afterEach(async () => mf.dispose());

describe("petcare tenancy schema", () => {
  it("allows only one active home per owner", async () => {
    await db
      .prepare("INSERT INTO homes (id, owner_sub, created_at) VALUES (?, ?, ?)")
      .bind("home-a", "owner-a", "2026-07-20T00:00:00.000Z")
      .run();

    await expect(
      db
        .prepare("INSERT INTO homes (id, owner_sub, created_at) VALUES (?, ?, ?)")
        .bind("home-b", "owner-a", "2026-07-20T00:00:01.000Z")
        .run(),
    ).rejects.toThrow(/UNIQUE/);
  });

  it("allows only one active agent and camera per home", async () => {
    await db.batch([
      db
        .prepare("INSERT INTO homes (id, owner_sub, created_at) VALUES (?, ?, ?)")
        .bind("home-a", "owner-a", "2026-07-20T00:00:00.000Z"),
      db
        .prepare(
          "INSERT INTO agents (id, home_id, public_key, tunnel_origin) VALUES (?, ?, ?, ?)",
        )
        .bind("agent-a", "home-a", "key-a", "https://a.invalid"),
      db
        .prepare(
          "INSERT INTO cameras (id, home_id, agent_id, local_camera_id, created_at) VALUES (?, ?, ?, ?, ?)",
        )
        .bind("camera-a", "home-a", "agent-a", "usb-0", "2026-07-20T00:00:00.000Z"),
    ]);

    await expect(
      db
        .prepare(
          "INSERT INTO agents (id, home_id, public_key, tunnel_origin) VALUES (?, ?, ?, ?)",
        )
        .bind("agent-b", "home-a", "key-b", "https://b.invalid")
        .run(),
    ).rejects.toThrow(/UNIQUE/);
    await expect(
      db
        .prepare(
          "INSERT INTO cameras (id, home_id, agent_id, local_camera_id, created_at) VALUES (?, ?, ?, ?, ?)",
        )
        .bind("camera-b", "home-a", "agent-a", "usb-1", "2026-07-20T00:00:01.000Z")
        .run(),
    ).rejects.toThrow(/UNIQUE/);
  });
});
