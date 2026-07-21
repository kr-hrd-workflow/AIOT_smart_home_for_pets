import { PetCareError } from "./errors";

export type ActiveRoute = {
  homeId: string;
  agentId: string;
  cameraId: string;
  tunnelOrigin: string;
  publicKey: string;
  lastSeenAt: string | null;
};

export type ClipEvent = {
  eventType: "eating" | "resting" | "bed_sensor_mismatch";
  eventId: string;
};

export type OwnedClip = {
  id: string;
  homeId: string;
  cameraId: string;
  objectKey: string;
  startedAt: string;
  endedAt: string;
  createdAt: string;
  expiresAt: string;
  events: ClipEvent[];
};

export type PublishClipInput = {
  id: string;
  homeId: string;
  agentId: string;
  cameraId: string;
  objectKey: string;
  sha256: string;
  sizeBytes: number;
  startedAt: string;
  endedAt: string;
  createdAt: string;
  expiresAt: string;
  events: ClipEvent[];
};

export type ExactClipInput = Omit<PublishClipInput, "createdAt" | "expiresAt">;

export type ClipReceipt = {
  id: string;
  createdAt: string;
  expiresAt: string;
};

export type TunnelStatus =
  | "provisioning"
  | "activation_pending"
  | "active"
  | "cleanup_pending"
  | "revocation_pending"
  | "revoked";

export type ResourceLedger = {
  tunnelId?: string | null;
  tunnelOrigin?: string | null;
  dnsRecordId?: string | null;
  accessAppId?: string | null;
  accessAud?: string | null;
  accessPolicyId?: string | null;
};

export type TunnelRouteRecord = Required<ResourceLedger> & {
  homeId: string;
  agentId: string;
  status: TunnelStatus;
  activationExpiresAt: string | null;
  leaseId: string | null;
  leaseExpiresAt: string | null;
  bound: boolean;
  createdAt: string;
  updatedAt: string;
};

export type TunnelErrorCode =
  | "provisioning_failed"
  | "remote_delete_failed"
  | "resource_state_write_failed"
  | "remote_cleanup_failed"
  | "activation_expired";

export type ObjectDeletionJob = {
  homeId: string;
  objectKey: string;
  requestedAt: string;
};

export type ClipObject = { id: string; objectKey: string };

export type TenantCleanupRecord = {
  ownerSub: string;
  homeId: string;
  status: "cleanup_pending";
};

type RouteRow = {
  home_id: string;
  agent_id: string;
  camera_id: string;
  tunnel_origin: string;
  public_key: string;
  last_seen_at: string | null;
};

type TunnelRow = {
  home_id: string;
  agent_id: string;
  tunnel_id: string | null;
  tunnel_origin: string | null;
  dns_record_id: string | null;
  access_app_id: string | null;
  access_aud: string | null;
  access_policy_id: string | null;
  activation_expires_at: string | null;
  lease_id: string | null;
  lease_expires_at: string | null;
  bound?: number;
  status: TunnelStatus;
  created_at: string;
  updated_at: string;
};

type ClipRow = {
  id: string;
  home_id: string;
  camera_id: string;
  object_key: string;
  started_at: string;
  ended_at: string;
  created_at: string;
  expires_at: string;
  event_type: ClipEvent["eventType"] | null;
  event_id: string | null;
};

const activeRouteSql = `
  SELECT tr.home_id, tr.agent_id, tr.tunnel_origin, a.public_key,
         a.last_seen_at, c.id AS camera_id
  FROM tunnel_routes tr
  JOIN homes h ON h.id = tr.home_id AND h.deleted_at IS NULL
  JOIN agents a ON a.id = tr.agent_id AND a.home_id = tr.home_id
  JOIN cameras c ON c.agent_id = a.id AND c.home_id = tr.home_id
  LEFT JOIN tenant_cleanup tc ON tc.home_id = tr.home_id
  WHERE tr.home_id = ? AND tr.status = 'active'
    AND tc.home_id IS NULL
    AND a.revoked_at IS NULL AND c.disabled_at IS NULL
  LIMIT 1
`;

const activationRouteSql = activeRouteSql.replace(
  "tr.status = 'active'",
  `(tr.status = 'active' OR (
    tr.status = 'activation_pending' AND tr.activation_expires_at > ?
  ))`,
);

function route(row: RouteRow): ActiveRoute {
  return {
    homeId: row.home_id,
    agentId: row.agent_id,
    cameraId: row.camera_id,
    tunnelOrigin: row.tunnel_origin,
    publicKey: row.public_key,
    lastSeenAt: row.last_seen_at,
  };
}

function validOrigin(value: string): boolean {
  try {
    const url = new URL(value);
    return (
      url.protocol === "https:" &&
      url.username === "" &&
      url.password === "" &&
      url.pathname === "/" &&
      url.search === "" &&
      url.hash === ""
    );
  } catch {
    return false;
  }
}

function validLeaseWindow(now: string, leaseExpiresAt: string): boolean {
  const duration = Date.parse(leaseExpiresAt) - Date.parse(now);
  return Number.isFinite(duration) && duration >= 60_000 && duration <= 120_000;
}

function tunnel(row: TunnelRow): TunnelRouteRecord {
  return {
    homeId: row.home_id,
    agentId: row.agent_id,
    tunnelId: row.tunnel_id,
    tunnelOrigin: row.tunnel_origin,
    dnsRecordId: row.dns_record_id,
    accessAppId: row.access_app_id,
    accessAud: row.access_aud,
    accessPolicyId: row.access_policy_id,
    activationExpiresAt: row.activation_expires_at,
    leaseId: row.lease_id,
    leaseExpiresAt: row.lease_expires_at,
    bound: row.bound === 1,
    status: row.status,
    createdAt: row.created_at,
    updatedAt: row.updated_at,
  };
}

function clips(rows: ClipRow[]): OwnedClip[] {
  const byId = new Map<string, OwnedClip>();
  for (const row of rows) {
    let item = byId.get(row.id);
    if (!item) {
      item = {
        id: row.id,
        homeId: row.home_id,
        cameraId: row.camera_id,
        objectKey: row.object_key,
        startedAt: row.started_at,
        endedAt: row.ended_at,
        createdAt: row.created_at,
        expiresAt: row.expires_at,
        events: [],
      };
      byId.set(row.id, item);
    }
    if (row.event_type && row.event_id) {
      item.events.push({ eventType: row.event_type, eventId: row.event_id });
    }
  }
  return [...byId.values()];
}

function isConstraint(error: unknown): boolean {
  return error instanceof Error && /constraint|unique/i.test(error.message);
}

export class PetCareRepository {
  constructor(private readonly db: D1Database) {}

