// @vitest-environment node

import { beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("cloudflare:workers", () => ({ env: {} }));

import { EnrollmentRejectedError } from "../lib/tenancy/repository";
import {
  EnrollmentProvisioningService,
  provisioningResourceNames,
} from "../lib/petcare/enrollment";
import { PetCareError } from "../lib/petcare/errors";

const NOW = new Date("2026-07-20T03:00:00.000Z");
const CODE = "AQEBAQEBAQEBAQEBAQEBAQ";
const PUBLIC_KEY = "INsaaxAGzWH6psMGL2Y-gJaRBGfO4on_-P_-e8Qiais";
const HOSTNAME = "home-homea-111111111111.agents.example.com";

function setup(clock: () => Date = () => NOW) {
  const tenants = {
    requireHome: vi.fn(async () => ({ id: "home-a" })),
    consumeEnrollment: vi.fn(async (input) => ({
      homeId: "home-a",
      agentId: input.agent.id,
      cameraId: input.camera.id,
    })),
  };
  const route = {
    homeId: "home-a",
    agentId: "agent_fixed",
    status: "provisioning",
    activationExpiresAt: null,
    tunnelId: null,
    tunnelOrigin: null,
    dnsRecordId: null,
    accessAppId: null,
    accessAud: null,
    accessPolicyId: null,
  };
  const petcare = {
    checkRateLimit: vi.fn(async () => undefined),
    findEnrollmentHome: vi.fn(async () => ({ homeId: "home-a" })),
    getTunnelLedger: vi.fn(async (): Promise<typeof route | null> => null),
    claimTunnelProvisioning: vi.fn(async () => undefined),
    renewTunnelLease: vi.fn(async () => undefined),
    reserveTunnel: vi.fn(async (_homeId, agentId) => ({
      ...route,
      agentId,
    })),
    updateTunnelResource: vi.fn(async (_homeId, _agentId, patch) => {
      Object.assign(route, patch);
    }),
    recordCleanupPending: vi.fn(async () => undefined),
    markActivationPending: vi.fn(async () => undefined),
    requestRevocation: vi.fn(async () => ({ ...route })),
    markTunnelState: vi.fn(async () => undefined),
    deleteTunnelRoute: vi.fn(async () => undefined),
  };
  const cloudflare = {
    zoneName: "agents.example.com",
    findTunnelByName: vi.fn(async () => null),
    findDnsRecordByHostname: vi.fn(async () => null),
    findAccessAppByDomain: vi.fn(async () => null),
    findAccessPolicyByName: vi.fn(async () => null),
    createTunnel: vi.fn(async () => ({ id: "tunnel-1" })),
    createDnsRecord: vi.fn(async () => ({ id: "dns-1" })),
    createAccessApp: vi.fn(async () => ({ id: "app-1", aud: "aud-1" })),
    createAccessPolicy: vi.fn(async () => ({ id: "policy-1" })),
    configureTunnel: vi.fn(async () => undefined),
    getConnectorToken: vi.fn(async () => "connector-once"),
    deleteAccessPolicy: vi.fn(async () => undefined),
    deleteAccessApp: vi.fn(async () => undefined),
    deleteDnsRecord: vi.fn(async () => undefined),
    deleteTunnel: vi.fn(async () => undefined),
  };
  const service = new EnrollmentProvisioningService(
    tenants as never,
    petcare as never,
    cloudflare as never,
    clock,
  );
  return { service, tenants, petcare, cloudflare, route };
}

describe("EnrollmentProvisioningService", () => {
  beforeEach(() => vi.restoreAllMocks());

  it("freezes opaque deterministic resource names for reconciliation", () => {
    expect(
      provisioningResourceNames(
        "home-a",
        "agent_11111111-1111-4111-8111-111111111111",
        "agents.example.com",
      ),
    ).toEqual({
      tunnelName: "petcare-home-homea-111111111111",
      hostname: HOSTNAME,
      accessName: "PetCare home-homea-111111111111",
    });
  });

  it("persists every remote ID before consuming and returns the token once", async () => {
    vi.spyOn(crypto, "randomUUID")
      .mockReturnValueOnce("11111111-1111-4111-8111-111111111111")
      .mockReturnValueOnce("22222222-2222-4222-8222-222222222222")
      .mockReturnValueOnce("33333333-3333-4333-8333-333333333333");
    const { service, tenants, petcare, cloudflare } = setup();

    await expect(
      service.enroll({
        code: CODE,
        publicKey: PUBLIC_KEY,
        localCameraId: "pc-webcam-01",
        connectingIp: "203.0.113.10",
      }),
    ).resolves.toEqual({
      agentId: "agent_11111111-1111-4111-8111-111111111111",
      cameraId: "camera_22222222-2222-4222-8222-222222222222",
      connectorToken: "connector-once",
    });

    expect(petcare.checkRateLimit).toHaveBeenNthCalledWith(
      1,
      "203.0.113.10",
      "enroll-ip",
      10,
      600,
      NOW,
    );
    expect(petcare.reserveTunnel).toHaveBeenCalledWith(
      "home-a",
      "agent_11111111-1111-4111-8111-111111111111",
      expect.stringMatching(/^[0-9a-f]{64}$/),
      NOW.toISOString(),
    );
    expect(petcare.checkRateLimit).toHaveBeenNthCalledWith(
      2,
      expect.stringMatching(/^[0-9a-f]{64}$/),
      "enroll-code",
      5,
      600,
      NOW,
    );
    expect(petcare.updateTunnelResource.mock.calls.map((call) => call[2])).toEqual([
      { tunnelId: "tunnel-1" },
      {
        dnsRecordId: "dns-1",
        tunnelOrigin: `https://${HOSTNAME}`,
      },
      { accessAppId: "app-1", accessAud: "aud-1" },
      { accessPolicyId: "policy-1" },
    ]);
    expect(cloudflare.configureTunnel).toHaveBeenCalledBefore(
      cloudflare.getConnectorToken,
    );
    expect(cloudflare.createTunnel).toHaveBeenCalledWith(
      "petcare-home-homea-111111111111",
    );
    expect(cloudflare.createDnsRecord).toHaveBeenCalledWith(
      HOSTNAME,
      "tunnel-1",
    );
    expect(cloudflare.getConnectorToken).toHaveBeenCalledBefore(
      tenants.consumeEnrollment,
    );
    expect(tenants.consumeEnrollment).toHaveBeenCalledWith({
      codeHash: expect.stringMatching(/^[0-9a-f]{64}$/),
      consumedAt: NOW.toISOString(),
      agent: {
        id: "agent_11111111-1111-4111-8111-111111111111",
        publicKey: PUBLIC_KEY,
        tunnelOrigin: `https://${HOSTNAME}`,
      },
      camera: {
        id: "camera_22222222-2222-4222-8222-222222222222",
        localCameraId: "pc-webcam-01",
      },
    });
    expect(petcare.markActivationPending).toHaveBeenCalledWith(
      "home-a",
      "agent_11111111-1111-4111-8111-111111111111",
      "2026-07-20T03:10:00.000Z",
      NOW.toISOString(),
      "33333333-3333-4333-8333-333333333333",
    );
    expect(
      JSON.stringify(
        [
          ...Object.values(petcare).flatMap(
            (mock) => mock.mock?.calls ?? [],
          ),
          ...Object.values(tenants).flatMap(
            (mock) => mock.mock?.calls ?? [],
          ),
        ],
      ),
    ).not.toContain("connector-once");
  });

  it("allows exactly one concurrent provisioning lease and one token", async () => {
    const { service, petcare, cloudflare, route } = setup();
    const existing = { ...route, agentId: "agent_existing" };
    petcare.getTunnelLedger.mockResolvedValue(existing);
    petcare.reserveTunnel.mockResolvedValue(existing);
    let claimed = false;
    petcare.claimTunnelProvisioning.mockImplementation(async () => {
      if (claimed) throw new PetCareError(503, "enrollment_retryable");
      claimed = true;
    });
    let releaseTunnel!: (value: { id: string }) => void;
    cloudflare.createTunnel.mockImplementation(
      () =>
        new Promise<{ id: string }>((resolve) => {
          releaseTunnel = resolve;
        }),
    );

    const winner = service.enroll({
      code: CODE,
      publicKey: PUBLIC_KEY,
      localCameraId: "pc-webcam-01",
      connectingIp: "203.0.113.10",
    });
    await vi.waitFor(() => expect(cloudflare.createTunnel).toHaveBeenCalledTimes(1));
    const loser = service.enroll({
      code: CODE,
      publicKey: PUBLIC_KEY,
      localCameraId: "pc-webcam-01",
      connectingIp: "203.0.113.10",
    });
    await expect(loser).rejects.toMatchObject({
      status: 503,
      code: "enrollment_retryable",
    });
    releaseTunnel({ id: "tunnel-1" });
    await expect(winner).resolves.toMatchObject({
      agentId: "agent_existing",
      connectorToken: "connector-once",
    });
    expect(cloudflare.createTunnel).toHaveBeenCalledTimes(1);
    expect(cloudflare.getConnectorToken).toHaveBeenCalledTimes(1);
    expect(petcare.recordCleanupPending).not.toHaveBeenCalled();
  });

  it("claims and resumes a stale consistent partial ledger", async () => {
    const { service, petcare, cloudflare, route } = setup();
    const partial = {
      ...route,
      agentId: "agent_existing",
      tunnelId: "tunnel-existing",
    };
    petcare.getTunnelLedger.mockResolvedValueOnce(partial);
    petcare.reserveTunnel.mockResolvedValueOnce(partial);

    await expect(
      service.enroll({
        code: CODE,
        publicKey: PUBLIC_KEY,
        localCameraId: "pc-webcam-01",
        connectingIp: "203.0.113.10",
      }),
    ).resolves.toMatchObject({
      agentId: "agent_existing",
      connectorToken: "connector-once",
    });
    expect(petcare.claimTunnelProvisioning).toHaveBeenCalledWith(
      "home-a",
      "agent_existing",
      expect.any(String),
      NOW.toISOString(),
      "2026-07-20T03:02:00.000Z",
    );
    expect(cloudflare.createTunnel).not.toHaveBeenCalled();
    expect(cloudflare.createDnsRecord).toHaveBeenCalledWith(
      "home-homea-existing.agents.example.com",
      "tunnel-existing",
    );
  });

  it("reclaims deterministically discovered orphan resources without duplicates", async () => {
    vi.spyOn(crypto, "randomUUID")
      .mockReturnValueOnce("11111111-1111-4111-8111-111111111111")
      .mockReturnValueOnce("22222222-2222-4222-8222-222222222222")
      .mockReturnValueOnce("33333333-3333-4333-8333-333333333333");
    const { service, petcare, cloudflare } = setup();
    cloudflare.findTunnelByName.mockResolvedValueOnce({ id: "tunnel-orphan" });
    cloudflare.findDnsRecordByHostname.mockResolvedValueOnce({ id: "dns-orphan" });
    cloudflare.findAccessAppByDomain.mockResolvedValueOnce({
      id: "app-orphan",
      aud: "aud-orphan",
    });
    cloudflare.findAccessPolicyByName.mockResolvedValueOnce({
      id: "policy-orphan",
    });

    await expect(
      service.enroll({
        code: CODE,
        publicKey: PUBLIC_KEY,
        localCameraId: "pc-webcam-01",
        connectingIp: "203.0.113.10",
      }),
    ).resolves.toMatchObject({ connectorToken: "connector-once" });
    expect(cloudflare.findTunnelByName).toHaveBeenCalledWith(
      "petcare-home-homea-111111111111",
    );
    expect(cloudflare.findDnsRecordByHostname).toHaveBeenCalledWith(
      HOSTNAME,
      "tunnel-orphan",
    );
    expect(cloudflare.findAccessAppByDomain).toHaveBeenCalledWith(
      HOSTNAME,
      "PetCare home-homea-111111111111",
    );
    expect(cloudflare.findAccessPolicyByName).toHaveBeenCalledWith("app-orphan");
    expect(cloudflare.createTunnel).not.toHaveBeenCalled();
    expect(cloudflare.createDnsRecord).not.toHaveBeenCalled();
    expect(cloudflare.createAccessApp).not.toHaveBeenCalled();
    expect(cloudflare.createAccessPolicy).not.toHaveBeenCalled();
    expect(petcare.updateTunnelResource.mock.calls.map((call) => call[2])).toEqual([
      { tunnelId: "tunnel-orphan" },
      { dnsRecordId: "dns-orphan", tunnelOrigin: `https://${HOSTNAME}` },
      { accessAppId: "app-orphan", accessAud: "aud-orphan" },
      { accessPolicyId: "policy-orphan" },
    ]);
  });

  it("rejects a consumed code before any Cloudflare request", async () => {
    const { service, petcare, cloudflare } = setup();
    petcare.findEnrollmentHome.mockRejectedValue(
      new EnrollmentRejectedError("Enrollment rejected"),
    );

    await expect(
      service.enroll({
        code: CODE,
        publicKey: PUBLIC_KEY,
        localCameraId: "pc-webcam-01",
        connectingIp: "203.0.113.10",
      }),
    ).rejects.toMatchObject({ status: 409, code: "enrollment_rejected" });
    expect(cloudflare.createTunnel).not.toHaveBeenCalled();
    expect(cloudflare.getConnectorToken).not.toHaveBeenCalled();
  });

  it("passes the exact instant to expiry lookup and rejects equality", async () => {
    const { service, petcare, cloudflare } = setup();
    petcare.findEnrollmentHome.mockImplementation(async (_hash, at) => {
      expect(at).toBe("2026-07-20T03:00:00.000Z");
      throw new PetCareError(409, "enrollment_rejected");
    });
    await expect(
      service.enroll({
        code: CODE,
        publicKey: PUBLIC_KEY,
        localCameraId: "pc-webcam-01",
        connectingIp: "203.0.113.10",
      }),
    ).rejects.toMatchObject({ status: 409, code: "enrollment_rejected" });
    expect(cloudflare.createTunnel).not.toHaveBeenCalled();
  });

  it("rechecks exact code expiry after provisioning before token lookup", async () => {
    let clock = NOW;
    const { service, petcare, cloudflare } = setup(() => clock);
    petcare.findEnrollmentHome.mockImplementation(async (_hash, at) => {
      if (at >= "2026-07-20T03:10:00.000Z") {
        throw new PetCareError(409, "enrollment_rejected");
      }
      return { homeId: "home-a" };
    });
    cloudflare.configureTunnel.mockImplementation(async () => {
      clock = new Date("2026-07-20T03:10:00.000Z");
    });

    await expect(
      service.enroll({
        code: CODE,
        publicKey: PUBLIC_KEY,
        localCameraId: "pc-webcam-01",
        connectingIp: "203.0.113.10",
      }),
    ).rejects.toMatchObject({ status: 409, code: "enrollment_rejected" });
    expect(petcare.findEnrollmentHome).toHaveBeenLastCalledWith(
      expect.any(String),
      "2026-07-20T03:10:00.000Z",
    );
    expect(cloudflare.getConnectorToken).not.toHaveBeenCalled();
  });

  it("starts the full activation window from the fresh consume time", async () => {
    let clock = NOW;
    const { service, tenants, petcare, cloudflare } = setup(() => clock);
    cloudflare.getConnectorToken.mockImplementation(async () => {
      clock = new Date("2026-07-20T03:04:00.000Z");
      return "connector-once";
    });

    await expect(
      service.enroll({
        code: CODE,
        publicKey: PUBLIC_KEY,
        localCameraId: "pc-webcam-01",
        connectingIp: "203.0.113.10",
      }),
    ).resolves.toMatchObject({ connectorToken: "connector-once" });
    expect(tenants.consumeEnrollment).toHaveBeenCalledWith(
      expect.objectContaining({ consumedAt: "2026-07-20T03:04:00.000Z" }),
    );
    expect(petcare.markActivationPending).toHaveBeenCalledWith(
      "home-a",
      expect.stringMatching(/^agent_/),
      "2026-07-20T03:14:00.000Z",
      "2026-07-20T03:04:00.000Z",
      expect.any(String),
    );
  });

  it("rolls back in reverse order when consume collides", async () => {
    const { service, tenants, petcare, cloudflare } = setup();
    tenants.consumeEnrollment.mockRejectedValue(
      new EnrollmentRejectedError("Enrollment rejected"),
    );

    await expect(
      service.enroll({
        code: CODE,
        publicKey: PUBLIC_KEY,
        localCameraId: "pc-webcam-01",
        connectingIp: "203.0.113.10",
      }),
    ).rejects.toMatchObject({ status: 409, code: "enrollment_rejected" });
    expect([
      cloudflare.deleteAccessPolicy.mock.invocationCallOrder[0],
      cloudflare.deleteAccessApp.mock.invocationCallOrder[0],
      cloudflare.deleteDnsRecord.mock.invocationCallOrder[0],
      cloudflare.deleteTunnel.mock.invocationCallOrder[0],
    ]).toEqual([...cloudflare.deleteAccessPolicy.mock.invocationCallOrder,
      ...cloudflare.deleteAccessApp.mock.invocationCallOrder,
      ...cloudflare.deleteDnsRecord.mock.invocationCallOrder,
      ...cloudflare.deleteTunnel.mock.invocationCallOrder].sort((a, b) => a - b));
    expect(petcare.deleteTunnelRoute).not.toHaveBeenCalled();
  });

  it.each([1, 2, 3, 4])(
    "deletes every created resource when persistence step %s fails",
    async (failedWrite) => {
      const { service, petcare, cloudflare, route } = setup();
      let writes = 0;
      petcare.updateTunnelResource.mockImplementation(
        async (_homeId, _agentId, patch) => {
          writes += 1;
          if (writes === failedWrite) throw new Error("d1 unavailable");
          Object.assign(route, patch);
        },
      );

      await expect(
        service.enroll({
          code: CODE,
          publicKey: PUBLIC_KEY,
          localCameraId: "pc-webcam-01",
          connectingIp: "203.0.113.10",
        }),
      ).rejects.toMatchObject({ status: 503, code: "enrollment_retryable" });

      expect(cloudflare.deleteTunnel).toHaveBeenCalledTimes(1);
      expect(cloudflare.deleteDnsRecord).toHaveBeenCalledTimes(
        failedWrite >= 2 ? 1 : 0,
      );
      expect(cloudflare.deleteAccessApp).toHaveBeenCalledTimes(
        failedWrite >= 3 ? 1 : 0,
      );
      expect(cloudflare.deleteAccessPolicy).toHaveBeenCalledTimes(
        failedWrite >= 4 ? 1 : 0,
      );
      expect(petcare.deleteTunnelRoute).not.toHaveBeenCalled();
      expect(petcare.recordCleanupPending).toHaveBeenCalledWith(
        "home-a",
        expect.stringMatching(/^agent_/),
        expect.objectContaining({ tunnelId: "tunnel-1" }),
        "provisioning_failed",
        expect.any(String),
        NOW.toISOString(),
      );
    },
  );

  it.each([
    "createTunnel",
    "createDnsRecord",
    "createAccessApp",
    "createAccessPolicy",
  ] as const)("durably rolls back a %s provider failure", async (method) => {
    const { service, petcare, cloudflare } = setup();
    cloudflare[method].mockRejectedValueOnce(new Error("provider secret"));

    await expect(
      service.enroll({
        code: CODE,
        publicKey: PUBLIC_KEY,
        localCameraId: "pc-webcam-01",
        connectingIp: "203.0.113.10",
      }),
    ).rejects.toMatchObject({ status: 503, code: "enrollment_retryable" });
    expect(petcare.recordCleanupPending).toHaveBeenCalledTimes(1);
    expect(petcare.deleteTunnelRoute).not.toHaveBeenCalled();
    const agentId = petcare.reserveTunnel.mock.calls[0][1] as string;
    const names = provisioningResourceNames(
      "home-a",
      agentId,
      "agents.example.com",
    );
    expect(names.tunnelName).toMatch(/^petcare-home-homea-[a-f0-9]{12}$/);
    expect(cloudflare.findTunnelByName).toHaveBeenCalledWith(names.tunnelName);
    expect(JSON.stringify(petcare.recordCleanupPending.mock.calls)).not.toContain(
      "provider secret",
    );
  });

  it("retains cleanup state when reverse deletion fails", async () => {
    const { service, petcare, cloudflare } = setup();
    cloudflare.createAccessPolicy.mockRejectedValue(new Error("provider secret"));
    cloudflare.deleteAccessApp.mockRejectedValue(new Error("provider secret"));

    await expect(
      service.enroll({
        code: CODE,
        publicKey: PUBLIC_KEY,
        localCameraId: "pc-webcam-01",
        connectingIp: "203.0.113.10",
      }),
    ).rejects.toMatchObject({ status: 503, code: "enrollment_retryable" });
    expect(petcare.recordCleanupPending).toHaveBeenCalledWith(
      "home-a",
      expect.stringMatching(/^agent_/),
      expect.objectContaining({
        tunnelId: "tunnel-1",
        dnsRecordId: "dns-1",
        accessAppId: "app-1",
      }),
      "provisioning_failed",
      expect.any(String),
      NOW.toISOString(),
    );
  });

  it("does not let an unfenced rollback touch deterministic remote resources", async () => {
    const { service, petcare, cloudflare } = setup();
    petcare.updateTunnelResource.mockRejectedValueOnce(
      new Error("initial d1 write failed"),
    );
    petcare.recordCleanupPending.mockRejectedValue(new Error("d1 unavailable"));
    cloudflare.deleteTunnel.mockRejectedValue(new Error("provider unavailable"));

    await expect(
      service.enroll({
        code: CODE,
        publicKey: PUBLIC_KEY,
        localCameraId: "pc-webcam-01",
        connectingIp: "203.0.113.10",
      }),
    ).rejects.toMatchObject({ status: 503, code: "enrollment_retryable" });
    expect(petcare.recordCleanupPending).toHaveBeenCalledTimes(3);
    expect(cloudflare.deleteTunnel).not.toHaveBeenCalled();
    expect(cloudflare.findTunnelByName).toHaveBeenCalledWith(
      expect.stringMatching(/^petcare-home-homea-[a-f0-9]{12}$/),
    );
    expect(cloudflare.createTunnel).toHaveBeenCalledWith(
      expect.stringMatching(/^petcare-home-homea-[a-f0-9]{12}$/),
    );
  });

  it("leaves consumed provisioning for stale cleanup when activation cannot persist", async () => {
    const { service, tenants, petcare, cloudflare } = setup();
    let consumed = false;
    petcare.findEnrollmentHome.mockImplementation(async () => {
      if (consumed) throw new PetCareError(409, "enrollment_rejected");
      return { homeId: "home-a" };
    });
    tenants.consumeEnrollment.mockImplementation(async (input) => {
      consumed = true;
      return {
        homeId: "home-a",
        agentId: input.agent.id,
        cameraId: input.camera.id,
      };
    });
    petcare.markActivationPending.mockRejectedValue(new Error("d1 unavailable"));

    const input = {
      code: CODE,
      publicKey: PUBLIC_KEY,
      localCameraId: "pc-webcam-01",
      connectingIp: "203.0.113.10",
    };
    await expect(
      service.enroll(input),
    ).rejects.toMatchObject({ status: 503, code: "enrollment_retryable" });
    expect(petcare.requestRevocation).not.toHaveBeenCalled();
    expect(cloudflare.deleteTunnel).not.toHaveBeenCalled();
    expect(petcare.deleteTunnelRoute).not.toHaveBeenCalled();
    expect(petcare.recordCleanupPending).not.toHaveBeenCalled();
    expect(petcare.markTunnelState).not.toHaveBeenCalled();
    cloudflare.getConnectorToken.mockClear();
    cloudflare.createTunnel.mockClear();
    await expect(service.enroll(input)).rejects.toMatchObject({
      status: 409,
      code: "enrollment_rejected",
    });
    expect(cloudflare.getConnectorToken).not.toHaveBeenCalled();
    expect(cloudflare.createTunnel).not.toHaveBeenCalled();
  });

  it("does not contact Cloudflare after either enrollment rate limit rejects", async () => {
    const { service, petcare, cloudflare } = setup();
    petcare.checkRateLimit.mockRejectedValueOnce(
      Object.assign(new Error("rate_limited"), {
        status: 429,
        code: "rate_limited",
      }),
    );
    await expect(
      service.enroll({
        code: CODE,
        publicKey: PUBLIC_KEY,
        localCameraId: "pc-webcam-01",
        connectingIp: "203.0.113.10",
      }),
    ).rejects.toMatchObject({ status: 429, code: "rate_limited" });
    expect(cloudflare.createTunnel).not.toHaveBeenCalled();
  });

  it("enforces the sixth code and eleventh IP attempts exactly", async () => {
    const { service, petcare, cloudflare } = setup();
    const counts = new Map<string, number>();
    petcare.checkRateLimit.mockImplementation(
      async (subject, routeName, limit) => {
        const key = `${routeName}:${subject}`;
        const count = (counts.get(key) ?? 0) + 1;
        counts.set(key, count);
        if (count > limit) throw new PetCareError(429, "rate_limited");
      },
    );
    petcare.findEnrollmentHome.mockRejectedValue(
      new PetCareError(409, "enrollment_rejected"),
    );
    const attempt = (code: string, connectingIp = "203.0.113.10") =>
      service.enroll({
        code,
        publicKey: PUBLIC_KEY,
        localCameraId: "pc-webcam-01",
        connectingIp,
      });

    for (let index = 0; index < 5; index += 1) {
      await expect(attempt(CODE)).rejects.toMatchObject({ status: 409 });
    }
    await expect(attempt(CODE)).rejects.toMatchObject({
      status: 429,
      code: "rate_limited",
    });

    counts.clear();
    for (let index = 0; index < 10; index += 1) {
      await expect(
        attempt(`${String(index).padStart(2, "0")}AAAAAAAAAAAAAAAAAAAA`),
      ).rejects.toMatchObject({ status: 409 });
    }
    await expect(attempt("10AAAAAAAAAAAAAAAAAAAA")).rejects.toMatchObject({
      status: 429,
      code: "rate_limited",
    });
    expect(cloudflare.createTunnel).not.toHaveBeenCalled();
  });

  it("never re-fetches a token after the code was consumed", async () => {
    const { service, tenants, petcare, cloudflare } = setup();
    let consumed = false;
    petcare.findEnrollmentHome.mockImplementation(async () => {
      if (consumed) throw new PetCareError(409, "enrollment_rejected");
      return { homeId: "home-a" };
    });
    tenants.consumeEnrollment.mockImplementation(async (input) => {
      consumed = true;
      return {
        homeId: "home-a",
        agentId: input.agent.id,
        cameraId: input.camera.id,
      };
    });
    const input = {
      code: CODE,
      publicKey: PUBLIC_KEY,
      localCameraId: "pc-webcam-01",
      connectingIp: "203.0.113.10",
    };
    await expect(service.enroll(input)).resolves.toMatchObject({
      connectorToken: "connector-once",
    });
    cloudflare.getConnectorToken.mockClear();
    cloudflare.createTunnel.mockClear();
    await expect(service.enroll(input)).rejects.toMatchObject({
      status: 409,
      code: "enrollment_rejected",
    });
    expect(cloudflare.getConnectorToken).not.toHaveBeenCalled();
    expect(cloudflare.createTunnel).not.toHaveBeenCalled();
  });

  it.each([
    ["createTunnel", {}],
    ["createDnsRecord", { id: "" }],
    ["createAccessApp", { id: "app-1" }],
    ["createAccessPolicy", { id: 42 }],
  ] as const)("rejects malformed %s success results", async (method, result) => {
    const { service, cloudflare } = setup();
    cloudflare[method].mockResolvedValueOnce(result as never);
    await expect(
      service.enroll({
        code: CODE,
        publicKey: PUBLIC_KEY,
        localCameraId: "pc-webcam-01",
        connectingIp: "203.0.113.10",
      }),
    ).rejects.toMatchObject({ status: 503, code: "enrollment_retryable" });
  });

  it("never exposes a non-string connector token", async () => {
    const { service, cloudflare } = setup();
    cloudflare.getConnectorToken.mockResolvedValueOnce({
      leaked_provider_json: "sentinel",
    } as never);
    await expect(
      service.enroll({
        code: CODE,
        publicKey: PUBLIC_KEY,
        localCameraId: "pc-webcam-01",
        connectingIp: "203.0.113.10",
      }),
    ).rejects.toMatchObject({ status: 503, code: "enrollment_retryable" });
  });

  it("rejects a malformed Ed25519 key before repository or provider work", async () => {
    const { service, petcare, cloudflare } = setup();
    await expect(
      service.enroll({
        code: CODE,
        publicKey: "short",
        localCameraId: "pc-webcam-01",
        connectingIp: "203.0.113.10",
      }),
    ).rejects.toMatchObject({ status: 400, code: "invalid_request" });
    expect(petcare.checkRateLimit).not.toHaveBeenCalled();
    expect(cloudflare.createTunnel).not.toHaveBeenCalled();
  });

  it("rejects a second active agent before provisioning", async () => {
    const { service, petcare, cloudflare, route } = setup();
    petcare.getTunnelLedger.mockResolvedValueOnce({
      ...route,
      status: "active",
    });
    await expect(
      service.enroll({
        code: CODE,
        publicKey: PUBLIC_KEY,
        localCameraId: "pc-webcam-01",
        connectingIp: "203.0.113.10",
      }),
    ).rejects.toMatchObject({ status: 409, code: "enrollment_rejected" });
    expect(petcare.reserveTunnel).not.toHaveBeenCalled();
    expect(cloudflare.createTunnel).not.toHaveBeenCalled();
  });

  it("revokes inconsistent partial provisioning instead of resuming it", async () => {
    const { service, petcare, cloudflare, route } = setup();
    const inconsistent = {
      ...route,
      dnsRecordId: "dns-without-tunnel",
      tunnelOrigin: `https://${HOSTNAME}`,
    };
    petcare.getTunnelLedger.mockResolvedValueOnce(inconsistent);
    petcare.reserveTunnel.mockResolvedValueOnce(inconsistent);

    await expect(
      service.enroll({
        code: CODE,
        publicKey: PUBLIC_KEY,
        localCameraId: "pc-webcam-01",
        connectingIp: "203.0.113.10",
      }),
    ).rejects.toMatchObject({ status: 503, code: "enrollment_retryable" });
    expect(cloudflare.createTunnel).not.toHaveBeenCalled();
    expect(cloudflare.deleteDnsRecord).toHaveBeenCalledWith(
      "dns-without-tunnel",
    );
    expect(petcare.deleteTunnelRoute).not.toHaveBeenCalled();
  });

  it("logically revokes before reverse remote deletion", async () => {
    const { service, tenants, petcare, cloudflare, route } = setup();
    Object.assign(route, {
      tunnelId: "tunnel-1",
      dnsRecordId: "dns-1",
      accessAppId: "app-1",
      accessPolicyId: "policy-1",
    });
    petcare.getTunnelLedger.mockResolvedValueOnce(route);
    await expect(service.revoke("owner-a", NOW)).resolves.toEqual({
      status: "revoked",
    });
    expect(tenants.requireHome).toHaveBeenCalledWith("owner-a");
    expect(petcare.requestRevocation).toHaveBeenCalledBefore(
      cloudflare.deleteAccessPolicy,
    );
    expect(petcare.markTunnelState).toHaveBeenCalledWith(
      "home-a",
      "agent_fixed",
      expect.any(String),
      "revocation_pending",
      "revoked",
      NOW.toISOString(),
    );
    expect(
      JSON.stringify([
        cloudflare.deleteAccessPolicy.mock.calls,
        cloudflare.deleteAccessApp.mock.calls,
        cloudflare.deleteDnsRecord.mock.calls,
        cloudflare.deleteTunnel.mock.calls,
      ]),
    ).not.toContain("-b");
  });

  it("leaves logical revocation pending when a remote delete fails", async () => {
    const { service, petcare, cloudflare, route } = setup();
    const active = {
      ...route,
      tunnelId: "tunnel-a",
      dnsRecordId: "dns-a",
      accessAppId: "app-a",
      accessAud: "aud-a",
      accessPolicyId: "policy-a",
    };
    petcare.getTunnelLedger.mockResolvedValueOnce(active);
    petcare.requestRevocation.mockResolvedValueOnce(active);
    cloudflare.deleteDnsRecord.mockRejectedValueOnce(new Error("offline"));

    await expect(service.revoke("owner-a", NOW)).resolves.toEqual({
      status: "revocation_pending",
    });
    expect(petcare.requestRevocation).toHaveBeenCalledWith(
      "home-a",
      "agent_fixed",
      expect.any(String),
      NOW.toISOString(),
      "2026-07-20T03:02:00.000Z",
    );
    expect(petcare.markTunnelState).toHaveBeenCalledWith(
      "home-a",
      "agent_fixed",
      expect.any(String),
      "revocation_pending",
      "revocation_pending",
      NOW.toISOString(),
      "remote_delete_failed",
    );
  });
});
