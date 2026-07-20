import { getDb } from "../../db";
import { TenantRepository } from "./repository";

const ENROLLMENT_TTL_MS = 600_000;
const MAX_COLLISION_ATTEMPTS = 3;

function generateEnrollmentCode(): string {
  const bytes = crypto.getRandomValues(new Uint8Array(16));
  const binary = Array.from(bytes, (byte) => String.fromCharCode(byte)).join("");
  return btoa(binary)
    .replaceAll("+", "-")
    .replaceAll("/", "_")
    .replace(/=+$/, "");
}

export async function hashEnrollmentCode(code: string): Promise<string> {
  const digest = await crypto.subtle.digest(
    "SHA-256",
    new TextEncoder().encode(code),
  );
  return Array.from(new Uint8Array(digest), (byte) =>
    byte.toString(16).padStart(2, "0"),
  ).join("");
}

export async function issueEnrollment(
  ownerSub: string,
): Promise<{ code: string; expiresAt: string }> {
  const repository = new TenantRepository(getDb());
  const home = await repository.requireHome(ownerSub);
  const expiresAt = new Date(Date.now() + ENROLLMENT_TTL_MS).toISOString();

  for (let attempt = 0; attempt < MAX_COLLISION_ATTEMPTS; attempt += 1) {
    const code = generateEnrollmentCode();
    try {
      await repository.replaceEnrollmentToken(
        home.id,
        await hashEnrollmentCode(code),
        expiresAt,
      );
      return { code, expiresAt };
    } catch (error) {
      if (
        !String(error).includes("UNIQUE") ||
        attempt === MAX_COLLISION_ATTEMPTS - 1
      ) {
        throw error;
      }
    }
  }
  throw new Error("Enrollment code generation failed");
}
