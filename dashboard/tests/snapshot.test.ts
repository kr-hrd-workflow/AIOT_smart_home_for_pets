// @vitest-environment node

import { afterEach, beforeEach, describe, expect, it } from "vitest";

import { vi } from "vitest";
vi.mock("cloudflare:workers", () => ({ env: {} }));

import { PetCareRepository } from "../lib/petcare/repository";
import { FakeD1 } from "./helpers/petcare-fakes";
import { readFileSync } from "node:fs";
import { resolve } from "node:path";

const now = "2026-07-27T00:00:00.000Z";
let fake: FakeD1;
let db: D1Database;
let repository: PetCareRepository;

async function run(sql: string, ...values: unknown[]) {
  await db.prepare(sql).bind(...values).run();
}

async function seed(suffix: "a" | "b") {
  const homeId = `home-${suffix}`;
  const agentId = `agent-${suffix}`;
  const cameraId = `camera-${suffix}`;
  await run("INSERT INTO homes (id, owner_sub, created_at) VALUES (?, ?, ?)", homeId, `owner-${suffix}`, now);
  await run(
    "INSERT INTO agents (id, home_id, public_key, tunnel_origin, connection_mode) VALUES (?, ?, ?, NULL, 'outbound')",
    agentId,
    homeId,
    `public-${suffix}`,
  );
  await run(
    "INSERT INTO cameras (id, home_id, agent_id, local_camera_id, created_at) VALUES (?, ?, ?, ?, ?)",
    cameraId,
    homeId,
    agentId,
    "pc-webcam-01",
    now,
  );
  return { agentId, cameraId };
}

beforeEach(async () => {
  fake = new FakeD1();
  db = fake as unknown as D1Database;
  await db.exec(
    readFileSync(resolve(import.meta.dirname, "../drizzle/0003_petcare_outbound.sql"), "utf8")
      .replaceAll("--> statement-breakpoint", ""),
  );
  repository = new PetCareRepository(db);
  await seed("a");
  await seed("b");
});

afterEach(() => fake.dispose());

describe("latest dashboard snapshots", () => {
  it("replaces only the uploading agent's single owner-scoped snapshot", async () => {
    const putAgentSnapshot = (repository as unknown as {
      putAgentSnapshot: (agentId: string, body: string, generatedAt: string, receivedAt: string) => Promise<void>;
    }).putAgentSnapshot;
    expect(putAgentSnapshot).toBeTypeOf("function");

    await putAgentSnapshot.call(repository, "agent-a", '{"version":1}', "2026-07-27T00:00:00.000Z", now);
    await putAgentSnapshot.call(repository, "agent-a", '{"version":2}', "2026-07-27T00:00:01.000Z", "2026-07-27T00:00:02.000Z");
    await putAgentSnapshot.call(repository, "agent-b", '{"version":3}', "2026-07-27T00:00:03.000Z", "2026-07-27T00:00:04.000Z");

    expect((await db.prepare("SELECT home_id, agent_id, body, received_at FROM agent_snapshots ORDER BY home_id").all()).results).toEqual([
      { home_id: "home-a", agent_id: "agent-a", body: '{"version":2}', received_at: "2026-07-27T00:00:02.000Z" },
      { home_id: "home-b", agent_id: "agent-b", body: '{"version":3}', received_at: "2026-07-27T00:00:04.000Z" },
    ]);
    expect(await db.prepare("SELECT last_seen_at FROM agents WHERE id = ?").bind("agent-a").first()).toEqual({
      last_seen_at: "2026-07-27T00:00:02.000Z",
    });

    const getOwnerSnapshot = (repository as unknown as {
      getOwnerSnapshot: (ownerSub: string) => Promise<unknown>;
    }).getOwnerSnapshot;
    expect(await getOwnerSnapshot.call(repository, "owner-a")).toEqual({
      agentId: "agent-a",
      cameraId: "camera-a",
      body: '{"version":2}',
      receivedAt: "2026-07-27T00:00:02.000Z",
    });
    expect(await getOwnerSnapshot.call(repository, "owner-b")).toMatchObject({ agentId: "agent-b" });
  });
});
