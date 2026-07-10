# AIoT 스마트 홈 반려동물·사람 상태 분석 웹 대시보드 프로젝트 기획안

> **프로젝트명:** PetCare Vision AIoT Smart Home Dashboard  
> **작성일:** 2026-07-09  
> **기획 방향:** Raspberry Pi Pico 2 W 센서 노드 + PC 웹캠 객체탐지 + 웹 대시보드  
> **핵심 목표:** 집 안의 사람, 강아지, 고양이의 현재 상태·행동 패턴·이상징후를 실시간으로 분석하고 웹 대시보드에 표시한다.

---

## 1. 프로젝트 개요

이 프로젝트는 집 안에 설치된 **Pico 2 W 센서 노드**와 **PC에 연결된 USB 웹캠**을 활용하여 사람과 반려동물의 현재 상태, 생활 패턴, 이상징후를 감지하는 AIoT 스마트 홈 시스템이다.

초기 설계에서는 Raspberry Pi 카메라 허브를 고려할 수 있지만, MVP 단계에서는 **기존 PC + USB Webcam** 구조가 더 현실적이다. PC는 웹캠 영상을 읽고 객체탐지를 수행하며, Pico 2 W는 온습도, 움직임, 문열림, 밥그릇/물그릇 무게 같은 센서 데이터를 수집한다. 모든 데이터는 FastAPI 백엔드와 MQTT를 통해 통합되고, React 또는 Next.js 기반 웹 대시보드에서 실시간으로 시각화된다.

### 한 줄 요약

**PC는 눈, Pico 2 W는 집 안의 감각기관, 웹 대시보드는 관제센터 역할을 하는 AIoT 스마트 홈 시스템**이다.

---

## 2. 프로젝트 배경 및 필요성

### 2.1 해결하려는 문제

1. 집을 비운 동안 반려동물이 잘 지내는지 확인하기 어렵다.
2. 강아지나 고양이의 식사, 음수, 활동량, 휴식 패턴 변화를 놓치기 쉽다.
3. 혼자 사는 사람이나 고령자의 낙상, 장시간 미움직임 같은 이상 상황을 빠르게 감지하기 어렵다.
4. 일반 CCTV는 영상을 보여줄 뿐, 상태 분석이나 이상징후 판단까지 제공하지 못한다.
5. 센서 데이터와 영상 데이터가 분리되어 있으면 신뢰도 높은 상태 판단이 어렵다.

### 2.2 해결 방향

이 프로젝트는 카메라 객체탐지와 IoT 센서 데이터를 결합하여 다음을 제공한다.

- 사람, 강아지, 고양이 실시간 감지
- 현재 위치와 활동 상태 파악
- 밥그릇, 물그릇, 침대, 현관 등 특정 구역 접근 감지
- 식사, 음수, 휴식, 화장실 사용 추정
- 평소 패턴 대비 이상징후 감지
- 웹 대시보드 실시간 표시
- 위험 상황 발생 시 알림 전송

---

## 3. 프로젝트 목표

### 3.1 최종 목표

PC 웹캠 기반 객체탐지와 Pico 2 W 센서 데이터를 융합하여, 사람과 반려동물의 상태를 실시간으로 분석하고 웹 대시보드에 표시하는 AIoT 스마트 홈 플랫폼을 구축한다.

### 3.2 세부 목표

| 구분 | 목표 |
|---|---|
| 영상 분석 | 웹캠 영상에서 사람, 강아지, 고양이를 탐지한다. |
| 센서 수집 | Pico 2 W로 온습도, 움직임, 문열림, 무게 데이터를 수집한다. |
| 데이터 융합 | 카메라 이벤트와 센서 데이터를 결합해 행동을 추정한다. |
| 이상징후 감지 | 활동량 급감, 식사 없음, 장시간 미움직임, 낙상 의심 등을 감지한다. |
| 대시보드 | 실시간 영상, 객체탐지 박스, 상태 카드, 이벤트 타임라인, 그래프를 제공한다. |
| 알림 | 위험 또는 주의 이벤트 발생 시 Telegram, Discord, Push 등으로 알림을 보낸다. |
| 보안/프라이버시 | 로컬 우선 처리, 인증, 영상 저장 최소화, 보관 기간 제한을 적용한다. |

---

## 4. 전체 시스템 컨셉

### 4.1 역할 분리

| 구성 요소 | 역할 |
|---|---|
| PC + USB Webcam | 실시간 영상 수집, 객체탐지, 객체 박스 표시, 행동 분석 |
| Pico 2 W | 센서 데이터 수집, MQTT 전송, 부저/LED/릴레이 제어 |
| MQTT Broker | Pico 센서 데이터와 시스템 이벤트 메시지 중계 |
| FastAPI Backend | API 서버, 영상 스트림, WebSocket, DB 저장, 이상징후 판단 |
| PostgreSQL/TimescaleDB | 센서 시계열 데이터, 탐지 이벤트, 이상징후 로그 저장 |
| React/Next.js Dashboard | 실시간 영상, 상태 카드, 그래프, 타임라인, 설정 UI 표시 |
| Alert Service | 위험 상황 발생 시 외부 알림 전송 |

### 4.2 MVP 기준 핵심 기능

MVP에서는 너무 많은 AI 기능을 한 번에 구현하기보다, 다음 기능을 우선 구현한다.

1. 웹캠 실시간 화면 대시보드 표시
2. 사람, 강아지, 고양이 객체탐지
3. 객체 박스와 confidence 표시
4. Pico 2 W 센서 데이터 MQTT 전송
5. 온습도, 움직임, 문열림, 밥그릇 무게 표시
6. 감지 이벤트 DB 저장
7. 활동량, 식사 추정, 장시간 미움직임 같은 기본 이상징후 감지
8. 대시보드 이벤트 타임라인 표시

---

## 5. 최종 권장 아키텍처

```mermaid
flowchart LR
    subgraph SensorNodes["Pico 2 W 센서 노드"]
        P1["Pico 2 W #1\n거실 센서\n온습도 / 조도 / PIR"]
        P2["Pico 2 W #2\n반려동물 구역\n밥그릇 무게 / 물그릇 무게 / 침대 무게"]
        P3["Pico 2 W #3\n현관/문 센서\n문열림 / 움직임 / 부저"]
    end

    subgraph PCVision["PC Vision Server"]
        CAM["USB Webcam"]
        CV["OpenCV\n프레임 수집"]
        YOLO["YOLO 객체탐지\nperson / dog / cat"]
        TRACK["객체 추적\nROI 분석\n행동 추정"]
        CAM --> CV --> YOLO --> TRACK
    end

    subgraph Backend["Backend Server"]
        MQTT["Mosquitto MQTT Broker"]
        API["FastAPI Backend"]
        WS["WebSocket / MJPEG Stream"]
        DB["PostgreSQL + TimescaleDB"]
        OBJ["Local Storage / MinIO\n이벤트 이미지 저장"]
        RULE["Rule Engine\n이상징후 판단"]
    end

    subgraph Dashboard["Web Dashboard"]
        WEB["React / Next.js"]
        LIVE["실시간 카메라"]
        STATUS["현재 상태 카드"]
        CHART["활동량 / 식사 / 센서 그래프"]
        ALERT["알림 화면"]
    end

    P1 -->|MQTT 센서 데이터| MQTT
    P2 -->|MQTT 센서 데이터| MQTT
    P3 -->|MQTT 센서 데이터| MQTT

    TRACK -->|탐지 결과 JSON| API
    TRACK -->|박스 포함 영상 스트림| WS
    TRACK -->|이벤트 이미지| OBJ

    MQTT --> API
    API --> DB
    API --> RULE
    RULE --> ALERT
    API --> WEB
    WS --> LIVE
    DB --> STATUS
    DB --> CHART
    OBJ --> WEB
```

