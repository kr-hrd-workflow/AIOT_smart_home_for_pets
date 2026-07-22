import { z } from "zod";

import type {
  ApiError,
  BedCalibrationError,
  BedCalibrationSuccess,
  DashboardMessage,
  DashboardSummary,
  ZoneIn,
  ZoneName,
  ZoneOut,
} from "./types";

export const API_BASE_URL = "http://127.0.0.1:8000";
export const VIDEO_FEED_URL = `${API_BASE_URL}/api/video_feed`;
export const WEBSOCKET_URL = "ws://127.0.0.1:8000/ws/dashboard";
const RECONNECT_DELAY_MS = 1_000;

const utc = z.string().refine(
  (value) => /(?:Z|[+-]\d{2}:\d{2})$/.test(value) && Number.isFinite(Date.parse(value)),
  "Expected an ISO-8601 timestamp with timezone",
);
const nullableUtc = utc.nullable();
const finite = z.number().finite();
const nonNegativeInteger = z.number().int().nonnegative();
const channel = z.enum(["left", "center", "right"]);
const DEVICE_ORDER = ["entrance-01", "petzone-01"] as const;
const SENSOR_ORDER = [
  "temperature",
  "humidity",
  "presence_moving",
  "presence_stationary",
  "food_weight",
  "water_weight",
  "bed_pressure_left",
  "bed_pressure_center",
  "bed_pressure_right",
] as const;

function isCanonicalSubset(values: string[], order: readonly string[]): boolean {
  let previous = -1;
  const seen = new Set<string>();
  for (const value of values) {
    const position = order.indexOf(value);
    if (position < 0 || position <= previous || seen.has(value)) return false;
    previous = position;
    seen.add(value);
  }
  return true;
}

function isNewestFirst<T extends { id: number }>(values: T[], timestamp: keyof T): boolean {
  const seen = new Set<number>();
  for (let index = 0; index < values.length; index += 1) {
    const current = values[index];
    if (seen.has(current.id)) return false;
    seen.add(current.id);
    if (index === 0) continue;
    const previous = values[index - 1];
    const currentTime = Date.parse(String(current[timestamp]));
    const previousTime = Date.parse(String(previous[timestamp]));
    if (currentTime > previousTime || (currentTime === previousTime && current.id >= previous.id)) {
      return false;
    }
  }
  return true;
}

function roundHalfUpOneDecimal(value: number): number {
  const magnitude = Math.floor(Math.abs(value) * 10 + 0.5) / 10;
  return value < 0 ? -magnitude : magnitude;
}

const health = z
  .object({
    status: z.enum(["healthy", "degraded"]),
    database: z.enum(["up", "down"]),
    mqtt: z.enum(["up", "down", "disabled"]),
    camera: z.enum(["online", "offline"]),
    queue: z.enum(["ok", "full"]),
    worker: z.enum(["running", "stopped"]),
  })
  .strict();

const device = z
  .object({
    device_id: z.enum(["entrance-01", "petzone-01"]),
    status: z.enum(["online", "offline", "unknown"]),
    last_seen_at: nullableUtc,
  })
  .strict();

const sensor = z
  .object({
    id: nonNegativeInteger,
    device_id: z.enum(["entrance-01", "petzone-01"]),
    sensor_type: z.enum([
      "temperature",
      "humidity",
      "presence_moving",
      "presence_stationary",
      "food_weight",
      "water_weight",
      "bed_pressure_left",
      "bed_pressure_center",
      "bed_pressure_right",
    ]),
    value: z.union([finite, z.boolean()]),
    unit: z.enum(["C", "%", "bool", "g", "adc"]),
    observed_at: utc,
  })
  .strict()
  .refine((value) => {
    if (value.sensor_type === "temperature") return typeof value.value === "number" && value.unit === "C";
    if (value.sensor_type === "humidity") return typeof value.value === "number" && value.unit === "%";
    if (value.sensor_type === "presence_moving" || value.sensor_type === "presence_stationary") {
      return typeof value.value === "boolean" && value.unit === "bool";
    }
    if (value.sensor_type === "food_weight" || value.sensor_type === "water_weight") {
      return value.device_id === "petzone-01" && typeof value.value === "number" && value.unit === "g";
    }
    return (
      value.device_id === "petzone-01" &&
      typeof value.value === "number" &&
      Number.isInteger(value.value) &&
      Number(value.value) >= 0 &&
      Number(value.value) <= 4095 &&
      value.unit === "adc"
    );
  });

