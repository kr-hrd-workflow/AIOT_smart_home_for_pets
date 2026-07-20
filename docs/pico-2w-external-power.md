# Pico 2 W Wi-Fi MQTT 외부 전원 운용

Pico 2 W는 PC에 계속 연결하지 않는다. PC에서는 하드웨어용 UF2를 한 번 올리고, 설치 위치에서는 **규제된 5 V micro-USB 어댑터**로 부팅해 집 안 사설 Wi-Fi와 MQTT 브로커에 접속한다. 이 프로젝트는 Pico 한 대당 정격 **5 V, 1 A 이상** 어댑터를 권장한다.

Pico는 Sites나 Cloudflare에 직접 접속하지 않는다. 경로는 항상 `Pico 2 W -> 집 안 RFC1918 LAN -> 로컬 MQTT 브로커:18883 -> 홈 에이전트`다.

## 1. 브로커와 LAN 준비

1. 브로커 PC에는 공유기의 DHCP 예약으로 고정된 사설 IPv4 주소를 사용한다. 허용 범위는 `10/8`, `172.16/12`, `192.168/16`뿐이다.
2. Windows 네트워크 프로필이 `Private`인지 확인한다.
3. TCP 18883 인바운드는 해당 브로커 주소의 `Private` 프로필, 원격 `LocalSubnet`에만 허용한다. `Public`, `Any`, `0.0.0.0`, 인터넷 포트포워딩은 금지한다. 프로젝트 도구는 방화벽을 자동 변경하지 않는다.
4. 실제 주소를 `$BrokerAddress`에 넣어 하드웨어 프로필을 생성하고 서비스를 시작한다.

```powershell
$BrokerAddress = '192.168.1.10'
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\tools\bootstrap_services.ps1 -HardwareAddress $BrokerAddress

# 자격 증명 값은 명령 인수나 로그에 쓰지 말고 현재 프로세스의 환경변수로만 제공한다.
$env:PETCARE_MQTT_USERNAME = '<secret-manager-or-private-input>'
$env:PETCARE_MQTT_PASSWORD = '<secret-manager-or-private-input>'
$env:PETCARE_POSTGRES_PASSWORD = '<secret-manager-or-private-input>'

powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\tools\services.ps1 `
  -Action Start -Provider native -Profile hardware -HardwareAddress $BrokerAddress

Remove-Item Env:PETCARE_POSTGRES_PASSWORD -ErrorAction SilentlyContinue
```

`services.ps1`가 기존 방화벽에서 위 범위를 증명하지 못하면 `hardware NOT_RUN`으로 중단되는 것이 정상이다. 방화벽과 DHCP 예약은 라우터/Windows 관리자가 먼저 설정한다.

## 2. 실제 자격 증명으로 UF2 빌드

계약용 예제 자격 증명으로 만든 UF2는 설치하지 않는다. Wi-Fi와 MQTT 비밀값을 현재 프로세스 환경변수로 제공한 뒤 각 물리 노드에 맞는 프로필 하나만 빌드한다. 생성되는 `petcare_secrets.hpp`는 gitignored이고 ACL로 현재 사용자만 읽을 수 있지만, 빌드 후에도 저장소나 로그에 복사하지 않는다.

```powershell
$env:PETCARE_WIFI_SSID = '<home-private-ssid>'
$env:PETCARE_WIFI_PASSWORD = '<secret-manager-or-private-input>'
$env:PETCARE_MQTT_USERNAME = '<secret-manager-or-private-input>'
$env:PETCARE_MQTT_PASSWORD = '<secret-manager-or-private-input>'

powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\tools\build_pico.ps1 `
  -Profile entrance-01 -Hardware
# 또는 두 번째 보드:
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\tools\build_pico.ps1 `
  -Profile petzone-01 -Hardware