---

## 6. 데이터 흐름도

```mermaid
sequenceDiagram
    participant Pico as Pico 2 W
    participant MQTT as MQTT Broker
    participant Cam as USB Webcam
    participant Vision as PC Vision Agent
    participant API as FastAPI Backend
    participant DB as Database
    participant Web as Dashboard
    participant Alert as Alert Service

    Pico->>MQTT: 센서 데이터 publish
    MQTT->>API: 센서 데이터 subscribe
    API->>DB: 센서 데이터 저장

    Cam->>Vision: 실시간 프레임 입력
    Vision->>Vision: OpenCV 프레임 전처리
    Vision->>Vision: YOLO 객체탐지
    Vision->>Vision: ROI/행동/이상징후 1차 분석
    Vision->>API: 탐지 결과 JSON 전송
    Vision->>Web: 박스 포함 영상 스트리밍
    API->>DB: 탐지 이벤트 저장

    API->>API: 센서 + 탐지 결과 융합
    API->>DB: 행동 이벤트 저장
    API->>Web: WebSocket 상태 업데이트

    API->>Alert: 이상징후 알림 요청
    Alert->>Web: 알림 상태 반영
```

---

## 7. 하드웨어 구성

### 7.1 필수 하드웨어

| 구분 | 부품 | 역할 | 비고 |
|---|---|---|---|
| 비전 서버 | 기존 PC 또는 노트북 | 웹캠 수집, 객체탐지, 백엔드 실행 | GPU가 있으면 성능 향상 |
| 카메라 | USB Webcam | 실시간 영상 입력 | 720p 또는 1080p 권장 |
| 센서 노드 | Raspberry Pi Pico 2 W | 센서 데이터 수집 및 MQTT 전송 | Wi-Fi 사용 |
| 움직임 감지 | PIR 센서 또는 mmWave 센서 | 사람/반려동물 움직임 감지 | PIR은 저렴, mmWave는 정밀 |
| 환경 센서 | SHT31, BME280, DHT22 등 | 온도, 습도 측정 | BME280은 기압도 가능 |
| 무게 센서 | Load Cell + HX711 | 밥그릇, 물그릇, 침대 무게 변화 감지 | 식사/음수/휴식 추정에 유용 |
| 문 센서 | Magnetic Reed Switch | 문열림/현관 접근 감지 | 현관 위험 알림에 유용 |
| 출력 장치 | LED, 부저 | 상태 표시, 경고음 | 선택 사항 |

### 7.2 권장 센서 노드 구성

#### Node 1: 거실 환경/움직임 노드

```text
Pico 2 W
- 온습도 센서: SHT31 또는 BME280
- 조도 센서: BH1750 또는 LDR
- 움직임 센서: PIR 또는 mmWave
- 상태 LED
```

역할:

- 거실 환경 상태 측정
- 사람/반려동물 움직임 감지
- 카메라 분석 결과와 비교하여 움직임 신뢰도 향상

#### Node 2: 반려동물 케어 노드

```text
Pico 2 W
- 밥그릇 Load Cell + HX711
- 물그릇 Load Cell + HX711 또는 수위센서
- 침대/방석 Load Cell + HX711
```

역할:

- 식사량 변화 감지
- 물 섭취량 변화 감지
- 침대/방석 체류 여부 추정
- 카메라 ROI 분석과 결합하여 행동 이벤트 생성

#### Node 3: 현관/위험구역 노드

```text
Pico 2 W
- 문열림 센서
- PIR 또는 mmWave 센서
- 부저 또는 LED
```

역할:

- 현관 문열림 감지
- 반려동물 현관 접근 감지
- 외출/귀가 이벤트 기록
- 위험 상황 알림

### 7.3 Pico 2 W 사용 이유

Pico 2 W는 마이크로컨트롤러 기반 보드로, Wi-Fi를 이용한 IoT 센서 노드 구현에 적합하다. 영상 처리나 객체탐지는 PC에서 수행하고, Pico 2 W는 센서 수집과 간단한 제어를 담당하도록 역할을 분리한다.

Pico 2 W가 적합한 작업:

- 센서값 읽기
- MQTT 메시지 전송
- 저전력 상태 모니터링
- LED, 부저, 릴레이 제어
- 일정 주기 데이터 전송

Pico 2 W가 부적합한 작업:

- 고해상도 영상 처리
- 실시간 YOLO 객체탐지
- 대규모 AI 모델 추론
- 장시간 영상 스트리밍

---

## 8. 소프트웨어 기술 스택

### 8.1 권장 스택

| 계층 | 기술 | 선정 이유 |
|---|---|---|
| 임베디드 | MicroPython 또는 C/C++ SDK | Pico 2 W 센서 제어, 빠른 개발 |
| 센서 통신 | MQTT | IoT 메시지 통신에 적합, publish/subscribe 구조 |
| MQTT Broker | Mosquitto | 가볍고 로컬 개발에 적합 |
| 영상 처리 | OpenCV | 웹캠 프레임 수집 및 이미지 처리 표준 도구 |
| 객체탐지 | Ultralytics YOLO 계열 | person/dog/cat 탐지 MVP 구현이 쉬움 |
| 백엔드 | FastAPI | Python AI 코드와 통합이 쉽고 WebSocket 지원 |
| 실시간 영상 | MJPEG Stream 또는 WebSocket | MVP는 MJPEG가 쉬움, 추후 WebRTC 확장 가능 |
| 실시간 상태 | WebSocket | 탐지 결과와 센서 상태 실시간 push |
| DB | PostgreSQL + TimescaleDB | 이벤트/시계열 데이터 저장에 적합 |
| 프론트엔드 | React 또는 Next.js | 대시보드 구현에 적합 |
| 차트 | Recharts, Chart.js, ECharts | 활동량/센서 그래프 구현 |
| 알림 | Telegram Bot, Discord Webhook, Push API | 빠른 MVP 구현 가능 |
| 배포 | Docker Compose | MQTT, DB, API, Dashboard 통합 실행 |

### 8.2 기술 선택 비교

#### MQTT vs HTTP

| 항목 | MQTT | HTTP |
|---|---|---|
| 구조 | Pub/Sub | Request/Response |
| 센서 데이터 | 매우 적합 | 가능하지만 비효율적일 수 있음 |
| 실시간성 | 좋음 | 폴링 필요 가능 |
| 구현 난이도 | 중간 | 쉬움 |
| 권장 사용 | Pico 센서 데이터 | 대시보드 API, 설정 API |

권장: **Pico 2 W → MQTT**, **Dashboard/API → HTTP + WebSocket**

#### MJPEG vs WebSocket vs WebRTC

| 방식 | 장점 | 단점 | 추천 단계 |
|---|---|---|---|
| MJPEG | 구현이 매우 쉬움, `<img>` 태그로 표시 가능 | 압축 효율 낮음, 지연/대역폭 한계 | MVP |
| WebSocket Binary | 탐지 결과와 이미지 프레임을 같은 채널로 보낼 수 있음 | 프론트 구현 복잡도 증가 | 중급 |
| WebRTC | 저지연 실시간 영상에 강함 | 구현 난이도 높음, NAT/시그널링 필요 | 고도화 |