  async findEnrollmentHome(codeHash: string, now: string): Promise<{ homeId: string }> {
    const row = await this.db
      .prepare(`
        SELECT et.home_id
        FROM enrollment_tokens et
        JOIN homes h ON h.id = et.home_id AND h.deleted_at IS NULL
        LEFT JOIN tenant_cleanup tc ON tc.home_id = h.id
        WHERE et.token_hash = ? AND et.consumed_at IS NULL AND et.expires_at > ?
          AND tc.home_id IS NULL
        LIMIT 1
      `)
      .bind(codeHash, now)
      .first<{ home_id: string }>();
    if (!row) throw new PetCareError(409, "enrollment_rejected");
    return { homeId: row.home_id };
  }

  async getHomeConnection(
    homeId: string,
  ): Promise<
    | { state: "needs_enrollment" }
    | { state: "ready"; route: ActiveRoute; revoked: boolean }
  > {
    const row = await this.db
      .prepare(`
        SELECT tr.home_id, tr.agent_id, tr.tunnel_origin, tr.status,
               CASE WHEN tr.status = 'active' OR (
                 tr.status = 'activation_pending' AND
                 tr.activation_expires_at > strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
               ) THEN 1 ELSE 0 END AS route_eligible,
               a.public_key, a.last_seen_at, a.revoked_at,
               c.id AS camera_id, c.disabled_at,
               h.deleted_at, tc.home_id AS cleanup_home_id
        FROM tunnel_routes tr
        JOIN homes h ON h.id = tr.home_id
        JOIN agents a ON a.id = tr.agent_id AND a.home_id = tr.home_id
        JOIN cameras c ON c.agent_id = a.id AND c.home_id = tr.home_id
        LEFT JOIN tenant_cleanup tc ON tc.home_id = tr.home_id
        WHERE tr.home_id = ?
        LIMIT 1
      `)
      .bind(homeId)
      .first<RouteRow & {
        status: TunnelStatus;
        route_eligible: number;
        revoked_at: string | null;
        disabled_at: string | null;
        deleted_at: string | null;
        cleanup_home_id: string | null;
      }>();
    if (!row) return { state: "needs_enrollment" };
    return {
      state: "ready",
      route: route(row),
      revoked:
        row.route_eligible !== 1 ||
        row.revoked_at !== null ||
        row.disabled_at !== null ||
        row.deleted_at !== null ||
        row.cleanup_home_id !== null,
    };
  }

  async requireActiveRoute(homeId: string): Promise<ActiveRoute> {
    const row = await this.db.prepare(activeRouteSql).bind(homeId).first<RouteRow>();
    if (!row || !validOrigin(row.tunnel_origin)) {
      throw new PetCareError(503, "agent_offline");
    }
    return route(row);
  }

  async requireActivationRoute(homeId: string, now: string): Promise<ActiveRoute> {
    const row = await this.db
      .prepare(activationRouteSql)
      .bind(homeId, now)
      .first<RouteRow>();
    if (!row || !validOrigin(row.tunnel_origin)) {
      throw new PetCareError(503, "agent_offline");
    }
    return route(row);
  }

  async requireActiveAgent(agentId: string, cameraId: string): Promise<ActiveRoute> {
    const row = await this.db
      .prepare(`
        SELECT tr.home_id, tr.agent_id, tr.tunnel_origin, a.public_key,
               a.last_seen_at, c.id AS camera_id
        FROM tunnel_routes tr
        JOIN homes h ON h.id = tr.home_id AND h.deleted_at IS NULL
        JOIN agents a ON a.id = tr.agent_id AND a.home_id = tr.home_id
        JOIN cameras c ON c.agent_id = a.id AND c.home_id = tr.home_id
        LEFT JOIN tenant_cleanup tc ON tc.home_id = tr.home_id
        WHERE tr.agent_id = ? AND c.id = ?
          AND (
            tr.status = 'active' OR
            (tr.status = 'activation_pending' AND tr.activation_expires_at > strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
          )
          AND tc.home_id IS NULL
          AND a.revoked_at IS NULL AND c.disabled_at IS NULL
        LIMIT 1
      `)
      .bind(agentId, cameraId)
      .first<RouteRow>();
    if (!row || !validOrigin(row.tunnel_origin)) {
      throw new PetCareError(503, "agent_offline");
    }
    return route(row);
  }

  async markAgentSeen(agentId: string, seenAt: string): Promise<void> {
    try {
      await this.db.batch([
        this.db
          .prepare(`
            UPDATE agents SET last_seen_at = ?
            WHERE id = ? AND revoked_at IS NULL AND EXISTS (
              SELECT 1 FROM tunnel_routes tr
              JOIN homes h ON h.id = tr.home_id AND h.deleted_at IS NULL
              JOIN cameras c ON c.home_id = tr.home_id AND c.agent_id = tr.agent_id
                AND c.disabled_at IS NULL
              LEFT JOIN tenant_cleanup tc ON tc.home_id = tr.home_id
              WHERE tr.agent_id = agents.id AND tr.home_id = agents.home_id
                AND (
                  tr.status = 'active' OR
                  (tr.status = 'activation_pending' AND tr.activation_expires_at > ?)
                )
                AND tc.home_id IS NULL
            )
          `)
          .bind(seenAt, agentId, seenAt),
        this.db
          .prepare(`
            UPDATE tunnel_routes
            SET status = 'active', activation_expires_at = NULL, updated_at = ?
            WHERE agent_id = ? AND (
              status = 'active' OR
              (status = 'activation_pending' AND activation_expires_at > ?)
            )
              AND EXISTS (
                SELECT 1 FROM homes h
                JOIN agents a ON a.home_id = h.id AND a.id = tunnel_routes.agent_id
                  AND a.revoked_at IS NULL
                JOIN cameras c ON c.home_id = h.id AND c.agent_id = a.id
                  AND c.disabled_at IS NULL
                LEFT JOIN tenant_cleanup tc ON tc.home_id = h.id
                WHERE h.id = tunnel_routes.home_id AND h.deleted_at IS NULL
                  AND tc.home_id IS NULL
              )
          `)
          .bind(seenAt, agentId, seenAt),
        this.db
          .prepare(`
            INSERT INTO tenant_cleanup (owner_sub, home_id, status, started_at, updated_at)
            SELECT NULL, NULL, NULL, NULL, NULL
            WHERE NOT EXISTS (
              SELECT 1 FROM tunnel_routes tr
              JOIN homes h ON h.id = tr.home_id AND h.deleted_at IS NULL
              JOIN agents a ON a.id = tr.agent_id AND a.home_id = h.id
                AND a.revoked_at IS NULL AND a.last_seen_at = ?
              JOIN cameras c ON c.home_id = h.id AND c.agent_id = a.id
                AND c.disabled_at IS NULL
              LEFT JOIN tenant_cleanup tc ON tc.home_id = h.id
              WHERE tr.agent_id = ? AND tr.status = 'active' AND tc.home_id IS NULL
            )
          `)
          .bind(seenAt, agentId),
      ]);
    } catch {
      throw new PetCareError(503, "agent_offline");
    }
  }

