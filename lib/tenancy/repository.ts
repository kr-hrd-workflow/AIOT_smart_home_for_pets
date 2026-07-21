import { and, eq, isNotNull, isNull } from "drizzle-orm";
import type { PetCareDb } from "../../db";
import { enrollmentTokens, homes } from "../../db/schema";

export type HomeRecord = typeof homes.$inferSelect;

export type ConsumeEnrollmentInput = {
  codeHash: string;
  consumedAt: string;
  agent: { id: string; publicKey: string; tunnelOrigin: string };
  camera: { id: string; localCameraId: string };
};

export type EnrollmentBinding = {
  homeId: string;
  agentId: string;
  cameraId: string;
};

export class TenantNotFoundError extends Error {
  readonly status = 404;
  readonly code = "home_not_found";
}

export class AccountDeletedError extends Error {
  readonly status = 410;
  readonly code = "account_deleted";
}

export class EnrollmentRejectedError extends Error {
  readonly status = 409;
  readonly code = "enrollment_rejected";
}

export class TenantInfrastructureError extends Error {
  readonly status = 503;
  readonly code = "tenancy_unavailable";
}

function isEnrollmentConstraint(error: unknown): boolean {
  const message = error instanceof Error ? `${error.name} ${error.message}` : "";
  return /SQLITE_CONSTRAINT|UNIQUE constraint failed|FOREIGN KEY constraint failed/.test(
    message,
  );
}

export class TenantRepository {
  constructor(private readonly db: PetCareDb) {}

  async requireHome(ownerSub: string): Promise<HomeRecord> {
    const [home] = await this.db
      .select()
      .from(homes)
      .where(and(eq(homes.ownerSub, ownerSub), isNull(homes.deletedAt)))
      .limit(1);
    if (!home) throw new TenantNotFoundError("Active home not found");
    return home;
  }

  async ensureHome(ownerSub: string): Promise<HomeRecord> {
    if (!ownerSub) throw new TenantNotFoundError("Active home not found");
    const [active] = await this.db
      .select()
      .from(homes)
      .where(and(eq(homes.ownerSub, ownerSub), isNull(homes.deletedAt)))
      .limit(1);
    if (active) return active;

    const [deleted] = await this.db
      .select({ id: homes.id })
      .from(homes)
      .where(and(eq(homes.ownerSub, ownerSub), isNotNull(homes.deletedAt)))
      .limit(1);
    if (deleted) throw new AccountDeletedError("PetCare account deleted");

    try {
      await this.db
        .insert(homes)
        .values({
          id: crypto.randomUUID(),
          ownerSub,
          createdAt: new Date().toISOString(),
        })
        .onConflictDoNothing();
    } catch (error) {
      if (error instanceof Error && error.message.includes("account_deleted")) {
        throw new AccountDeletedError("PetCare account deleted");
      }
      throw error;
    }
    return this.requireHome(ownerSub);
  }

  async replaceEnrollmentToken(
    homeId: string,
    tokenHash: string,
    expiresAt: string,
  ): Promise<void> {
    await this.db.batch([
      this.db
        .delete(enrollmentTokens)
        .where(
          and(
            eq(enrollmentTokens.homeId, homeId),
            isNull(enrollmentTokens.consumedAt),
          ),
        ),
      this.db.insert(enrollmentTokens).values({
        id: crypto.randomUUID(),
        homeId,
        tokenHash,
        expiresAt,
      }),
    ]);
  }

  async consumeEnrollment(
    input: ConsumeEnrollmentInput,
  ): Promise<EnrollmentBinding> {
    const client = this.db.$client;
    try {
      const results = await client.batch([
        client
          .prepare(`
            INSERT INTO agents (id, home_id, public_key, tunnel_origin)
            SELECT ?, et.home_id, ?, ? FROM enrollment_tokens et
            JOIN tunnel_routes tr ON tr.home_id = et.home_id
            WHERE tr.agent_id = ? AND tr.status = 'provisioning'
              AND et.token_hash = ? AND et.consumed_at IS NULL AND et.expires_at > ?
          `)
          .bind(
            input.agent.id,
            input.agent.publicKey,
            input.agent.tunnelOrigin,
            input.agent.id,
            input.codeHash,
            input.consumedAt,
          ),
        client
          .prepare(`
            INSERT INTO cameras (id, home_id, agent_id, local_camera_id, created_at)
            SELECT ?, et.home_id, ?, ?, ? FROM enrollment_tokens et
            JOIN tunnel_routes tr ON tr.home_id = et.home_id
            WHERE tr.agent_id = ? AND tr.status = 'provisioning'
              AND et.token_hash = ? AND et.consumed_at IS NULL AND et.expires_at > ?
          `)
          .bind(
            input.camera.id,
            input.agent.id,
            input.camera.localCameraId,
            input.consumedAt,
            input.agent.id,
            input.codeHash,
            input.consumedAt,
          ),
        client
          .prepare(`
            UPDATE enrollment_tokens SET consumed_at = ?
            WHERE token_hash = ? AND consumed_at IS NULL AND expires_at > ?
              AND EXISTS (
                SELECT 1 FROM tunnel_routes tr
                WHERE tr.home_id = enrollment_tokens.home_id
                  AND tr.agent_id = ? AND tr.status = 'provisioning'
              )
            RETURNING home_id
          `)
          .bind(
            input.consumedAt,
            input.codeHash,
            input.consumedAt,
            input.agent.id,
          ),
      ]);
      const homeId = results[2].results[0]?.home_id;
      if (
        results[0].meta.changes !== 1 ||
        results[1].meta.changes !== 1 ||
        typeof homeId !== "string"
      ) {
        throw new EnrollmentRejectedError("Enrollment rejected");
      }
      return {
        homeId,
        agentId: input.agent.id,
        cameraId: input.camera.id,
      };
    } catch (error) {
      if (error instanceof EnrollmentRejectedError) throw error;
      if (isEnrollmentConstraint(error)) {
        throw new EnrollmentRejectedError("Enrollment rejected");
      }
      throw new TenantInfrastructureError("Tenancy unavailable");
    }
  }
}
