import type { DashboardData } from "./types";

const observedAt = "2026-07-15T01:42:00Z";

export const demoDashboardData: DashboardData = {
  generated_at: observedAt,
  health: {
    status: "healthy",
    database: "up",
    mqtt: "up",
    camera: "online",
    queue: "ok",
    worker: "running",
  },
  devices: [
    { device_id: "entrance-01", status: "online", last_seen_at: observedAt },
    { device_id: "petzone-01", status: "online", last_seen_at: observedAt },
  ],
  latest_sensors: [
    { id: 1, device_id: "entrance-01", sensor_type: "temperature", value: 24.3, unit: "C", observed_at: observedAt },
    { id: 2, device_id: "entrance-01", sensor_type: "humidity", value: 48, unit: "%", observed_at: observedAt },
    { id: 3, device_id: "entrance-01", sensor_type: "presence_moving", value: true, unit: "bool", observed_at: observedAt },
    { id: 4, device_id: "entrance-01", sensor_type: "presence_stationary", value: false, unit: "bool", observed_at: observedAt },
    { id: 5, device_id: "petzone-01", sensor_type: "temperature", value: 23.8, unit: "C", observed_at: observedAt },
    { id: 6, device_id: "petzone-01", sensor_type: "humidity", value: 51, unit: "%", observed_at: observedAt },
    { id: 7, device_id: "petzone-01", sensor_type: "presence_moving", value: false, unit: "bool", observed_at: observedAt },
    { id: 8, device_id: "petzone-01", sensor_type: "presence_stationary", value: true, unit: "bool", observed_at: observedAt },
    { id: 9, device_id: "petzone-01", sensor_type: "food_weight", value: 742, unit: "g", observed_at: observedAt },
    { id: 10, device_id: "petzone-01", sensor_type: "water_weight", value: 512, unit: "g", observed_at: observedAt },
    { id: 11, device_id: "petzone-01", sensor_type: "bed_pressure_left", value: 1042, unit: "adc", observed_at: observedAt },
    { id: 12, device_id: "petzone-01", sensor_type: "bed_pressure_center", value: 1398, unit: "adc", observed_at: observedAt },
    { id: 13, device_id: "petzone-01", sensor_type: "bed_pressure_right", value: 1156, unit: "adc", observed_at: observedAt },
  ],
  camera: {
    state: "online",
    fps: 11.8,
    inference_ms: 72.4,
    last_frame_at: observedAt,
    reason: null,
  },
  bed: {
    device_id: "petzone-01",
    sensor_state: "ready",
    pressure_state: "occupied",
    fusion_state: "confirmed_rest",
    camera_confirmed: true,
    channels: [
      { channel: "left", raw: 1042, baseline: 812, delta: 230, polarity: 1, available: true, observed_at: observedAt },
      { channel: "center", raw: 1398, baseline: 905, delta: 493, polarity: 1, available: true, observed_at: observedAt },
      { channel: "right", raw: 1156, baseline: 844, delta: 312, polarity: 1, available: true, observed_at: observedAt },
    ],
    current_rest_seconds: 2520,
    today_rest_seconds: 14340,
    nighttime_exit_count: 1,
    seven_day: {
      status: "ready",
      today_seconds: 14340,
      baseline_seconds: 13140,
      difference_seconds: 1200,
      percent_change: 9.1,
      complete_days: 7,
    },
    calibrated_at: "2026-07-14T22:00:00Z",
  },
  behaviors: [
    { id: 4, subject_id: "dog_001", behavior_type: "resting", started_at: "2026-07-15T01:00:00Z", ended_at: null, duration_seconds: null },
    { id: 3, subject_id: "cat_001", behavior_type: "eating", started_at: "2026-07-15T00:30:00Z", ended_at: "2026-07-15T00:31:14Z", duration_seconds: 74 },
    { id: 2, subject_id: "cat_001", behavior_type: "resting", started_at: "2026-07-14T23:10:00Z", ended_at: "2026-07-14T23:48:00Z", duration_seconds: 2280 },
  ],
  anomalies: [
    { id: 3, subject_id: null, anomaly_type: "bed_sensor_mismatch", severity: "warning", mismatch_kind: "unconfirmed_pressure", message: "침대 압력은 감지됐지만 카메라 확인이 없습니다.", occurred_at: "2026-07-15T01:40:00Z" },
    { id: 2, subject_id: "cat_001", anomaly_type: "bed_sensor_mismatch", severity: "warning", mismatch_kind: "sensor_check", message: "침대 센서 확인 필요", occurred_at: "2026-07-15T01:20:00Z" },
    { id: 1, subject_id: "dog_001", anomaly_type: "no_meal_12h", severity: "warning", mismatch_kind: null, message: "12시간 식사 기록 없음", occurred_at: "2026-07-15T00:10:00Z" },
  ],
  zones: [
    { zone_name: "food_bowl", x1: 40, y1: 260, x2: 260, y2: 470, enabled: true, updated_at: observedAt },
    { zone_name: "pet_bed", x1: 320, y1: 180, x2: 630, y2: 470, enabled: true, updated_at: observedAt },
  ],
  calibration: {
    phase: "disabled",
    code: null,
    channels: [],
    message: "로컬 연결 모드에서 실행할 수 있습니다.",
  },
};
