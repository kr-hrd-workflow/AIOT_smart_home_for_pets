import { CloudflareClient } from "./cloudflare";
import type { PetCareEnv } from "./env";
import { readPetCareConfig } from "./env";
import { provisioningResourceNames } from "./enrollment";
import {
  PetCareRepository,
  type TunnelRouteRecord,
} from "./repository";

const R2_CURSOR = "r2_clips_orphan_scan";
const D1_CLIP_CURSOR = "d1_clips_stale_scan";

export type ReconcileResult = {
  expiredClips: number;
  orphanObjects: number;
  staleMetadata: number;
  expiredNonces: number;
  expiredRateLimits: number;
  cleanedTunnels: number;
  cleanedTenants: number;
  retryableFailures: number;
};

function emptyResult(): ReconcileResult {
  return {
    expiredClips: 0,
    orphanObjects: 0,
    staleMetadata: 0,
    expiredNonces: 0,
    expiredRateLimits: 0,
    cleanedTunnels: 0,
    cleanedTenants: 0,
    retryableFailures: 0,
  };
}

async function runStep(
  result: ReconcileResult,
  step: () => Promise<void>,
) {
  try {
    await step();
  } catch {
    result.retryableFailures += 1;
  }
}

async function retryQueuedObjects(
  env: PetCareEnv,
  repository: PetCareRepository,
  now: string,
  result: ReconcileResult,
) {
  for (const job of await repository.listObjectDeletionJobs(100)) {
    try {
      await env.CLIPS.delete(job.objectKey);
      await repository.completeObjectDeletion(job.homeId, job.objectKey);
    } catch {
      result.retryableFailures += 1;
      await repository
        .recordObjectDeletionFailure(job.homeId, job.objectKey, now)
        .catch(() => {
          result.retryableFailures += 1;
        });
    }
  }
}

async function expireClipMetadata(
  repository: PetCareRepository,
  now: string,
  result: ReconcileResult,
) {
  try {
    result.expiredClips = await repository.queueExpiredClips(now, 100);
  } catch {
    result.retryableFailures += 1;
  }
}

async function removeStaleMetadata(
  env: PetCareEnv,
  repository: PetCareRepository,
  now: string,
  result: ReconcileResult,
) {
  const cursor = await repository.getReconcileCursor(D1_CLIP_CURSOR);
  const rows = await repository.listUnexpiredClipObjects(now, cursor, 100);
  for (const row of rows) {
    try {
      if (await env.CLIPS.head(row.objectKey)) continue;
      if (await repository.deleteClipMetadataByObjectKey(row.objectKey)) {
        result.staleMetadata += 1;
      }
    } catch {
      result.retryableFailures += 1;
    }
  }
  await repository.setReconcileCursor(
    D1_CLIP_CURSOR,
    rows.length === 100 ? rows.at(-1)!.id : null,
    now,
  );
}

async function deleteExpiredWindows(
  repository: PetCareRepository,
  now: string,
  result: ReconcileResult,
) {
  try {
    result.expiredNonces = await repository.deleteExpiredNonces(now, 500);
  } catch {
    result.retryableFailures += 1;
    return;
  }
  const remaining = 500 - result.expiredNonces;
  if (!remaining) return;
  try {
    result.expiredRateLimits = await repository.deleteExpiredRateLimits(
      now,
      remaining,
    );
  } catch {
    result.retryableFailures += 1;
  }
}

async function scanR2Orphans(
  env: PetCareEnv,
  repository: PetCareRepository,
  now: Date,
  result: ReconcileResult,
) {
  const cursor = await repository.getReconcileCursor(R2_CURSOR);
  let page: Awaited<ReturnType<PetCareEnv["CLIPS"]["list"]>>;
  try {
    page = await env.CLIPS.list({
      prefix: "clips/",
      limit: 100,
      ...(cursor ? { cursor } : {}),
    });
  } catch {
    result.retryableFailures += 1;
    await repository.setReconcileCursor(R2_CURSOR, null, now.toISOString()).catch(() => {
      result.retryableFailures += 1;
    });
    return;
  }

  const orphanCutoff = now.getTime() - 10 * 60 * 1000;
  for (const object of page.objects) {
    try {
      const referenced = await repository.hasClipOrDeletionJob(object.key);
      if (referenced || object.uploaded.getTime() > orphanCutoff) continue;
      await env.CLIPS.delete(object.key);
      result.orphanObjects += 1;
    } catch {
      result.retryableFailures += 1;
    }
  }
  await repository.setReconcileCursor(
    R2_CURSOR,
    page.truncated ? page.cursor : null,
    now.toISOString(),
  );
}

async function discoverRouteResources(
  route: TunnelRouteRecord,
  cloudflare: CloudflareClient,
  zoneName: string,
) {
  const names = provisioningResourceNames(route.homeId, route.agentId, zoneName);
  const tunnel = route.tunnelId
    ? null
    : await cloudflare.findTunnelByName(names.tunnelName);
  const tunnelId = route.tunnelId ?? tunnel?.id ?? null;
  const dns =
    route.dnsRecordId || !tunnelId
      ? null
      : await cloudflare.findDnsRecordByHostname(names.hostname, tunnelId);
  const app = route.accessAppId
    ? null
    : await cloudflare.findAccessAppByDomain(names.hostname, names.accessName);
  const accessAppId = route.accessAppId ?? app?.id ?? null;
  const policy =
    route.accessPolicyId || !accessAppId
      ? null
      : await cloudflare.findAccessPolicyByName(accessAppId);
  return {
    accessAppId,
    accessPolicyId: route.accessPolicyId ?? policy?.id ?? null,
    dnsRecordId: route.dnsRecordId ?? dns?.id ?? null,
    tunnelId,
  };
}

