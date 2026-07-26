// @vitest-environment node

import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { Miniflare } from "miniflare";
import { afterEach, beforeEach, expect, it } from "vitest";

import { miniflarePort } from "../helpers/miniflare";

let mf: Miniflare;
let db: D1Database;

async function applyMigration(name: string) {
  const migration = readFileSync(
    resolve(import.meta.dirname, `../../drizzle/${name}`),
    "utf8",
  );
  await db.batch(
    migration
      .split("--> statement-breakpoint")
      .map((statement) => statement.trim())
      .filter(Boolean)
      .map((statement) => db.prepare(statement)),
  );
}

beforeEach(async () => {
  mf = new Miniflare({
    modules: true,
    port: miniflarePort(0),
    script: "export default { fetch() { return new Response('ok') } }",
    d1Databases: ["DB"],
  });
  db = await mf.getD1Database("DB");
  for (const migration of [
    "0000_petcare_tenancy.sql",
    "0001_petcare_tunnels_clips.sql",
    "0002_activity_cleanup_commands.sql",
  ]) {
    await applyMigration(migration);
  }
});

afterEach(async () => mf.dispose());

it("migrates legacy tunnel agents and accepts outbound ownership state", async () => {
  await db.batch([
    db
      .prepare("INSERT INTO homes (id, owner_sub, created_at) VALUES (?, ?, ?)")
      .bind("home-legacy", "owner-legacy", "2026-07-27T00:00:00.000Z"),
    db
      .prepare(
        "INSERT INTO agents (id, home_id, public_key, tunnel_origin) VALUES (?, ?, ?, ?)",
      )
      .bind("agent-legacy", "home-legacy", "legacy-key", "https://legacy.invalid"),
    db
      .prepare(
        "INSERT INTO cameras (id, home_id, agent_id, local_camera_id, created_at) VALUES (?, ?, ?, ?, ?)",
      )
      .bind(
        "camera-legacy",
        "home-legacy",
        "agent-legacy",
        "pc-webcam-01",
        "2026-07-27T00:00:00.000Z",
      ),
  ]);

  await applyMigration("0003_petcare_outbound.sql");

  await expect(
    db
      .prepare(
        "INSERT INTO agents (id, home_id, public_key, tunnel_origin, connection_mode) VALUES (?, ?, ?, ?, ?)",
      )
      .bind("agent-outbound", "home-legacy", "outbound-key", null, "outbound")
      .run(),
  ).rejects.toThrow(/UNIQUE/);
  expect(
    await db
      .prepare("SELECT tunnel_origin, connection_mode FROM agents WHERE id = ?")
      .bind("agent-legacy")
      .first(),
  ).toEqual({ tunnel_origin: "https://legacy.invalid", connection_mode: "tunnel" });

  await db
    .prepare("UPDATE agents SET revoked_at = ? WHERE id = ?")
    .bind("2026-07-27T00:01:00.000Z", "agent-legacy")
    .run();
  await db
    .prepare("UPDATE cameras SET disabled_at = ? WHERE id = ?")
    .bind("2026-07-27T00:01:00.000Z", "camera-legacy")
    .run();
  await db
    .prepare(
      "INSERT INTO agents (id, home_id, public_key, tunnel_origin, connection_mode) VALUES (?, ?, ?, ?, ?)",
    )
    .bind("agent-outbound", "home-legacy", "outbound-key", null, "outbound")
    .run();
  await db
    .prepare(
      "INSERT INTO cameras (id, home_id, agent_id, local_camera_id, created_at) VALUES (?, ?, ?, ?, ?)",
    )
    .bind(
      "camera-outbound",
      "home-legacy",
      "agent-outbound",
      "pc-webcam-01",
      "2026-07-27T00:01:00.000Z",
    )
    .run();
  await db.batch([
    db
      .prepare(
        "INSERT INTO agent_snapshots (home_id, agent_id, body, generated_at, received_at) VALUES (?, ?, ?, ?, ?)",
      )
      .bind(
        "home-legacy",
        "agent-outbound",
        "{}",
        "2026-07-27T00:01:00.000Z",
        "2026-07-27T00:01:00.000Z",
      ),
    db
      .prepare(
        "INSERT INTO live_streams (home_id, agent_id, camera_id, boot_id, init_object_key, newest_sequence, updated_at, expires_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
      )
      .bind(
        "home-legacy",
        "agent-outbound",
        "camera-outbound",
        "boot-1",
        "live/home-legacy/camera-outbound/boot-1/init.mp4",
        0,
        "2026-07-27T00:01:00.000Z",
        "2026-07-27T00:01:08.000Z",
      ),
    db
      .prepare(
        "INSERT INTO live_parts (home_id, boot_id, sequence, object_key, sha256, size_bytes, started_at, duration_ms, created_at, expires_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
      )
      .bind(
        "home-legacy",
        "boot-1",
        0,
        "live/home-legacy/camera-outbound/boot-1/0.m4s",
        "digest",
        1,
        "2026-07-27T00:01:00.000Z",
        1000,
        "2026-07-27T00:01:00.000Z",
        "2026-07-27T00:01:08.000Z",
      ),
  ]);
  await expect(
    db
      .prepare(
        "INSERT INTO live_parts (home_id, boot_id, sequence, object_key, sha256, size_bytes, started_at, duration_ms, created_at, expires_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
      )
      .bind(
        "home-legacy",
        "boot-1",
        1,
        "live/home-legacy/camera-outbound/boot-1/1.m4s",
        "digest",
        -1,
        "2026-07-27T00:01:01.000Z",
        999,
        "2026-07-27T00:01:01.000Z",
        "2026-07-27T00:01:09.000Z",
      )
      .run(),
  ).rejects.toThrow(/CHECK/);
});
