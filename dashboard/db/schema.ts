import { sql } from "drizzle-orm";
import { index, sqliteTable, text, uniqueIndex } from "drizzle-orm/sqlite-core";

export const homes = sqliteTable(
  "homes",
  {
    id: text("id").primaryKey(),
    ownerSub: text("owner_sub").notNull(),
    createdAt: text("created_at").notNull().default(sql`CURRENT_TIMESTAMP`),
    deletedAt: text("deleted_at"),
  },
  (table) => [
    uniqueIndex("homes_one_active_owner")
      .on(table.ownerSub)
      .where(sql`${table.deletedAt} IS NULL`),
  ],
);

export const agents = sqliteTable(
  "agents",
  {
    id: text("id").primaryKey(),
    homeId: text("home_id")
      .notNull()
      .references(() => homes.id, { onDelete: "restrict" }),
    publicKey: text("public_key").notNull(),
    tunnelOrigin: text("tunnel_origin").notNull(),
    lastSeenAt: text("last_seen_at"),
    revokedAt: text("revoked_at"),
  },
  (table) => [
    uniqueIndex("agents_one_active_home")
      .on(table.homeId)
      .where(sql`${table.revokedAt} IS NULL`),
    index("agents_home_idx").on(table.homeId),
  ],
);

export const cameras = sqliteTable(
  "cameras",
  {
    id: text("id").primaryKey(),
    homeId: text("home_id")
      .notNull()
      .references(() => homes.id, { onDelete: "restrict" }),
    agentId: text("agent_id")
      .notNull()
      .references(() => agents.id, { onDelete: "restrict" }),
    localCameraId: text("local_camera_id").notNull(),
    createdAt: text("created_at").notNull().default(sql`CURRENT_TIMESTAMP`),
    disabledAt: text("disabled_at"),
  },
  (table) => [
    uniqueIndex("cameras_one_active_home")
      .on(table.homeId)
      .where(sql`${table.disabledAt} IS NULL`),
    index("cameras_agent_idx").on(table.agentId),
  ],
);

export const enrollmentTokens = sqliteTable(
  "enrollment_tokens",
  {
    id: text("id").primaryKey(),
    homeId: text("home_id")
      .notNull()
      .references(() => homes.id, { onDelete: "cascade" }),
    tokenHash: text("token_hash").notNull().unique(),
    expiresAt: text("expires_at").notNull(),
    consumedAt: text("consumed_at"),
  },
  (table) => [index("enrollment_tokens_home_idx").on(table.homeId)],
);
