# PetCare Vision AIoT 구현 계획

## 결정된 방향

- 프로젝트명: PetCare Vision AIoT Smart Home Dashboard
- MVP 방향: Pico 2 W 센서 노드 + PC USB Webcam 객체탐지 + 웹 대시보드
- Pico 구현 언어: C++
- PC Vision 역할: USB Webcam 프레임 수집, OpenCV 전처리, YOLO `person`/`dog`/`cat` 탐지, ROI 매핑, 행동 후보 생성
- Backend 역할: FastAPI REST/MJPEG/WebSocket, MQTT sensor ingest, DB 저장, Rule Engine, 알림 연동
- Dashboard 역할: 실시간 영상, 객체 박스, 상태 카드, 센서 그래프, 이벤트 타임라인, ROI 설정

## 근거

- `aiot_smart_home_webcam_full_proposal.md`는 MVP에서 Raspberry Pi 카메라 허브보다 기존 PC + USB Webcam 구조를 우선한다.
- Pico 2 W는 영상 처리가 아니라 센서 데이터 수집, MQTT 송신, LED/부저/릴레이 제어를 담당한다.
- PC Vision은 OpenCV `VideoCapture`, YOLO 객체탐지, ROI/행동 분석을 담당한다.
- MVP 영상 전달은 구현이 쉬운 MJPEG를 우선하고, 추후 WebRTC로 확장한다.
- 데모 단계에서는 DB/MQTT는 실제 서비스로, 웹캠은 로컬 PC 프로세스로 실행하는 구성이 가장 현실적이다.

## 시스템 구성

```text
Pico 2 W sensor nodes
  - livingroom: temperature, humidity, light, motion
  - petzone: food weight, water weight, bed weight
  - entry: door, motion, buzzer/LED

PC Vision Server
  - USB Webcam
  - OpenCV frame capture and FPS limiting
  - YOLO person/dog/cat detection
  - ROI mapping: food_bowl, water_bowl, pet_bed, entrance, toilet
  - behavior candidates: eating, drinking, resting, toilet, entrance_access, fall_suspected

Backend
  - Mosquitto MQTT
  - FastAPI
  - MJPEG stream at /api/video_feed
  - WebSocket updates at /ws/dashboard
  - PostgreSQL + TimescaleDB later; SQLite acceptable only for quick demo
  - Rule Engine for no_meal_12h, low_activity_24h, entrance_risk, fall_suspected

Dashboard
  - React or Next.js
  - live camera panel, detection labels, status cards, charts, timeline, alerts, ROI editor
```

## Success Criteria

### C1: Pico MQTT sensor/status contract

Scenario:

```text
bash tools/build_pico_host.sh
```

PASS when the built demo emits:

- `home/pico/pico_petzone_01/sensor/food_weight`
- `home/pico/pico_petzone_01/status`
- JSON containing `device_id`, `sensor_type`, `value`, `unit`, `battery`, `rssi`, and `timestamp`

RED proof:

```text
python tools/pico_contract_check.py
```

Fails before the C++ contract contains proposal sensor/status topics and fields.

### C2: PC webcam detection/ROI/behavior contract

Scenario:

```text
bash tools/build_pico_host.sh
```

PASS when the built demo emits:

- `home/camera/pc_webcam_01/detection`
- `home/camera/pc_webcam_01/behavior`
- JSON containing `camera_id`, `detected_type`, `confidence`, `bbox`, `zone`, `track_id`, `timestamp`
- an `eating` behavior when a `dog` detection overlaps `food_bowl` and food weight decreases

RED proof:

```text
python tools/pico_contract_check.py
```

Fails before the C++ contract contains camera detection, ROI, and behavior inference.

### C3: Rule Engine contract

Scenario:

```text
bash tools/build_pico_host.sh
```

PASS when the built test confirms:

- `entrance_risk` creates `danger` when door is open and pet is in entrance ROI
- `no_meal_12h` warning message shape is represented
- dashboard/anomaly payload vocabulary uses `warning` and `danger`, not medical diagnosis language

RED proof:

```text
python tools/pico_contract_check.py
```

Fails before the C++ contract contains rule IDs, severity strings, and anomaly payload shape.

### C4: Build path robustness

Scenario:

```text
bash tools/build_pico_host.sh
```

PASS when the script stages sources under `C:/tools/aiot-pico-host-stage`, builds with `C:/tools/codex-winlibs`, runs CTest, and prints demo payloads without using the non-ASCII workspace path as the compiler working source.

## Implementation Waves

### Wave 1: Contract plan and C++ model layer

- Replace old camera-hub plan with this PC webcam plan.
- Extend C++ core with proposal-aligned value types:
  - `SensorReading`
  - `DeviceStatus`
  - `BoundingBox`
  - `CameraDetection`
  - `RoiZone`
  - `BehaviorEvent`
  - `AnomalyEvent`
- Keep serialization dependency-free for Pico friendliness.

### Wave 2: Behavior/rule functions

- Map detection center to ROI.
- Infer eating from pet detection in `food_bowl` plus food weight decrease.
- Infer entrance danger from door open plus pet detection in `entrance`.
- Keep medical/safety copy as `suspected`, `warning`, `danger`, and pattern-change wording.

### Wave 3: Verification tooling

- Extend `tools/pico_contract_check.py` for sensor/status, camera detection, behavior, and anomaly contracts.
- Keep `tools/build_pico_host.sh` as the primary build command because it avoids Korean-path failures in MinGW/Ninja.

## Deferred

- Full FastAPI implementation
- Real OpenCV/YOLO model loading
- React/Next.js dashboard implementation
- PostgreSQL/Timescale migrations
- Telegram/Discord credentials and live notification delivery
- Authentication/authorization

These are not skipped permanently. They are separate implementation waves once the shared data contracts are proven.