  async consumeNonce(agentId: string, nonce: string, now: string): Promise<void> {
    const expiresAt = new Date(new Date(now).getTime() + 300_000).toISOString();
    try {
      await this.db
        .prepare("INSERT INTO upload_nonces (agent_id, nonce, used_at, expires_at) VALUES (?, ?, ?, ?)")
        .bind(agentId, nonce, now, expiresAt)
        .run();
    } catch (error) {
      if (isConstraint(error)) throw new PetCareError(409, "replay");
      throw error;
    }
  }

  async checkRateLimit(
    subject: string,
    routeName: string,
    limit: number,
    windowSeconds: number,
    now: Date,
  ): Promise<void> {
    const epochSeconds = Math.floor(now.getTime() / 1000);
    const windowStart = Math.floor(epochSeconds / windowSeconds) * windowSeconds;
    const expiresAt = new Date((windowStart + windowSeconds) * 1000).toISOString();
    const row = await this.db
      .prepare(`
        INSERT INTO request_limits (subject, route, window_start, count, expires_at)
        VALUES (?, ?, ?, 1, ?)
        ON CONFLICT(subject, route, window_start)
        DO UPDATE SET count = count + 1
        RETURNING count
      `)
      .bind(subject, routeName, windowStart, expiresAt)
      .first<{ count: number }>();
    if (!row || row.count > limit) throw new PetCareError(429, "rate_limited");
  }

  async publishClip(input: PublishClipInput): Promise<void> {
    const statements: D1PreparedStatement[] = [
      this.db
        .prepare(`
          INSERT INTO clips (
            id, home_id, camera_id, object_key, sha256, size_bytes,
            started_at, ended_at, expires_at, created_at
          )
          SELECT ?, h.id, c.id, ?, ?, ?, ?, ?, ?, ?
          FROM homes h
          JOIN agents a ON a.home_id = h.id AND a.id = ? AND a.revoked_at IS NULL
          JOIN cameras c ON c.home_id = h.id AND c.agent_id = a.id
            AND c.id = ? AND c.disabled_at IS NULL
          JOIN tunnel_routes tr ON tr.home_id = h.id AND tr.agent_id = a.id
            AND (
              tr.status = 'active' OR
              (tr.status = 'activation_pending' AND tr.activation_expires_at > ?)
            )
          LEFT JOIN tenant_cleanup tc ON tc.home_id = h.id
          WHERE h.id = ? AND h.deleted_at IS NULL AND tc.home_id IS NULL
        `)
        .bind(
          input.id,
          input.objectKey,
          input.sha256,
          input.sizeBytes,
          input.startedAt,
          input.endedAt,
          input.expiresAt,
          input.createdAt,
          input.agentId,
          input.cameraId,
          input.createdAt,
          input.homeId,
        ),
      ...input.events.map((event) =>
        this.db
          .prepare(`
            INSERT INTO clip_events (clip_id, event_type, event_id)
            SELECT cl.id, ?, ?
            FROM clips cl
            JOIN homes h ON h.id = cl.home_id AND h.deleted_at IS NULL
            JOIN agents a ON a.home_id = h.id AND a.id = ? AND a.revoked_at IS NULL
            JOIN cameras c ON c.home_id = h.id AND c.agent_id = a.id
              AND c.id = cl.camera_id AND c.id = ? AND c.disabled_at IS NULL
            JOIN tunnel_routes tr ON tr.home_id = h.id AND tr.agent_id = a.id
              AND (
                tr.status = 'active' OR
                (tr.status = 'activation_pending' AND tr.activation_expires_at > ?)
              )
            LEFT JOIN tenant_cleanup tc ON tc.home_id = h.id
            WHERE cl.id = ? AND cl.home_id = ? AND cl.object_key = ?
              AND tc.home_id IS NULL
          `)
          .bind(
            event.eventType,
            event.eventId,
            input.agentId,
            input.cameraId,
            input.createdAt,
            input.id,
            input.homeId,
            input.objectKey,
          ),
      ),
    ];
    try {
      const results = await this.db.batch(statements);
      if (results[0].meta.changes !== 1) {
        throw new PetCareError(410, "account_deleted");
      }
    } catch (error) {
      if (error instanceof PetCareError) throw error;
      if (isConstraint(error)) throw new PetCareError(409, "clip_conflict");
      throw error;
    }
  }

  async findExactClip(input: ExactClipInput, now: string): Promise<ClipReceipt | null> {
    const result = await this.db
      .prepare(`
        SELECT cl.id, cl.home_id, c.agent_id, cl.camera_id, cl.object_key,
               cl.sha256, cl.size_bytes, cl.started_at, cl.ended_at,
               cl.created_at, cl.expires_at, ce.event_type, ce.event_id
        FROM clips cl
        JOIN homes h ON h.id = cl.home_id AND h.deleted_at IS NULL
        JOIN cameras c ON c.id = cl.camera_id AND c.home_id = cl.home_id
        LEFT JOIN tenant_cleanup tc ON tc.home_id = cl.home_id
        LEFT JOIN clip_events ce ON ce.clip_id = cl.id
        WHERE cl.id = ? AND cl.home_id = ? AND cl.expires_at > ?
          AND tc.home_id IS NULL
        ORDER BY ce.event_type, ce.event_id
      `)
      .bind(input.id, input.homeId, now)
      .all<{
        id: string;
        home_id: string;
        agent_id: string;
        camera_id: string;
        object_key: string;
        sha256: string;
        size_bytes: number;
        started_at: string;
        ended_at: string;
        created_at: string;
        expires_at: string;
        event_type: ClipEvent["eventType"] | null;
        event_id: string | null;
      }>();
    const [row] = result.results;
    if (!row) return null;
    const events = result.results.flatMap((item) =>
      item.event_type && item.event_id
        ? [{ eventType: item.event_type, eventId: item.event_id }]
        : [],
    );
    if (
      row.agent_id !== input.agentId ||
      row.camera_id !== input.cameraId ||
      row.object_key !== input.objectKey ||
      row.sha256 !== input.sha256 ||
      row.size_bytes !== input.sizeBytes ||
      row.started_at !== input.startedAt ||
      row.ended_at !== input.endedAt ||
      JSON.stringify(events) !== JSON.stringify(input.events)
    ) {
      return null;
    }
    return { id: row.id, createdAt: row.created_at, expiresAt: row.expires_at };
  }

  async listOwnedClips(homeId: string, now: string): Promise<OwnedClip[]> {
    const result = await this.db
      .prepare(`
        SELECT cl.id, cl.home_id, cl.camera_id, cl.object_key, cl.started_at,
               cl.ended_at, cl.created_at, cl.expires_at,
               ce.event_type, ce.event_id
        FROM clips cl
        JOIN homes h ON h.id = cl.home_id AND h.deleted_at IS NULL
        LEFT JOIN tenant_cleanup tc ON tc.home_id = h.id
        LEFT JOIN clip_events ce ON ce.clip_id = cl.id
        WHERE cl.home_id = ? AND cl.expires_at > ? AND tc.home_id IS NULL
        ORDER BY cl.created_at DESC, cl.id,
          CASE ce.event_type WHEN 'eating' THEN 0 WHEN 'resting' THEN 1 ELSE 2 END,
          ce.event_id
      `)
      .bind(homeId, now)
      .all<ClipRow>();
    return clips(result.results);
  }

