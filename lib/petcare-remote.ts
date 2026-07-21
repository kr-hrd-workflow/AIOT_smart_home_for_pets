import type { DashboardSummary } from "./types";

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
  dashboard: DashboardSummary | null;
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

function hasExactKeys(
  value: unknown,
  keys: readonly string[],
): value is JsonObject {
  if (!isObject(value) || Object.keys(value).length !== keys.length) return false;
  return keys.every((key) => Object.prototype.hasOwnProperty.call(value, key));
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

function isDevice(value: unknown): boolean {
  return (
    hasExactKeys(value, ["device_id", "status", "last_seen_at"]) &&
    isOneOf(value.device_id, ["entrance-01", "petzone-01"]) &&
    isOneOf(value.status, ["online", "offline", "unknown"]) &&
    isNullableString(value.last_seen_at)
  );
}

function isSensor(value: unknown): boolean {
  return (
    hasExactKeys(value, [
      "id",
      "device_id",
      "sensor_type",
      "value",
      "unit",
      "observed_at",
    ]) &&
    isNumber(value.id) &&
    isOneOf(value.device_id, ["entrance-01", "petzone-01"]) &&
    isOneOf(value.sensor_type, [
      "temperature",
      "humidity",
      "presence_moving",
      "presence_stationary",
      "food_weight",
      "water_weight",
      "bed_pressure_left",
      "bed_pressure_center",
      "bed_pressure_right",
    ]) &&
    (isNumber(value.value) || typeof value.value === "boolean") &&
    isOneOf(value.unit, ["C", "%", "bool", "g", "adc"]) &&
    typeof value.observed_at === "string"
  );
}

function isBehavior(value: unknown): boolean {
  return (
    hasExactKeys(value, [
      "id",
      "subject_id",
      "behavior_type",
      "started_at",
      "ended_at",
      "duration_seconds",
    ]) &&
    isNumber(value.id) &&
    isOneOf(value.subject_id, ["dog_001", "cat_001"]) &&
    isOneOf(value.behavior_type, ["eating", "resting"]) &&
    typeof value.started_at === "string" &&
    isNullableString(value.ended_at) &&
    isNullableNumber(value.duration_seconds)
  );
}

function isAnomaly(value: unknown): boolean {
  return (
    hasExactKeys(value, [
      "id",
      "subject_id",
      "anomaly_type",
      "severity",
      "mismatch_kind",
      "message",
      "occurred_at",
    ]) &&
    isNumber(value.id) &&
    (value.subject_id === null ||
      isOneOf(value.subject_id, ["dog_001", "cat_001"])) &&
    isOneOf(value.anomaly_type, ["no_meal_12h", "bed_sensor_mismatch"]) &&
    value.severity === "warning" &&
    (value.mismatch_kind === null ||
      isOneOf(value.mismatch_kind, ["unconfirmed_pressure", "sensor_check"])) &&
    typeof value.message === "string" &&
    typeof value.occurred_at === "string"
  );
}

function isCameraStatus(value: unknown): boolean {
  return (
    hasExactKeys(value, [
      "state",
      "fps",
      "inference_ms",
      "last_frame_at",
      "reason",
    ]) &&
    isOneOf(value.state, ["online", "offline"]) &&
    isNumber(value.fps) &&
    isNumber(value.inference_ms) &&
    isNullableString(value.last_frame_at) &&
    isNullableString(value.reason)
  );
}

function isBedChannel(value: unknown): boolean {
  return (
    hasExactKeys(value, [
      "channel",
      "raw",
      "baseline",
      "delta",
      "polarity",
      "available",
      "observed_at",
    ]) &&
    isOneOf(value.channel, ["left", "center", "right"]) &&
    isNullableNumber(value.raw) &&
    isNullableNumber(value.baseline) &&
    isNullableNumber(value.delta) &&
    (value.polarity === null || value.polarity === -1 || value.polarity === 1) &&
    typeof value.available === "boolean" &&
    isNullableString(value.observed_at)
  );
}

function isSevenDay(value: unknown): boolean {
  return (
    hasExactKeys(value, [
      "status",
      "today_seconds",
      "baseline_seconds",
      "difference_seconds",
      "percent_change",
      "complete_days",
    ]) &&
    isOneOf(value.status, [
      "insufficient_data",
      "zero_baseline",
      "ready",
    ]) &&
    isNumber(value.today_seconds) &&
    isNullableNumber(value.baseline_seconds) &&
    isNullableNumber(value.difference_seconds) &&
    isNullableNumber(value.percent_change) &&
    isNumber(value.complete_days)
  );
}

function isBed(value: unknown): boolean {
  return (
    hasExactKeys(value, [
      "device_id",
      "sensor_state",
      "pressure_state",
      "fusion_state",
      "camera_confirmed",
      "channels",
      "current_rest_seconds",
      "today_rest_seconds",
      "nighttime_exit_count",
      "seven_day",
      "calibrated_at",
    ]) &&
    value.device_id === "petzone-01" &&
    isOneOf(value.sensor_state, ["unavailable", "uncalibrated", "ready"]) &&
    isOneOf(value.pressure_state, [
      "unavailable",
      "uncalibrated",
      "empty",
      "occupied",
    ]) &&
    isOneOf(value.fusion_state, [
      "unavailable",
      "empty",
      "confirmed_rest",
      "unconfirmed_pressure",
      "sensor_check",
    ]) &&
    typeof value.camera_confirmed === "boolean" &&
    Array.isArray(value.channels) &&
    value.channels.length === 3 &&
    value.channels.every(isBedChannel) &&
    isNumber(value.current_rest_seconds) &&
    isNumber(value.today_rest_seconds) &&
    isNumber(value.nighttime_exit_count) &&
    isSevenDay(value.seven_day) &&
    isNullableString(value.calibrated_at)
  );
}

function isHealth(value: unknown): boolean {
  return (
    hasExactKeys(value, [
      "status",
      "database",
      "mqtt",
      "camera",
      "queue",
      "worker",
    ]) &&
    isOneOf(value.status, ["healthy", "degraded"]) &&
    isOneOf(value.database, ["up", "down"]) &&
    isOneOf(value.mqtt, ["up", "down", "disabled"]) &&
    isOneOf(value.camera, ["online", "offline"]) &&
    isOneOf(value.queue, ["ok", "full"]) &&
    isOneOf(value.worker, ["running", "stopped"])
  );
}

function isDashboardSummary(value: unknown): value is DashboardSummary {
  return (
    hasExactKeys(value, [
      "generated_at",
      "health",
      "devices",
      "latest_sensors",
      "camera",
      "bed",
      "behaviors",
      "anomalies",
    ]) &&
    typeof value.generated_at === "string" &&
    isHealth(value.health) &&
    Array.isArray(value.devices) &&
    value.devices.every(isDevice) &&
    Array.isArray(value.latest_sensors) &&
    value.latest_sensors.every(isSensor) &&
    isCameraStatus(value.camera) &&
    isBed(value.bed) &&
    Array.isArray(value.behaviors) &&
    value.behaviors.every(isBehavior) &&
    Array.isArray(value.anomalies) &&
    value.anomalies.every(isAnomaly)
  );
}

function isEnrollment(value: unknown): value is Enrollment {
  return (
    hasExactKeys(value, ["code", "expiresAt"]) &&
    typeof value.code === "string" &&
    typeof value.expiresAt === "string"
  );
}

function isConnection(value: unknown): boolean {
  return (
    hasExactKeys(value, ["id", "state", "last_seen_at"]) &&
    typeof value.id === "string" &&
    value.state === "online" &&
    typeof value.last_seen_at === "string"
  );
}

function isStatus(value: unknown): value is PetCareStatus {
  return (
    hasExactKeys(value, ["home", "agent", "camera", "dashboard"]) &&
    hasExactKeys(value.home, ["id", "state"]) &&
    typeof value.home.id === "string" &&
    isOneOf(value.home.state, ["ready", "needs_enrollment"]) &&
    (value.agent === null || isConnection(value.agent)) &&
    (value.camera === null || isConnection(value.camera)) &&
    (value.dashboard === null || isDashboardSummary(value.dashboard))
  );
}

function isClip(value: unknown): value is PetCareClip {
  return (
    hasExactKeys(value, [
      "id",
      "camera_id",
      "event_types",
      "started_at",
      "ended_at",
      "expires_at",
    ]) &&
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
    hasExactKeys(value, ["clips"]) &&
    Array.isArray(value.clips) &&
    value.clips.every(isClip)
  );
}

function isAgentOffline(value: unknown): value is AgentOffline {
  return (
    hasExactKeys(value, [
      "code",
      "agent_id",
      "camera_id",
      "last_seen_at",
    ]) &&
    value.code === "agent_offline" &&
    isNullableString(value.agent_id) &&
    isNullableString(value.camera_id) &&
    isNullableString(value.last_seen_at)
  );
}

function isCleanupPending(value: unknown): value is AccountDeletionAccepted {
  return hasExactKeys(value, ["status"]) && value.status === "cleanup_pending";
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
