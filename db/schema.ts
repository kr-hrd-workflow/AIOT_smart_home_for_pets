import { sql } from "drizzle-orm";
import {
  index,
  integer,
  primaryKey,
  sqliteTable,
  text,
  uniqueIndex,
} from "drizzle-orm/sqlite-core";

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

export const tunnelRoutes = sqliteTable("tunnel_routes", {
  homeId: text("home_id")
    .primaryKey()
    .references(() => homes.id),
  agentId: text("agent_id").notNull(),
  tunnelId: text("tunnel_id"),
  tunnelOrigin: text("tunnel_origin"),
  accessAppId: text("access_app_id"),
  accessPolicyId: text("access_policy_id"),
  accessAud: text("access_aud"),
  dnsRecordId: text("dns_record_id"),
  activationExpiresAt: text("activation_expires_at"),
  status: text("status", {
    enum: [
      "provisioning",
      "activation_pending",
      "active",
      "cleanup_pending",
      "revocation_pending",
      "revoked",
    ],
  }).notNull(),
  createdAt: text("created_at").notNull(),
  updatedAt: text("updated_at").notNull(),
  lastError: text("last_error"),
});

export const clips = sqliteTable(
  "clips",
  {
    id: text("id").primaryKey(),
    homeId: text("home_id")
      .notNull()
      .references(() => homes.id),
    cameraId: text("camera_id").notNull(),
    objectKey: text("object_key").notNull().unique(),
    sha256: text("sha256").notNull(),
    sizeBytes: integer("size_bytes").notNull(),
    startedAt: text("started_at").notNull(),
    endedAt: text("ended_at").notNull(),
    expiresAt: text("expires_at").notNull(),
    createdAt: text("created_at").notNull(),
  },
  (table) => [
    index("clips_home_expires_idx").on(table.homeId, table.expiresAt),
  ],
);

export const clipEvents = sqliteTable(
  "clip_events",
  {
    clipId: text("clip_id")
      .notNull()
      .references(() => clips.id, { onDelete: "cascade" }),
    eventType: text("event_type", {
      enum: ["eating", "resting", "bed_sensor_mismatch"],
    }).notNull(),
    eventId: text("event_id").notNull(),
  },
  (table) => [
    primaryKey({ columns: [table.clipId, table.eventType, table.eventId] }),
  ],
);

export const uploadNonces = sqliteTable(
  "upload_nonces",
  {
    agentId: text("agent_id").notNull(),
    nonce: text("nonce").notNull(),
    usedAt: text("used_at").notNull(),
    expiresAt: text("expires_at").notNull(),
  },
  (table) => [
    primaryKey({ columns: [table.agentId, table.nonce] }),
    index("upload_nonces_expires_idx").on(table.expiresAt),
  ],
);

export const objectDeletionJobs = sqliteTable(
  "object_deletion_jobs",
  {
    objectKey: text("object_key").primaryKey(),
    homeId: text("home_id")
      .notNull()
      .references(() => homes.id),
    requestedAt: text("requested_at").notNull(),
    lastError: text("last_error"),
  },
  (table) => [index("object_deletion_jobs_home_idx").on(table.homeId)],
);

export const requestLimits = sqliteTable(
  "request_limits",
  {
    subject: text("subject").notNull(),
    route: text("route").notNull(),
    windowStart: integer("window_start").notNull(),
    count: integer("count").notNull(),
    expiresAt: text("expires_at").notNull(),
  },
  (table) => [
    primaryKey({ columns: [table.subject, table.route, table.windowStart] }),
    index("request_limits_expires_idx").on(table.expiresAt),
  ],
);

export const tenantCleanup = sqliteTable("tenant_cleanup", {
  ownerSub: text("owner_sub").primaryKey(),
  homeId: text("home_id").notNull().unique(),
  status: text("status", { enum: ["cleanup_pending"] }).notNull(),
  startedAt: text("started_at").notNull(),
  updatedAt: text("updated_at").notNull(),
  lastError: text("last_error"),
});

export const reconcileState = sqliteTable("reconcile_state", {
  name: text("name").primaryKey(),
  cursor: text("cursor"),
  updatedAt: text("updated_at").notNull(),
});