  async requireOwnedClip(homeId: string, clipId: string, now: string): Promise<OwnedClip> {
    const result = await this.db
      .prepare(`
        SELECT cl.id, cl.home_id, cl.camera_id, cl.object_key, cl.started_at,
               cl.ended_at, cl.created_at, cl.expires_at,
               ce.event_type, ce.event_id
        FROM clips cl
        JOIN homes h ON h.id = cl.home_id AND h.deleted_at IS NULL
        LEFT JOIN tenant_cleanup tc ON tc.home_id = h.id
        LEFT JOIN clip_events ce ON ce.clip_id = cl.id
        WHERE cl.id = ? AND cl.home_id = ? AND cl.expires_at > ?
          AND tc.home_id IS NULL
        ORDER BY CASE ce.event_type WHEN 'eating' THEN 0 WHEN 'resting' THEN 1 ELSE 2 END,
          ce.event_id
      `)
      .bind(clipId, homeId, now)
      .all<ClipRow>();
    const [owned] = clips(result.results);
    if (!owned) throw new PetCareError(404, "not_found");
    return owned;
  }

  async queueObjectDeletion(homeId: string, objectKey: string, now: string): Promise<void> {
    await this.db
      .prepare(`
        INSERT INTO object_deletion_jobs (object_key, home_id, requested_at)
        VALUES (?, ?, ?)
        ON CONFLICT(object_key) DO NOTHING
      `)
      .bind(objectKey, homeId, now)
      .run();
  }

  async completeObjectDeletion(homeId: string, objectKey: string): Promise<void> {
    await this.db
      .prepare("DELETE FROM object_deletion_jobs WHERE home_id = ? AND object_key = ?")
      .bind(homeId, objectKey)
      .run();
  }

  async recordObjectDeletionFailure(
    homeId: string,
    objectKey: string,
    now: string,
  ): Promise<void> {
    await this.db
      .prepare(`
        UPDATE object_deletion_jobs
        SET requested_at = ?, last_error = 'object_delete_failed'
        WHERE home_id = ? AND object_key = ?
      `)
      .bind(now, homeId, objectKey)
      .run();
  }

  async deleteClipAndQueueObject(
    homeId: string,
    clipId: string,
    now: string,
  ): Promise<{ objectKey: string }> {
    const owned = await this.requireOwnedClip(homeId, clipId, now);
    const results = await this.db.batch([
      this.db
        .prepare(`
          INSERT INTO object_deletion_jobs (object_key, home_id, requested_at)
          SELECT object_key, home_id, ? FROM clips WHERE id = ? AND home_id = ?
          ON CONFLICT(object_key) DO NOTHING
        `)
        .bind(now, clipId, homeId),
      this.db
        .prepare("DELETE FROM clips WHERE id = ? AND home_id = ?")
        .bind(clipId, homeId),
    ]);
    if (results[1].meta.changes !== 1) throw new PetCareError(404, "not_found");
    return { objectKey: owned.objectKey };
  }

  async reserveTunnel(
    homeId: string,
    agentId: string,
    codeHash: string,
    now: string,
  ): Promise<TunnelRouteRecord> {
    await this.db
      .prepare(`
        INSERT INTO tunnel_routes (home_id, agent_id, status, created_at, updated_at)
        SELECT h.id, ?, 'provisioning', ?, ?
        FROM homes h LEFT JOIN tenant_cleanup tc ON tc.home_id = h.id
        WHERE h.id = ? AND h.deleted_at IS NULL AND tc.home_id IS NULL
          AND EXISTS (
            SELECT 1 FROM enrollment_tokens et
            WHERE et.home_id = h.id AND et.token_hash = ?
              AND et.consumed_at IS NULL AND et.expires_at > ?
          )
        ON CONFLICT(home_id) DO NOTHING
      `)
      .bind(agentId, now, now, homeId, codeHash, now)
      .run();
    const row = await this.db
      .prepare(`
        SELECT tr.*, EXISTS (
          SELECT 1 FROM agents a JOIN cameras c ON c.agent_id = a.id
          WHERE a.id = tr.agent_id AND a.home_id = tr.home_id
            AND c.home_id = tr.home_id
        ) AS bound
        FROM tunnel_routes tr
        JOIN homes h ON h.id = tr.home_id AND h.deleted_at IS NULL
        JOIN enrollment_tokens et ON et.home_id = h.id
        LEFT JOIN tenant_cleanup tc ON tc.home_id = h.id
        WHERE tr.home_id = ? AND tr.agent_id = ? AND tr.status = 'provisioning'
          AND et.token_hash = ? AND et.consumed_at IS NULL AND et.expires_at > ?
          AND tc.home_id IS NULL
        LIMIT 1
      `)
      .bind(homeId, agentId, codeHash, now)
      .first<TunnelRow>();
    if (!row) {
      throw new PetCareError(409, "enrollment_rejected");
    }
    return tunnel(row);
  }

  async getTunnelLedger(homeId: string): Promise<TunnelRouteRecord | null> {
    const row = await this.db
      .prepare(`
        SELECT tr.*, EXISTS (
          SELECT 1 FROM agents a JOIN cameras c ON c.agent_id = a.id
          WHERE a.id = tr.agent_id AND a.home_id = tr.home_id
            AND c.home_id = tr.home_id
        ) AS bound
        FROM tunnel_routes tr WHERE tr.home_id = ? LIMIT 1
      `)
      .bind(homeId)
      .first<TunnelRow>();
    return row ? tunnel(row) : null;
  }

  async claimTunnelProvisioning(
    homeId: string,
    agentId: string,
    leaseId: string,
    now: string,
    leaseExpiresAt: string,
  ): Promise<void> {
    if (!validLeaseWindow(now, leaseExpiresAt)) {
      throw new PetCareError(503, "enrollment_retryable");
    }
    const result = await this.db
      .prepare(`
        UPDATE tunnel_routes
        SET lease_id = ?, lease_expires_at = ?, updated_at = ?, last_error = NULL
        WHERE home_id = ? AND agent_id = ? AND status = 'provisioning'
          AND (lease_id IS NULL OR lease_expires_at <= ?)
          AND ? > ?
      `)
      .bind(leaseId, leaseExpiresAt, now, homeId, agentId, now, leaseExpiresAt, now)
      .run();
    if (result.meta.changes !== 1) {
      throw new PetCareError(503, "enrollment_retryable");
    }
  }

