import { hashEnrollmentCode } from "../tenancy/enrollment";
import {
  EnrollmentRejectedError,
  type TenantRepository,
} from "../tenancy/repository";
import type { CloudflareClient } from "./cloudflare";
import { PetCareError } from "./errors";
import type {
  PetCareRepository,
  ResourceLedger,
  TunnelRouteRecord,
} from "./repository";

const ACTIVATION_TTL_MS = 600_000;
const PROVISIONING_LEASE_MS = 120_000;
const CLEANUP_WRITE_ATTEMPTS = 3;

export type EnrollmentInput = {
  code: string;
  publicKey: string;
  localCameraId: string;
  connectingIp: string;
};

export function provisioningResourceNames(
  homeId: string,
  agentId: string,
  zoneName: string,
) {
  const homePart = homeId.replace(/[^A-Za-z0-9]/g, "").toLowerCase().slice(0, 8);
  const agentPart = agentId
    .replace(/^agent_/, "")
    .replace(/[^A-Za-z0-9]/g, "")
    .toLowerCase()
    .slice(0, 12);
  const label = `home-${homePart}-${agentPart}`;
  return {
    tunnelName: `petcare-${label}`,
    hostname: `${label}.${zoneName}`,
    accessName: `PetCare ${label}`,
  };
}

function isCanonicalBase64Url(value: string, bytes: number): boolean {
  if (!/^[A-Za-z0-9_-]+$/.test(value)) return false;
  try {
    const standard = value.replaceAll("-", "+").replaceAll("_", "/");
    const decoded = atob(standard.padEnd(Math.ceil(value.length / 4) * 4, "="));
    if (decoded.length !== bytes) return false;
    const canonical = btoa(decoded)
      .replaceAll("+", "-")
      .replaceAll("/", "_")
      .replace(/=+$/, "");
    return canonical === value;
  } catch {
    return false;
  }
}

function remoteString(value: unknown): string {
  if (
    typeof value !== "string" ||
    value.length === 0 ||
    value.trim() !== value
  ) {
    throw new PetCareError(503, "enrollment_retryable");
  }
  return value;
}

function ids() {
  return {
    agentId: `agent_${crypto.randomUUID()}`,
    cameraId: `camera_${crypto.randomUUID()}`,
  };
}

function ledgerFrom(route: TunnelRouteRecord): ResourceLedger {
  return {
    tunnelId: route.tunnelId,
    tunnelOrigin: route.tunnelOrigin,
    dnsRecordId: route.dnsRecordId,
    accessAppId: route.accessAppId,
    accessAud: route.accessAud,
    accessPolicyId: route.accessPolicyId,
  };
}

function isConsistentLedger(
  ledger: ResourceLedger,
  expectedOrigin: string,
): boolean {
  if (!!ledger.dnsRecordId !== !!ledger.tunnelOrigin) return false;
  if (ledger.tunnelOrigin && ledger.tunnelOrigin !== expectedOrigin) return false;
  if (ledger.dnsRecordId && !ledger.tunnelId) return false;
  if (!!ledger.accessAppId !== !!ledger.accessAud) return false;
  if (ledger.accessAppId && !ledger.dnsRecordId) return false;
  if (ledger.accessPolicyId && !ledger.accessAppId) return false;
  return true;
}

export class EnrollmentProvisioningService {
  constructor(
    private readonly tenants: TenantRepository,
    private readonly petcare: PetCareRepository,
    private readonly cloudflare: CloudflareClient,
    private readonly now: () => Date = () => new Date(),
  ) {}

