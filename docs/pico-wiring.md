# Pico 2 W 배선과 펌웨어 계약

## 노드 분리

세 보드는 모두 Raspberry Pi Pico 2 W(`pico2_w`, RP2350 ARM Secure platform)입니다. `entrance-01`은 현관 온습도/재실, `petzone-01`은 food/water 무게, `bed-01`은 침대 온습도/FSR을 담당합니다. 각 SHT31은 서로 다른 Pico에 연결되므로 공통 주소 `0x44`가 충돌하지 않습니다.

모든 센서와 Pico의 GND를 공통으로 연결합니다. GPIO 입력은 3.3V를 넘기지 않습니다. LD2410C 전원은 안정된 5V/최소 200mA를 사용하고 센서 TX(3.3V UART)를 Pico GPIO9에 연결합니다. Pico TX는 연결하지 않습니다.

| Pico 프로필 | 센서 | Pico 인터페이스 | 핀 | 전원/주의 |
| --- | --- | --- | --- | --- |
| `entrance-01` | SHT31 | I2C0 | SDA GPIO4, SCL GPIO5 | 3.3V, 주소 `0x44`, pull-up 사용 |
| `entrance-01` | LD2410C | UART1 RX | GPIO9 | 센서 5V/200mA 이상, UART 256000 8N1, Pico TX 미연결 |
| `petzone-01` | Food HX711 | GPIO | DOUT GPIO10, SCK GPIO11 | 3.3V logic, food tare/scale 필요 |
| `petzone-01` | Water HX711 | GPIO | DOUT GPIO12, SCK GPIO13 | 3.3V logic, water tare/scale 필요 |
| `bed-01` | SHT31 | I2C0 | SDA GPIO4, SCL GPIO5 | 3.3V, 주소 `0x44`, pull-up 사용 |
| `bed-01` | FSR left | ADC0 | GPIO26 | 3.3V divider, 고정 저항 10kΩ |
| `bed-01` | FSR center | ADC1 | GPIO27 | 3.3V divider, 고정 저항 10kΩ |
| `bed-01` | FSR right | ADC2 | GPIO28 | 3.3V divider, 고정 저항 10kΩ |

FSR은 각 채널에서 `3.3V → FSR → ADC 접점 → 10kΩ → GND` 전압분배기로 연결합니다. 펌웨어는 `adc` 원값만 발행하며 baseline/polarity/stability/entry/exit/occupancy/fusion을 계산하지 않습니다.

출고 영점은 침대 FSR `left=7`, `center=4095`, `right=4095`로 고정합니다. Home Agent는 저장된 현장 보정값이 없으면 이 값을 자동 사용하며, 수동 영점 재설정은 센서 교체나 장기 편차 복구용으로만 남깁니다.

## 식기 calibration

각 HX711은 빈 그릇의 `tare_raw`와 알려진 기준 추로 구한 `counts_per_gram`을 `petcare_config.hpp`의 food/water 항목에 별도로 기록한 뒤 다시 빌드합니다. 기본값은 배선 확인용 placeholder이므로 실제 중량 신뢰성 증거가 아닙니다. 두 그릇을 하나의 scale 값으로 공유하지 않습니다.

1. 빈 그릇에서 HX711 signed raw를 기록해 해당 채널의 `tare_raw`로 사용합니다.
2. 무게를 아는 기준추를 올리고 `(기준추 raw - tare_raw) / 기준추 g`를 `counts_per_gram`으로 사용합니다.
3. 무게를 올렸을 때 raw가 감소하는 채널은 `counts_per_gram`을 음수로 둡니다. 현재 water 기본 방향은 이 배선에 맞춰 음수입니다.
4. 펌웨어는 계산 결과가 음수면 `0g`으로 제한하고, 보정 오류로 `5000g`를 넘으면 값을 발행하지 않습니다. 화면에 `10000g`처럼 표시되면 placeholder 대신 실제 tare/scale을 넣어 다시 빌드해야 합니다.