  async renewTunnelLease(
    homeId: string,
    agentId: string,
    leaseId: string,
    now: string,
    leaseExpiresAt: string,
  ): Promise<void> {
    if (!validLeaseWindow(now, leaseExpiresAt)) {
      throw new PetCareError(503, "cleanup_retryable");
    }
    const result = await this.db
      .prepare(`
        UPDATE tunnel_routes SET lease_expires_at = ?, updated_at = ?
        WHERE home_id = ? AND agent_id = ? AND lease_id = ?
          AND lease_expires_at > ?
          AND ? > ?
          AND status IN ('provisioning', 'cleanup_pending', 'revocation_pending')
      `)
      .bind(leaseExpiresAt, now, homeId, agentId, leaseId, now, leaseExpiresAt, now)
      .run();
    if (result.meta.changes !== 1) {
      throw new PetCareError(503, "cleanup_retryable");
    }
  }

  async updateTunnelResource(
    homeId: string,
    agentId: string,
    patch: Partial<ResourceLedger>,
    now: string,
    leaseId: string,
  ): Promise<void> {
    const columns: Record<keyof ResourceLedger, string> = {
      tunnelId: "tunnel_id",
      tunnelOrigin: "tunnel_origin",
      dnsRecordId: "dns_record_id",
      accessAppId: "access_app_id",
      accessAud: "access_aud",
      accessPolicyId: "access_policy_id",
    };
    const entries = Object.entries(patch).filter((entry) => entry[1] !== undefined) as Array<
      [keyof ResourceLedger, string | null]
    >;
    if (entries.length === 0) return;
    const result = await this.db
      .prepare(`
        UPDATE tunnel_routes SET
          ${entries.map(([key]) => `${columns[key]} = ?`).join(", ")}, updated_at = ?
        WHERE home_id = ? AND agent_id = ? AND lease_id = ?
          AND lease_expires_at > ?
          AND status IN ('provisioning', 'cleanup_pending', 'revocation_pending')
      `)
      .bind(
        ...entries.map(([, value]) => value),
        now,
        homeId,
        agentId,
        leaseId,
        now,
      )
      .run();
    if (result.meta.changes !== 1) throw new PetCareError(503, "enrollment_retryable");
  }

  async recordCleanupPending(
    homeId: string,
    agentId: string,
    ledger: ResourceLedger,
    code: TunnelErrorCode,
    leaseId: string,
    now: string,
  ): Promise<void> {
    const result = await this.db
      .prepare(`
        UPDATE tunnel_routes SET
          tunnel_id = COALESCE(?, tunnel_id),
          tunnel_origin = COALESCE(?, tunnel_origin),
          dns_record_id = COALESCE(?, dns_record_id),
          access_app_id = COALESCE(?, access_app_id),
          access_aud = COALESCE(?, access_aud),
          access_policy_id = COALESCE(?, access_policy_id),
          status = 'cleanup_pending', updated_at = ?, last_error = ?
        WHERE home_id = ? AND agent_id = ? AND lease_id = ?
          AND lease_expires_at > ?
          AND status IN ('provisioning', 'cleanup_pending', 'revocation_pending')
      `)
      .bind(
        ledger.tunnelId ?? null,
        ledger.tunnelOrigin ?? null,
        ledger.dnsRecordId ?? null,
        ledger.accessAppId ?? null,
        ledger.accessAud ?? null,
        ledger.accessPolicyId ?? null,
        now,
        code,
        homeId,
        agentId,
        leaseId,
        now,
      )
      .run();
    if (result.meta.changes !== 1) {
      throw new PetCareError(503, "cleanup_retryable");
    }
  }

  async markActivationPending(
    homeId: string,
    agentId: string,
    activationExpiresAt: string,
    now: string,
    leaseId: string,
  ): Promise<void> {
    const result = await this.db
      .prepare(`
        UPDATE tunnel_routes
        SET status = 'activation_pending', activation_expires_at = ?,
          lease_id = NULL, lease_expires_at = NULL, updated_at = ?, last_error = NULL
        WHERE home_id = ? AND agent_id = ? AND status = 'provisioning'
          AND lease_id = ? AND lease_expires_at > ?
      `)
      .bind(
        activationExpiresAt,
        now,
        homeId,
        agentId,
        leaseId,
        now,
      )
      .run();
    if (result.meta.changes !== 1) throw new PetCareError(503, "enrollment_retryable");
  }

  async requestRevocation(
    homeId: string,
    agentId: string,
    leaseId: string,
    now: string,
    leaseExpiresAt: string,
  ): Promise<ResourceLedger> {
    if (!validLeaseWindow(now, leaseExpiresAt)) {
      throw new PetCareError(503, "revocation_retryable");
    }
    const record = await this.getTunnelLedger(homeId);
    if (!record || record.agentId !== agentId) throw new PetCareError(404, "not_found");
    try {
      await this.db.batch([
        this.db
          .prepare(`
            UPDATE tunnel_routes SET status = 'revocation_pending',
              activation_expires_at = NULL, lease_id = ?, lease_expires_at = ?, updated_at = ?
            WHERE home_id = ? AND agent_id = ?
              AND status IN ('active', 'activation_pending')
              AND (lease_id IS NULL OR lease_expires_at <= ?)
              AND ? > ?
          `)
          .bind(leaseId, leaseExpiresAt, now, homeId, agentId, now, leaseExpiresAt, now),
        this.db
          .prepare(`
            UPDATE agents SET revoked_at = ?
            WHERE home_id = ? AND id = ? AND revoked_at IS NULL
              AND EXISTS (
                SELECT 1 FROM tunnel_routes tr
                WHERE tr.home_id = agents.home_id AND tr.agent_id = agents.id
                  AND tr.status = 'revocation_pending' AND tr.lease_id = ?
                  AND tr.lease_expires_at > ?
              )
          `)
          .bind(now, homeId, agentId, leaseId, now),
        this.db
          .prepare(`
            UPDATE cameras SET disabled_at = ?
            WHERE home_id = ? AND agent_id = ? AND disabled_at IS NULL
              AND EXISTS (
                SELECT 1 FROM tunnel_routes tr
                WHERE tr.home_id = cameras.home_id AND tr.agent_id = cameras.agent_id
                  AND tr.status = 'revocation_pending' AND tr.lease_id = ?
                  AND tr.lease_expires_at > ?
              )
          `)
          .bind(now, homeId, agentId, leaseId, now),
        this.db
          .prepare(`
            INSERT INTO tenant_cleanup (owner_sub, home_id, status, started_at, updated_at)
            SELECT NULL, NULL, NULL, NULL, NULL
            WHERE NOT EXISTS (
              SELECT 1 FROM tunnel_routes
              WHERE home_id = ? AND agent_id = ? AND status = 'revocation_pending'
                AND lease_id = ? AND lease_expires_at > ?
            )
          `)
          .bind(homeId, agentId, leaseId, now),
      ]);
    } catch {
      throw new PetCareError(503, "revocation_retryable");
    }
    return {
      tunnelId: record.tunnelId,
      tunnelOrigin: record.tunnelOrigin,
      dnsRecordId: record.dnsRecordId,
      accessAppId: record.accessAppId,
      accessAud: record.accessAud,
      accessPolicyId: record.accessPolicyId,
    };
  }

