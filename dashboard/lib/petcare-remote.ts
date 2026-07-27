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
export type PicoProduct = "entrance-01" | "petzone-01" | "bed-01";
export type PicoProvisioned = {
  status: "provisioned";
  product: PicoProduct;
};

export type PetCareClip = {
  id: string;
  camera_id: string;
  event_types: Array<"eating" | "resting" | "bed_sensor_mismatch">;
  started_at: string;
  ended_at: string;
  expires_at: string;
};

export interface PetCareRemoteClient {
  detectHomeAgent?(): Promise<boolean>;
  enroll(): Promise<Enrollment>;
  provisionPico(
    product: PicoProduct,
    wifi: { ssid: string; password: string },
  ): Promise<PicoProvisioned>;
  getStatus(signal?: AbortSignal): Promise<PetCareStatus>;
  getClips(): Promise<PetCareClip[]>;
  deleteClip(id: string): Promise<void>;
}

export interface PetCareRemoteMedia {
  videoFeedUrl(cameraId: string): string;
  clipUrl(clipId: string): string;
  liveManifest(
    cameraId: string,
    signal?: AbortSignal,
  ): Promise<PetCareLiveManifest>;
  livePart(url: string, signal?: AbortSignal): Promise<ArrayBuffer>;
}

export type PetCareLiveManifest = {
  boot_id: string;
  codec: "avc1.42E01E";
  newest_sequence: number;
  target_latency_seconds: 6;
  init_url: string;
  parts: Array<{ sequence: number; url: string }>;
};

export interface PetCareLiveClient {
  liveManifest(
    cameraId: string,
    signal?: AbortSignal,
  ): Promise<PetCareLiveManifest>;
  livePart(url: string, signal?: AbortSignal): Promise<ArrayBuffer>;
}

export type AccountDeletionAccepted = {
  status: "cleanup_pending" | "complete";
};

export interface PetCareAccountClient {
  deleteAccount(currentPassword: string): Promise<AccountDeletionAccepted>;
}

type JsonObject = Record<string, unknown>;
type Guard<T> = (value: unknown) => value is T;
const LOCAL_HOME_AGENT = "http://127.0.0.1:8000";

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

function isPicoProvisioned(value: unknown): value is PicoProvisioned {
  return (
    hasExactKeys(value, ["status", "product"]) &&
    value.status === "provisioned" &&
    (value.product === "entrance-01" || value.product === "petzone-01" || value.product === "bed-01")
  );
}

function isNumber(value: unknown): value is number {
  return typeof value === "number" && Number.isFinite(value);
}

function isNonNegativeInteger(value: unknown): value is number {
  return isNumber(value) && Number.isInteger(value) && value >= 0;
}

function isUtcTimestamp(value: unknown): value is string {
  return (
    typeof value === "string" &&
    /(?:Z|[+-]\d{2}:\d{2})$/.test(value) &&
    Number.isFinite(Date.parse(value))
  );
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
    isOneOf(value.device_id, ["entrance-01", "petzone-01", "bed-01"]) &&
    isOneOf(value.status, ["online", "offline", "unknown"]) &&
    isNullableString(value.last_seen_at)
  );
}