  async enroll(input: EnrollmentInput): Promise<{
    agentId: string;
    cameraId: string;
    connectorToken: string;
  }> {
    if (
      !isCanonicalBase64Url(input.publicKey, 32) ||
      input.localCameraId !== "pc-webcam-01"
    ) {
      throw new PetCareError(400, "invalid_request");
    }

    const now = this.now();
    const nowIso = now.toISOString();
    const codeHash = await hashEnrollmentCode(input.code);
    await this.petcare.checkRateLimit(
      input.connectingIp,
      "enroll-ip",
      10,
      600,
      now,
    );
    await this.petcare.checkRateLimit(
      codeHash,
      "enroll-code",
      5,
      600,
      now,
    );

    let homeId: string | undefined;
    let agentId: string | undefined;
    let ledger: ResourceLedger | undefined;
    let leaseId: string | undefined;
    let consumed = false;
    try {
      homeId = (await this.petcare.findEnrollmentHome(codeHash, nowIso)).homeId;
      const existing = await this.petcare.getTunnelLedger(homeId);
      if (existing && existing.status !== "provisioning") {
        throw new PetCareError(
          existing.status === "cleanup_pending" ||
            existing.status === "revocation_pending"
            ? 503
            : 409,
          existing.status === "cleanup_pending" ||
            existing.status === "revocation_pending"
            ? "enrollment_retryable"
            : "enrollment_rejected",
        );
      }

      const generated = ids();
      agentId = existing?.agentId ?? generated.agentId;
      const cameraId = generated.cameraId;
      const route = await this.petcare.reserveTunnel(
        homeId,
        agentId,
        codeHash,
        nowIso,
      );
      leaseId = crypto.randomUUID();
      await this.petcare.claimTunnelProvisioning(
        homeId,
        agentId,
        leaseId,
        nowIso,
        new Date(now.getTime() + PROVISIONING_LEASE_MS).toISOString(),
      );
      ledger = ledgerFrom(route);
      const names = provisioningResourceNames(
        homeId,
        agentId,
        this.cloudflare.zoneName,
      );
      const hostname = names.hostname;
      const tunnelOrigin = `https://${hostname}`;
      if (!isConsistentLedger(ledger, tunnelOrigin)) {
        throw new PetCareError(503, "enrollment_retryable");
      }

      if (!ledger.tunnelId) {
        ledger.tunnelId = remoteString(
          (await this.cloudflare.findTunnelByName(names.tunnelName))?.id ??
            (await this.cloudflare.createTunnel(names.tunnelName)).id,
        );
        await this.petcare.updateTunnelResource(
          homeId,
          agentId,
          { tunnelId: ledger.tunnelId },
          this.now().toISOString(),
          leaseId,
        );
        await this.renewLease(homeId, agentId, leaseId);
      }
      if (!ledger.dnsRecordId) {
        ledger.dnsRecordId = remoteString(
          (
            await this.cloudflare.findDnsRecordByHostname(
              hostname,
              ledger.tunnelId,
            )
          )?.id ??
            (await this.cloudflare.createDnsRecord(hostname, ledger.tunnelId))
              .id,
        );
        ledger.tunnelOrigin = tunnelOrigin;
        await this.petcare.updateTunnelResource(
          homeId,
          agentId,
          {
            dnsRecordId: ledger.dnsRecordId,
            tunnelOrigin: ledger.tunnelOrigin,
          },
          this.now().toISOString(),
          leaseId,
        );
        await this.renewLease(homeId, agentId, leaseId);
      }
      if (!ledger.accessAppId) {
        const app =
          (await this.cloudflare.findAccessAppByDomain(
            hostname,
            names.accessName,
          )) ??
          (await this.cloudflare.createAccessApp(hostname, names.accessName));
        ledger.accessAppId = remoteString(app.id);
        ledger.accessAud = remoteString(app.aud);
        await this.petcare.updateTunnelResource(
          homeId,
          agentId,
          {
            accessAppId: ledger.accessAppId,
            accessAud: ledger.accessAud,
          },
          this.now().toISOString(),
          leaseId,
        );
        await this.renewLease(homeId, agentId, leaseId);
      }
      if (!ledger.accessPolicyId) {
        ledger.accessPolicyId = remoteString(
          (
            await this.cloudflare.findAccessPolicyByName(ledger.accessAppId)
          )?.id ??
            (await this.cloudflare.createAccessPolicy(ledger.accessAppId)).id,
        );
        await this.petcare.updateTunnelResource(
          homeId,
          agentId,
          { accessPolicyId: ledger.accessPolicyId },
          this.now().toISOString(),
          leaseId,
        );
        await this.renewLease(homeId, agentId, leaseId);
      }
      if (!ledger.accessAud || !ledger.tunnelOrigin) {
        throw new PetCareError(503, "enrollment_retryable");
      }

      await this.cloudflare.configureTunnel(
        ledger.tunnelId,
        hostname,
        ledger.accessAud,
      );
      await this.renewLease(homeId, agentId, leaseId);
      const preTokenNow = this.now();
      const stillValid = await this.petcare.findEnrollmentHome(
        codeHash,
        preTokenNow.toISOString(),
      );
      if (stillValid.homeId !== homeId) {
        throw new PetCareError(409, "enrollment_rejected");
      }
      const connectorToken = remoteString(
        await this.cloudflare.getConnectorToken(ledger.tunnelId),
      );
      await this.renewLease(homeId, agentId, leaseId);
      const consumedAt = this.now();
      await this.tenants.consumeEnrollment({
        codeHash,
        consumedAt: consumedAt.toISOString(),
        agent: { id: agentId, publicKey: input.publicKey, tunnelOrigin },
        camera: { id: cameraId, localCameraId: input.localCameraId },
      });
      consumed = true;
      const activationNow = this.now();
      await this.petcare.markActivationPending(
        homeId,
        agentId,
        new Date(activationNow.getTime() + ACTIVATION_TTL_MS).toISOString(),
        activationNow.toISOString(),
        leaseId,
      );
      return { agentId, cameraId, connectorToken };
    } catch (error) {
      if (!consumed && homeId && agentId && ledger && leaseId) {
        await this.rollbackProvisioning(
          homeId,
          agentId,
          ledger,
          leaseId,
        );
      }
      if (
        error instanceof EnrollmentRejectedError ||
        (error instanceof PetCareError && error.status === 409)
      ) {
        throw new PetCareError(409, "enrollment_rejected");
      }
      if (
        !consumed &&
        error instanceof PetCareError &&
        (error.status === 400 || error.status === 429)
      ) {
        throw error;
      }
      throw new PetCareError(503, "enrollment_retryable");
    }
  }