  async markTunnelState(
    homeId: string,
    agentId: string,
    leaseId: string,
    expectedStatus: TunnelStatus,
    status: TunnelStatus,
    now: string,
    code?: TunnelErrorCode,
  ): Promise<void> {
    const result = await this.db
      .prepare(`
        UPDATE tunnel_routes SET status = ?, updated_at = ?, last_error = ?,
          activation_expires_at = CASE WHEN ? IN ('active', 'revoked') THEN NULL ELSE activation_expires_at END,
          lease_id = NULL, lease_expires_at = NULL
        WHERE home_id = ? AND agent_id = ? AND status = ?
          AND lease_id = ? AND lease_expires_at > ?
      `)
      .bind(
        status,
        now,
        code ?? null,
        status,
        homeId,
        agentId,
        expectedStatus,
        leaseId,
        now,
      )
      .run();
    if (result.meta.changes !== 1) {
      throw new PetCareError(503, "cleanup_retryable");
    }
  }

  async deleteTunnelRoute(
    homeId: string,
    agentId: string,
    leaseId: string,
    now: string,
  ): Promise<void> {
    const result = await this.db
      .prepare(`
        DELETE FROM tunnel_routes WHERE home_id = ? AND agent_id = ?
          AND status = 'cleanup_pending' AND lease_id = ? AND lease_expires_at > ?
          AND tunnel_id IS NULL AND dns_record_id IS NULL
          AND access_app_id IS NULL AND access_policy_id IS NULL
      `)
      .bind(homeId, agentId, leaseId, now)
      .run();
    if (result.meta.changes !== 1) throw new PetCareError(503, "cleanup_retryable");
  }

  async beginTenantCleanup(
    ownerSub: string,
    now: string,
  ): Promise<{ homeId: string; status: "cleanup_pending" } | { status: "absent" }> {
    const existing = await this.getTenantCleanup(ownerSub);
    if (existing) return existing;
    const home = await this.db
      .prepare("SELECT id FROM homes WHERE owner_sub = ? LIMIT 1")
      .bind(ownerSub)
      .first<{ id: string }>();
    if (!home) return { status: "absent" };
    try {
      await this.db.batch([
        this.db
          .prepare(`
            INSERT INTO tenant_cleanup (owner_sub, home_id, status, started_at, updated_at)
            SELECT owner_sub, id, 'cleanup_pending', ?, ? FROM homes
            WHERE id = ? AND owner_sub = ?
          `)
          .bind(now, now, home.id, ownerSub),
        this.db
          .prepare(`
            UPDATE homes SET deleted_at = COALESCE(deleted_at, ?)
            WHERE id = ? AND owner_sub = ? AND EXISTS (
              SELECT 1 FROM tenant_cleanup tc
              WHERE tc.owner_sub = ? AND tc.home_id = homes.id
            )
          `)
          .bind(now, home.id, ownerSub, ownerSub),
        this.db
          .prepare(`
            UPDATE agents SET revoked_at = COALESCE(revoked_at, ?)
            WHERE home_id = ? AND EXISTS (
              SELECT 1 FROM homes h JOIN tenant_cleanup tc ON tc.home_id = h.id
              WHERE h.id = agents.home_id AND h.owner_sub = ? AND tc.owner_sub = ?
            )
          `)
          .bind(now, home.id, ownerSub, ownerSub),
        this.db
          .prepare(`
            UPDATE cameras SET disabled_at = COALESCE(disabled_at, ?)
            WHERE home_id = ? AND EXISTS (
              SELECT 1 FROM homes h JOIN tenant_cleanup tc ON tc.home_id = h.id
              WHERE h.id = cameras.home_id AND h.owner_sub = ? AND tc.owner_sub = ?
            )
          `)
          .bind(now, home.id, ownerSub, ownerSub),
        this.db
          .prepare(`
            DELETE FROM enrollment_tokens WHERE home_id = ? AND EXISTS (
              SELECT 1 FROM homes h JOIN tenant_cleanup tc ON tc.home_id = h.id
              WHERE h.id = enrollment_tokens.home_id AND h.owner_sub = ? AND tc.owner_sub = ?
            )
          `)
          .bind(home.id, ownerSub, ownerSub),
        this.db
          .prepare(`
            UPDATE tunnel_routes SET status = 'revocation_pending', activation_expires_at = NULL,
              lease_id = NULL, lease_expires_at = NULL, updated_at = ? WHERE home_id = ? AND EXISTS (
                SELECT 1 FROM homes h JOIN tenant_cleanup tc ON tc.home_id = h.id
                WHERE h.id = tunnel_routes.home_id AND h.owner_sub = ? AND tc.owner_sub = ?
              )
          `)
          .bind(now, home.id, ownerSub, ownerSub),
        this.db
          .prepare(`
            INSERT INTO object_deletion_jobs (object_key, home_id, requested_at)
            SELECT cl.object_key, cl.home_id, ? FROM clips cl
            JOIN homes h ON h.id = cl.home_id
            JOIN tenant_cleanup tc ON tc.home_id = h.id
            WHERE cl.home_id = ? AND h.owner_sub = ? AND tc.owner_sub = ?
            ON CONFLICT(object_key) DO NOTHING
          `)
          .bind(now, home.id, ownerSub, ownerSub),
        this.db
          .prepare(`
            DELETE FROM clips WHERE home_id = ? AND EXISTS (
              SELECT 1 FROM homes h JOIN tenant_cleanup tc ON tc.home_id = h.id
              WHERE h.id = clips.home_id AND h.owner_sub = ? AND tc.owner_sub = ?
            )
          `)
          .bind(home.id, ownerSub, ownerSub),
        this.db
          .prepare(`
            INSERT INTO tenant_cleanup (owner_sub, home_id, status, started_at, updated_at)
            SELECT NULL, NULL, NULL, NULL, NULL
            WHERE NOT EXISTS (
              SELECT 1 FROM homes h JOIN tenant_cleanup tc ON tc.home_id = h.id
              WHERE h.id = ? AND h.owner_sub = ? AND tc.owner_sub = ?
                AND h.deleted_at IS NOT NULL
            )
          `)
          .bind(home.id, ownerSub, ownerSub),
      ]);
      return { homeId: home.id, status: "cleanup_pending" };
    } catch (error) {
      const concurrent = await this.getTenantCleanup(ownerSub);
      if (concurrent) return concurrent;
      throw error;
    }
  }

  async getTenantCleanup(
    ownerSub: string,
  ): Promise<{ homeId: string; status: "cleanup_pending" } | null> {
    const row = await this.db
      .prepare("SELECT home_id, status FROM tenant_cleanup WHERE owner_sub = ? LIMIT 1")
      .bind(ownerSub)
      .first<{ home_id: string; status: "cleanup_pending" }>();
    return row ? { homeId: row.home_id, status: row.status } : null;
  }

