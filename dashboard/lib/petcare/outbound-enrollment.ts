import { hashEnrollmentCode } from "../tenancy/enrollment";
import {
  EnrollmentRejectedError,
  type TenantRepository,
} from "../tenancy/repository";
import { PetCareError } from "./errors";
import type { PetCareRepository } from "./repository";

export type OutboundEnrollmentInput = {
  code: string;
  publicKey: string;
  localCameraId: string;
  connectingIp: string;
};

function isCanonicalBase64Url(value: string, bytes: number): boolean {
  if (!/^[A-Za-z0-9_-]+$/.test(value)) return false;
  try {
    const standard = value.replaceAll("-", "+").replaceAll("_", "/");
    const decoded = atob(standard.padEnd(Math.ceil(value.length / 4) * 4, "="));
    return (
      decoded.length === bytes &&
      btoa(decoded)
        .replaceAll("+", "-")
        .replaceAll("/", "_")
        .replace(/=+$/, "") === value
    );
  } catch {
    return false;
  }
}

export class OutboundEnrollmentService {
  constructor(
    private readonly tenants: TenantRepository,
    private readonly petcare: PetCareRepository,
    private readonly now: () => Date = () => new Date(),
  ) {}

  async enroll(input: OutboundEnrollmentInput): Promise<{
    agentId: string;
    cameraId: string;
  }> {
    if (
      !isCanonicalBase64Url(input.publicKey, 32) ||
      input.localCameraId !== "pc-webcam-01"
    ) {
      throw new PetCareError(400, "invalid_request");
    }

    const now = this.now();
    const codeHash = await hashEnrollmentCode(input.code);
    await this.petcare.checkRateLimit(input.connectingIp, "enroll-ip", 10, 600, now);
    await this.petcare.checkRateLimit(codeHash, "enroll-code", 5, 600, now);

    const agentId = `agent_${crypto.randomUUID()}`;
    const cameraId = `camera_${crypto.randomUUID()}`;
    try {
      await this.tenants.consumeOutboundEnrollment({
        codeHash,
        consumedAt: now.toISOString(),
        agent: { id: agentId, publicKey: input.publicKey },
        camera: { id: cameraId, localCameraId: "pc-webcam-01" },
      });
      return { agentId, cameraId };
    } catch (error) {
      if (error instanceof EnrollmentRejectedError) {
        throw new PetCareError(409, "enrollment_rejected");
      }
      throw new PetCareError(503, "enrollment_retryable");
    }
  }
}
