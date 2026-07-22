# 설치와 실행

## 원칙

저장소의 유일한 버전 권위는 `tools/platform-manifest.json`입니다. 시스템 PATH의 Python/npm을 직접 고르지 말고 `tools/bootstrap_toolchain.ps1`가 만든 `.runtime/toolchain.json`의 절대 경로를 사용합니다. 비밀번호·토큰은 명령행, 문서, `.env`, Git remote에 기록하지 않습니다.

아래 명령은 저장소 루트의 Windows PowerShell에서 실행합니다. bootstrap은 대용량 다운로드와 설치를 수행할 수 있으므로 기존 `.runtime`이 검증되는 경우 `-CheckOnly`를 먼저 사용할 수 있습니다.

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File tools/bootstrap_toolchain.ps1
powershell -NoProfile -ExecutionPolicy Bypass -File tools/bootstrap_pico_sdk.ps1
powershell -NoProfile -ExecutionPolicy Bypass -File tools/bootstrap_services.ps1
powershell -NoProfile -ExecutionPolicy Bypass -File tools/provision_vision_model.ps1
```

## 빌드와 검증

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File tools/build_pico.ps1 -Profile all
powershell -NoProfile -ExecutionPolicy Bypass -File tools/run_integration.ps1 -Provider Native
powershell -NoProfile -ExecutionPolicy Bypass -File tools/check_all.ps1
$runtime = Get-Content -Raw .runtime/toolchain.json | ConvertFrom-Json
& $runtime.paths.python_path tools/docs_check.py --root .
```

`run_integration.ps1`은 테스트 DB를 reset하고 PostgreSQL `127.0.0.1:55432`, MQTT `127.0.0.1:18883`, backend `127.0.0.1:8000`, dashboard `127.0.0.1:3000`을 격리된 자식 프로세스로 실행한 뒤 종료합니다. Native가 기준 경로입니다. Docker provider는 DB/MQTT 대안일 뿐 Windows USB 웹캠 전달을 보장하지 않습니다.

실기기 빌드는 `PETCARE_WIFI_SSID`, `PETCARE_WIFI_PASSWORD`, `PETCARE_MQTT_USERNAME`, `PETCARE_MQTT_PASSWORD`가 현재 프로세스 환경에 있을 때만 `tools/build_pico.ps1 -Hardware`가 임시 secrets header를 만들고 정리합니다. MQTT hardware profile은 `tools/bootstrap_services.ps1 -HardwareAddress <명시적 RFC1918 IPv4>`로 만들며 wildcard나 loopback 주소를 실기기 profile로 사용하지 않습니다.

<!-- petcare-docs:operations -->
```json
{
  "commands": {
    "bootstrap_toolchain": "powershell -NoProfile -ExecutionPolicy Bypass -File tools/bootstrap_toolchain.ps1",
    "bootstrap_pico_sdk": "powershell -NoProfile -ExecutionPolicy Bypass -File tools/bootstrap_pico_sdk.ps1",
    "bootstrap_services": "powershell -NoProfile -ExecutionPolicy Bypass -File tools/bootstrap_services.ps1",
    "provision_model": "powershell -NoProfile -ExecutionPolicy Bypass -File tools/provision_vision_model.ps1",
    "build_pico": "powershell -NoProfile -ExecutionPolicy Bypass -File tools/build_pico.ps1 -Profile all",
    "local_integration": "powershell -NoProfile -ExecutionPolicy Bypass -File tools/run_integration.ps1 -Provider Native",
    "full_check": "powershell -NoProfile -ExecutionPolicy Bypass -File tools/check_all.ps1",
    "docs_check": "$runtime = Get-Content -Raw .runtime/toolchain.json | ConvertFrom-Json; & $runtime.paths.python_path tools/docs_check.py --root ."
  },
  "pins": {
    "python": "3.12.13+20260623",
    "uv": "0.11.28",
    "node": "22.23.1",
    "pico_sdk": {
      "tag": "2.1.1",
      "commit": "bddd20f928ce76142793bef434d4f75f4af6e433",
      "board": "pico2_w",
      "platform": "rp2350",
      "resolved_platform": "rp2350-arm-s"
    },
    "model": {
      "package": "ultralytics",
      "version": "8.3.0",
      "file": "yolo11n.pt",
      "bytes": 5613764,
      "sha256": "0EBBC80D4A7680D14987A577CD21342B65ECFD94632BD9A8DA63AE6417644EE1"
    },
    "containers": {
      "postgres": "postgres:17.10@sha256:0af65001d05296a2ead57ac4a6412433d8913d1bb5d0c88435a7d1e1ee5cb04b",
      "mosquitto": "eclipse-mosquitto:2.0.22@sha256:212f89e1eaeb2c322d6441b64396e3346026674db8fa9c27beac293405c32b3c"
    },
    "chromium": {
      "package": "@playwright/test",
      "version": "1.61.1",
      "runtime_manifest": ".runtime/playwright.json"
    },
    "actions_checkout": "93cb6efe18208431cddfb8368fd83d5badbf9bfd",
    "sites": {
      "version": "0.1.30",
      "starter": "vinext"
    }
  }
}
```

## Dashboard 명령

dashboard 명령도 runtime Node/npm으로 실행합니다.

```powershell
$runtime = Get-Content -Raw .runtime/toolchain.json | ConvertFrom-Json
$oldPath = $env:PATH
$oldShell = $env:npm_config_script_shell
try {
  $env:PATH = "$(Split-Path $runtime.paths.node_path);$env:SystemRoot\System32"
  $env:npm_config_script_shell = $runtime.paths.bash_path
  & $runtime.paths.node_path $runtime.paths.npm_cli_path ci --prefix dashboard
  & $runtime.paths.node_path $runtime.paths.npm_cli_path --prefix dashboard run test
  & $runtime.paths.node_path $runtime.paths.npm_cli_path --prefix dashboard run build
} finally {
  $env:PATH = $oldPath
  $env:npm_config_script_shell = $oldShell
}
```

Playwright Chromium은 `dashboard/node_modules/playwright/cli.js install chromium`으로 설치하고 `.runtime/playwright.json`의 package version, revision, 실행 파일 SHA-256이 맞아야 합니다.