현재 보정값은 빈 그릇과 기준추 측정값으로 계산했습니다. Food는 iPhone 16 본체 `170g`(`tare_raw=-251961`, `counts_per_gram=408.54117647058825`), water는 iPhone 17 Pro 본체 `204g`으로 구한 기울기를 유지하고 2026-07-28 빈 그릇 12회 평균으로 영점을 다시 맞춘 값(`tare_raw=112689`, `counts_per_gram=-413.1862745098039`)입니다. 케이스·필름·부착물이 있으면 그 실제 무게만큼 보정 오차가 생깁니다.

침대 calibration은 펌웨어가 아니라 `POST /api/bed/calibration`에서 수행합니다. 빈 침대, 사용 가능한 카메라, 최근 60초 동안 채널별 최소 45개 안정된 raw sample이 필요합니다.

## 시간과 MQTT

부팅 후 `pool.ntp.org`, 실패 시 `time.google.com`으로 SNTP 동기화되기 전에는 telemetry를 발행하지 않습니다. timestamp는 UTC millisecond `YYYY-MM-DDTHH:mm:ss.SSSZ`이고 역행을 거부합니다. 재동기화는 6시간, 실패 재시도는 15초입니다.

센서 topic은 `home/pico/{device_id}/sensor/{sensor_type}`, 상태 topic은 `home/pico/{device_id}/status`입니다. QoS 1, 센서 retain false, 상태 retain true이며 10초 heartbeat와 retained offline LWT를 사용합니다. 재연결 backoff는 1, 2, 4, 8, 16, 30초입니다.

<!-- petcare-docs:pico-contract -->
```json
{
  "board": "pico2_w",
  "platform": "rp2350",
  "resolved_platform": "rp2350-arm-s",
  "profiles": {
    "entrance-01": ["temperature", "humidity", "presence_moving", "presence_stationary"],
    "petzone-01": ["food_weight", "water_weight"],
    "bed-01": ["temperature", "humidity", "bed_pressure_left", "bed_pressure_center", "bed_pressure_right"]
  },
  "pins": {
    "sht31": {"i2c": 0, "sda": 4, "scl": 5, "address": 68},
    "ld2410c": {"uart": 1, "rx": 9},
    "food_hx711": {"dout": 10, "sck": 11},
    "water_hx711": {"dout": 12, "sck": 13},
    "fsr": {"left": 26, "center": 27, "right": 28}
  },
  "electrical": {
    "logic_mv": 3300,
    "gpio_max_mv": 3300,
    "ld2410c_supply_mv": 5000,
    "ld2410c_uart_tx_mv": 3300,
    "ld2410c_min_supply_ma": 200,
    "fsr_supply_mv": 3300,
    "fsr_fixed_resistor_ohms": 10000,
    "fsr_adc_max": 4095
  },
  "cadence_ms": {"sht31": 30000, "presence": 1000, "weight": 1000, "fsr": 1000, "status": 10000},
  "mqtt": {"qos": 1, "sensor_retain": false, "status_retain": true},
  "sntp": {"primary": "pool.ntp.org", "fallback": "time.google.com", "retry_ms": 15000, "resync_ms": 21600000},
  "status_payload_keys": ["device_id", "status", "observed_at"],
  "status_values": ["online", "offline"],
  "timestamp_format": "YYYY-MM-DDTHH:mm:ss.SSSZ",
  "fsr_payload": {"unit": "adc", "range": [0, 4095], "interpretation_owner": "backend"}
}
```

## 빌드 결과

`tools/build_pico.ps1 -Profile all -Hardware`는 profile별 `entrance-01.uf2`, `petzone-01.uf2`, `bed-01.uf2`를 만듭니다. 세 보드의 serial boot, 인증된 sensor/status 구독, offline LWT, Wi-Fi/MQTT 재연결은 각각 물리 검수해야 합니다.