const behavior = z
  .object({
    id: nonNegativeInteger,
    subject_id: z.enum(["dog_001", "cat_001"]),
    behavior_type: z.enum(["eating", "resting"]),
    started_at: utc,
    ended_at: nullableUtc,
    duration_seconds: nonNegativeInteger.nullable(),
  })
  .strict()
  .refine((value) => (value.ended_at === null) === (value.duration_seconds === null))
  .refine(
    (value) => value.ended_at === null || Date.parse(value.ended_at) >= Date.parse(value.started_at),
  );

const anomaly = z
  .object({
    id: nonNegativeInteger,
    subject_id: z.enum(["dog_001", "cat_001"]).nullable(),
    anomaly_type: z.enum(["no_meal_12h", "bed_sensor_mismatch"]),
    severity: z.literal("warning"),
    mismatch_kind: z.enum(["unconfirmed_pressure", "sensor_check"]).nullable(),
    message: z.string().min(1),
    occurred_at: utc,
  })
  .strict()
  .refine((value) => {
    if (value.anomaly_type === "no_meal_12h") {
      return value.subject_id !== null && value.mismatch_kind === null;
    }
    if (value.mismatch_kind === "sensor_check") return value.subject_id !== null;
    return value.mismatch_kind === "unconfirmed_pressure" && value.subject_id === null;
  });

const cameraStatus = z
  .object({
    state: z.enum(["online", "offline"]),
    fps: finite.nonnegative(),
    inference_ms: finite.nonnegative(),
    last_frame_at: nullableUtc,
    reason: z.string().nullable(),
  })
  .strict();

const bedChannel = z
  .object({
    channel,
    raw: z.number().int().min(0).max(4095).nullable(),
    baseline: finite.min(0).max(4095).nullable(),
    delta: finite.nonnegative().nullable(),
    polarity: z.union([z.literal(-1), z.literal(1)]).nullable(),
    available: z.boolean(),
    observed_at: nullableUtc,
  })
  .strict();

const sevenDay = z
  .object({
    status: z.enum(["insufficient_data", "zero_baseline", "ready"]),
    today_seconds: nonNegativeInteger,
    baseline_seconds: nonNegativeInteger.nullable(),
    difference_seconds: z.number().int().nullable(),
    percent_change: finite.nullable(),
    complete_days: z.number().int().min(0).max(7),
  })
  .strict()
  .refine((value) => {
    if (value.status === "insufficient_data") {
      return value.complete_days <= 6 && value.baseline_seconds === null && value.difference_seconds === null && value.percent_change === null;
    }
    if (value.status === "zero_baseline") {
      return value.complete_days === 7 && value.baseline_seconds === 0 && value.difference_seconds === value.today_seconds && value.percent_change === null;
    }
    if (value.complete_days !== 7 || value.baseline_seconds === null || value.baseline_seconds <= 0) {
      return false;
    }
    const difference = value.today_seconds - value.baseline_seconds;
    return (
      value.difference_seconds === difference &&
      value.percent_change === roundHalfUpOneDecimal((100 * difference) / value.baseline_seconds)
    );
  });

const bedStatus = z
  .object({
    device_id: z.literal("petzone-01"),
    sensor_state: z.enum(["unavailable", "uncalibrated", "ready"]),
    pressure_state: z.enum(["unavailable", "uncalibrated", "empty", "occupied"]),
    fusion_state: z.enum(["unavailable", "empty", "confirmed_rest", "unconfirmed_pressure", "sensor_check"]),
    camera_confirmed: z.boolean(),
    channels: z.tuple([bedChannel, bedChannel, bedChannel]),
    current_rest_seconds: nonNegativeInteger,
    today_rest_seconds: nonNegativeInteger,
    nighttime_exit_count: nonNegativeInteger,
    seven_day: sevenDay,
    calibrated_at: nullableUtc,
  })
  .strict()
  .refine((value) => value.channels.map((item) => item.channel).join(",") === "left,center,right")
  .refine((value) => value.camera_confirmed === (value.fusion_state === "confirmed_rest"));

