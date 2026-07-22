# 물리 하드웨어 검수

## 상태 규칙

각 항목은 `PASS`, `FAIL`, `NOT RUN` 중 하나입니다. PASS에는 날짜, 장치 식별자, 실행 명령, redacted log/사진 경로가 필요합니다. 부품이 없거나 실제 연결을 하지 않았으면 `NOT RUN`을 유지합니다. 소프트웨어 fixture, mock, CI 통과는 물리 PASS의 대체 증거가 아닙니다.

현재 저장소에는 실제 Pico serial, 설치 센서, 실웹캠 frame/FPS, 빈 침대 calibration 증거가 없으므로 전체 상태는 `NOT RUN`입니다.

<!-- petcare-docs:hardware-gate -->
```json
{
  "aggregate": "NOT RUN",
  "nodes": {
    "entrance-01": "NOT RUN",
    "petzone-01": "NOT RUN",
    "home-camera": "NOT RUN"
  },
  "components": [
    {"id": "entrance_serial_boot", "status": "NOT RUN", "evidence": null},
    {"id": "petzone_serial_boot", "status": "NOT RUN", "evidence": null},
    {"id": "authenticated_sensor_subscription", "status": "NOT RUN", "evidence": null},
    {"id": "authenticated_status_subscription", "status": "NOT RUN", "evidence": null},
    {"id": "mqtt_reconnect", "status": "NOT RUN", "evidence": null},
    {"id": "entrance_sht31_installed", "status": "NOT RUN", "evidence": null},
    {"id": "petzone_sht31_installed", "status": "NOT RUN", "evidence": null},
    {"id": "spare_sht31_test", "status": "NOT RUN", "evidence": null},
    {"id": "entrance_ld2410c_installed", "status": "NOT RUN", "evidence": null},
    {"id": "petzone_ld2410c_installed", "status": "NOT RUN", "evidence": null},
    {"id": "food_bowl_calibrated", "status": "NOT RUN", "evidence": null},
    {"id": "water_bowl_calibrated", "status": "NOT RUN", "evidence": null},
    {"id": "fsr_left_raw", "status": "NOT RUN", "evidence": null},
    {"id": "fsr_center_raw", "status": "NOT RUN", "evidence": null},
    {"id": "fsr_right_raw", "status": "NOT RUN", "evidence": null},
    {"id": "webcam_fps_frame", "status": "NOT RUN", "evidence": null},
    {"id": "empty_bed_calibration", "status": "NOT RUN", "evidence": null},
    {"id": "dashboard_reflection", "status": "NOT RUN", "evidence": null}
  ]
}
```

## 실행 체크리스트

1. 두 UF2를 올리고 각 serial boot와 정확한 device ID를 기록합니다.
2. hardware MQTT listener를 한 명시적 RFC1918 주소에만 열고 사용자/비밀번호 인증으로 sensor wildcard와 두 retained status topic을 구독합니다.
3. 두 Pico의 전원 또는 AP를 순차 재시작해 retained offline, online 복구, 1/2/4/8/16/30초 backoff를 확인합니다.
4. 노드별 SHT31 두 대와 spare 한 대, LD2410C 두 대를 각각 확인합니다.
5. 식기와 물그릇을 별도 tare/known-mass calibration하고 raw/gram 변화를 기록합니다.
6. FSR left/center/right를 하나씩 누르며 `0..4095 adc` raw 채널이 서로 뒤바뀌지 않았는지 확인합니다.
7. 실웹캠의 유효 `640x480x3 uint8` frame, FPS, inference latency, dog/cat bbox/zone을 확인합니다.
8. 빈 침대에서 60초 calibration을 수행하고 채널별 45개 이상 sample, 안정성, camera empty를 확인합니다.
9. 침대 진입/이탈과 dog/cat 교대가 API, WebSocket, 대시보드에 반영되는지 확인합니다.

## 구매 문서 보존

구매 workbook은 이 작업에서 수정하지 않습니다. 기존 FSR 용도 label이 부정확하더라도 문서 파일 자체를 고치지 않고 별도 승인 작업으로 남깁니다.

<!-- petcare-docs:workbook -->
```json
{
  "path": "재료구입신청서 (워크플로우).xlsx",
  "bytes": 24663,
  "sha256": "bb58fecc63a50f4cdc0795d7937855e7b24d9bd4ba4c1377a798e1473e1458dc",
  "modified": false
}
```
