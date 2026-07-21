import { getDb } from "../../db";
import type { DashboardSummary } from "../types";
import type { AuthUser } from "../auth/require-auth";
import { TenantRepository } from "../tenancy/repository";
import type { PetCareEnv } from "./env";
import { PetCareError } from "./errors";
import {
  PetCareRepository,
  type ActiveRoute,
} from "./repository";

const PRIVATE_HEADERS = {
  "Cache-Control": "private, no-store, no-transform",
  Pragma: "no-cache",
  "X-Content-Type-Options": "nosniff",
};
const MJPEG_CONTENT_TYPE = "multipart/x-mixed-replace; boundary=frame";

type JsonObject = Record<string, unknown>;

function exact(value: unknown, keys: readonly string[]): value is JsonObject {
  return (
    typeof value === "object" &&
    value !== null &&
    !Array.isArray(value) &&
    Object.keys(value).length === keys.length &&
    keys.every((key) => Object.prototype.hasOwnProperty.call(value, key))
  );
}

function oneOf(value: unknown, choices: readonly string[]) {
  return typeof value === "string" && choices.includes(value);
}

function finite(value: unknown): value is number {
  return typeof value === "number" && Number.isFinite(value);
}

function nullableString(value: unknown) {
  return value === null || typeof value === "string";
}

function nullableNumber(value: unknown) {
  return value === null || finite(value);
}

function isHealth(value: unknown) {
  return (
    exact(value, ["status", "database", "mqtt", "camera", "queue", "worker"]) &&
    oneOf(value.status, ["healthy", "degraded"]) &&
    oneOf(value.database, ["up", "down"]) &&
    oneOf(value.mqtt, ["up", "down", "disabled"]) &&
    oneOf(value.camera, ["online", "offline"]) &&
    oneOf(value.queue, ["ok", "full"]) &&
    oneOf(value.worker, ["running", "stopped"])
  );
}

function isDevice(value: unknown) {
  return (
    exact(value, ["device_id", "status", "last_seen_at"]) &&
    oneOf(value.device_id, ["entrance-01", "petzone-01"]) &&
    oneOf(value.status, ["online", "offline", "unknown"]) &&
    nullableString(value.last_seen_at)
  );
}

