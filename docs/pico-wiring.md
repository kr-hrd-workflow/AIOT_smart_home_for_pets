# Pico 2 W 배선과 펌웨어 계약

## 노드 분리

두 보드는 모두 Raspberry Pi Pico 2 W(`pico2_w`, RP2350 ARM Secure platform)입니다. `entrance-01`과 `petzone-01`은 각각 SHT31 한 대와 LD2410C 한 대를 사용하므로 SHT31의 공통 주소 `0x44`가 충돌하지 않습니다. HX711 두 채널과 FSR 세 채널은 `petzone-01`에만 연결합니다.

모든 센서와 Pico의 GND를 공통으로 연결합니다. GPIO 입력은 3.3V를 넘기지 않습니다. LD2410C 전원은 안정된 5V/최소 200mA를 사용하고 센서 TX(3.3V UART)를 Pico GPIO9에 연결합니다. Pico TX는 연결하지 않습니다.

| 장치 | Pico 인터페이스 | 핀 | 전원/주의 |
| --- | --- | --- | --- |
| SHT31 | I2C0 | SDA GPIO4, SCL GPIO5 | 3.3V, 주소 `0x44`, pull-up 사용 |
| LD2410C | UART1 RX | GPIO9 | 센서 5V/200mA 이상, UART 256000 8N1, Pico TX 미연결 |
| Food HX711 | GPIO | DOUT 10, SCK 11 | 3.3V logic, 식기별 tare/scale 필요 |
| Water HX711 | GPIO | DOUT 12, SCK 13 | 3.3V logic, 물그릇별 tare/scale 필요 |
| FSR left | ADC0 | GPIO26 | 3.3V divider, 고정 저항 10kΩ |
| FSR center | ADC1 | GPIO27 | 3.3V divider, 고정 저항 10kΩ |
| FSR right | ADC2 | GPIO28 | 3.3V divider, 고정 저항 10kΩ |

FSR은 각 채널에서 `3.3V → FSR → ADC 접점 → 10kΩ → GND` 전압분배기로 연결합니다. 펌웨어는 `adc` 원값만 발행하며 baseline/polarity/stability/entry/exit/occupancy/fusion을 계산하지 않습니다.

## 식기 calibration

각 HX711은 빈 그릇의 `tare_raw`와 알려진 기준 추로 구한 `counts_per_gram`을 `petcare_config.hpp`의 food/water 항목에 별도로 기록한 뒤 다시 빌드합니다. 기본값은 배선 확인용 placeholder이므로 실제 중량 신뢰성 증거가 아닙니다. 두 그릇을 하나의 scale 값으로 공유하지 않습니다.

침대 calibration은 펌웨어가 아니라 `POST /api/bed/calibration`에서 수행합니다. 빈 침대, 사용 가능한 카메라, 최근 60초 동안 채널별 최소 45개 안정된 raw sample이 필요합니다.

## 시간과 MQTT

부팅 후 `pool.ntp.org`, 실패 시 `time.cloudflare.com`으로 SNTP 동기화되기 전에는 telemetry를 발행하지 않습니다. timestamp는 UTC millisecond `YYYY-MM-DDTHH:mm:ss.SSSZ`이고 역행을 거부합니다. 재동기화는 6시간, 실패 재시도는 15초입니다.

센서 topic은 `home/pico/{device_id}/sensor/{sensor_type}`, 상태 topic은 `home/pico/{device_id}/status`입니다. QoS 1, 센서 retain false, 상태 retain true이며 10초 heartbeat와 retained offline LWT를 사용합니다. 재연결 backoff는 1, 2, 4, 8, 16, 30초입니다.

<!-- petcare-docs:pico-contract -->
```json
{
  "board": "pico2_w",
  "platform": "rp2350",
  "resolved_platform": "rp2350-arm-s",
  "profiles": {
    "entrance-01": ["temperature", "humidity", "presence_moving", "presence_stationary"],
    "petzone-01": ["temperature", "humidity", "presence_moving", "presence_stationary", "food_weight", "water_weight", "bed_pressure_left", "bed_pressure_center", "bed_pressure_right"]
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
  "sntp": {"primary": "pool.ntp.org", "fallback": "time.cloudflare.com", "retry_ms": 15000, "resync_ms": 21600000},
  "status_payload_keys": ["device_id", "status", "observed_at"],
  "status_values": ["online", "offline"],
  "timestamp_format": "YYYY-MM-DDTHH:mm:ss.SSSZ",
  "fsr_payload": {"unit": "adc", "range": [0, 4095], "interpretation_owner": "backend"}
}
```

## 빌드 결과

`tools/build_pico.ps1 -Profile all -Hardware`는 profile별 `entrance-01.uf2`, `petzone-01.uf2`를 만듭니다. 두 보드의 serial boot, 인증된 sensor/status 구독, offline LWT, Wi-Fi/MQTT 재연결은 각각 물리 검수해야 합니다.