  async revoke(
    ownerSub: string,
    now: Date,
  ): Promise<{ status: "revoked" | "revocation_pending" }> {
    const home = await this.tenants.requireHome(ownerSub);
    const route = await this.petcare.getTunnelLedger(home.id);
    if (!route) throw new PetCareError(404, "not_found");
    const leaseId = crypto.randomUUID();
    const ledger = await this.petcare.requestRevocation(
      home.id,
      route.agentId,
      leaseId,
      now.toISOString(),
      new Date(now.getTime() + PROVISIONING_LEASE_MS).toISOString(),
    );
    const deleted = await this.deleteRemote(ledger);
    if (!deleted) {
      await this.petcare.markTunnelState(
        home.id,
        route.agentId,
        leaseId,
        "revocation_pending",
        "revocation_pending",
        now.toISOString(),
        "remote_delete_failed",
      );
      return { status: "revocation_pending" };
    }
    try {
      await this.petcare.updateTunnelResource(
        home.id,
        route.agentId,
        {
          tunnelId: null,
          tunnelOrigin: null,
          dnsRecordId: null,
          accessAppId: null,
          accessAud: null,
          accessPolicyId: null,
        },
        now.toISOString(),
        leaseId,
      );
      await this.petcare.markTunnelState(
        home.id,
        route.agentId,
        leaseId,
        "revocation_pending",
        "revoked",
        now.toISOString(),
      );
      return { status: "revoked" };
    } catch {
      await this.petcare.markTunnelState(
        home.id,
        route.agentId,
        leaseId,
        "revocation_pending",
        "revocation_pending",
        now.toISOString(),
        "resource_state_write_failed",
      );
      return { status: "revocation_pending" };
    }
  }

  private async rollbackProvisioning(
    homeId: string,
    agentId: string,
    ledger: ResourceLedger,
    leaseId: string,
  ): Promise<void> {
    let cleanupStored = false;
    for (let attempt = 0; attempt < CLEANUP_WRITE_ATTEMPTS; attempt += 1) {
      try {
        await this.petcare.recordCleanupPending(
          homeId,
          agentId,
          ledger,
          "provisioning_failed",
          leaseId,
          this.now().toISOString(),
        );
        cleanupStored = true;
        break;
      } catch {}
    }

    if (!cleanupStored) {
      throw new PetCareError(503, "enrollment_retryable");
    }
    await this.deleteRemote(ledger);
  }

  private async renewLease(
    homeId: string,
    agentId: string,
    leaseId: string,
  ): Promise<void> {
    const now = this.now();
    await this.petcare.renewTunnelLease(
      homeId,
      agentId,
      leaseId,
      now.toISOString(),
      new Date(now.getTime() + PROVISIONING_LEASE_MS).toISOString(),
    );
  }

  private async deleteRemote(ledger: ResourceLedger): Promise<boolean> {
    let ok = true;
    const attempt = async (operation: () => Promise<void>) => {
      try {
        await operation();
      } catch {
        ok = false;
      }
    };
    if (ledger.accessAppId && ledger.accessPolicyId) {
      await attempt(() =>
        this.cloudflare.deleteAccessPolicy(
          ledger.accessAppId!,
          ledger.accessPolicyId!,
        ),
      );
    }
    if (ledger.accessAppId) {
      await attempt(() => this.cloudflare.deleteAccessApp(ledger.accessAppId!));
    }
    if (ledger.dnsRecordId) {
      await attempt(() => this.cloudflare.deleteDnsRecord(ledger.dnsRecordId!));
    }
    if (ledger.tunnelId) {
      await attempt(() => this.cloudflare.deleteTunnel(ledger.tunnelId!));
    }
    return ok;
  }
}
