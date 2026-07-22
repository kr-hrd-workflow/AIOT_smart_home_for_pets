# PetCare 구현 현황

## 현재 아키텍처

PetCare는 센서 노드와 Home 런타임을 분리합니다. 두 Pico 2 W는 C++로 센서 원값과 online/offline 상태를 MQTT QoS 1로 발행합니다. Home 런타임은 loopback PostgreSQL/MQTT, FastAPI, 카메라 파이프라인, 규칙 worker, vinext 대시보드를 실행합니다. 카메라 입력은 `usb`, 테스트용 `file`, 승인된 `jetson`, 또는 `disabled` 중 하나입니다.

센서·카메라 fact는 UTC observed time과 process-local monotonic deadline을 함께 사용합니다. 규칙 엔진만 eating/resting, FSR 점유, camera fusion, owner/handoff, mismatch를 판정하고 커밋된 결과만 대시보드 허브에 전달합니다.

## 완료 범위

| 범위 | 상태 | 근거 |
| --- | --- | --- |
| Pico 두 프로필과 MQTT 계약 | 구현·테스트됨 | host CTest 및 firmware contract |
| Backend/API/DB/rules | 구현·테스트됨 | unit/component/local-live |
| Dashboard/demo/responsive QA | 구현·테스트됨 | Vitest, Playwright, build |
| CI | PASS | exact-SHA 6-job workflow |
| Sites | private production PASS | owner-only access, saved version, private deployment |
| 실제 Pico/센서/웹캠 설치 | NOT RUN | 물리 증거 없음 |

Sites production `/demo`는 fixture UI만 제공하고 Home API·WebSocket을 생성하지 않습니다. 소유자 인증 후 DOM smoke 확인은 브라우저 로그인 세션이 있어야 하며, 이 인증 요구 자체가 private access 경계입니다.

<!-- petcare-docs:delivery-status -->
```json
{
  "implemented": ["pico firmware", "backend", "dashboard", "local-live integration", "CI", "private Sites"],
  "sites_production": "PASS",
  "physical_hardware": "NOT RUN",
  "deferred": ["physical installation evidence"]
}
```

## 운영 순서

1. [setup.md](setup.md)의 sealed toolchain·service·model을 준비합니다.
2. [pico-wiring.md](pico-wiring.md)에 따라 각 노드를 분리 배선하고 profile별 UF2를 빌드합니다.
3. [hardware-acceptance.md](hardware-acceptance.md)의 물리 항목을 증거와 함께 실행합니다.
4. [demo-runbook.md](demo-runbook.md)의 local-live 및 calibration 절차를 실행합니다.
5. [privacy.md](privacy.md)의 loopback, Origin, secret, Sites 경계를 유지합니다.

## 의도적으로 포함하지 않는 범위

- retired `bed_weight`, 조도, 문열림 센서
- `entrance_risk`, 낙상, 의료·수면·체중 신뢰성 주장
- Telegram/Discord 등 외부 알림 채널
- Docker 컨테이너에서 Windows USB 웹캠을 사용할 수 있다는 주장
- 물리 증거 없이 하드웨어 항목을 PASS로 전환하는 절차