권장: **MVP는 MJPEG**, 추후 **WebRTC**로 확장

#### PostgreSQL vs SQLite

| 항목 | PostgreSQL | SQLite |
|---|---|---|
| 장점 | 확장성, 동시성, 서버 운영 적합 | 설치 간단, 단일 파일 |
| 단점 | 초기 설정 필요 | 동시성/확장성 한계 |
| 추천 | 최종 프로젝트 | 빠른 데모 |

권장: 데모만 빠르게 만들면 SQLite, 기획안/완성도 기준은 PostgreSQL.

---

## 9. AI 객체탐지 및 행동 분석 설계

### 9.1 웹캠 객체탐지 파이프라인

```text
USB Webcam
→ OpenCV VideoCapture
→ 프레임 resize / FPS 제한
→ YOLO 객체탐지
→ person/dog/cat 필터링
→ bbox 표시
→ ROI 구역 매핑
→ 객체 추적
→ 행동 이벤트 생성
→ FastAPI/DB/Dashboard 전송
```

### 9.2 탐지 대상

| 클래스 | 설명 | 대시보드 활용 |
|---|---|---|
| person | 사람 | 재실 여부, 낙상 의심, 장시간 미움직임 |
| dog | 강아지 | 활동량, 위치, 식사/휴식 패턴 |
| cat | 고양이 | 활동량, 위치, 화장실/식사/휴식 패턴 |

### 9.3 탐지 결과 데이터 구조

```json
{
  "camera_id": "pc_webcam_01",
  "detected_type": "dog",
  "confidence": 0.87,
  "bbox": {
    "x": 214,
    "y": 152,
    "w": 180,
    "h": 130
  },
  "zone": "living_room",
  "track_id": "dog_001",
  "timestamp": "2026-07-09T17:00:00+09:00"
}
```

### 9.4 ROI 기반 구역 분석

대시보드 또는 설정 파일에서 카메라 화면의 주요 구역을 정의한다.

```json
{
  "zones": [
    {
      "zone_id": "food_bowl",
      "name": "밥그릇",
      "x1": 100,
      "y1": 350,
      "x2": 300,
      "y2": 520
    },
    {
      "zone_id": "water_bowl",
      "name": "물그릇",
      "x1": 320,
      "y1": 350,
      "x2": 480,
      "y2": 520
    },
    {
      "zone_id": "pet_bed",
      "name": "방석/침대",
      "x1": 520,
      "y1": 260,
      "x2": 900,
      "y2": 520
    },
    {
      "zone_id": "entrance",
      "name": "현관",
      "x1": 0,
      "y1": 0,
      "x2": 250,
      "y2": 250
    }
  ]
}
```

ROI 활용 예시:

- 강아지가 밥그릇 ROI에 30초 이상 머무름 → 식사 후보 이벤트
- 고양이가 화장실 ROI에 45초 이상 머무름 → 화장실 사용 후보 이벤트
- 반려동물이 현관 ROI에 2분 이상 머무름 → 현관 접근 주의 이벤트
- 사람이 바닥 근처 ROI에서 장시간 미움직임 → 낙상 의심 이벤트

### 9.5 행동 추정 로직

#### 식사 감지

```text
조건 1: dog 또는 cat이 food_bowl ROI에 30초 이상 머무름
조건 2: 밥그릇 Load Cell 무게가 일정량 이상 감소
판단: eating 이벤트 생성
```

#### 물 마심 감지

```text
조건 1: dog 또는 cat이 water_bowl ROI에 15초 이상 머무름
조건 2: 물그릇 무게 또는 수위가 감소
판단: drinking 이벤트 생성
```

#### 휴식/수면 추정

```text
조건 1: dog 또는 cat이 pet_bed ROI에 있음
조건 2: bbox 중심 이동량이 작음
조건 3: 침대 Load Cell 무게 변화 감지
조건 4: 10분 이상 지속
판단: resting 또는 sleeping 이벤트 생성
```

#### 활동량 분석

```text
입력: 객체 bbox 중심 좌표 변화량
처리: 1분/5분/1시간 단위 이동량 집계
출력: low / normal / high 활동 상태
```

#### 사람 낙상 의심

```text
조건 1: person 탐지됨
조건 2: bbox 높이/너비 비율이 급격히 변함
조건 3: 바닥 근처 영역에서 2~5분 이상 움직임 없음
조건 4: PIR/mmWave 센서에서도 움직임 낮음
판단: fall_suspected 이벤트 생성
```

주의: 이 시스템은 의료 진단 장비가 아니므로 “낙상 확정”, “질병 진단”처럼 표현하지 않는다. 대시보드에는 “낙상 의심”, “활동량 감소”, “식사 패턴 변화”처럼 표시한다.

### 9.6 이상징후 판단 예시

| 이상징후 | 조건 예시 | 심각도 |
|---|---|---|
| 장시간 식사 없음 | 12시간 이상 식사 이벤트 없음 | warning |
| 물 섭취 급증 | 최근 24시간 물 감소량이 평소 대비 2배 이상 | warning |
| 활동량 급감 | 최근 24시간 이동량이 7일 평균 대비 50% 이하 | warning |
| 장시간 미움직임 | 사람 또는 반려동물이 특정 위치에서 2시간 이상 거의 움직이지 않음 | warning |
| 사람 낙상 의심 | 바닥 근처에서 3분 이상 움직임 없음 | danger |
| 현관 접근 위험 | 문열림 상태 + 반려동물이 현관 ROI 접근 | danger |
| 고온/저온 환경 | 실내 온도가 설정 범위 초과 | warning |

---

## 10. Pico 2 W 센서 데이터 설계

### 10.1 MQTT 토픽 구조

```text
home/pico/{device_id}/status
home/pico/{device_id}/sensor/temperature
home/pico/{device_id}/sensor/humidity
home/pico/{device_id}/sensor/light
home/pico/{device_id}/sensor/motion
home/pico/{device_id}/sensor/door
home/pico/{device_id}/sensor/food_weight
home/pico/{device_id}/sensor/water_weight
home/pico/{device_id}/sensor/bed_weight

home/camera/{camera_id}/detection
home/camera/{camera_id}/behavior
home/camera/{camera_id}/anomaly

home/alert/warning
home/alert/danger
```

### 10.2 센서 메시지 예시

```json
{
  "device_id": "pico_petzone_01",
  "sensor_type": "food_weight",
  "value": 182.4,
  "unit": "g",
  "battery": 92,
  "rssi": -51,
  "timestamp": "2026-07-09T17:05:00+09:00"
}
```

### 10.3 디바이스 상태 메시지 예시

```json
{
  "device_id": "pico_livingroom_01",
  "status": "online",
  "firmware_version": "0.1.0",
  "ip": "192.168.0.23",
  "uptime_sec": 3600,
  "timestamp": "2026-07-09T17:05:00+09:00"
}
```

### 10.4 Pico 센서 송신 주기

