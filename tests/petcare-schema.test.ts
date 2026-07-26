import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { describe, expect, it } from "vitest";

const root = resolve(import.meta.dirname, "..");

describe("PetCare Sites storage contract", () => {
  it("ships the tunnel and clip migration after the tenancy migration", () => {
    expect(
      readFileSync(resolve(root, "drizzle/0000_petcare_tenancy.sql"), "utf8"),
    ).toContain("CREATE TABLE `homes`");
    const sql = readFileSync(
      resolve(root, "drizzle/0001_petcare_tunnels_clips.sql"),
      "utf8",
    );
    for (const table of [
      "tunnel_routes",
      "clips",
      "clip_events",
      "upload_nonces",
      "object_deletion_jobs",
      "request_limits",
      "tenant_cleanup",
      "reconcile_state",
    ]) {
      expect(sql).toContain(`CREATE TABLE \`${table}\``);
    }
    expect(sql).toContain(
      "CHECK (`status` IN ('provisioning','activation_pending','active','cleanup_pending','revocation_pending','revoked'))",
    );
    expect(sql).toContain("UNIQUE (`agent_id`, `nonce`)");
    expect(sql).toContain("ON `clips` (`home_id`, `expires_at`)");
    expect(sql).toContain(
      "CREATE TRIGGER `block_home_recreation_during_petcare_cleanup`",
    );
    expect(sql).not.toMatch(
      /connector_token|access_client_secret|api_token/i,
    );
  });

  it("ships a command ledger for the outbound local activity cleanup", () => {
    const sql = readFileSync(
      resolve(root, "drizzle/0002_activity_cleanup_commands.sql"),
      "utf8",
    );
    expect(sql).toContain("CREATE TABLE `activity_cleanup_commands`");
    expect(sql).toContain("'delete_activity_observations'");
    expect(sql).toContain("'pending','acknowledged'");
    expect(sql).toContain("UNIQUE INDEX `activity_cleanup_commands_home_id_unique`");
    expect(sql).not.toMatch(/private_key|connector_token|access_client_secret|api_token/i);
  });
});
