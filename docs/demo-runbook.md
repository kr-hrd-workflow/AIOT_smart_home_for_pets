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
- Activity: dog/cat의 bounding-box 중심을 UTC 1초 bucket으로 모으고 직전 유효 관측에서 24 px 이상 움직인 bucket만 활동으로 셉니다. 카메라 관측 공백은 활동 0초로 계산하지 않습니다.
- Repetitive motion: 최근 120초에 관측 30개 이상, 이동 bucket 12개 이상, 누적 640 px 이상, 반대 방향 전환 6회 이상일 때 `repetitive_motion` warning을 한 번 기록하고 15분 동안 중복을 억제합니다. 이는 의료·건강 판정이 아니며 클립 trigger가 아닙니다.
- Shutdown/restart: intake→MQTT→rule drain→camera→agent→dashboard hub→DB 순서입니다. controlled shutdown은 확인 상태에 따라 `shutdown`으로 닫고 hard restart는 eating을 마지막 jointly-fresh fact, resting을 `last_confirmed_at`/`restart`로 닫아 replay하지 않습니다.

## Jetson live와 60분 soak

라이브 화면은 추론 FPS와 분리된 최신 캡처 JPEG를 TLS/HMAC으로 인증된 `/v1/live.mjpeg`에서 Home Agent와 Sites BFF를 거쳐 전달합니다. 실기기 PASS는 응답 반복 횟수가 아니라 SHA-256이 다른 JPEG를 세어 모든 연결 구간에서 30 FPS 이상인지 확인합니다. 동시에 추론 3 FPS 이상, Home 관측 age 3초 이하, clip bucket 10 FPS, 온도 80°C 미만, throttling 없음, 스트림 장애 뒤 재연결, 종료 후 남은 worker/request 없음이 필요합니다. 이 증거가 없는 후보는 소프트웨어 테스트가 통과해도 30 FPS 하드웨어 PASS로 기록하지 않습니다.

문제 해결 기준:

- 화면이 고정된 채 요청만 반복되면 응답 횟수가 아니라 multipart JPEG digest의 고유 개수를 확인합니다. 동일 JPEG 반복은 30 FPS로 인정하지 않습니다.
- `camera_unavailable` 또는 중간 연결 종료가 발생하면 stale 브라우저 스트림이 닫혔는지 확인한 뒤 Home Agent의 전용 live connection이 다시 인증·연결되는지 봅니다. Jetson은 동시에 한 스트림만 허용합니다.
- 활동이 갑자기 0으로 보이면 추정치를 만들지 말고 마지막 관측이 3초보다 오래됐는지 확인합니다. 정상 UI는 이 경우 `관측 없음`을 표시합니다.
- `repetitive_motion`은 카메라 위치 추정 warning입니다. 센서 고장·질병·불안으로 단정하지 말고 실제 영상과 환경을 사람이 확인합니다.

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
    "anomalies": ["no_meal_12h", "bed_sensor_mismatch", "repetitive_motion"]
  },
  "schema": {
    "application_tables": ["devices", "sensor_readings", "cameras", "zones", "camera_events", "activity_observations", "activity_cleanup_state", "behavior_events", "anomaly_events", "clip_trigger_outbox", "bed_calibrations", "rest_sessions"],
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
    "runtime_config": ["SUPABASE_URL", "SUPABASE_PUBLISHABLE_KEY", "CF_ACCOUNT_ID", "CF_ZONE_ID", "CF_ZONE_NAME", "CF_ACCESS_TEAM_NAME", "CF_TUNNEL_API_TOKEN", "CF_ACCESS_SERVICE_TOKEN_ID", "CF_ACCESS_CLIENT_ID", "CF_ACCESS_CLIENT_SECRET"],
    "demo_network": "document and same-origin static assets only"
  }
}
```

## Public Sites

Sites source는 후보 commit의 `dashboard` subtree를 split하고 tree equality를 확인한 뒤, 짧은 source credential을 단일 Git process 환경에서만 사용해 전용 `main`에 push합니다. 같은 source SHA로 vinext build와 Sites archive를 만들고 saved version ID를 public deployment로 배포한 뒤 exact project/version/deployment ID로 `succeeded`를 확인합니다. `dashboard/.openai/hosting.json`의 D1/R2 binding과 opaque project ID를 재사용합니다. 공개 랜딩·인증에는 Supabase URL과 publishable key를, Home Agent 등록·원격 상태·영상에는 `dashboard/.env.example`의 `CF_*` 8개 값을 사용하며 token/client secret은 Sites secret으로 저장합니다.

Supabase Auth의 production Site URL은 `https://kr-hrd-petcare-aiot.parkccccc3.chatgpt.site`여야 합니다. Redirect URL allowlist는 `/auth/callback`과 `/auth/callback?next=/reset-password`의 정확한 production URL 두 개만 허용합니다. Custom SMTP가 없으면 일반 고객 이메일 확인·비밀번호 복구를 운영 PASS로 기록하지 않습니다. `CF_*` 8개 값이 완전하지 않은 환경에서는 인증된 10분 코드 요청이 `503 enrollment_unavailable`로 조기 실패하고 D1에 코드를 저장하지 않는지 확인합니다.

익명 세션으로 `/`와 `/demo`가 렌더링되고, `/dashboard`, `/api/petcare/**`, R2 설치 파일은 인증 없이는 접근할 수 없는지 확인합니다. 기존 `/downloads/PetCare-Home-Agent-Setup.exe`가 더 이상 정적 배포물에 없고, 익명 `/api/petcare/installer`가 401을 반환하는지도 확인합니다. `/demo`는 fixture-only이며 실제 카메라·센서·등록·클립 데이터를 제공하지 않습니다.

Sites provider에서 시간별 scheduled trigger와 R2 `clips/` 7일 lifecycle을 구성한 뒤 실제 실행 시각, 삭제된 D1/R2 수, 실패·재시도 수를 운영 증거로 남깁니다. Worker scheduled handler만 존재하거나 읽기 시 만료가 적용된 것만으로는 물리 삭제 PASS가 아닙니다.
