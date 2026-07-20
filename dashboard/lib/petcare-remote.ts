import type { DashboardData } from "./types";

export type AgentOffline = {
  code: "agent_offline";
  agent_id: string | null;
  camera_id: string | null;
  last_seen_at: string | null;
};

export type PetCareStatus = {
  home: { id: string; state: "ready" | "needs_enrollment" };
  agent: { id: string; state: "online"; last_seen_at: string } | null;
  camera: { id: string; state: "online"; last_seen_at: string } | null;
  dashboard: DashboardData | null;
};

export type Enrollment = { code: string; expiresAt: string };

export type PetCareClip = {
  id: string;
  camera_id: string;
  event_types: Array<"eating" | "resting" | "bed_sensor_mismatch">;
  started_at: string;
  ended_at: string;
  expires_at: string;
};

export interface PetCareRemoteClient {
  enroll(): Promise<Enrollment>;
  getStatus(signal?: AbortSignal): Promise<PetCareStatus>;
  getClips(): Promise<PetCareClip[]>;
  deleteClip(id: string): Promise<void>;
}

export interface PetCareRemoteMedia {
  videoFeedUrl(cameraId: string): string;
  clipUrl(clipId: string): string;
}

export type AccountDeletionAccepted = {
  status: "cleanup_pending" | "complete";
};

export interface PetCareAccountClient {
  deleteAccount(currentPassword: string): Promise<AccountDeletionAccepted>;
}

type JsonObject = Record<string, unknown>;
type Guard<T> = (value: unknown) => value is T;