  async markTenantCleanupError(
    ownerSub: string,
    homeId: string,
    now: string,
    code: "tenant_cleanup_failed",
  ): Promise<void> {
    await this.db
      .prepare(`
        UPDATE tenant_cleanup SET updated_at = ?, last_error = ?
        WHERE owner_sub = ? AND home_id = ?
      `)
      .bind(now, code, ownerSub, homeId)
      .run();
  }

  async completeTenantCleanup(ownerSub: string, homeId: string, now: string): Promise<void> {
    const owned = await this.db
      .prepare(`
        SELECT 1 AS found FROM homes h JOIN tenant_cleanup tc ON tc.home_id = h.id
        WHERE h.id = ? AND h.owner_sub = ? AND tc.owner_sub = ?
        LIMIT 1
      `)
      .bind(homeId, ownerSub, ownerSub)
      .first();
    if (!owned) {
      const conflicting = await this.db
        .prepare(`
          SELECT 1 AS found FROM tenant_cleanup WHERE owner_sub = ? OR home_id = ?
          UNION ALL
          SELECT 1 FROM homes WHERE owner_sub = ? OR id = ?
          LIMIT 1
        `)
        .bind(ownerSub, homeId, ownerSub, homeId)
        .first();
      if (conflicting) throw new PetCareError(503, "cleanup_retryable");
      return;
    }

    try {
      await this.db.batch([
        this.db
          .prepare(`
            INSERT INTO tenant_cleanup (owner_sub, home_id, status, started_at, updated_at)
            SELECT NULL, NULL, NULL, NULL, NULL
            WHERE NOT EXISTS (
              SELECT 1 FROM homes h JOIN tenant_cleanup tc ON tc.home_id = h.id
              WHERE h.id = ? AND h.owner_sub = ? AND tc.owner_sub = ?
                AND NOT EXISTS (SELECT 1 FROM clips WHERE home_id = h.id)
                AND NOT EXISTS (SELECT 1 FROM object_deletion_jobs WHERE home_id = h.id)
                AND NOT EXISTS (
                  SELECT 1 FROM tunnel_routes tr WHERE tr.home_id = h.id AND (
                    tr.status <> 'revoked' OR
                    (tr.lease_id IS NULL AND tr.lease_expires_at IS NOT NULL) OR
                    (tr.lease_id IS NOT NULL AND (
                      tr.lease_expires_at IS NULL OR tr.lease_expires_at > ?
                    )) OR
                    tr.tunnel_id IS NOT NULL OR tr.dns_record_id IS NOT NULL OR
                    tr.access_app_id IS NOT NULL OR tr.access_policy_id IS NOT NULL
                  )
                )
            )
          `)
          .bind(homeId, ownerSub, ownerSub, now),
        this.db
          .prepare(`
            DELETE FROM tunnel_routes WHERE home_id = ? AND EXISTS (
              SELECT 1 FROM homes h JOIN tenant_cleanup tc ON tc.home_id = h.id
              WHERE h.id = tunnel_routes.home_id AND h.owner_sub = ? AND tc.owner_sub = ?
            )
          `)
          .bind(homeId, ownerSub, ownerSub),
        this.db
          .prepare(`
            DELETE FROM enrollment_tokens WHERE home_id = ? AND EXISTS (
              SELECT 1 FROM homes h JOIN tenant_cleanup tc ON tc.home_id = h.id
              WHERE h.id = enrollment_tokens.home_id AND h.owner_sub = ? AND tc.owner_sub = ?
            )
          `)
          .bind(homeId, ownerSub, ownerSub),
        this.db
          .prepare(`
            DELETE FROM cameras WHERE home_id = ? AND EXISTS (
              SELECT 1 FROM homes h JOIN tenant_cleanup tc ON tc.home_id = h.id
              WHERE h.id = cameras.home_id AND h.owner_sub = ? AND tc.owner_sub = ?
            )
          `)
          .bind(homeId, ownerSub, ownerSub),
        this.db
          .prepare(`
            DELETE FROM agents WHERE home_id = ? AND EXISTS (
              SELECT 1 FROM homes h JOIN tenant_cleanup tc ON tc.home_id = h.id
              WHERE h.id = agents.home_id AND h.owner_sub = ? AND tc.owner_sub = ?
            )
          `)
          .bind(homeId, ownerSub, ownerSub),
        this.db
          .prepare("DELETE FROM homes WHERE id = ? AND owner_sub = ?")
          .bind(homeId, ownerSub),
        this.db
          .prepare("DELETE FROM tenant_cleanup WHERE owner_sub = ? AND home_id = ?")
          .bind(ownerSub, homeId),
        this.db
          .prepare(`
            INSERT INTO tenant_cleanup (owner_sub, home_id, status, started_at, updated_at)
            SELECT NULL, NULL, NULL, NULL, NULL
            WHERE EXISTS (
              SELECT 1 FROM homes WHERE id = ? AND owner_sub = ?
              UNION ALL
              SELECT 1 FROM tenant_cleanup WHERE owner_sub = ? AND home_id = ?
            )
          `)
          .bind(homeId, ownerSub, ownerSub, homeId),
      ]);
    } catch {
      const remaining = await this.db
        .prepare(`
          SELECT 1 AS found FROM tenant_cleanup WHERE owner_sub = ? OR home_id = ?
          UNION ALL
          SELECT 1 FROM homes WHERE owner_sub = ? OR id = ?
          LIMIT 1
        `)
        .bind(ownerSub, homeId, ownerSub, homeId)
        .first()
        .catch(() => {
          throw new PetCareError(503, "cleanup_retryable");
        });
      if (!remaining) return;
      await this.markTenantCleanupError(
        ownerSub,
        homeId,
        new Date().toISOString(),
        "tenant_cleanup_failed",
      ).catch(() => undefined);
      throw new PetCareError(503, "cleanup_retryable");
    }
  }

  async listObjectDeletionJobs(limit: number): Promise<ObjectDeletionJob[]> {
    const result = await this.db
      .prepare(`
        SELECT home_id, object_key, requested_at FROM object_deletion_jobs
        ORDER BY requested_at, object_key LIMIT ?
      `)
      .bind(limit)
      .all<{ home_id: string; object_key: string; requested_at: string }>();
    return result.results.map((row) => ({
      homeId: row.home_id,
      objectKey: row.object_key,
      requestedAt: row.requested_at,
    }));
  }

  async queueExpiredClips(now: string, limit: number): Promise<number> {
    const result = await this.db
      .prepare(`
        SELECT id, home_id, object_key FROM clips
        WHERE expires_at <= ? ORDER BY expires_at, id LIMIT ?
      `)
      .bind(now, limit)
      .all<{ id: string; home_id: string; object_key: string }>();
    for (const item of result.results) {
      await this.db.batch([
        this.db
          .prepare(`
            INSERT INTO object_deletion_jobs (object_key, home_id, requested_at)
            SELECT object_key, home_id, ? FROM clips WHERE id = ? AND home_id = ?
            ON CONFLICT(object_key) DO NOTHING
          `)
          .bind(now, item.id, item.home_id),
        this.db
          .prepare("DELETE FROM clips WHERE id = ? AND home_id = ?")
          .bind(item.id, item.home_id),
      ]);
    }
    return result.results.length;
  }

