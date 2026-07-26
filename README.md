# PetCare Vision AIoT Smart Home

PetCare는 두 대의 Raspberry Pi Pico 2 W, Windows Home Agent, Jetson 카메라, 공개 Sites 웹을 연결해 반려동물의 식사·휴식·카메라 관측 활동을 보여주는 제품입니다. 공개 랜딩과 fixture 전용 체험 화면은 누구나 볼 수 있고, 실제 가정 데이터와 기기 등록은 Supabase 로그인과 tenant 범위로 보호됩니다.

공개 Sites 주소는 [kr-hrd-petcare-aiot.parkccccc3.chatgpt.site](https://kr-hrd-petcare-aiot.parkccccc3.chatgpt.site)입니다. 현재 공개 배포는 Sites v8이며, 검증된 `dashboard` subtree의 source commit `634fe4e`를 사용합니다.

랜딩 영상은 휠 틱마다 장면을 점프하거나 재생을 다시 시작하지 않습니다. native scroll 목표를 1초 동안 연속 보간해 한 번의 큰 스크롤에 영상이 약 1~2초 전진하고, 입력이 멈추면 영상도 정지합니다.

## 현재 상태

| 범위 | 상태 |
| --- | --- |
| Pico 2 W C++ 펌웨어와 MQTT 계약 | `entrance`·`petzone` 두 프로필 빌드 PASS |
| FastAPI, PostgreSQL, MQTT, 행동 규칙 | 구현·로컬 통합 테스트됨 |
| 공개 랜딩, `/demo`, 로그인 후 대시보드 | 연속 스크롤 scrub·인증 장애 fallback까지 구현·테스트됨 |
| Supabase 고객 가입·로그인과 tenant 분리 | 구현·production Site URL·두 콜백 설정됨; 일반 고객 메일용 Custom SMTP는 미구성 |
| Sites↔Home Agent 원격 연결 | 코드·UI 구현됨; 연결된 Cloudflare 계정에 Zone·Access가 없어 운영 등록은 `BLOCKED` |
| Windows Home Agent 설치 파일 | 고정 크기·SHA-256 검증 후 Sites R2 업로드 PASS; 로그인 후 제공 |
| Sites scheduled 정리·R2 7일 lifecycle | 코드 구현됨; provider 운영 trigger/lifecycle 연결 증거는 `NOT VERIFIED` |
| Sites 공개 배포 | v8 공개 배포 PASS (source `634fe4e`) |
| 실제 Jetson 비전 서비스·USB 카메라·서명 프리뷰 | 실기기 통과 (JetPack 4.6.6, L4T 32.7.6, TensorRT 8.2.1) |
| Jetson 고유 프레임 30 FPS 라이브·활동·반복 이동 관측 | 60 Hz 보정 후 단기 실기기 게이트 PASS |
| Jetson 60분 지속 실행 | `NOT RUN` |
| 실제 Pico·센서 설치 검수 | `NOT RUN` |
| 깨끗한 Windows PC에서 전체 설치 검수 | `NOT RUN` |

Jetson 단기 실기기 라이브 게이트는 카메라 전원 주파수를 60 Hz로 보정한 뒤 1초 창 10회 모두 `30/30` 고유 프레임, 합산 `30.064 FPS`, 창 길이 p99 `1.0146초`로 PASS했습니다. 이 결과는 60분 지속 실행을 대체하지 않으며, 60분 soak는 `NOT RUN`입니다.

Pico UF2 빌드는 두 프로필 모두 PASS했습니다. `entrance` SHA-256은 `6620E63041C2ADE5764D3F1F175F5BB42E1B3400707A79E23E0BEDD1EDD19203`, `petzone` SHA-256은 `FC241E1AA4517753316ACEA9FEDAC5A84A7BB3A575029943AB81770B433515A3`입니다. 실제 보드 플래시·센서 스모크와 깨끗한 Windows PC 설치 검수는 모두 `NOT RUN`입니다.

## 구성과 데이터 흐름

1. 고객은 공개 랜딩을 보고 `/signup`에서 일반 PetCare 계정을 만듭니다. 고객이 Supabase 프로젝트나 키를 따로 설정할 필요는 없습니다.
2. 로그인한 `/dashboard`에서 Windows Home Agent 설치 파일과 10분 유효 등록 코드를 받습니다.
3. 설치 프로그램은 관리자 승인을 받아 `%ProgramData%\PetCare\HomeAgent` 아래에 런타임을 설치하고 `PetCarePostgres`, `PetCareMqtt`, `PetCareHomeAgent` 서비스를 등록합니다.
4. Pico를 Home Agent PC에 USB로 한 번 연결해 집 Wi-Fi를 입력합니다. Home Agent가 MQTT 자격 증명을 기기에 직접 결합하며, 이후 센서 운영 데이터는 USB가 아니라 Wi-Fi/MQTT로 전달됩니다.
5. Home Agent는 센서·카메라 데이터와 카메라가 실제로 관측한 1초 활동 bucket을 로컬 PostgreSQL에 저장하고 행동 규칙을 처리한 뒤, 등록된 Sites origin을 통해 해당 가정의 로그인 사용자에게만 상태를 제공합니다.
6. 승인된 Jetson `pairing.json`을 로컬 설정 화면에 전달하면 Home Agent가 자동으로 다시 연결하고, 로그인한 Sites 대시보드에서 상태와 프리뷰를 확인할 수 있습니다. 이 제품의 연결 완료 판정에는 Jetson 카메라 온라인 상태가 포함됩니다.

### 장치 역할

- `entrance-01`: SHT31 온습도와 LD2410C 이동/정지 존재 센서
- `petzone-01`: SHT31·LD2410C에 식기/물그릇 HX711 채널과 침대 FSR 3채널 추가
- Home Agent: 로컬 MQTT 수신, PostgreSQL, FastAPI, 카메라 추론, 활동·휴식·반복 이동 관측, 행동 규칙, Sites 연결
- 카메라: 기본 USB, 테스트용 `file`, 승인된 `jetson`, 또는 `disabled`
- Sites: 공개 제품 소개·데모와 Supabase 인증 기반 원격 대시보드

Pico는 FSR의 `0..4095` ADC 원값만 발행합니다. 침대 baseline, polarity, 안정성, 점유 hysteresis, 카메라 융합, dog/cat 소유권과 handoff는 백엔드 책임입니다.

## 계정 삭제와 활동 기록

로그인한 사용자가 PetCare 계정 데이터를 삭제하면 Sites는 해당 Home Agent에만 유효한 Ed25519 서명 정리 명령을 대기열에 넣습니다. Home Agent는 외부에서 들어오는 삭제 포트를 열지 않고 Sites를 주기적으로 확인하며, 명령을 받으면 로컬 PostgreSQL의 `activity_observations`만 트랜잭션으로 삭제하고 활동 수집을 즉시 중지한 뒤 ACK를 보냅니다. Home Agent가 오프라인이면 웹 응답은 `cleanup_pending`으로 남고, 로컬 삭제 ACK 전에는 tenant 정리를 완료로 처리하지 않습니다. 같은 Home Agent의 재시도는 멱등이며, 새 Home Agent를 명시적으로 등록해야만 활동 수집이 다시 활성화됩니다.

<!-- petcare-docs:architecture -->
```json
{
  "pico_nodes": ["entrance-01", "petzone-01"],
  "camera_id": "pc-webcam-01",
  "camera_sources": ["usb", "file", "jetson", "disabled"],
  "subjects": ["dog_001", "cat_001"],
  "zones": ["food_bowl", "pet_bed"],
  "behaviors": ["eating", "resting"],
  "anomalies": ["no_meal_12h", "bed_sensor_mismatch", "repetitive_motion"],
  "pico_emits_raw_fsr_only": true,
  "backend_owns_fsr_interpretation": true,
  "notification_channels": []
}
```

## 웹 접근 경계

| 경로 | 접근 | 용도 |
| --- | --- | --- |
| `/` | 공개 | 랜딩 페이지 |
| `/demo` | 공개 | 외부 API·WebSocket을 호출하지 않는 fixture 데모 |
| `/login`, `/signup`, 비밀번호 복구 화면 | 공개 | Supabase 고객 인증 |
| `/dashboard` | 로그인 필요 | 등록 코드, Home Agent 설치, 기기 상태와 가정 데이터 |
| `/api/petcare/installer` | 로그인 필요 | R2의 버전 고정 Windows Home Agent 설치 파일 |
| `/api/petcare/**`와 실데이터 경로 | 로그인·tenant 검증 필요 | 등록, 상태, 카메라, 클립, 계정 작업 |

루트가 랜딩보다 먼저 설정 코드 화면으로 바뀌지 않습니다. 10분 코드는 로그인한 대시보드에서 고객이 명시적으로 만들 때만 표시됩니다.

운영 Supabase Auth의 Site URL은 공개 Sites 주소로 고정했고, Redirect URL allowlist는 `/auth/callback`과 `/auth/callback?next=/reset-password`의 production URL 두 개만 사용합니다. 이메일 확인은 유지하므로 일반 고객 가입·복구 메일을 실제 운영하려면 별도 Custom SMTP가 필요합니다. Cloudflare `CF_*` 8개 값이 완전하지 않으면 10분 코드는 D1에 저장되지 않고 `503 enrollment_unavailable`로 조기 실패하며 화면은 “원격 연결 준비 중”이라고 안내합니다.

## 활동과 반복 이동 관측

활동 시간은 dog/cat bounding box 중심이 이전의 유효한 관측보다 24 px 이상 이동한 1초 bucket의 합계입니다. 카메라가 대상을 보지 못했거나 3초보다 오래 끊긴 시간은 정지나 활동 0초로 채우지 않고 `관측 없음`으로 표시합니다. 휴식 시간은 기존 FSR·카메라 융합 규칙에서 별도로 계산하므로 활동 시간과 서로 대체하지 않습니다.

`repetitive_motion`은 최근 120초의 카메라 좌표에서 충분한 이동 거리와 방향 반전이 반복될 때 15분 단위로 중복을 억제해 보여주는 보수적 warning입니다. 건강·불안·질병을 판정하지 않으며 자동 클립이나 외부 알림을 만들지 않습니다.

## 보안과 비밀정보

- PetCare 운영자는 Sites runtime에 공개 가능한 `SUPABASE_URL`·`SUPABASE_PUBLISHABLE_KEY`를 설정합니다. Home Agent 등록과 원격 상태·영상 연결에는 서버 전용 `CF_ACCOUNT_ID`, `CF_ZONE_ID`, `CF_ZONE_NAME`, `CF_ACCESS_TEAM_NAME`, `CF_TUNNEL_API_TOKEN`, `CF_ACCESS_SERVICE_TOKEN_ID`, `CF_ACCESS_CLIENT_ID`, `CF_ACCESS_CLIENT_SECRET`도 필요합니다.
- Cloudflare API token과 Access client secret은 Sites의 secret으로만 저장하며 브라우저·설치 파일·Git·문서·로그에 노출하지 않습니다. 설치 파일을 R2에 올릴 때만 일회성 `PETCARE_INSTALLER_UPLOAD_TOKEN`을 설정하고, 업로드 직후 제거한 상태로 다시 배포합니다.
- Supabase secret/service-role key와 JWKS URL은 Sites runtime, 설치 파일, Git, 문서, 로그에 넣지 않습니다.
- 고객의 Wi-Fi 비밀번호는 브라우저에서 `127.0.0.1:8000`의 Home Agent로 직접 전달되며 Sites Worker나 Supabase를 통과하지 않습니다.
- Home Agent의 DB/MQTT/connector 자격 증명은 owner/SYSTEM 전용 runtime 파일에 저장합니다.
- 로컬 PostgreSQL과 FastAPI는 loopback에만 바인딩하고, Pico MQTT는 Windows의 Private LAN과 LocalSubnet으로 제한합니다.
- 설치 파일은 공개 정적 asset에 두지 않고, 정확한 크기와 SHA-256을 확인한 R2 객체만 로그인 세션에 내려줍니다.

자세한 경계는 [docs/privacy.md](docs/privacy.md)를 확인하세요.

## 개발 환경 시작

저장소 루트의 Windows PowerShell에서 sealed toolchain과 서비스, 모델을 준비합니다.

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File tools/bootstrap_toolchain.ps1
powershell -NoProfile -ExecutionPolicy Bypass -File tools/bootstrap_pico_sdk.ps1
powershell -NoProfile -ExecutionPolicy Bypass -File tools/bootstrap_services.ps1
powershell -NoProfile -ExecutionPolicy Bypass -File tools/provision_vision_model.ps1
```

Pico 빌드와 저장소 검증은 다음 명령을 사용합니다.

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File tools/build_pico.ps1 -Profile all
powershell -NoProfile -ExecutionPolicy Bypass -File tools/run_integration.ps1 -Provider Native
powershell -NoProfile -ExecutionPolicy Bypass -File tools/check_all.ps1
$runtime = Get-Content -Raw .runtime/toolchain.json | ConvertFrom-Json
& $runtime.paths.python_path tools/docs_check.py --root .
```

대시보드는 manifest가 지정한 Node/npm과 Bash로 실행합니다.

```powershell
$runtime = Get-Content -Raw .runtime/toolchain.json | ConvertFrom-Json
$env:PATH = "$(Split-Path $runtime.paths.node_path);$env:SystemRoot\System32"
$env:npm_config_script_shell = $runtime.paths.bash_path
& $runtime.paths.node_path $runtime.paths.npm_cli_path ci --prefix dashboard
& $runtime.paths.node_path $runtime.paths.npm_cli_path --prefix dashboard run dev
```

전체 dashboard 게이트:

```powershell
& $runtime.paths.node_path $runtime.paths.npm_cli_path --prefix dashboard run test
& $runtime.paths.node_path $runtime.paths.npm_cli_path --prefix dashboard run lint
& $runtime.paths.node_path $runtime.paths.npm_cli_path --prefix dashboard run test:e2e:demo:dev
& $runtime.paths.node_path $runtime.paths.npm_cli_path --prefix dashboard run test:e2e:demo:production
& $runtime.paths.node_path $runtime.paths.npm_cli_path --prefix dashboard run test:e2e:connected
```

정확한 설치·운영 절차는 [docs/setup.md](docs/setup.md), Pico 배선은 [docs/pico-wiring.md](docs/pico-wiring.md), 체험 화면 검증은 [docs/demo-runbook.md](docs/demo-runbook.md), 물리 검수는 [docs/hardware-acceptance.md](docs/hardware-acceptance.md)를 따릅니다. dashboard 구현 세부사항은 [dashboard/README.md](dashboard/README.md)에 있습니다.

## 안전 한계

이 제품은 행동 패턴 관찰 도구입니다. 질병 진단, 수면 품질 판정, 체중계 수준의 측정 정확도, 위험 감지 또는 응급 알림을 보장하지 않습니다. 이상 이벤트는 `no_meal_12h`, `bed_sensor_mismatch`, `repetitive_motion` 세 종류의 보수적 `warning`만 사용하며 외부 알림 채널은 구현하지 않습니다.