| 데이터 | 권장 주기 | 설명 |
|---|---:|---|
| 온도/습도 | 30초~60초 | 실시간성이 높지 않아도 됨 |
| 조도 | 30초~60초 | 야간/주간 판단 |
| PIR 움직임 | 이벤트 즉시 | 감지 시 바로 전송 |
| 문열림 | 이벤트 즉시 | 열림/닫힘 변화 발생 시 전송 |
| 밥그릇 무게 | 5초~30초 | 식사 감지 정확도와 전력 고려 |
| 물그릇 무게 | 10초~60초 | 큰 변화 중심 감지 |
| 침대 무게 | 10초~30초 | 휴식/수면 추정 |

---

## 11. 백엔드 설계

### 11.1 백엔드 책임

FastAPI 백엔드는 다음 역할을 담당한다.

1. 웹캠 영상 스트림 제공
2. 객체탐지 결과 수신 및 저장
3. MQTT 센서 데이터 수신 및 저장
4. 센서 데이터와 카메라 이벤트 융합
5. 이상징후 판단
6. 대시보드 REST API 제공
7. WebSocket 실시간 상태 전송
8. 알림 서비스 연동
9. 사용자 설정 및 ROI 설정 관리

### 11.2 서버 구성

```text
backend/
├── main.py                 # FastAPI 앱 진입점
├── config.py               # 환경 변수, 설정값
├── vision/
│   ├── camera.py           # OpenCV 웹캠 캡처
│   ├── detector.py         # YOLO 객체탐지
│   ├── tracker.py          # 객체 추적
│   ├── roi.py              # ROI 구역 매핑
│   └── stream.py           # MJPEG/WebSocket 영상 스트림
├── mqtt/
│   ├── client.py           # MQTT subscriber
│   └── handlers.py         # 토픽별 처리
├── rules/
│   ├── engine.py           # 이상징후 Rule Engine
│   ├── baseline.py         # 평소 패턴 계산
│   └── alerts.py           # 알림 조건 판단
├── api/
│   ├── devices.py
│   ├── sensors.py
│   ├── camera.py
│   ├── events.py
│   ├── dashboard.py
│   └── settings.py
├── db/
│   ├── database.py
│   ├── models.py
│   └── migrations/
└── services/
    ├── notification.py
    ├── storage.py
    └── websocket_manager.py
```

### 11.3 REST API 설계

| Method | Endpoint | 설명 |
|---|---|---|
| GET | `/health` | 서버 상태 확인 |
| GET | `/api/devices` | 등록된 Pico/카메라 목록 |
| POST | `/api/devices` | 디바이스 등록 |
| GET | `/api/sensors/latest` | 최신 센서값 조회 |
| GET | `/api/sensors/history` | 센서 히스토리 조회 |
| GET | `/api/camera/status` | 카메라 상태 조회 |
| GET | `/api/camera/events` | 카메라 탐지 이벤트 조회 |
| GET | `/api/video_feed` | MJPEG 실시간 영상 스트림 |
| GET | `/api/behaviors` | 행동 이벤트 조회 |
| GET | `/api/anomalies` | 이상징후 이벤트 조회 |
| POST | `/api/zones` | ROI 구역 등록 |
| GET | `/api/zones` | ROI 구역 조회 |
| PUT | `/api/zones/{zone_id}` | ROI 구역 수정 |
| GET | `/api/dashboard/summary` | 대시보드 요약 정보 |
| POST | `/api/alert-rules` | 알림 규칙 생성 |
| PUT | `/api/alert-rules/{rule_id}` | 알림 규칙 수정 |

### 11.4 WebSocket 메시지 설계

WebSocket endpoint:

```text
/ws/dashboard
```

대시보드로 전달되는 메시지 예시:

```json
{
  "type": "dashboard_update",
  "payload": {
    "home_status": "normal",
    "detected": {
      "person": 1,
      "dog": 1,
      "cat": 0
    },
    "latest_sensors": {
      "temperature": 24.2,
      "humidity": 48.5,
      "food_weight": 182.4
    },
    "latest_event": {
      "event_type": "dog_detected",
      "message": "강아지가 거실에서 감지되었습니다.",
      "created_at": "2026-07-09T17:10:00+09:00"
    }
  }
}
```

이상징후 메시지 예시:

```json
{
  "type": "anomaly_alert",
  "payload": {
    "severity": "warning",
    "subject_type": "dog",
    "anomaly_type": "low_activity",
    "message": "강아지의 최근 24시간 활동량이 평소보다 55% 감소했습니다.",
    "created_at": "2026-07-09T17:10:00+09:00"
  }
}
```

---

## 12. 데이터베이스 설계

### 12.1 주요 엔티티

```mermaid
erDiagram
    DEVICES ||--o{ SENSOR_READINGS : produces
    CAMERAS ||--o{ CAMERA_EVENTS : produces
    CAMERA_EVENTS ||--o{ BEHAVIOR_EVENTS : derives
    SENSOR_READINGS ||--o{ BEHAVIOR_EVENTS : supports
    BEHAVIOR_EVENTS ||--o{ ANOMALY_EVENTS : triggers
    ZONES ||--o{ CAMERA_EVENTS : maps
    ALERT_RULES ||--o{ NOTIFICATIONS : creates
    ANOMALY_EVENTS ||--o{ NOTIFICATIONS : sends
```

### 12.2 테이블 설계

#### devices

```sql
CREATE TABLE devices (
    id BIGSERIAL PRIMARY KEY,
    device_id VARCHAR(80) UNIQUE NOT NULL,
    device_name VARCHAR(100) NOT NULL,
    device_type VARCHAR(50) NOT NULL, -- pico, camera, server
    location VARCHAR(100),
    status VARCHAR(30) DEFAULT 'unknown', -- online, offline, unknown
    firmware_version VARCHAR(50),
    ip_address VARCHAR(50),
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);
```

#### sensor_readings

```sql
CREATE TABLE sensor_readings (
    id BIGSERIAL PRIMARY KEY,
    device_id VARCHAR(80) NOT NULL,
    sensor_type VARCHAR(50) NOT NULL,
    value DOUBLE PRECISION NOT NULL,
    unit VARCHAR(20),
    battery DOUBLE PRECISION,
    rssi DOUBLE PRECISION,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX idx_sensor_readings_device_time
ON sensor_readings (device_id, created_at DESC);

CREATE INDEX idx_sensor_readings_type_time
ON sensor_readings (sensor_type, created_at DESC);
```

#### cameras

```sql
CREATE TABLE cameras (
    id BIGSERIAL PRIMARY KEY,
    camera_id VARCHAR(80) UNIQUE NOT NULL,
    camera_name VARCHAR(100) NOT NULL,
    source_type VARCHAR(30) NOT NULL, -- usb, rtsp, file
    source_uri VARCHAR(255), -- usb camera는 '0', rtsp는 URL
    width INT,
    height INT,
    fps INT,
    status VARCHAR(30) DEFAULT 'unknown',
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);
```

#### zones

```sql
CREATE TABLE zones (
    id BIGSERIAL PRIMARY KEY,
    camera_id VARCHAR(80) NOT NULL,
    zone_id VARCHAR(80) NOT NULL,
    zone_name VARCHAR(100) NOT NULL,
    zone_type VARCHAR(50), -- food, water, bed, entrance, toilet, living_room
    x1 INT NOT NULL,
    y1 INT NOT NULL,
    x2 INT NOT NULL,
    y2 INT NOT NULL,
    enabled BOOLEAN DEFAULT TRUE,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE(camera_id, zone_id)
);
```

#### camera_events