const dashboardSummary = z
  .object({
    generated_at: utc,
    health,
    devices: z.array(device),
    latest_sensors: z.array(sensor),
    camera: cameraStatus,
    bed: bedStatus,
    behaviors: z.array(behavior),
    anomalies: z.array(anomaly),
  })
  .strict()
  .refine((value) => isCanonicalSubset(value.devices.map((item) => item.device_id), DEVICE_ORDER))
  .refine((value) => {
    const seen = new Set<string>();
    let previousRank = -1;
    for (const item of value.latest_sensors) {
      const key = `${item.device_id}:${item.sensor_type}`;
      const rank = DEVICE_ORDER.indexOf(item.device_id) * SENSOR_ORDER.length + SENSOR_ORDER.indexOf(item.sensor_type);
      if (seen.has(key) || rank <= previousRank) return false;
      seen.add(key);
      previousRank = rank;
    }
    return true;
  })
  .refine((value) => isNewestFirst(value.behaviors, "started_at"))
  .refine((value) => isNewestFirst(value.anomalies, "occurred_at"));

const zoneInput = z
  .object({
    x1: z.number().int(),
    y1: z.number().int(),
    x2: z.number().int(),
    y2: z.number().int(),
    enabled: z.boolean(),
  })
  .strict()
  .refine((value) => 0 <= value.x1 && value.x1 < value.x2 && value.x2 <= 640)
  .refine((value) => 0 <= value.y1 && value.y1 < value.y2 && value.y2 <= 480);

const zone = z
  .object({
    zone_name: z.enum(["food_bowl", "pet_bed"]),
    x1: z.number().int(),
    y1: z.number().int(),
    x2: z.number().int(),
    y2: z.number().int(),
    enabled: z.boolean(),
    updated_at: utc,
  })
  .strict()
  .refine((value) => 0 <= value.x1 && value.x1 < value.x2 && value.x2 <= 640)
  .refine((value) => 0 <= value.y1 && value.y1 < value.y2 && value.y2 <= 480);
const zones = z
  .tuple([zone, zone])
  .refine((value) => value[0].zone_name === "food_bowl" && value[1].zone_name === "pet_bed");

const calibrationChannel = z
  .object({
    channel,
    sample_count: z.number().int().min(45),
    baseline: finite.min(0).max(4095),
    polarity: z.union([z.literal(-1), z.literal(1)]),
  })
  .strict();
const calibrationSuccess = z
  .object({
    device_id: z.literal("petzone-01"),
    calibrated_at: utc,
    window_start: utc,
    window_end: utc,
    channels: z.tuple([calibrationChannel, calibrationChannel, calibrationChannel]),
  })
  .strict()
  .refine((value) => value.channels.map((item) => item.channel).join(",") === "left,center,right")
  .refine(
    (value) =>
      Date.parse(value.window_end) - Date.parse(value.window_start) === 60_000 &&
      Date.parse(value.calibrated_at) >= Date.parse(value.window_end),
  );

const calibrationError = z
  .object({
    code: z.enum(["insufficient_samples", "occupied", "unstable", "camera_unavailable", "sensor_unavailable"]),
    message: z.string().min(1),
    channels: z.array(channel),
  })
  .strict()
  .refine((value) => value.channels.join(",") === ["left", "center", "right"].filter((item) => value.channels.includes(item as "left" | "center" | "right")).join(","))
  .refine((value) =>
    value.code === "camera_unavailable" || value.code === "occupied"
      ? value.channels.length === 0
      : value.channels.length > 0,
  );

const apiError = z
  .object({
    code: z.enum([
      "queue_unavailable",
      "worker_unavailable",
      "database_unavailable",
      "validation_error",
      "zone_not_found",
      "zone_conflict",
      "camera_unavailable",
      "origin_forbidden",
    ]),
    message: z.string().min(1),
  })
  .strict();

const dashboardMessage = z.discriminatedUnion("type", [
  z.object({ type: z.literal("dashboard_update"), payload: dashboardSummary }).strict(),
  z.object({ type: z.literal("bed_status"), payload: bedStatus }).strict(),
  z.object({ type: z.literal("anomaly_alert"), payload: anomaly }).strict(),
]);