function isSensor(value: unknown) {
  return (
    exact(value, ["id", "device_id", "sensor_type", "value", "unit", "observed_at"]) &&
    finite(value.id) &&
    oneOf(value.device_id, ["entrance-01", "petzone-01"]) &&
    oneOf(value.sensor_type, [
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
    (finite(value.value) || typeof value.value === "boolean") &&
    oneOf(value.unit, ["C", "%", "bool", "g", "adc"]) &&
    typeof value.observed_at === "string"
  );
}

function isCamera(value: unknown) {
  return (
    exact(value, ["state", "fps", "inference_ms", "last_frame_at", "reason"]) &&
    oneOf(value.state, ["online", "offline"]) &&
    finite(value.fps) &&
    finite(value.inference_ms) &&
    nullableString(value.last_frame_at) &&
    nullableString(value.reason)
  );
}

function isBedChannel(value: unknown) {
  return (
    exact(value, ["channel", "raw", "baseline", "delta", "polarity", "available", "observed_at"]) &&
    oneOf(value.channel, ["left", "center", "right"]) &&
    nullableNumber(value.raw) &&
    nullableNumber(value.baseline) &&
    nullableNumber(value.delta) &&
    (value.polarity === null || value.polarity === -1 || value.polarity === 1) &&
    typeof value.available === "boolean" &&
    nullableString(value.observed_at)
  );
}

function isSevenDay(value: unknown) {
  return (
    exact(value, ["status", "today_seconds", "baseline_seconds", "difference_seconds", "percent_change", "complete_days"]) &&
    oneOf(value.status, ["insufficient_data", "zero_baseline", "ready"]) &&
    finite(value.today_seconds) &&
    nullableNumber(value.baseline_seconds) &&
    nullableNumber(value.difference_seconds) &&
    nullableNumber(value.percent_change) &&
    finite(value.complete_days)
  );
}

function isBed(value: unknown) {
  return (
    exact(value, [
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
    oneOf(value.sensor_state, ["unavailable", "uncalibrated", "ready"]) &&
    oneOf(value.pressure_state, ["unavailable", "uncalibrated", "empty", "occupied"]) &&
    oneOf(value.fusion_state, ["unavailable", "empty", "confirmed_rest", "unconfirmed_pressure", "sensor_check"]) &&
    typeof value.camera_confirmed === "boolean" &&
    Array.isArray(value.channels) &&
    value.channels.length === 3 &&
    value.channels.every(isBedChannel) &&
    finite(value.current_rest_seconds) &&
    finite(value.today_rest_seconds) &&
    finite(value.nighttime_exit_count) &&
    isSevenDay(value.seven_day) &&
    nullableString(value.calibrated_at)
  );
}

function isBehavior(value: unknown) {
  return (
    exact(value, ["id", "subject_id", "behavior_type", "started_at", "ended_at", "duration_seconds"]) &&
    finite(value.id) &&
    oneOf(value.subject_id, ["dog_001", "cat_001"]) &&
    oneOf(value.behavior_type, ["eating", "resting"]) &&
    typeof value.started_at === "string" &&
    nullableString(value.ended_at) &&
    nullableNumber(value.duration_seconds)
  );
}

function isAnomaly(value: unknown) {
  return (
    exact(value, ["id", "subject_id", "anomaly_type", "severity", "mismatch_kind", "message", "occurred_at"]) &&
    finite(value.id) &&
    (value.subject_id === null || oneOf(value.subject_id, ["dog_001", "cat_001"])) &&
    oneOf(value.anomaly_type, ["no_meal_12h", "bed_sensor_mismatch"]) &&
    value.severity === "warning" &&
    (value.mismatch_kind === null || oneOf(value.mismatch_kind, ["unconfirmed_pressure", "sensor_check"])) &&
    typeof value.message === "string" &&
    typeof value.occurred_at === "string"
  );
}

function isDashboardSummary(value: unknown): value is DashboardSummary {
  return (
    exact(value, ["generated_at", "health", "devices", "latest_sensors", "camera", "bed", "behaviors", "anomalies"]) &&
    typeof value.generated_at === "string" &&
    isHealth(value.health) &&
    Array.isArray(value.devices) &&
    value.devices.every(isDevice) &&
    Array.isArray(value.latest_sensors) &&
    value.latest_sensors.every(isSensor) &&
    isCamera(value.camera) &&
    isBed(value.bed) &&
    Array.isArray(value.behaviors) &&
    value.behaviors.every(isBehavior) &&
    Array.isArray(value.anomalies) &&
    value.anomalies.every(isAnomaly)
  );
}

function accessHeaders(env: PetCareEnv) {
  if (!env.CF_ACCESS_CLIENT_ID || !env.CF_ACCESS_CLIENT_SECRET) {
    throw new Error("missing_access_credentials");
  }
  return {
    "CF-Access-Client-Id": env.CF_ACCESS_CLIENT_ID,
    "CF-Access-Client-Secret": env.CF_ACCESS_CLIENT_SECRET,
  };
}

function routeUrl(route: ActiveRoute, path: string) {
  const origin = new URL(route.tunnelOrigin);
  if (
    origin.protocol !== "https:" ||
    origin.username ||
    origin.password ||
    origin.pathname !== "/" ||
    origin.search ||
    origin.hash
  ) {
    throw new Error("invalid_tunnel_origin");
  }
  return new URL(path, origin);
}

function offline(route: ActiveRoute | null) {
  return Response.json(
    {
      code: "agent_offline",
      agent_id: route?.agentId ?? null,
      camera_id: route?.cameraId ?? null,
      last_seen_at: route?.lastSeenAt ?? null,
    },
    { status: 503, headers: PRIVATE_HEADERS },
  );
}

async function discard(response: Response) {
  if (response.body) await response.body.cancel().catch(() => undefined);
}

async function connection(user: AuthUser, env: PetCareEnv) {
  const home = await new TenantRepository(getDb(env.DB)).requireHome(user.sub);
  const petcare = new PetCareRepository(env.DB);
  return {
    home,
    petcare,
    state: await petcare.getHomeConnection(home.id),
  };
}

export async function proxyStatus(
  user: AuthUser,
  env: PetCareEnv,
  now: Date,
): Promise<Response> {
  const resolved = await connection(user, env);
  if (resolved.state.state === "needs_enrollment") {
    return Response.json(
      {
        home: { id: resolved.home.id, state: "needs_enrollment" },
        agent: null,
        camera: null,
        dashboard: null,
      },
      { headers: PRIVATE_HEADERS },
    );
  }

  const knownRoute = resolved.state.route;
  if (resolved.state.revoked) return offline(knownRoute);
  try {
    const route = await resolved.petcare.requireActivationRoute(
      resolved.home.id,
      now.toISOString(),
    );
    const upstream = await fetch(routeUrl(route, "/api/dashboard/summary"), {
      method: "GET",
      headers: accessHeaders(env),
      signal: AbortSignal.timeout(2_000),
    });
    if (
      upstream.status !== 200 ||
      upstream.headers
        .get("Content-Type")
        ?.split(";", 1)[0]
        .trim()
        .toLowerCase() !== "application/json"
    ) {
      await discard(upstream);
      return offline(knownRoute);
    }
    const dashboard: unknown = await upstream.json();
    if (!isDashboardSummary(dashboard)) return offline(knownRoute);

    const seenAt = now.toISOString();
    await resolved.petcare.markAgentSeen(route.agentId, seenAt);
    return Response.json(
      {
        home: { id: resolved.home.id, state: "ready" },
        agent: { id: route.agentId, state: "online", last_seen_at: seenAt },
        camera: { id: route.cameraId, state: "online", last_seen_at: seenAt },
        dashboard,
      },
      { headers: PRIVATE_HEADERS },
    );
  } catch {
    return offline(knownRoute);
  }
}

export async function proxyMjpeg(
  user: AuthUser,
  env: PetCareEnv,
  cameraId: string,
): Promise<Response> {
  const resolved = await connection(user, env);
  if (resolved.state.state === "needs_enrollment") {
    throw new PetCareError(404, "not_found");
  }

  const knownRoute = resolved.state.route;
  if (cameraId !== knownRoute.cameraId) {
    throw new PetCareError(404, "not_found");
  }
  if (resolved.state.revoked) return offline(knownRoute);

  try {
    const route = await resolved.petcare.requireActiveRoute(resolved.home.id);
    if (cameraId !== route.cameraId) {
      throw new PetCareError(404, "not_found");
    }
    const url = routeUrl(route, "/api/video_feed");
    const headers = accessHeaders(env);
    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), 2_000);
    try {
      const upstream = await fetch(url, {
        method: "GET",
        headers,
        signal: controller.signal,
      });
      const contentType = upstream.headers.get("Content-Type");
      if (
        upstream.status !== 200 ||
        !contentType ||
        !/^multipart\/x-mixed-replace\s*;\s*boundary=(?:frame|"frame")\s*$/i.test(
          contentType,
        ) ||
        !upstream.body
      ) {
        await discard(upstream);
        return offline(knownRoute);
      }
      clearTimeout(timeout);
      return new Response(upstream.body, {
        status: 200,
        headers: { ...PRIVATE_HEADERS, "Content-Type": MJPEG_CONTENT_TYPE },
      });
    } finally {
      clearTimeout(timeout);
    }
  } catch (error) {
    if (error instanceof PetCareError && error.status === 404) throw error;
    return offline(knownRoute);
  }
}