  async listUnexpiredClipObjects(
    now: string,
    afterId: string | null,
    limit: number,
  ): Promise<ClipObject[]> {
    const result = await this.db
      .prepare(`
        SELECT id, object_key FROM clips
        WHERE expires_at > ? AND (? IS NULL OR id > ?) ORDER BY id LIMIT ?
      `)
      .bind(now, afterId, afterId, limit)
      .all<{ id: string; object_key: string }>();
    return result.results.map((row) => ({ id: row.id, objectKey: row.object_key }));
  }

  async hasClipOrDeletionJob(objectKey: string): Promise<boolean> {
    const row = await this.db
      .prepare(`
        SELECT 1 AS found FROM clips WHERE object_key = ?
        UNION ALL SELECT 1 FROM object_deletion_jobs WHERE object_key = ?
        LIMIT 1
      `)
      .bind(objectKey, objectKey)
      .first();
    return row !== null;
  }

  async deleteClipMetadataByObjectKey(objectKey: string): Promise<boolean> {
    const result = await this.db
      .prepare("DELETE FROM clips WHERE object_key = ?")
      .bind(objectKey)
      .run();
    return result.meta.changes === 1;
  }

  async deleteExpiredNonces(now: string, limit: number): Promise<number> {
    const result = await this.db
      .prepare(`
        DELETE FROM upload_nonces WHERE rowid IN (
          SELECT rowid FROM upload_nonces WHERE expires_at <= ? ORDER BY expires_at LIMIT ?
        )
      `)
      .bind(now, limit)
      .run();
    return result.meta.changes;
  }

  async deleteExpiredRateLimits(now: string, limit: number): Promise<number> {
    const result = await this.db
      .prepare(`
        DELETE FROM request_limits WHERE rowid IN (
          SELECT rowid FROM request_limits WHERE expires_at <= ? ORDER BY expires_at LIMIT ?
        )
      `)
      .bind(now, limit)
      .run();
    return result.meta.changes;
  }

  async claimPendingTunnelCleanup(
    now: string,
    provisioningCutoff: string,
    leaseId: string,
    leaseExpiresAt: string,
    limit: number,
  ): Promise<TunnelRouteRecord[]> {
    if (!validLeaseWindow(now, leaseExpiresAt)) {
      throw new PetCareError(503, "cleanup_retryable");
    }
    const workLimit = Number.isInteger(limit) && limit > 0 ? Math.min(limit, 25) : 0;
    await this.db
      .prepare(`
        UPDATE tunnel_routes SET
          status = CASE
            WHEN status IN ('provisioning', 'activation_pending') THEN 'cleanup_pending'
            ELSE status
          END,
          activation_expires_at = CASE
            WHEN status IN ('provisioning', 'activation_pending') THEN NULL
            ELSE activation_expires_at
          END,
          last_error = CASE
            WHEN status IN ('provisioning', 'activation_pending') THEN 'activation_expired'
            ELSE last_error
          END,
          lease_id = ?, lease_expires_at = ?, updated_at = ?
        WHERE home_id IN (
          SELECT home_id FROM tunnel_routes
          WHERE (
              status IN ('cleanup_pending', 'revocation_pending')
              OR (status = 'activation_pending' AND activation_expires_at <= ?)
              OR (status = 'provisioning' AND updated_at <= ?)
            )
            AND (lease_id IS NULL OR lease_expires_at <= ?)
            AND ? > ?
          ORDER BY updated_at, home_id LIMIT ?
        )
      `)
      .bind(
        leaseId,
        leaseExpiresAt,
        now,
        now,
        provisioningCutoff,
        now,
        leaseExpiresAt,
        now,
        workLimit,
      )
      .run();
    const result = await this.db
      .prepare(`
        SELECT tr.*, EXISTS (
          SELECT 1 FROM agents a JOIN cameras c ON c.agent_id = a.id
          WHERE a.id = tr.agent_id AND a.home_id = tr.home_id
            AND c.home_id = tr.home_id
        ) AS bound
        FROM tunnel_routes tr
        WHERE tr.lease_id = ? AND tr.lease_expires_at = ?
          AND tr.status IN ('cleanup_pending', 'revocation_pending')
        ORDER BY tr.updated_at, tr.home_id
      `)
      .bind(leaseId, leaseExpiresAt)
      .all<TunnelRow>();
    return result.results.map(tunnel);
  }

  async clearTunnelResource(
    homeId: string,
    agentId: string,
    leaseId: string,
    resource: keyof ResourceLedger,
    now: string,
  ): Promise<void> {
    const columns: Record<keyof ResourceLedger, string> = {
      tunnelId: "tunnel_id",
      tunnelOrigin: "tunnel_origin",
      dnsRecordId: "dns_record_id",
      accessAppId: "access_app_id",
      accessAud: "access_aud",
      accessPolicyId: "access_policy_id",
    };
    const result = await this.db
      .prepare(`
        UPDATE tunnel_routes SET ${columns[resource]} = NULL, updated_at = ?
        WHERE home_id = ? AND agent_id = ? AND lease_id = ?
          AND lease_expires_at > ?
          AND status IN ('cleanup_pending', 'revocation_pending')
      `)
      .bind(now, homeId, agentId, leaseId, now)
      .run();
    if (result.meta.changes !== 1) {
      throw new PetCareError(503, "cleanup_retryable");
    }
  }

  async listTenantCleanup(limit: number): Promise<TenantCleanupRecord[]> {
    const result = await this.db
      .prepare(`
        SELECT owner_sub, home_id, status FROM tenant_cleanup
        ORDER BY updated_at, owner_sub LIMIT ?
      `)
      .bind(limit)
      .all<{ owner_sub: string; home_id: string; status: "cleanup_pending" }>();
    return result.results.map((row) => ({
      ownerSub: row.owner_sub,
      homeId: row.home_id,
      status: row.status,
    }));
  }

  async getReconcileCursor(name: string): Promise<string | null> {
    const row = await this.db
      .prepare("SELECT cursor FROM reconcile_state WHERE name = ?")
      .bind(name)
      .first<{ cursor: string | null }>();
    return row?.cursor ?? null;
  }

  async setReconcileCursor(name: string, cursor: string | null, now: string): Promise<void> {
    await this.db
      .prepare(`
        INSERT INTO reconcile_state (name, cursor, updated_at) VALUES (?, ?, ?)
        ON CONFLICT(name) DO UPDATE SET cursor = excluded.cursor, updated_at = excluded.updated_at
      `)
      .bind(name, cursor, now)
      .run();
  }
}
