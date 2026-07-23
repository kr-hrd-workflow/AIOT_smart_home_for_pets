# PetCare Vision AIoT Smart Home

PetCare는 두 대의 Raspberry Pi Pico 2 W 센서 노드, Home PC의 FastAPI/카메라 파이프라인, React/vinext 대시보드를 결합한 반려동물 생활 관찰 MVP입니다. 펌웨어는 센서 원값을 인증된 MQTT로 발행하고, Home 백엔드가 저장·융합·행동 판정·WebSocket 전달을 담당합니다.

현재 구현 범위는 펌웨어, PostgreSQL/MQTT 백엔드, USB/file/Jetson 카메라 입력, dog/cat 급식·휴식 판정, 반응형 대시보드, 로컬 통합 게이트, CI, 공개 Sites 랜딩·데모와 로그인 보호 실데이터입니다. 물리 장치 설치·배선·실카메라 증거는 아직 `NOT RUN`이며 소프트웨어 통과와 구분합니다.

## 구성

- `entrance-01`: SHT31 온습도와 LD2410C 이동/정지 존재 센서
- `petzone-01`: 같은 환경/존재 센서에 식기·물그릇 HX711 채널과 침대 FSR 3채널 추가
- Home 백엔드: MQTT 수신, PostgreSQL, 카메라 추론, strict ROI, 행동/이상 이벤트, MJPEG/REST/WebSocket
- 대시보드: 로컬 connected 화면과 외부 호출이 없는 Sites `/demo`
- 선택형 Jetson 입력: 별도 승인·페어링된 경우에만 `camera_source=jetson`; 기본값은 USB 카메라

Pico는 FSR의 `0..4095` ADC 원값만 발행합니다. 침대 baseline, polarity, 안정성, 점유 hysteresis, 카메라 융합, dog/cat 소유권과 handoff는 백엔드 책임입니다.

<!-- petcare-docs:architecture -->
```json
{
  "pico_nodes": ["entrance-01", "petzone-01"],
  "camera_id": "pc-webcam-01",
  "camera_sources": ["usb", "file", "jetson", "disabled"],
  "subjects": ["dog_001", "cat_001"],
  "zones": ["food_bowl", "pet_bed"],
  "behaviors": ["eating", "resting"],
  "anomalies": ["no_meal_12h", "bed_sensor_mismatch"],
  "pico_emits_raw_fsr_only": true,
  "backend_owns_fsr_interpretation": true,
  "notification_channels": []
}
```

## 시작하기

정확한 설치·실행 명령은 [docs/setup.md](docs/setup.md), 배선은 [docs/pico-wiring.md](docs/pico-wiring.md), 데모 절차는 [docs/demo-runbook.md](docs/demo-runbook.md)를 따릅니다. 물리 검수 결과는 [docs/hardware-acceptance.md](docs/hardware-acceptance.md)에서만 갱신하며, 개인정보와 네트워크 경계는 [docs/privacy.md](docs/privacy.md)에 정리했습니다.

구조화 문서 계약은 런타임 manifest의 Python으로 검사합니다.

```powershell
$runtime = Get-Content -Raw .runtime/toolchain.json | ConvertFrom-Json
& $runtime.paths.python_path tools/docs_check.py --root .
```

## 안전 한계

이 MVP는 행동 패턴 관찰 도구입니다. 질병 진단, 수면 품질 판정, 체중계 수준의 측정 정확도, 위험 감지 또는 응급 알림을 보장하지 않습니다. 이상 이벤트는 `no_meal_12h`와 `bed_sensor_mismatch` 두 종류의 `warning`만 사용하며 외부 알림 채널은 구현하지 않습니다.