```

UF2 위치:

- `.runtime/pico-build/entrance-01/entrance-01.uf2`
- `.runtime/pico-build/petzone-01/petzone-01.uf2`

빌드가 끝나면 현재 셸의 비밀 환경변수를 제거한다.

```powershell
Remove-Item Env:PETCARE_WIFI_SSID,Env:PETCARE_WIFI_PASSWORD,Env:PETCARE_MQTT_USERNAME,Env:PETCARE_MQTT_PASSWORD -ErrorAction SilentlyContinue
```

## 3. BOOTSEL로 UF2 올리기

1. **Pico와 모든 센서의 전원을 끈다.** 배선 중에는 micro-USB도 분리한다.
2. Pico 2 W의 `BOOTSEL`을 누른 상태에서 PC의 micro-USB 데이터 케이블을 연결한다.
3. Windows에 나타난 `RP2350` 드라이브에 해당 보드의 UF2 하나를 복사한다.
4. 복사가 끝나면 보드가 자동으로 분리되고 재부팅한다. 프로필을 뒤바꾸지 않았는지 파일명을 다시 확인한다.
5. PC 데이터 USB를 뽑는다. 이후 설치 운용에는 PC USB를 사용하지 않는다.

공식 절차와 전원 한계는 [Raspberry Pi Pico 2 W Datasheet](https://pip.raspberrypi.com/documents/RP-008304-DS), [Getting started with Raspberry Pi Pico-series](https://datasheets.raspberrypi.com/pico/getting-started-with-pico.pdf), [Pico-series 공식 문서](https://www.raspberrypi.com/documentation/microcontrollers/pico-series.html)를 따른다. Pico 2 W BOOTSEL 드라이브 이름은 `RP2350`이다.

## 4. 전원과 센서 배선

전원을 끈 상태에서 배선하고, 모든 모듈의 GND를 공통으로 연결한다.

- Pico 전원: 규제된 5 V micro-USB 어댑터(프로젝트 권장 1 A 이상).
- LD2410C VCC: 규제된 5 V. 모듈의 3.3 V UART TX만 Pico GP9 RX에 연결하고 반대 방향 UART는 연결하지 않는다.
- SHT31, HX711 논리 전원, FSR 분압: Pico 3V3.
- 모든 Pico GPIO/ADC 입력: `0..3.3 V`. 5 V를 GPIO, ADC, 3V3 핀에 넣지 않는다.

Pico 2 W 데이터시트상 micro-USB VBUS는 5 V ±10%이고 GPIO는 3.3 V 영역이다. 외부 5 V를 VSYS에 직접 배선하는 방식은 이번 설치에서 사용하지 않는다. USB와 VSYS 외부 전원을 동시에 연결하려면 공식 데이터시트의 전원 OR 회로가 필요하므로, 이 문서의 단순 설치 절차에서는 금지한다.

배선을 확인한 뒤 PC 데이터 USB가 빠진 상태에서 벽면 어댑터를 연결한다.

## 5. MQTT 스모크 검증

스모크 도구는 `.runtime/services.json`의 정확한 `hardware` 주소와 환경변수 자격 증명만 사용한다. 비밀번호용 명령행 옵션은 없고 보고서에도 사용자명·비밀번호를 출력하지 않는다. 기본 제한시간 45초 안에 다음을 요구한다.

- `entrance-01`: status와 temperature, humidity, moving/stationary presence 4종
- `petzone-01`: 위 4종과 food/water weight, left/center/right pressure까지 9종
- 정확한 토픽, JSON 키 순서, 타입, 단위, UTC 밀리초 timestamp
- 2회의 연속 online으로 10초 heartbeat 증명
- 누락, 잘못된 payload, 미래 또는 30초 초과 sensor/online 데이터는 종료 코드 1

```powershell
$env:PETCARE_MQTT_PROFILE = 'hardware'
$env:PETCARE_MQTT_USERNAME = '<secret-manager-or-private-input>'
$env:PETCARE_MQTT_PASSWORD = '<secret-manager-or-private-input>'

& .\.runtime\managed\python\python\python.exe .\tools\pico_mqtt_smoke.py entrance-01
& .\.runtime\managed\python\python\python.exe .\tools\pico_mqtt_smoke.py petzone-01
```

성공 기준은 `PASS Pico MQTT smoke`, 전체 센서 수, `heartbeat=PASS`다. 정상 상태에서는 `lwt=NOT_CONFIRMED`와 `reconnect=NOT_OBSERVED`여도 된다.

## 6. 장애 복구 검증

### 전원 차단

Pico가 online인 상태에서 재접속 요구 모드로 스모크를 시작하고, 벽면 어댑터를 뽑았다가 제한시간 안에 다시 연결한다.

```powershell
$env:PETCARE_MQTT_REQUIRE_RECONNECT = '1'
$env:PETCARE_MQTT_SMOKE_TIMEOUT = '120'
& .\.runtime\managed\python\python\python.exe .\tools\pico_mqtt_smoke.py entrance-01
```

`lwt=CONFIRMED`, `reconnect=PASS`, `heartbeat=PASS`, 전체 센서 수가 모두 나와야 한다. 도구는 실시간 offline을 받은 뒤 status 토픽을 다시 구독하고, 브로커가 QoS 1 `retain=true`로 되돌려 준 offline만 retained LWT로 확인한다. 확인 시 장애 전 센서와 heartbeat 증거를 모두 버리므로, PASS는 재접속 뒤 전체 프로필과 새 10초 heartbeat가 다시 들어왔다는 뜻이다. 테스트 후 두 환경변수를 제거한다.

### AP/Wi-Fi 차단

같은 재접속 요구 모드를 180초로 실행한다. 검증 PC와 MQTT 브로커는 유선 LAN 등 AP와 독립된 경로에 둔 채 Pico가 사용하는 Wi-Fi/AP만 차단한다. 검증 PC까지 같은 AP에서 끊으면 LWT를 관찰할 수 없으므로 이 시험이 성립하지 않는다. Pico가 online인 것을 확인한 후 Pico Wi-Fi를 끄고 MQTT keepalive 30초보다 길게 기다렸다가 다시 켠다. 확인된 retained offline LWT, 새 online, 새 10초 heartbeat, 전체 센서 재개를 모두 확인한다.

### MQTT 브로커 중단

브로커 중단 중에는 구독자인 스모크 도구도 끊기므로 LWT를 관찰할 수 없다. 브로커를 복구한 뒤 재접속 요구를 끄고 기본 스모크를 다시 실행해 새 online, heartbeat, 전체 센서가 돌아왔는지 확인한다. 브로커 장애 시험에서 LWT 부재를 Pico 실패로 판정하지 않는다.

```powershell
Remove-Item Env:PETCARE_MQTT_REQUIRE_RECONNECT -ErrorAction SilentlyContinue
$env:PETCARE_MQTT_SMOKE_TIMEOUT = '60'
& .\.runtime\managed\python\python\python.exe .\tools\pico_mqtt_smoke.py entrance-01
```

검증 후 비밀 환경변수를 제거한다. 이 작업은 공개 클라우드, Sites, 포트포워딩을 사용하지 않는다.
