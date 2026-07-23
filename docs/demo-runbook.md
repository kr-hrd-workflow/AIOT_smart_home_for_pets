# 데모 운영 runbook

## Local-live

1. [setup.md](setup.md)의 bootstrap과 model provisioning을 완료합니다.
2. runtime manifest와 service manifest의 hash가 `tools/platform-manifest.json`과 일치하는지 `tools/check_all.ps1`로 확인합니다.
3. `powershell -NoProfile -ExecutionPolicy Bypass -File tools/run_integration.ps1 -Provider Native`를 실행합니다.
4. runner가 `PETCARE_LOCAL_INTEGRATION=PASS`를 출력하고 서비스를 종료하는지 확인합니다.

integration runner는 실제 60초 빈 침대 calibration, 30초 food-bowl dwell와 5g 감소, dog/cat bed 선택·소유·handoff, 두 mismatch, strict zone, origin, graceful shutdown, hard restart를 fixture camera와 실제 DB/MQTT/backend/dashboard process로 검증합니다. 12시간을 기다리지 않고 UTC/monotonic clock을 명시적으로 주입합니다.

## API와 ROI

API 문서는 외부 OpenAPI route를 노출하지 않으며 `/docs`, `/redoc`, `/openapi.json`은 비활성입니다. `GET /api/zones`와 `PUT /api/zones/{zone_name}`만 사용합니다. zone 이름은 `food_bowl`, `pet_bed`로 제한되고 좌표는 640×480 frame 안의 `x1 < x2`, `y1 < y2`여야 하며 enabled zone끼리 겹치면 409입니다. POST/DELETE zone route는 없습니다.

침대 calibration 전 카메라가 online인지, `pet_bed`가 비어 있는지, 세 FSR channel이 최근 3초 이내인지 확인합니다. `POST /api/bed/calibration`은 최근 60초, 채널별 최소 45 sample, channel별 안정성 범위를 만족할 때만 baseline을 저장합니다.

## 행동과 소유권

- Eating: `food_bowl`의 dog/cat camera dwell 30초, 진입 전 10초 median(최소 5점)과 현재 5초 median(최소 4점)의 차이가 5g 이상일 때 엽니다. 동일 시작 시 dog가 tie-break입니다.
- Bed selection: `pet_bed` 검출 중 confidence가 가장 높은 pet을 선택하고 confidence가 같으면 dog를 선택합니다.
- Rest: FSR occupied와 선택 pet이 함께 2초 유지되면 한 명의 owner로 엽니다. 기존 owner가 보이면 유지하고, 사라진 뒤 다른 pet이 충분히 확인되면 camera-exit close 후 handoff합니다.
- Mismatch: pet은 보이지만 압력이 empty면 `sensor_check`; 압력은 occupied인데 pet이 없으면 `unconfirmed_pressure`입니다. 둘 다 `bed_sensor_mismatch` warning이며 별도 위험 이벤트가 아닙니다.
- Shutdown/restart: intake→MQTT→rule drain→camera→agent→dashboard hub→DB 순서입니다. controlled shutdown은 확인 상태에 따라 `shutdown`으로 닫고 hard restart는 eating을 마지막 jointly-fresh fact, resting을 `last_confirmed_at`/`restart`로 닫아 replay하지 않습니다.

<!-- petcare-docs:demo-contract -->
```json
{
  "api_routes": [
    "GET /api/health",
    "GET /api/dashboard/summary",
    "GET /api/devices",
    "GET /api/sensors/latest",
    "GET /api/behaviors",
    "GET /api/anomalies",
    "GET /api/camera/status",
    "GET /api/video_feed",
    "GET /api/bed/status",
    "POST /api/bed/calibration",
    "GET /api/zones",
    "PUT /api/zones/{zone_name}",
    "WS /ws/dashboard"
  ],
  "zones": {
    "allowed": ["food_bowl", "pet_bed"],
    "frame": {"width": 640, "height": 480},
    "seed": {
      "food_bowl": [40, 260, 260, 470],
      "pet_bed": [320, 180, 630, 470]
    },
    "enabled_zones_must_not_overlap": true
  },
  "rules": {
    "subjects": ["dog_001", "cat_001"],
    "eating": "30-second camera dwell; pre-entry 10-second median minus current 5-second median is at least 5 g",
    "bed_selection": "highest-confidence pet_bed detection; dog wins an exact confidence tie",
    "rest_owner": "one owner is retained until exit or handoff completes",
    "mismatch": ["sensor_check", "unconfirmed_pressure"],
    "anomalies": ["no_meal_12h", "bed_sensor_mismatch"]
  },
  "schema": {
    "application_tables": ["devices", "sensor_readings", "cameras", "zones", "camera_events", "behavior_events", "anomaly_events", "clip_trigger_outbox", "bed_calibrations", "rest_sessions"],
    "metadata_table": "alembic_version",
    "core_tables_before_clip_outbox": 9,
    "global_open_constraints": ["one open behavior event per behavior_type", "one open rest session globally"]
  },
  "shutdown_order": ["stop ingress", "stop MQTT", "drain rule worker", "stop camera", "stop agent components", "stop dashboard hub", "dispose database"],
  "restart_disposition": {
    "eating": "close at last jointly fresh camera/sensor fact",
    "resting": "close at last_confirmed_at with close_reason restart",
    "replay": false
  },
  "sites": {
    "plugin_version": "0.1.30",
    "starter": "vinext",
    "bindings": {"d1": "DB", "r2": "CLIPS"},
    "project_id_present": true,
    "source_chain": ["dashboard subtree split", "tree equality", "per-command source credential", "vinext build", "Sites archive", "saved version", "public deployment", "status poll", "anonymous / and /demo"],
    "access": "public",
    "environment_mutation": false,
    "demo_network": "document and same-origin static assets only"
  }
}
```

## Public Sites

Sites source는 후보 commit의 `dashboard` subtree를 split하고 tree equality를 확인한 뒤, 짧은 source credential을 단일 Git process 환경에서만 사용해 전용 `main`에 push합니다. 같은 source SHA로 vinext build와 Sites archive를 만들고 saved version ID를 public deployment로 배포한 뒤 exact project/version/deployment ID로 `succeeded`를 확인합니다. `dashboard/.openai/hosting.json`의 D1/R2 binding과 opaque project ID를 재사용하며 environment update는 하지 않습니다.

익명 요청으로 `/`와 `/demo`가 렌더링되는지 확인합니다. `/demo`는 fixture-only이며 실제 카메라·센서·등록·클립 데이터는 공개하지 않습니다.
