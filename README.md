# PetCare Dashboard

PetCare 웹은 React 19와 [vinext](https://github.com/cloudflare/vinext)로 동작합니다. 공개 Sites 랜딩과 fixture 전용 데모, Supabase 고객 인증, tenant별 원격 대시보드, Windows Home Agent 등록·다운로드 화면을 한 앱에서 제공합니다. D1은 tenant·기기·클립 메타데이터를, R2는 승인된 이벤트 클립과 버전 고정 설치 파일을 저장합니다.

공개 URL은 [kr-hrd-petcare-aiot.parkccccc3.chatgpt.site](https://kr-hrd-petcare-aiot.parkccccc3.chatgpt.site)입니다. 배포 시 검증된 `dashboard` subtree와 공개 URL의 커밋 SHA를 함께 확인합니다.

## 제품 경계

| 경로 | 접근 | 동작 |
| --- | --- | --- |
| `/` | 공개 | 랜딩 페이지 |
| `/demo` | 공개 | 같은 origin의 정적 asset만 사용하는 fixture 화면 |
| `/login`, `/signup`, `/forgot-password`, `/reset-password` | 공개 | Supabase 고객 인증 |
| `/dashboard` | 로그인 필요 | Home Agent 등록, Pico/Jetson 상태, tenant 데이터 |
| `/api/petcare/installer` | 로그인 필요 | R2의 Windows Home Agent 코드서명 전 설치 파일 |
| `/api/petcare/**` | 로그인·tenant 검증 필요 | 등록, 상태, 카메라, 클립, 계정 삭제 |

`/`는 인증 상태와 관계없이 랜딩을 유지합니다. 10분 등록 코드는 로그인한 `/dashboard`에서 사용자가 요청할 때만 발급됩니다. `/demo`는 Home Agent, PetCare API, WebSocket, localhost, cross-origin image를 호출하지 않습니다.

## 고객 설정 흐름

1. 고객이 일반 PetCare 계정을 가입하고 로그인합니다.
2. `/dashboard`에서 Windows Home Agent 설치 파일을 내려받고 10분 코드를 만듭니다.
3. 설치 프로그램이 `%ProgramData%\PetCare\HomeAgent`에 로컬 PostgreSQL, MQTT, FastAPI와 Windows 서비스를 설치한 뒤 코드를 사용해 해당 tenant에 등록합니다.
4. 고객은 Pico 2 W를 Home Agent PC에 USB로 한 번 연결하고 Wi-Fi를 입력합니다. 현관 `entrance-01`과 생활공간 `petzone-01`은 이후 Wi-Fi/MQTT로 동작합니다.
5. Jetson 카메라는 로컬 `http://127.0.0.1:8000/setup`에서 pairing 파일로 연결합니다. 등록이 끝나면 Home Agent가 자동으로 다시 연결하고 로그인한 Sites 대시보드에 상태와 인증된 라이브 영상을 표시합니다. Jetson 온라인 상태까지 확인되어야 연결 완료로 표시됩니다.

고객은 Supabase 프로젝트, URL, 키 또는 JWKS를 설정하지 않습니다. PetCare 운영자가 Sites runtime의 `SUPABASE_URL`과 `SUPABASE_PUBLISHABLE_KEY`를 한 번 구성합니다.

## 활동·휴식·반복 이동 표시

`오늘 활동 추정`은 카메라가 dog/cat을 실제로 관측한 1초 bucket만 합산하고 `카메라 관측 N분 기준`을 함께 표시합니다. 관측 coverage가 0이면 `0분` 대신 `관측 없음`을 표시합니다. `오늘 휴식 추정`은 FSR·카메라 융합 결과이므로 활동과 별도로 유지합니다.

`반복 이동 관측`은 제한된 카메라 좌표 창에서 반복 이동 조건을 만족했음을 알리는 보수적 warning입니다. 건강 상태를 판단하거나 진단하지 않으며 외부 알림·자동 클립을 만들지 않습니다. 데모는 이 계약을 fixture로만 보여주고, 로그인한 원격 화면은 같은 strict parser를 통과한 Home Agent 데이터만 렌더링합니다.

## 보안

- Supabase claims의 issuer, `authenticated` audience, expiry를 서버에서 확인하고 `sub`를 tenant 소유권 키로 사용합니다.
- secret/service-role key는 Sites runtime, 브라우저, 설치 파일, Git에 넣지 않습니다.
- Wi-Fi 비밀번호는 로그인한 대시보드에서 고객 PC의 loopback Home Agent로 직접 전달되며 Sites나 Supabase에 저장하지 않습니다.
- Home Agent 설치 파일은 아직 코드서명 전이므로 SmartScreen 확인이 나타날 수 있습니다.
- 설치 파일은 공개 정적 asset이 아니며, 서버가 R2 객체의 고정 크기와 SHA-256 메타데이터를 확인한 뒤 로그인 세션에만 반환합니다.
- 실제 클립·센서·카메라 경로는 인증과 tenant scope 없이 접근할 수 없습니다.

## 구조

- `app/`: 랜딩, 데모, 고객 인증, dashboard, PetCare API route
- `components/`: 랜딩, fixture demo, 로컬 connected UI, tenant 원격 UI
- `lib/auth/`: Supabase 세션과 claims 검증
- `lib/petcare/`: 등록, live proxy, clip, cleanup, reconciliation
- `db/schema.ts`, `drizzle/`: D1 tenant·agent·camera·clip schema와 migration
- `.openai/hosting.json`: Sites project ID와 D1/R2 binding
- `tests/`, `e2e/`: 단위, 통합, 접근성, 반응형, demo network isolation 검증

`wrangler.jsonc`는 사용하지 않습니다. Sites binding은 `.openai/hosting.json`에 선언하고 로컬 테스트에서는 `vite.config.ts`가 모사합니다.

## 로컬 실행

요구 버전은 Node.js `>=22.13.0`이며, 저장소에서는 `.runtime/toolchain.json`의 고정 Node/npm과 Bash를 사용합니다. 저장소 루트의 PowerShell에서:

```powershell
$runtime = Get-Content -Raw .runtime/toolchain.json | ConvertFrom-Json
$oldPath = $env:PATH
$oldShell = $env:npm_config_script_shell
try {
  $env:PATH = "$(Split-Path $runtime.paths.node_path);$env:SystemRoot\System32"
  $env:npm_config_script_shell = $runtime.paths.bash_path
  & $runtime.paths.node_path $runtime.paths.npm_cli_path ci --prefix dashboard
  & $runtime.paths.node_path $runtime.paths.npm_cli_path --prefix dashboard run dev
} finally {
  $env:PATH = $oldPath
  $env:npm_config_script_shell = $oldShell
}
```

## 검증 명령

같은 runtime 환경에서 다음 package script를 실행합니다.

```powershell
$runtime = Get-Content -Raw .runtime/toolchain.json | ConvertFrom-Json
$env:PATH = "$(Split-Path $runtime.paths.node_path);$env:SystemRoot\System32"
$env:npm_config_script_shell = $runtime.paths.bash_path
& $runtime.paths.node_path $runtime.paths.npm_cli_path --prefix dashboard run test
& $runtime.paths.node_path $runtime.paths.npm_cli_path --prefix dashboard run lint
& $runtime.paths.node_path $runtime.paths.npm_cli_path --prefix dashboard run test:e2e:demo:dev
& $runtime.paths.node_path $runtime.paths.npm_cli_path --prefix dashboard run test:e2e:demo:production
& $runtime.paths.node_path $runtime.paths.npm_cli_path --prefix dashboard run test:e2e:connected
```

- `test`: Vitest dashboard suite
- `lint`: ESLint
- `test:e2e:demo:dev`: 개발 서버의 공개 demo 격리
- `test:e2e:demo:production`: production build와 demo 격리
- `test:e2e:connected`: 로컬 connected 상태와 ROI 편집
- `db:generate`: 의도적인 D1 schema 변경 후 Drizzle migration 생성

Playwright Chromium은 `dashboard/node_modules/playwright/cli.js install chromium`으로 설치하고 `.runtime/playwright.json`의 package version, revision, executable SHA-256과 일치해야 합니다.

## 배포와 검수 상태

Sites는 공개로 배포하되, 정상 운영 runtime에는 `SUPABASE_URL`과 `SUPABASE_PUBLISHABLE_KEY`만 설정합니다. `.openai/hosting.json`의 기존 opaque project ID와 `DB`/`CLIPS` binding을 재사용합니다.

설치 파일 릴리스는 `packaging/windows/release`의 고정 EXE와 SHA-256 sidecar를 사용합니다. 운영 배포 시에만 임시 업로드 토큰을 Sites secret으로 설정해 고정 R2 키에 업로드하고, 업로드 직후 secret을 제거한 같은 버전을 다시 배포합니다. 정상 운영 runtime에는 이 임시 토큰이 남지 않습니다.

Jetson 비전 서비스는 JetPack 4.6.6/L4T 32.7.6/TensorRT 8.2.1 실기기에서 기존 USB 카메라와 서명 프리뷰까지 통과했습니다. 새 고유 프레임 30 FPS 라이브·재연결·60분 soak는 최종 후보 SHA로 다시 측정하기 전까지 `NOT RUN`입니다. 실제 Pico·센서 설치와 깨끗한 Windows PC에서의 Home Agent 전체 설치도 아직 `NOT RUN`입니다.