async function cleanRemoteResources(
  env: PetCareEnv,
  repository: PetCareRepository,
  now: string,
  result: ReconcileResult,
) {
  const provisioningCutoff = new Date(
    new Date(now).getTime() - 10 * 60 * 1000,
  ).toISOString();
  const leaseId = crypto.randomUUID();
  const leaseExpiresAt = new Date(
    new Date(now).getTime() + 120_000,
  ).toISOString();
  const routes = await repository.claimPendingTunnelCleanup(
    now,
    provisioningCutoff,
    leaseId,
    leaseExpiresAt,
    25,
  );
  const cloudflare = new CloudflareClient(readPetCareConfig(env));
  const blockedTenantHomes = new Set<string>();

  for (const route of routes) {
    const operationNow = now;
    const renewedLeaseExpiresAt = new Date(
      new Date(operationNow).getTime() + 120_000,
    ).toISOString();
    try {
      await repository.renewTunnelLease(
        route.homeId,
        route.agentId,
        leaseId,
        operationNow,
        renewedLeaseExpiresAt,
      );
      const resources = await discoverRouteResources(
        route,
        cloudflare,
        env.CF_ZONE_NAME,
      );
      if (resources.accessPolicyId) {
        if (!resources.accessAppId) throw new Error("invalid_remote_ledger");
        await cloudflare.deleteAccessPolicy(
          resources.accessAppId,
          resources.accessPolicyId,
        );
        await repository.clearTunnelResource(
          route.homeId,
          route.agentId,
          leaseId,
          "accessPolicyId",
          operationNow,
        );
      }
      if (resources.accessAppId) {
        await cloudflare.deleteAccessApp(resources.accessAppId);
        await repository.clearTunnelResource(
          route.homeId,
          route.agentId,
          leaseId,
          "accessAppId",
          operationNow,
        );
      }
      if (resources.dnsRecordId) {
        await cloudflare.deleteDnsRecord(resources.dnsRecordId);
        await repository.clearTunnelResource(
          route.homeId,
          route.agentId,
          leaseId,
          "dnsRecordId",
          operationNow,
        );
      }
      if (resources.tunnelId) {
        await cloudflare.deleteTunnel(resources.tunnelId);
        await repository.clearTunnelResource(
          route.homeId,
          route.agentId,
          leaseId,
          "tunnelId",
          operationNow,
        );
      }
      await repository.clearTunnelResource(
        route.homeId,
        route.agentId,
        leaseId,
        "tunnelOrigin",
        operationNow,
      );
      await repository.clearTunnelResource(
        route.homeId,
        route.agentId,
        leaseId,
        "accessAud",
        operationNow,
      );
      if (!route.bound && route.status === "cleanup_pending") {
        await repository.deleteTunnelRoute(
          route.homeId,
          route.agentId,
          leaseId,
          operationNow,
        );
      } else {
        await repository.markTunnelState(
          route.homeId,
          route.agentId,
          leaseId,
          route.status,
          "revoked",
          operationNow,
        );
      }
      result.cleanedTunnels += 1;
    } catch {
      result.retryableFailures += 1;
      blockedTenantHomes.add(route.homeId);
      await repository
        .markTunnelState(
          route.homeId,
          route.agentId,
          leaseId,
          route.status,
          route.status,
          operationNow,
          "remote_cleanup_failed",
        )
        .catch(() => undefined);
      continue;
    }
  }

  const tenants = await repository.listTenantCleanup(25 - routes.length);
  for (const tenant of tenants) {
    if (blockedTenantHomes.has(tenant.homeId)) {
      await repository
        .markTenantCleanupError(
          tenant.ownerSub,
          tenant.homeId,
          now,
          "tenant_cleanup_failed",
        )
        .catch(() => {
          result.retryableFailures += 1;
        });
      continue;
    }
    try {
      await repository.completeTenantCleanup(tenant.ownerSub, tenant.homeId, now);
      result.cleanedTenants += 1;
    } catch {
      result.retryableFailures += 1;
      await repository
        .markTenantCleanupError(
          tenant.ownerSub,
          tenant.homeId,
          now,
          "tenant_cleanup_failed",
        )
        .catch(() => {
          result.retryableFailures += 1;
        });
    }
  }
}

export async function reconcilePetCare(
  env: PetCareEnv,
  now: Date,
): Promise<ReconcileResult> {
  const result = emptyResult();
  const nowIso = now.toISOString();
  const repository = new PetCareRepository(env.DB);
  await runStep(result, () =>
    retryQueuedObjects(env, repository, nowIso, result),
  );
  await runStep(result, () => expireClipMetadata(repository, nowIso, result));
  await runStep(result, () =>
    removeStaleMetadata(env, repository, nowIso, result),
  );
  await runStep(result, () => deleteExpiredWindows(repository, nowIso, result));
  await runStep(result, () => scanR2Orphans(env, repository, now, result));
  await runStep(result, () =>
    cleanRemoteResources(env, repository, nowIso, result),
  );
  return result;
}