function parsed<T>(schema: z.ZodType<T>, value: unknown, label: string): T {
  const result = schema.safeParse(value);
  if (!result.success) throw new Error(`Invalid ${label}`);
  return result.data;
}

async function responseJson(response: Response): Promise<unknown> {
  try {
    return await response.json();
  } catch {
    throw new Error("Invalid API response");
  }
}

export function parseDashboardMessage(value: unknown): DashboardMessage {
  return parsed(dashboardMessage, value, "dashboard message") as DashboardMessage;
}

export class PetCareApiError extends Error {
  constructor(
    readonly status: number,
    readonly detail: ApiError,
  ) {
    super(detail.message);
  }
}

export type CalibrationResult =
  | { ok: true; value: BedCalibrationSuccess }
  | { ok: false; status: 409; error: BedCalibrationError }
  | { ok: false; status: 503; error: ApiError };

export class PetCareClient {
  async getSummary(signal?: AbortSignal): Promise<DashboardSummary> {
    const response = await fetch(`${API_BASE_URL}/api/dashboard/summary`, {
      method: "GET",
      credentials: "omit",
      signal,
    });
    const body = await responseJson(response);
    if (!response.ok) throw new PetCareApiError(response.status, parsed(apiError, body, "API error"));
    return parsed(dashboardSummary, body, "dashboard summary") as DashboardSummary;
  }

  async getZones(signal?: AbortSignal): Promise<[ZoneOut, ZoneOut]> {
    const response = await fetch(`${API_BASE_URL}/api/zones`, {
      method: "GET",
      credentials: "omit",
      signal,
    });
    const body = await responseJson(response);
    if (!response.ok) throw new PetCareApiError(response.status, parsed(apiError, body, "API error"));
    return parsed(zones, body, "zones") as [ZoneOut, ZoneOut];
  }

  async calibrateBed(signal?: AbortSignal): Promise<CalibrationResult> {
    const response = await fetch(`${API_BASE_URL}/api/bed/calibration`, {
      method: "POST",
      credentials: "omit",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ device_id: "petzone-01" }),
      signal,
    });
    const body = await responseJson(response);
    if (response.status === 409) {
      return { ok: false, status: 409, error: parsed(calibrationError, body, "calibration error") as BedCalibrationError };
    }
    if (response.status === 503) {
      return { ok: false, status: 503, error: parsed(apiError, body, "API error") as ApiError };
    }
    if (!response.ok) throw new PetCareApiError(response.status, parsed(apiError, body, "API error"));
    return { ok: true, value: parsed(calibrationSuccess, body, "calibration success") as BedCalibrationSuccess };
  }

  async updateZone(zoneName: ZoneName, input: ZoneIn, signal?: AbortSignal): Promise<ZoneOut> {
    const body = parsed(zoneInput, input, "zone geometry");
    const response = await fetch(`${API_BASE_URL}/api/zones/${zoneName}`, {
      method: "PUT",
      credentials: "omit",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
      signal,
    });
    const payload = await responseJson(response);
    if (!response.ok) throw new PetCareApiError(response.status, parsed(apiError, payload, "API error"));
    const updated = parsed(zone, payload, "zone") as ZoneOut;
    if (updated.zone_name !== zoneName) throw new Error("Invalid zone response");
    return updated;
  }

  subscribe(
    onMessage: (message: DashboardMessage) => void,
    onError: (error: Error) => void = () => undefined,
  ): () => void {
    let active = true;
    let retry: ReturnType<typeof setTimeout> | undefined;
    let socket: WebSocket | undefined;

    const connect = () => {
      if (!active) return;
      socket = new WebSocket(WEBSOCKET_URL);
      socket.onmessage = (event) => {
        try {
          onMessage(parseDashboardMessage(JSON.parse(String(event.data))));
        } catch (error) {
          onError(error instanceof Error ? error : new Error("Invalid dashboard message"));
        }
      };
      socket.onerror = () => socket?.close();
      socket.onclose = () => {
        if (active) retry = setTimeout(connect, RECONNECT_DELAY_MS);
      };
    };

    connect();
    return () => {
      active = false;
      if (retry !== undefined) clearTimeout(retry);
      socket?.close();
    };
  }
}