```sql
CREATE TABLE camera_events (
    id BIGSERIAL PRIMARY KEY,
    camera_id VARCHAR(80) NOT NULL,
    detected_type VARCHAR(30) NOT NULL, -- person, dog, cat
    track_id VARCHAR(80),
    confidence DOUBLE PRECISION NOT NULL,
    bbox_x INT,
    bbox_y INT,
    bbox_w INT,
    bbox_h INT,
    center_x INT,
    center_y INT,
    zone_id VARCHAR(80),
    image_path TEXT,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX idx_camera_events_type_time
ON camera_events (detected_type, created_at DESC);

CREATE INDEX idx_camera_events_camera_time
ON camera_events (camera_id, created_at DESC);
```

#### behavior_events

```sql
CREATE TABLE behavior_events (
    id BIGSERIAL PRIMARY KEY,
    subject_type VARCHAR(30) NOT NULL, -- person, dog, cat
    subject_id VARCHAR(80),
    behavior_type VARCHAR(50) NOT NULL,
    -- detected, moving, eating, drinking, resting, sleeping, toilet, entrance_access, fall_suspected
    zone_id VARCHAR(80),
    confidence DOUBLE PRECISION,
    duration_sec INT,
    evidence JSONB,
    message TEXT,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX idx_behavior_events_subject_time
ON behavior_events (subject_type, created_at DESC);

CREATE INDEX idx_behavior_events_type_time
ON behavior_events (behavior_type, created_at DESC);
```

#### anomaly_events

```sql
CREATE TABLE anomaly_events (
    id BIGSERIAL PRIMARY KEY,
    subject_type VARCHAR(30), -- person, dog, cat, home
    subject_id VARCHAR(80),
    anomaly_type VARCHAR(80) NOT NULL,
    severity VARCHAR(20) NOT NULL, -- info, warning, danger
    score DOUBLE PRECISION,
    message TEXT NOT NULL,
    evidence JSONB,
    resolved BOOLEAN DEFAULT FALSE,
    resolved_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX idx_anomaly_events_severity_time
ON anomaly_events (severity, created_at DESC);
```

#### alert_rules

```sql
CREATE TABLE alert_rules (
    id BIGSERIAL PRIMARY KEY,
    rule_id VARCHAR(80) UNIQUE NOT NULL,
    rule_name VARCHAR(100) NOT NULL,
    target_type VARCHAR(30), -- person, dog, cat, home
    condition_type VARCHAR(80),
    threshold_value DOUBLE PRECISION,
    threshold_unit VARCHAR(30),
    severity VARCHAR(20),
    enabled BOOLEAN DEFAULT TRUE,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);
```

#### notifications

```sql
CREATE TABLE notifications (
    id BIGSERIAL PRIMARY KEY,
    anomaly_event_id BIGINT,
    channel VARCHAR(50) NOT NULL, -- telegram, discord, push, email
    recipient VARCHAR(255),
    title VARCHAR(255),
    message TEXT,
    status VARCHAR(30) DEFAULT 'pending', -- pending, sent, failed
    sent_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ DEFAULT NOW()
);
```

---

## 13. 웹 대시보드 설계

### 13.1 화면 구성

```text
Dashboard
├── 홈 요약 화면
├── 실시간 카메라 화면
├── 반려동물 상태 화면
├── 사람 상태/안전 화면
├── 센서 그래프 화면
├── 이벤트 타임라인
├── 이상징후/알림 화면
├── ROI 구역 설정 화면
└── 디바이스 관리 화면
```

### 13.2 홈 요약 화면

표시 항목:

- 현재 집 상태: 정상 / 주의 / 위험
- 현재 감지 대상: 사람 N명, 강아지 N마리, 고양이 N마리
- 현재 위치: 거실, 밥그릇, 침대, 현관 등
- 온도, 습도, 조도
- 밥그릇/물그릇 현재 무게
- 최근 이벤트 5개
- 최근 이상징후

예시:

```text
집 상태: 정상
현재 감지: 사람 1명, 강아지 1마리, 고양이 0마리
강아지 위치: 거실
고양이 위치: 최근 2시간 감지 없음
온도: 24.2°C
습도: 48%
밥그릇: 182g
최근 이상징후: 없음
```

### 13.3 실시간 카메라 화면

표시 항목:

- 웹캠 실시간 영상
- 객체탐지 bounding box
- `person`, `dog`, `cat` label
- confidence score
- ROI 구역 overlay
- FPS / inference time
- 탐지 이벤트 로그

예시 화면 컨셉:

```text
┌───────────────────────────────────────────────┐
│  person 0.91                                  │
│  ┌───────────────┐                            │
│  │               │                            │
│  └───────────────┘                            │
│                                               │
│                dog 0.88                       │
│                ┌─────────┐                    │
│                │         │                    │
│                └─────────┘                    │
│                                               │
│ [ROI] 밥그릇   [ROI] 물그릇   [ROI] 방석       │
└───────────────────────────────────────────────┘
```

### 13.4 반려동물 상태 화면

표시 항목:

- 오늘 활동량
- 최근 7일 활동량 비교
- 식사 횟수 및 추정 식사량
- 물 마신 횟수 및 추정 음수량
- 휴식/수면 시간
- 화장실 사용 추정 횟수
- 평소 대비 변화율
- 주의 메시지

예시:

```text
강아지 상태
- 오늘 활동량: 보통
- 식사 횟수: 2회
- 마지막 식사: 13:20
- 물 마심 추정: 4회
- 휴식 시간: 5시간 20분
- 특이사항: 최근 24시간 활동량이 7일 평균보다 18% 낮습니다.
```

### 13.5 이벤트 타임라인

예시:

```text
17:01 강아지 감지 - 거실
17:03 강아지 밥그릇 영역 접근
17:04 밥그릇 무게 18g 감소
17:04 식사 이벤트 생성
17:20 사람 감지 - 거실
17:32 고양이 감지 - 방석
18:10 현관 문열림 감지
18:11 강아지 현관 접근 주의
```

### 13.6 ROI 설정 화면

기능:

- 카메라 화면 위에서 드래그로 구역 생성
- 구역 이름 설정: 밥그릇, 물그릇, 방석, 현관, 화장실
- 구역별 알림 조건 설정
- JSON으로 ROI 저장

---

## 14. 알림 시스템 설계

### 14.1 알림 채널

| 채널 | 장점 | 추천 단계 |
|---|---|---|
| Telegram Bot | 구현 쉬움, 개인 프로젝트에 적합 | MVP |
| Discord Webhook | 구현 쉬움, 팀 프로젝트 알림에 적합 | MVP |
| Email | 기본적인 알림 가능 | 선택 |
| Browser Push | 웹앱 완성도 향상 | 고도화 |
| Kakao 알림 | 사용자 친화적 | 실제 서비스화 단계 |

### 14.2 알림 예시

#### warning

```text
[주의] 강아지 활동량 감소
최근 24시간 활동량이 7일 평균 대비 55% 감소했습니다.
식사 이벤트도 평소보다 적습니다.
```

#### danger

```text
[위험] 낙상 의심
사람이 바닥 근처 영역에서 3분 이상 움직이지 않는 상태로 감지되었습니다.
즉시 확인이 필요합니다.
```

#### info

```text
[정보] 식사 이벤트
강아지가 13:20에 밥그릇 영역에 접근했고, 사료 무게가 18g 감소했습니다.
```

---

## 15. 보안 및 프라이버시 설계

### 15.1 기본 원칙

