# 개인정보와 네트워크 경계

## 로컬 런타임

기본 local-live는 PostgreSQL `127.0.0.1:55432`, MQTT `127.0.0.1:18883`, FastAPI `127.0.0.1:8000`, dashboard `127.0.0.1:3000`만 사용합니다. HTTP/WebSocket Origin은 `http://127.0.0.1:3000`과 `http://localhost:3000`만 허용하며 wildcard, `null`, 외부 origin은 403으로 거부합니다.

비밀번호·토큰은 호출 프로세스 환경 또는 ACL로 제한된 runtime file에서만 읽습니다. 명령행 인수, Git, 문서, 일반 로그, evidence에는 남기지 않습니다. service/integration runner는 종료 시 자식 프로세스와 임시 인증 파일을 정리합니다.

## 카메라와 보관

기본 USB camera frame은 추론과 MJPEG 표시를 위해 메모리에서 처리하고 자동 녹화하지 않습니다. Docker provider는 PostgreSQL/MQTT 실행 옵션일 뿐 Windows USB webcam access를 제공한다고 가정하지 않습니다. 별도 Jetson clip 기능은 승인된 pair/config가 있을 때만 동작하며 Sites `/demo`와는 연결되지 않습니다.

## Sites

production `/demo`는 fixture-only 화면입니다. document와 같은 origin의 정적 asset만 허용하고 `PetCareClient`, PetCare API/WebSocket, localhost/loopback 요청, cross-origin image 요청을 생성하지 않습니다. Sites 프로젝트는 custom owner-only access이며 public fallback을 사용하지 않습니다. source token은 단일 Git 명령의 메모리에만 두고 remote/config/file에 저장하지 않으며, 배포 중 environment variable을 변경하지 않습니다.

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

PetCare 이벤트는 반려동물 행동의 보조 관찰 신호일 뿐입니다. `no_meal_12h`와 `bed_sensor_mismatch`는 확인이 필요한 패턴을 알리는 `warning`이며 의료 진단, 위험 감지, 체중계 또는 수면 분석 결과가 아닙니다.