function isObject(value: unknown): value is JsonObject {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function isNumber(value: unknown): value is number {
  return typeof value === "number" && Number.isFinite(value);
}

function isNullableNumber(value: unknown): boolean {
  return value === null || isNumber(value);
}

function isNullableString(value: unknown): boolean {
  return value === null || typeof value === "string";
}

function isOneOf(value: unknown, choices: readonly string[]): boolean {
  return typeof value === "string" && choices.includes(value);
}

function isDashboard(value: unknown): value is DashboardData {
  if (!isObject(value)) return false;
  const { health, camera, bed, calibration } = value;
  return (
    typeof value.generated_at === "string" &&
    isObject(health) &&
    isOneOf(health.status, ["healthy", "degraded"]) &&
    Array.isArray(value.devices) &&
    value.devices.every(isObject) &&
    Array.isArray(value.latest_sensors) &&
    value.latest_sensors.every(isObject) &&
    isObject(camera) &&
    isOneOf(camera.state, ["online", "offline"]) &&
    isNumber(camera.fps) &&
    isNumber(camera.inference_ms) &&
    isObject(bed) &&
    Array.isArray(bed.channels) &&
    bed.channels.length === 3 &&
    bed.channels.every(isObject) &&
    isNumber(bed.current_rest_seconds) &&
    isNumber(bed.today_rest_seconds) &&
    isNumber(bed.nighttime_exit_count) &&
    isObject(bed.seven_day) &&
    isOneOf(bed.seven_day.status, [
      "insufficient_data",
      "zero_baseline",
      "ready",
    ]) &&
    isNullableNumber(bed.seven_day.difference_seconds) &&
    isNullableNumber(bed.seven_day.percent_change) &&
    isNumber(bed.seven_day.complete_days) &&
    Array.isArray(value.behaviors) &&
    value.behaviors.every(isObject) &&
    Array.isArray(value.anomalies) &&
    value.anomalies.every(isObject) &&
    Array.isArray(value.zones) &&
    value.zones.length === 2 &&
    value.zones.every(isObject) &&
    isObject(calibration) &&
    isOneOf(calibration.phase, [
      "idle",
      "submitting",
      "success",
      "disabled",
      "error",
    ]) &&
    typeof calibration.message === "string"
  );
}

function isEnrollment(value: unknown): value is Enrollment {
  return (
    isObject(value) &&
    typeof value.code === "string" &&
    typeof value.expiresAt === "string"
  );
}

function isConnection(value: unknown): boolean {
  return (
    isObject(value) &&
    typeof value.id === "string" &&
    value.state === "online" &&
    typeof value.last_seen_at === "string"
  );
}

function isStatus(value: unknown): value is PetCareStatus {
  return (
    isObject(value) &&
    isObject(value.home) &&
    typeof value.home.id === "string" &&
    isOneOf(value.home.state, ["ready", "needs_enrollment"]) &&
    (value.agent === null || isConnection(value.agent)) &&
    (value.camera === null || isConnection(value.camera)) &&
    (value.dashboard === null || isDashboard(value.dashboard))
  );
}

function isClip(value: unknown): value is PetCareClip {
  return (
    isObject(value) &&
    typeof value.id === "string" &&
    typeof value.camera_id === "string" &&
    Array.isArray(value.event_types) &&
    value.event_types.every((event) =>
      isOneOf(event, ["eating", "resting", "bed_sensor_mismatch"]),
    ) &&
    typeof value.started_at === "string" &&
    typeof value.ended_at === "string" &&
    typeof value.expires_at === "string"
  );
}

function isClipList(value: unknown): value is { clips: PetCareClip[] } {
  return (
    isObject(value) &&
    Array.isArray(value.clips) &&
    value.clips.every(isClip)
  );
}

function isAgentOffline(value: unknown): value is AgentOffline {
  return (
    isObject(value) &&
    value.code === "agent_offline" &&
    isNullableString(value.agent_id) &&
    isNullableString(value.camera_id) &&
    isNullableString(value.last_seen_at)
  );
}

function isCleanupPending(value: unknown): value is AccountDeletionAccepted {
  return isObject(value) && value.status === "cleanup_pending";
}

class PetCareRemoteError extends Error {
  constructor(
    readonly status: number,
    readonly offline?: AgentOffline,
  ) {
    super(offline?.code ?? `petcare_request_${status}`);
  }
}

function request(path: string, init?: RequestInit): Promise<Response> {
  return fetch(path, {
    credentials: "same-origin",
    headers: { accept: "application/json" },
    ...init,
  });
}

async function rejectResponse(
  response: Response,
  allowOffline = false,
): Promise<never> {
  const body: unknown = await response.json().catch(() => undefined);
  throw new PetCareRemoteError(
    response.status,
    allowOffline && response.status === 503 && isAgentOffline(body)
      ? body
      : undefined,
  );
}

async function requestJson<T>(
  path: string,
  status: number,
  guard: Guard<T>,
  init?: RequestInit,
  allowOffline = false,
): Promise<T> {
  const response = await request(path, init);
  if (response.status !== status) return rejectResponse(response, allowOffline);
  const body: unknown = await response.json().catch(() => undefined);
  if (!guard(body)) throw new PetCareRemoteError(response.status);
  return body;
}

async function requestEmpty(
  path: string,
  status: number,
  init?: RequestInit,
): Promise<void> {
  const response = await request(path, init);
  if (response.status !== status) return rejectResponse(response);
}

export function createPetCareRemoteClient(): PetCareRemoteClient {
  return {
    enroll: () =>
      requestJson("/api/petcare/enrollment", 201, isEnrollment, {
        method: "POST",
      }),
    getStatus: (signal) =>
      requestJson("/api/petcare/status", 200, isStatus, { signal }, true),
    getClips: async () =>
      (await requestJson("/api/petcare/clips", 200, isClipList)).clips,
    deleteClip: (id) =>
      requestEmpty(
        `/api/petcare/clips/${encodeURIComponent(id)}`,
        204,
        { method: "DELETE" },
      ),
  };
}

export function createPetCareRemoteMedia(): PetCareRemoteMedia {
  return {
    videoFeedUrl: (id) =>
      `/api/petcare/cameras/${encodeURIComponent(id)}/stream.mjpeg`,
    clipUrl: (id) => `/api/petcare/clips/${encodeURIComponent(id)}.mp4`,
  };
}

export function createPetCareAccountClient(): PetCareAccountClient {
  return {
    deleteAccount: async (currentPassword) => {
      const response = await fetch("/api/petcare/account", {
        method: "DELETE",
        credentials: "same-origin",
        headers: {
          accept: "application/json",
          "content-type": "application/json",
        },
        body: JSON.stringify({ currentPassword }),
      });
      if (response.status === 204) return { status: "complete" };
      if (response.status === 202) {
        const body: unknown = await response.json().catch(() => undefined);
        if (isCleanupPending(body)) return body;
      }
      throw new PetCareRemoteError(response.status);
    },
  };
}
