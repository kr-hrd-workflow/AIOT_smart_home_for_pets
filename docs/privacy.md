# 개인정보와 네트워크 경계

## 로컬 런타임

기본 local-live는 PostgreSQL `127.0.0.1:55432`, MQTT `127.0.0.1:18883`, FastAPI `127.0.0.1:8000`, dashboard `127.0.0.1:3000`만 사용합니다. HTTP/WebSocket Origin은 `http://127.0.0.1:3000`과 `http://localhost:3000`만 허용하며 wildcard, `null`, 외부 origin은 403으로 거부합니다.

비밀번호·토큰은 호출 프로세스 환경 또는 ACL로 제한된 runtime file에서만 읽습니다. 명령행 인수, Git, 문서, 일반 로그, evidence에는 남기지 않습니다. service/integration runner는 종료 시 자식 프로세스와 임시 인증 파일을 정리합니다.

## 카메라와 보관

기본 USB camera frame은 추론과 MJPEG 표시를 위해 메모리에서 처리하고 자동 녹화하지 않습니다. Jetson 라이브도 TLS/HMAC으로 인증된 단일 MJPEG 스트림을 Home Agent와 로그인된 Sites BFF가 중계할 뿐 공개 Jetson 주소를 브라우저에 전달하지 않습니다. Docker provider는 PostgreSQL/MQTT 실행 옵션일 뿐 Windows USB webcam access를 제공한다고 가정하지 않습니다. 별도 Jetson clip 기능은 승인된 pair/config가 있을 때만 동작하며 Sites `/demo`와는 연결되지 않습니다.

카메라 활동은 프레임 자체가 아니라 dog/cat subject, UTC 1초 timestamp, bounding-box 중심, 이동 거리와 moving 여부를 로컬 PostgreSQL에 저장합니다. 관측되지 않은 시간은 정지로 간주하지 않습니다. `repetitive_motion`은 이 좌표의 제한된 시간 창에서 반복 이동을 보수적으로 알리는 warning이며 의료 진단이 아닙니다.

## Sites

Sites 자체는 공개입니다. production `/demo`는 fixture-only 화면으로 document와 같은 origin의 정적 asset만 허용하고 `PetCareClient`, PetCare API/WebSocket, localhost/loopback 요청, cross-origin image 요청을 생성하지 않습니다. 실제 카메라·센서·등록·클립 route는 Supabase 인증과 tenant scope로 보호합니다. Windows Home Agent 설치 파일도 공개 asset에 포함하지 않으며, 정확한 크기와 SHA-256을 확인한 R2 객체를 로그인 세션에만 반환합니다. 로그인한 `/dashboard`의 Pico 설정만 고객 브라우저에서 `127.0.0.1` Home Agent로 직접 요청하며, Home Agent는 등록된 Sites origin 하나와 두 고정 Pico 경로만 허용합니다. Wi-Fi 비밀번호는 Sites Worker나 Supabase를 통과하지 않습니다. source token은 단일 Git 명령의 메모리에만 두고 remote/config/file에 저장하지 않습니다. 운영자는 공개 가능한 `SUPABASE_URL`·`SUPABASE_PUBLISHABLE_KEY`와 Home Agent 터널 운영용 `CF_*` 8개 값을 Sites runtime에 설정합니다. Cloudflare API token과 Access client secret은 Sites secret으로만 저장하고 Supabase secret/service-role key는 저장하지 않습니다. 설치 파일을 R2에 올릴 때만 일회성 업로드 secret을 설정하고, 업로드 직후 제거한 상태로 같은 버전을 다시 배포합니다.

Supabase 프로젝트와 런타임 공개 키는 PetCare 운영자가 한 번 구성합니다. 고객은 PetCare 계정 가입·로그인만 하며 Supabase URL, publishable key, secret/service-role key, JWKS URL을 입력하지 않습니다. Windows Home Agent 설치 파일에도 Supabase 자격 증명을 포함하지 않습니다.

## 계정 삭제 시 로컬 활동 데이터

Sites 계정 삭제는 D1/R2와 외부 연결 정리만으로 완료되지 않습니다. Sites는 삭제 대상 tenant의 폐기된 Home Agent 공개키에 결합된 `delete_activity_observations` 명령을 만들고, Home Agent가 기존 Ed25519 개인키로 서명한 outbound poll을 보낼 때만 그 명령을 반환합니다. Home Agent는 활동 관측 행만 삭제하고 새 활동 쓰기를 차단한 뒤 ACK합니다. Sites는 ACK 전까지 `cleanup_pending`을 유지하므로, 오프라인 PC의 로컬 활동 데이터가 삭제됐다고 잘못 표시하지 않습니다.

이 경로는 브라우저나 인터넷에서 Home Agent로 들어오는 파괴적 API를 추가하지 않습니다. nonce 재사용, 오래된 timestamp, 다른 agent, 잘못된 digest/signature는 거부되며, 요청·응답은 `private, no-store`입니다. 센서, 행동, 휴식, 이상 관측 및 운영 로그는 이 명령으로 삭제하지 않습니다.

<!-- petcare-docs:privacy-contract -->
```json
{
  "local_bindings": {
    "postgresql": "127.0.0.1:55432",
    "mqtt": "127.0.0.1:18883",
    "backend": "127.0.0.1:8000",
    "dashboard": "127.0.0.1:3000"
  },
  "allowed_origins": ["http://127.0.0.1:3000", "http://localhost:3000"],
  "secrets": {
    "sources": ["process environment", "owner-only runtime files"],
    "docs": false,
    "logs": false,
    "git": false
  },
  "camera": {
    "default_source": "usb",
    "frames_persisted_by_default": false,
    "docker_webcam_claim": false
  },
  "sites_demo": {
    "fixture_only": true,
    "petcare_client": false,
    "api_or_websocket": false,
    "loopback_requests": false,
    "cross_origin_images": false
  },
  "claims": {
    "medical_diagnosis": false,
    "weight_reliability": false,
    "sleep_quality_reliability": false,
    "danger_detection": false
  }
}
```

## 사용자 안내

PetCare 이벤트는 반려동물 행동의 보조 관찰 신호일 뿐입니다. `no_meal_12h`, `bed_sensor_mismatch`, `repetitive_motion`은 확인이 필요한 패턴을 알리는 `warning`이며 의료 진단, 위험 감지, 체중계 또는 수면 분석 결과가 아닙니다.