function isSensor(value: unknown): boolean {
  if (
    !hasExactKeys(value, ["id", "device_id", "sensor_type", "value", "unit", "observed_at"]) ||
    !isNumber(value.id) ||
    typeof value.observed_at !== "string"
  ) return false;
  if (value.sensor_type === "temperature" || value.sensor_type === "humidity") {
    return isOneOf(value.device_id, ["entrance-01", "bed-01"]) &&
      isNumber(value.value) && value.unit === (value.sensor_type === "temperature" ? "C" : "%");
  }
  if (value.sensor_type === "presence_moving" || value.sensor_type === "presence_stationary") {
    return value.device_id === "entrance-01" && typeof value.value === "boolean" && value.unit === "bool";
  }
  if (value.sensor_type === "food_weight" || value.sensor_type === "water_weight") {
    return value.device_id === "petzone-01" && isNumber(value.value) &&
      value.value >= 0 && value.value <= 5000 && value.unit === "g";
  }
  if (isOneOf(value.sensor_type, ["bed_pressure_left", "bed_pressure_center", "bed_pressure_right"])) {
    return value.device_id === "bed-01" && isNumber(value.value) && Number.isInteger(value.value) &&
      value.value >= 0 && value.value <= 4095 && value.unit === "adc";
  }
  return false;
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

function isActivityStatus(value: unknown): boolean {
  return (
    hasExactKeys(value, [
      "subject_id",
      "today_active_seconds",
      "today_observed_seconds",
      "current_state",
      "last_observed_at",
    ]) &&
    isOneOf(value.subject_id, ["dog_001", "cat_001"]) &&
    isNonNegativeInteger(value.today_active_seconds) &&
    isNonNegativeInteger(value.today_observed_seconds) &&
    value.today_active_seconds <= value.today_observed_seconds &&
    isOneOf(value.current_state, ["active", "still", "unknown"]) &&
    (value.current_state === "unknown"
      ? value.last_observed_at === null || isUtcTimestamp(value.last_observed_at)
      : isUtcTimestamp(value.last_observed_at))
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
    (value.anomaly_type === "no_meal_12h" ||
      value.anomaly_type === "bed_sensor_mismatch" ||
      (value.anomaly_type === "repetitive_motion" &&
        value.subject_id !== null &&
        value.mismatch_kind === null)) &&
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
    value.device_id === "bed-01" &&
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
      "activity",
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
    value.anomalies.every(isAnomaly) &&
    Array.isArray(value.activity) &&
    value.activity.length === 2 &&
    value.activity[0]?.subject_id === "dog_001" &&
    value.activity[1]?.subject_id === "cat_001" &&
    value.activity.every(isActivityStatus)
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

function isLiveMediaRoute(value: unknown): value is string {
  return (
    typeof value === "string" &&
    /^\/api\/petcare\/cameras\/[A-Za-z0-9_-]{1,64}\/live\/[A-Za-z0-9_-]{1,64}\/(?:init\.mp4|[1-9]\d{0,9}\.m4s)$/.test(
      value,
    )
  );
}

function isLiveManifest(value: unknown): value is PetCareLiveManifest {
  if (
    !hasExactKeys(value, [
      "boot_id",
      "codec",
      "newest_sequence",
      "target_latency_seconds",
      "init_url",
      "parts",
    ]) ||
    typeof value.boot_id !== "string" ||
    !/^[A-Za-z0-9_-]{1,64}$/.test(value.boot_id) ||
    value.codec !== "avc1.42E01E" ||
    !isNonNegativeInteger(value.newest_sequence) ||
    value.target_latency_seconds !== 6 ||
    !isLiveMediaRoute(value.init_url) ||
    !Array.isArray(value.parts) ||
    value.parts.length > 8
  ) {
    return false;
  }
  const mediaBase = value.init_url.slice(0, -"init.mp4".length);
  if (!mediaBase.endsWith(`/live/${value.boot_id}/`)) return false;
  const newestSequence = value.newest_sequence;
  let previous = 0;
  const validParts = value.parts.every((part) => {
    if (
      !hasExactKeys(part, ["sequence", "url"]) ||
      !isNonNegativeInteger(part.sequence) ||
      part.sequence < 1 ||
      part.sequence <= previous ||
      part.sequence > newestSequence ||
      !isLiveMediaRoute(part.url) ||
      part.url !== `${mediaBase}${part.sequence}.m4s`
    ) {
      return false;
    }
    previous = part.sequence;
    return true;
  });
  return (
    validParts &&
    (newestSequence === 0
      ? value.parts.length === 0
      : value.parts.at(-1)?.sequence === newestSequence)
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

async function provisionPico(
  product: PicoProduct,
  wifi: { ssid: string; password: string },
): Promise<PicoProvisioned> {
  const response = await fetch(
    `${LOCAL_HOME_AGENT}/api/pico/${product}/provision`,
    {
      method: "POST",
      mode: "cors",
      credentials: "omit",
      referrerPolicy: "no-referrer",
      headers: {
        accept: "application/json",
        "content-type": "application/json",
      },
      body: JSON.stringify({
        wifi_ssid: wifi.ssid,
        wifi_password: wifi.password,
      }),
    },
  );
  if (response.status !== 200) return rejectResponse(response);
  const body: unknown = await response.json().catch(() => undefined);
  if (!isPicoProvisioned(body) || body.product !== product) {
    throw new PetCareRemoteError(response.status);
  }
  return body;
}

async function detectHomeAgent(): Promise<boolean> {
  try {
    await fetch(`${LOCAL_HOME_AGENT}/api/health`, {
      method: "GET",
      mode: "no-cors",
      credentials: "omit",
      cache: "no-store",
      referrerPolicy: "no-referrer",
      signal: AbortSignal.timeout(1_500),
    });
    return true;
  } catch {
    return false;
  }
}

export function createPetCareRemoteClient(): PetCareRemoteClient {
  return {
    detectHomeAgent,
    enroll: () =>
      requestJson("/api/petcare/enrollment", 201, isEnrollment, {
        method: "POST",
      }),
    getStatus: (signal) =>
      requestJson("/api/petcare/status", 200, isStatus, { signal }, true),
    provisionPico,
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
    liveManifest: (id, signal) =>
      requestJson(
        `/api/petcare/cameras/${encodeURIComponent(id)}/live`,
        200,
        isLiveManifest,
        { signal },
      ),
    livePart: async (url, signal) => {
      if (!isLiveMediaRoute(url)) throw new PetCareRemoteError(400);
      const response = await fetch(url, {
        credentials: "same-origin",
        headers: { accept: "video/mp4, video/iso.segment" },
        signal,
      });
      if (response.status !== 200) return rejectResponse(response);
      const body = await response.arrayBuffer();
      if (body.byteLength === 0) throw new PetCareRemoteError(response.status);
      return body;
    },
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