1. 영상은 가능하면 로컬 PC에서 처리한다.
2. 원본 영상을 장기간 저장하지 않는다.
3. 이벤트 발생 시에만 썸네일 또는 짧은 클립을 저장한다.
4. 얼굴, 사생활 영역은 선택적으로 blur 처리한다.
5. 대시보드는 로그인 후 접근하게 한다.
6. 외부 접속 시 HTTPS를 사용한다.
7. MQTT Broker는 인증 정보를 요구한다.
8. 관리자 설정 화면은 별도 권한을 적용한다.

### 15.2 영상 저장 정책

| 데이터 | 저장 여부 | 보관 기간 |
|---|---|---|
| 실시간 원본 영상 | 기본 미저장 | 없음 |
| 객체탐지 메타데이터 | 저장 | 3개월~1년 |
| 이벤트 썸네일 | 선택 저장 | 7일~30일 |
| 이상징후 이벤트 이미지 | 선택 저장 | 30일~90일 |
| 센서 시계열 데이터 | 저장 | 6개월~1년 |

### 15.3 개인정보 보호 기능

- 얼굴 blur 옵션
- 특정 시간대 카메라 비활성화
- 특정 구역 privacy mask 설정
- 관리자만 영상 접근 가능
- 접근 로그 기록
- 영상 다운로드 제한
- 외부 클라우드 전송 최소화

---

## 16. 이상징후 Rule Engine 설계

### 16.1 Rule Engine 구조

```text
입력 데이터
- camera_events
- behavior_events
- sensor_readings
- baseline statistics

처리
- threshold rule
- duration rule
- frequency rule
- baseline comparison rule
- sensor fusion rule

출력
- anomaly_event
- notification
- dashboard_update
```

### 16.2 Rule 예시

#### Rule 1: 식사 없음

```yaml
rule_id: no_meal_12h
name: 12시간 이상 식사 없음
subject_type: dog
condition:
  behavior_type: eating
  no_event_duration_hours: 12
severity: warning
message: "강아지가 12시간 이상 식사한 기록이 없습니다."
```

#### Rule 2: 활동량 급감

```yaml
rule_id: low_activity_24h
name: 24시간 활동량 급감
subject_type: cat
condition:
  metric: movement_distance_24h
  compare_to: avg_7d
  below_ratio: 0.5
severity: warning
message: "고양이의 최근 24시간 활동량이 평소 대비 50% 이하입니다."
```

#### Rule 3: 현관 접근 위험

```yaml
rule_id: entrance_risk
name: 현관 접근 위험
subject_type: dog
condition:
  zone_id: entrance
  door_status: open
  stay_duration_sec: 10
severity: danger
message: "현관문이 열린 상태에서 반려동물이 현관에 접근했습니다."
```

#### Rule 4: 낙상 의심

```yaml
rule_id: fall_suspected
name: 사람 낙상 의심
subject_type: person
condition:
  zone_type: floor
  no_movement_duration_sec: 180
  bbox_ratio_changed: true
severity: danger
message: "사람이 바닥 근처에서 장시간 움직이지 않아 낙상이 의심됩니다."
```

---

## 17. 개발 일정

### 17.1 8주 개발 계획

| 주차 | 목표 | 주요 작업 | 산출물 |
|---|---|---|---|
| 1주차 | 요구사항/환경 구축 | Git 저장소, Docker Compose, FastAPI, React 초기 세팅 | 프로젝트 기본 구조 |
| 2주차 | 웹캠 객체탐지 MVP | OpenCV 웹캠 입력, YOLO 탐지, MJPEG 스트림 | 실시간 객체탐지 화면 |
| 3주차 | Pico 센서 연동 | Pico 2 W MQTT 송신, Mosquitto, 센서값 수신 | 센서 데이터 수집 성공 |
| 4주차 | DB/API 구현 | PostgreSQL, sensor/camera event 저장, API 구현 | 데이터 저장/조회 API |
| 5주차 | 대시보드 구현 | 상태 카드, 실시간 영상, 이벤트 타임라인, 그래프 | 1차 대시보드 |
| 6주차 | ROI/행동 분석 | 밥그릇/물그릇/침대 ROI, 식사/음수/휴식 추정 | 행동 이벤트 생성 |
| 7주차 | 이상징후/알림 | Rule Engine, Telegram/Discord 알림, 경고 화면 | 이상징후 알림 기능 |
| 8주차 | 테스트/발표자료 | 통합 테스트, 시연 시나리오, 문서화, 발표자료 | 최종 결과물 |

### 17.2 MVP 최소 일정

시간이 부족하면 4주 MVP로 줄일 수 있다.

| 주차 | 목표 |
|---|---|
| 1주차 | FastAPI + React + 웹캠 스트리밍 |
| 2주차 | YOLO 객체탐지 + 박스 표시 |
| 3주차 | Pico MQTT 센서 연동 + DB 저장 |
| 4주차 | 대시보드 + 기본 이상징후 알림 |

---

## 18. 역할 분담 예시

### 4인 팀 기준

| 역할 | 담당 업무 |
|---|---|
| PM/기획 | 요구사항 정의, 일정 관리, 발표자료, 시연 시나리오 |
| 임베디드 담당 | Pico 2 W, 센서 회로, MQTT 송신, 디바이스 안정화 |
| AI/백엔드 담당 | OpenCV, YOLO, FastAPI, MQTT 수신, Rule Engine |
| 프론트엔드 담당 | React/Next.js 대시보드, 실시간 영상, 그래프, UI/UX |

### 3인 팀 기준

| 역할 | 담당 업무 |
|---|---|
| 임베디드/하드웨어 | Pico, 센서, MQTT |
| AI/백엔드 | 웹캠, YOLO, FastAPI, DB |
| 프론트/기획 | 대시보드, UI, 발표자료, 문서화 |

### 1인 개발 기준

우선순위:

1. 웹캠 + YOLO + FastAPI 영상 스트림
2. React 대시보드에서 영상 표시
3. Pico 2 W 센서 1개만 MQTT 연동
4. DB 저장
5. 이벤트 타임라인
6. 이상징후 Rule 2~3개 구현

---

## 19. 예상 비용

> 가격은 구입처, 환율, 재고에 따라 달라질 수 있으므로 실제 구매 전 확인이 필요하다. 기존 PC를 사용한다는 전제로 산정한다.

### 19.1 MVP 비용

| 품목 | 수량 | 예상 비용 범위 |
|---|---:|---:|
| USB Webcam | 1 | 30,000 ~ 100,000원 |
| Pico 2 W | 2 | 20,000 ~ 40,000원 |
| 온습도 센서 | 1 | 3,000 ~ 15,000원 |
| PIR 센서 | 1~2 | 2,000 ~ 10,000원 |
| Load Cell + HX711 | 1~3 | 10,000 ~ 40,000원 |
| 문열림 센서 | 1 | 2,000 ~ 8,000원 |
| 브레드보드/점퍼선/저항 | 1세트 | 10,000 ~ 30,000원 |
| 부저/LED | 1세트 | 2,000 ~ 10,000원 |
| 케이스/거치대 | 선택 | 10,000 ~ 30,000원 |
| 합계 |  | 약 89,000 ~ 283,000원 |

### 19.2 확장형 비용

