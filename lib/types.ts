export type DeviceId = "entrance-01" | "petzone-01";
export type SensorType =
  | "temperature"
  | "humidity"
  | "presence_moving"
  | "presence_stationary"
  | "food_weight"
  | "water_weight"
  | "bed_pressure_left"
  | "bed_pressure_center"
  | "bed_pressure_right";
export type Unit = "C" | "%" | "bool" | "g" | "adc";
export type SubjectId = "dog_001" | "cat_001";
export type ChannelName = "left" | "center" | "right";
export type ZoneName = "food_bowl" | "pet_bed";

export interface SensorReadingOut {
  id: number;
  device_id: DeviceId;
  sensor_type: SensorType;
  value: number | boolean;
  unit: Unit;
  observed_at: string;
}

export interface DeviceOut {
  device_id: DeviceId;
  status: "online" | "offline" | "unknown";
  last_seen_at: string | null;
}

export interface BehaviorEventOut {
  id: number;
  subject_id: SubjectId;
  behavior_type: "eating" | "resting";
  started_at: string;
  ended_at: string | null;
  duration_seconds: number | null;
}

export interface AnomalyEventOut {
  id: number;
  subject_id: SubjectId | null;
  anomaly_type: "no_meal_12h" | "bed_sensor_mismatch";
  severity: "warning";
  mismatch_kind: "unconfirmed_pressure" | "sensor_check" | null;
  message: string;
  occurred_at: string;
}

export interface CameraStatus {
  state: "online" | "offline";
  fps: number;
  inference_ms: number;
  last_frame_at: string | null;
  reason: string | null;
}

export interface BedChannelStatus {
  channel: ChannelName;
  raw: number | null;
  baseline: number | null;
  delta: number | null;
  polarity: -1 | 1 | null;
  available: boolean;
  observed_at: string | null;
}

export interface SevenDayComparison {
  status: "insufficient_data" | "zero_baseline" | "ready";
  today_seconds: number;
  baseline_seconds: number | null;
  difference_seconds: number | null;
  percent_change: number | null;
  complete_days: number;
}

export interface BedStatus {
  device_id: "petzone-01";
  sensor_state: "unavailable" | "uncalibrated" | "ready";
  pressure_state: "unavailable" | "uncalibrated" | "empty" | "occupied";
  fusion_state:
    | "unavailable"
    | "empty"
    | "confirmed_rest"
    | "unconfirmed_pressure"
    | "sensor_check";
  camera_confirmed: boolean;
  channels: [BedChannelStatus, BedChannelStatus, BedChannelStatus];
  current_rest_seconds: number;
  today_rest_seconds: number;
  nighttime_exit_count: number;
  seven_day: SevenDayComparison;
  calibrated_at: string | null;
}

export interface HealthOut {
  status: "healthy" | "degraded";
  database: "up" | "down";
  mqtt: "up" | "down" | "disabled";
  camera: "online" | "offline";
  queue: "ok" | "full";
  worker: "running" | "stopped";
}

export interface DashboardSummary {
  generated_at: string;
  health: HealthOut;
  devices: DeviceOut[];
  latest_sensors: SensorReadingOut[];
  camera: CameraStatus;
  bed: BedStatus;
  behaviors: BehaviorEventOut[];
  anomalies: AnomalyEventOut[];
}

export interface ZoneOut {
  zone_name: ZoneName;
  x1: number;
  y1: number;
  x2: number;
  y2: number;
  enabled: boolean;
  updated_at: string;
}

export interface CalibrationUiState {
  phase: "idle" | "submitting" | "success" | "disabled" | "error";
  code:
    | "insufficient_samples"
    | "occupied"
    | "unstable"
    | "camera_unavailable"
    | "sensor_unavailable"
    | null;
  channels: ChannelName[];
  message: string;
}

export interface DashboardData extends DashboardSummary {
  zones: [ZoneOut, ZoneOut];
  calibration: CalibrationUiState;
}

export type DashboardMode = "demo" | "connected" | "not_found";