| 품목 | 수량 | 예상 비용 범위 |
|---|---:|---:|
| Pico 2 W | 3~5 | 30,000 ~ 100,000원 |
| 웹캠 | 2 | 60,000 ~ 200,000원 |
| 고성능 센서류 | 여러 개 | 50,000 ~ 150,000원 |
| 저장장치/미니 서버 | 선택 | 100,000원 이상 |
| 합계 |  | 약 240,000원 이상 |

---

## 20. 테스트 계획

### 20.1 단위 테스트

| 대상 | 테스트 항목 |
|---|---|
| Pico 2 W | Wi-Fi 연결, MQTT publish, 센서값 정상 범위 확인 |
| 웹캠 | 카메라 열림 여부, FPS, 해상도, 조명 변화 대응 |
| YOLO | person/dog/cat 탐지 여부, confidence threshold 검증 |
| FastAPI | REST API 응답, WebSocket 연결, 영상 스트림 동작 |
| DB | 이벤트 저장, 히스토리 조회, 인덱스 성능 |
| Dashboard | 실시간 영상 표시, 그래프 렌더링, 이벤트 타임라인 표시 |

### 20.2 통합 테스트 시나리오

#### 시나리오 1: 강아지 식사 감지

```text
1. 강아지가 밥그릇 ROI로 이동
2. 웹캠에서 dog 탐지
3. 밥그릇 Load Cell 무게 감소
4. eating behavior_event 생성
5. 대시보드 타임라인에 표시
```

#### 시나리오 2: 고양이 휴식 감지

```text
1. 고양이가 방석 ROI에 들어감
2. cat 객체가 10분 이상 움직임 적음
3. 침대 Load Cell 무게 변화 감지
4. resting behavior_event 생성
5. 대시보드에 휴식 시간 누적
```

#### 시나리오 3: 현관 접근 위험

```text
1. 문열림 센서가 open 상태 전송
2. dog 또는 cat이 entrance ROI에 접근
3. Rule Engine이 danger 이벤트 생성
4. Telegram/Discord 알림 발송
5. 대시보드 상태가 위험으로 변경
```

#### 시나리오 4: 사람 낙상 의심

```text
1. person 탐지
2. 바닥 근처 영역에서 장시간 미움직임
3. PIR/mmWave 움직임도 낮음
4. fall_suspected anomaly_event 생성
5. 위험 알림 발송
```

---

## 21. 위험 요소 및 대응 방안

| 위험 요소 | 설명 | 대응 방안 |
|---|---|---|
| 객체탐지 오탐 | 강아지/고양이 구분 실패 가능 | confidence threshold, ROI, 센서 융합 적용 |
| 조명 변화 | 야간/역광에서 탐지 성능 저하 | 조명 보강, IR 웹캠/조명 고려, 밝기 보정 |
| 웹캠 위치 문제 | 사각지대 발생 | 설치 위치 가이드, ROI 재설정 기능 제공 |
| Wi-Fi 불안정 | Pico MQTT 데이터 누락 | 재전송, retained message, reconnect 로직 |
| DB 데이터 증가 | 이벤트/시계열 데이터 누적 | 보관 기간 정책, 요약 테이블, 자동 삭제 |
| 개인정보 이슈 | 실내 영상 처리에 대한 부담 | 로컬 처리, 영상 미저장, 얼굴 blur, 접근 인증 |
| 실시간 성능 부족 | PC 사양에 따라 FPS 저하 | 해상도/FPS 제한, 작은 모델 사용, GPU 옵션 |
| 건강 판단 과장 | 의료/수의학 진단처럼 보일 위험 | “이상징후 의심”, “패턴 변화” 표현 사용 |

---

## 22. 성공 기준

### 22.1 기능 성공 기준

| 항목 | 성공 기준 |
|---|---|
| 웹캠 스트리밍 | 대시보드에서 실시간 영상 확인 가능 |
| 객체탐지 | person/dog/cat 탐지 박스 표시 가능 |
| 센서 연동 | Pico 2 W 센서 데이터가 MQTT로 서버에 도착 |
| DB 저장 | 센서값과 탐지 이벤트가 DB에 저장 |
| 대시보드 | 현재 상태, 센서값, 이벤트 타임라인 표시 |
| 행동 분석 | 식사/휴식/현관 접근 중 2개 이상 추정 가능 |
| 이상징후 | warning/danger 이벤트 생성 및 알림 가능 |

### 22.2 시연 성공 기준

최종 발표 또는 시연에서 다음 흐름을 보여줄 수 있으면 성공이다.

```text
1. 웹캠 화면이 대시보드에 표시된다.
2. 사람이 화면에 들어오면 person 박스가 표시된다.
3. 강아지/고양이 사진 또는 실제 반려동물이 감지되면 dog/cat 박스가 표시된다.
4. Pico 센서값이 대시보드에 실시간 반영된다.
5. 밥그릇 무게가 감소하면 식사 이벤트가 생성된다.
6. 위험 조건을 발생시키면 알림이 전송된다.
```

---

## 23. 구현 코드 예시

### 23.1 FastAPI + OpenCV + YOLO 영상 스트림 예시

```python
# backend/main.py

import cv2
from fastapi import FastAPI
from fastapi.responses import StreamingResponse
from ultralytics import YOLO

app = FastAPI()

# 프로젝트 환경에 맞게 모델 파일 변경 가능
# 예: yolov8n.pt, yolo11n.pt 등
model = YOLO("yolov8n.pt")

# 기본 USB 웹캠. 웹캠이 여러 개면 1, 2 등으로 변경
camera = cv2.VideoCapture(0)

TARGET_CLASSES = {"person", "dog", "cat"}


def generate_frames():
    while True:
        success, frame = camera.read()
        if not success:
            break

        results = model(frame, verbose=False)
        annotated_frame = results[0].plot()

        ret, buffer = cv2.imencode(".jpg", annotated_frame)
        if not ret:
            continue

        frame_bytes = buffer.tobytes()
        yield (
            b"--frame\r\n"
            b"Content-Type: image/jpeg\r\n\r\n" + frame_bytes + b"\r\n"
        )


@app.get("/api/video_feed")
def video_feed():
    return StreamingResponse(
        generate_frames(),
        media_type="multipart/x-mixed-replace; boundary=frame"
    )


@app.get("/health")
def health():
    return {"status": "ok"}
```

### 23.2 React 실시간 카메라 컴포넌트 예시

```tsx
// dashboard/src/components/LiveCamera.tsx

export default function LiveCamera() {
  return (
    <section>
      <h2>실시간 객체탐지 화면</h2>
      <img
        src="http://localhost:8000/api/video_feed"
        alt="Live Object Detection"
        style={{
          width: "100%",
          maxWidth: "960px",
          borderRadius: "12px",
          border: "1px solid #ddd"
        }}
      />
    </section>
  );
}
```

### 23.3 Pico 2 W MQTT 전송 예시

```python
# pico/sensor_node.py
# MicroPython 예시 코드. 실제 Wi-Fi, 핀 번호, 센서 라이브러리는 환경에 맞게 수정 필요.

import time
import network
from umqtt.simple import MQTTClient

WIFI_SSID = "YOUR_WIFI_SSID"
WIFI_PASSWORD = "YOUR_WIFI_PASSWORD"
MQTT_BROKER = "192.168.0.10"
CLIENT_ID = "pico_livingroom_01"


def connect_wifi():
    wlan = network.WLAN(network.STA_IF)
    wlan.active(True)
    wlan.connect(WIFI_SSID, WIFI_PASSWORD)

    while not wlan.isconnected():
        time.sleep(1)

    print("Wi-Fi connected:", wlan.ifconfig())


def main():
    connect_wifi()
    client = MQTTClient(CLIENT_ID, MQTT_BROKER)
    client.connect()

    while True:
        # 실제 구현에서는 센서 라이브러리로 값을 읽는다.
        temperature = 24.2
        humidity = 48.0

        payload = '{{"device_id":"{}","sensor_type":"temperature","value":{},"unit":"C"}}'.format(
            CLIENT_ID,
            temperature
        )
        client.publish("home/pico/livingroom_01/sensor/temperature", payload)

        payload = '{{"device_id":"{}","sensor_type":"humidity","value":{},"unit":"%"}}'.format(
            CLIENT_ID,
            humidity
        )
        client.publish("home/pico/livingroom_01/sensor/humidity", payload)

        time.sleep(30)


main()
```

---

## 24. Docker Compose 구성 예시

```yaml
version: "3.9"

services:
  postgres:
    image: postgres:16
    container_name: aiot_postgres
    environment:
      POSTGRES_USER: aiot
      POSTGRES_PASSWORD: aiot_password
      POSTGRES_DB: aiot_db
    ports:
      - "5432:5432"
    volumes:
      - postgres_data:/var/lib/postgresql/data

  mosquitto:
    image: eclipse-mosquitto:2
    container_name: aiot_mqtt
    ports:
      - "1883:1883"
      - "9001:9001"
    volumes:
      - ./mosquitto/config:/mosquitto/config
      - ./mosquitto/data:/mosquitto/data
      - ./mosquitto/log:/mosquitto/log

  backend:
    build: ./backend
    container_name: aiot_backend
    ports:
      - "8000:8000"
    depends_on:
      - postgres
      - mosquitto
    environment:
      DATABASE_URL: postgresql://aiot:aiot_password@postgres:5432/aiot_db
      MQTT_HOST: mosquitto
      MQTT_PORT: 1883

  dashboard:
    build: ./dashboard
    container_name: aiot_dashboard
    ports:
      - "3000:3000"
    depends_on:
      - backend

volumes:
  postgres_data:
```

주의: Docker 컨테이너 안에서 USB 웹캠을 직접 접근하려면 OS와 Docker 설정에 따라 `/dev/video0` 장치 매핑이 필요할 수 있다. 개발 초기에는 백엔드를 로컬 Python으로 실행하고, DB/MQTT만 Docker로 실행하는 방식이 더 쉽다.

---

## 25. 발표/시연 시나리오

### 25.1 시연 구성

```text
노트북/PC
- USB Webcam 연결
- FastAPI 서버 실행
- React 대시보드 실행
- Mosquitto MQTT 실행
- PostgreSQL 실행

Pico 2 W
- 온습도 센서
- 밥그릇 무게센서 또는 버튼으로 대체 가능
- 문열림 센서 또는 스위치로 대체 가능
```

### 25.2 시연 흐름

```text
1. 대시보드 홈 화면을 연다.
2. 실시간 웹캠 영상이 표시된다.
3. 사람이 카메라 앞에 서면 person 객체가 탐지된다.
4. 강아지/고양이 사진 또는 실제 대상이 보이면 dog/cat 객체가 탐지된다.
5. Pico 센서값이 대시보드에 반영된다.
6. 밥그릇 무게를 일부러 줄이면 식사 이벤트가 생성된다.
7. 현관 문열림 센서를 작동시키고 반려동물이 현관 ROI에 있다고 가정하면 위험 알림이 발생한다.
8. 이벤트 타임라인과 알림 내역을 보여준다.
```

### 25.3 발표에서 강조할 포인트

- 단순 CCTV가 아니라 AI + IoT 센서 융합 시스템
- Pico 2 W와 PC 웹캠의 역할 분리로 구현 현실성 확보
- 영상 데이터와 센서 데이터를 결합해 행동 추정 신뢰도 향상
- 실시간 대시보드와 알림 기능 제공
- 프라이버시 보호를 위한 로컬 처리와 영상 저장 최소화
- 향후 다중 카메라, 모바일 앱, 고도화된 행동 분석으로 확장 가능

---

## 26. 향후 확장 방향

### 26.1 기능 확장

- 다중 웹캠 지원
- RTSP/IP 카메라 지원
- WebRTC 저지연 스트리밍
- 반려동물 개체별 식별
- 자세 추정 기반 낙상 감지 고도화
- 반려동물 행동 분류 모델 학습
- 모바일 앱 연동
- 자동 급식기/급수기 제어
- 스마트 조명/에어컨/공기청정기 연동

### 26.2 AI 고도화

- 객체탐지 → 객체추적 → 행동분류 → 이상탐지 단계로 확장
- 7일/30일 기준 baseline 생성
- 시간대별 생활 패턴 학습
- 카메라 ROI와 센서 변화량을 결합한 confidence score 계산
- 특정 반려동물 개체 식별 모델 추가
- 낙상 의심 시 pose estimation 활용

### 26.3 서비스화 방향

- 사용자 계정/가정 단위 관리
- 여러 집/여러 카메라 관리
- 클라우드 백업 옵션
- 보호자 공유 기능
- 병원 방문 전 참고용 생활 패턴 리포트
- 월간 반려동물 활동 리포트 자동 생성

---

## 27. 최종 결론

이 프로젝트는 **Pico 2 W 기반 센서 네트워크**와 **PC 웹캠 기반 객체탐지 서버**를 결합한 AIoT 스마트 홈 시스템이다.

가장 현실적인 MVP 구성은 다음과 같다.

```text
Pico 2 W 센서 노드
→ MQTT
→ FastAPI Backend
→ PostgreSQL/TimescaleDB
→ React/Next.js Dashboard

PC USB Webcam
→ OpenCV
→ YOLO 객체탐지
→ FastAPI 영상 스트림
→ 대시보드 실시간 표시
```

프로젝트의 핵심 차별점은 단순히 카메라 영상을 보여주는 것이 아니라, **카메라 객체탐지와 센서 데이터를 융합하여 사람과 반려동물의 생활 패턴과 이상징후를 분석한다는 점**이다.

최종적으로 사용자는 대시보드에서 다음을 확인할 수 있다.

- 지금 집에 사람이 있는지
- 강아지나 고양이가 어디에 있는지
- 오늘 얼마나 움직였는지
- 밥과 물을 먹었는지
- 평소와 다른 특이사항이 있는지
- 낙상 의심, 현관 접근, 장시간 미움직임 같은 위험 상황이 있는지

따라서 이 프로젝트는 졸업작품, 포트폴리오, 공모전, AIoT 실습 프로젝트로 적합하며, 하드웨어·AI·백엔드·프론트엔드·데이터베이스·보안까지 모두 포함하는 종합형 프로젝트로 발전시킬 수 있다.

---

## 28. 참고 자료

- Raspberry Pi Pico 2 공식 제품 페이지: https://www.raspberrypi.com/products/raspberry-pi-pico-2/
- OpenCV VideoCapture 공식 문서: https://docs.opencv.org/4.x/d8/dfe/classcv_1_1VideoCapture.html
- Ultralytics YOLO Predict 공식 문서: https://docs.ultralytics.com/modes/predict/
- FastAPI WebSocket 공식 문서: https://fastapi.tiangolo.com/advanced/websockets/

