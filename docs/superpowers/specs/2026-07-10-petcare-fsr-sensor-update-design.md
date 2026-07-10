# PetCare FSR Sensor Update Design

## Decision Summary

- Use two Pico 2 W nodes.
- Entrance node: one SHT31 and one LD2410C.
- Pet-zone node: one SHT31, one LD2410C, two HX711 load cells for food/water, and three FSR408 sensors for the bed.
- Keep the third purchased SHT31 as a tested spare.
- Remove BME280, PIR, reed-switch, and bed HX711 requirements from this MVP.
- Replace the previous entrance-danger success scenario with eating and confirmed-rest scenarios; entrance presence remains informational.
- Keep webcam inference on the PC and raw video local and ephemeral.

## Approaches Considered

1. **Backend-owned calibration and occupancy (selected):** Pico publishes three raw FSR ADC channels. FastAPI stores baselines, applies hysteresis, fuses camera detections, and serves the reset action. This keeps firmware small and avoids reverse MQTT commands or flash-write policy.
2. **Pico-owned calibration:** Pico stores baselines and publishes occupancy. Rejected because the dashboard reset button then needs a command protocol, acknowledgement, and flash persistence.
3. **Hybrid raw plus derived state:** Pico and backend both classify occupancy. Rejected because duplicated thresholds can disagree without improving the MVP.

## Hardware Topology

### `entrance-01`

- SHT31 over I2C.
- LD2410C over UART.
- Publishes temperature, humidity, moving-target presence, and stationary-target presence.

### `petzone-01`

- SHT31 over I2C.
- LD2410C over UART.
- Food and water load cells through two HX711 modules on digital GPIO.
- Three FSR408 voltage dividers on ADC GPIO 26, 27, and 28.
- Publishes scalar readings once per second for each FSR channel; no weight unit is assigned to FSR data.

The FSR installation also needs three fixed resistors, a stable 3.3 V divider, and a thin load-distribution sheet. Start with 10 kOhm resistors and leave per-channel polarity and thresholds configurable because the physical mounting determines the useful range.

## MQTT Contracts

All topics retain `home/pico/{device_id}/sensor/{sensor_type}`.

- `temperature`: degrees Celsius.
- `humidity`: percent relative humidity.
- `presence_moving` and `presence_stationary`: boolean LD2410C observations.
- `food_weight` and `water_weight`: calibrated grams from HX711.
- `bed_pressure_left`, `bed_pressure_center`, and `bed_pressure_right`: raw 12-bit ADC counts.

The production firmware no longer emits `bed_weight`. Existing host fixtures and tests are updated rather than carrying a disabled legacy field.

## Bed Calibration

The dashboard exposes one `침대 영점 재설정` command. `POST /api/bed/calibration` uses the latest 60 seconds of the three pressure channels and succeeds only when:

- every channel has at least 45 valid samples;
- no dog detection overlaps the bed ROI during the sample window;
- each channel remains within its configured stability range.

The backend stores the median of each channel as its baseline. Calibration returns HTTP 409 for occupied, unstable, or insufficient data and does not overwrite the previous valid baseline.

For each channel, the backend computes `max(0, polarity * (raw - baseline))`. The three deltas are summed. Entry and exit thresholds are separate configuration values:

- occupied candidate: aggregate delta at or above the entry threshold for 2 seconds;
- empty candidate: aggregate delta at or below the exit threshold for 7 seconds.

No automatic baseline drift correction is included in the MVP. Recalibration is required after moving or washing the cushion.

## Camera Fusion And Rest Sessions

- Pressure occupied plus a dog center inside `pet_bed` ROI: confirmed bed use.
- Pressure occupied without a dog: unconfirmed pressure; do not count rest.
- Dog inside `pet_bed` without pressure: sensor-check state; do not count rest.
- Confirmed bed use opens one `resting` session; confirmed exit closes it.

The dashboard reports current rest duration, today's confirmed rest total, nighttime bed-exit count, seven-day average comparison when enough history exists, camera confirmation, and raw/baseline/delta values for all three channels. UI copy says `휴식 추정` and `야간 침대 이탈`, not medical sleep diagnosis.

## Failure Handling

- Stale or missing FSR channels produce `sensor unavailable`, never zero pressure.
- MQTT topic/payload mismatch is rejected before persistence.
- Camera outage leaves pressure state visible but prevents confirmed-rest accumulation.
- Database or WebSocket failure cannot stop MQTT or camera worker shutdown.
- A failed calibration preserves the last valid baseline.
- Physical sensor and webcam checks remain `NOT RUN` until hardware is connected.

## Verification

- C++ host tests cover SHT31 conversion/CRC, LD2410C frame parsing, dual HX711 scheduling, three ADC channels, invalid values, and one-second cadence.
- Backend tests cover median calibration, stability rejection, polarity, hysteresis timing, stale channels, camera fusion, rest-session deduplication, and seven-day insufficient-data behavior.
- Local-live integration uses authenticated Mosquitto and PostgreSQL, publishes all three FSR channels, injects a dog in the bed ROI, and proves one rest session opens and closes.
- Dashboard tests and browser QA cover calibration success/409 states, current/total rest, night exits, raw/baseline values, offline camera, and mobile layout.
- Privacy checks prove no frame/video files are created.

## Task Model Routing

Fresh workers receive only their task, dependency contracts, and owned files. Independent tasks may run in parallel; tasks sharing files run sequentially. Model escalation is risk-based.

| Task | Work | Model | Effort |
| --- | --- | --- | --- |
| 1 | Runtime checks and exclusions | `gpt-5.6-luna` | medium |
| 2 | Shared C++ sensor contracts | `gpt-5.6-terra` | high |
| 3 | Backend contracts and PostgreSQL migration | `gpt-5.6-terra` | high |
| 4 | Deterministic PC vision pipeline | `gpt-5.6-sol` | high |
| 5 | Dashboard demo surface | `gpt-5.6-terra` | high |
| 6 | Native/Compose PostgreSQL and MQTT services | `gpt-5.6-sol` | high |
| 7 | Pico Wi-Fi and MQTT runtime | `gpt-5.6-sol` | xhigh |
| 8 | SHT31, LD2410C, HX711, and FSR408 drivers | `gpt-5.6-sol` | max |
| 9 | MQTT ingestion and persistence | `gpt-5.6-terra` | high |
| 10 | Camera persistence and MJPEG | `gpt-5.6-terra` | high |
| 11 | Eating, bed calibration, and confirmed-rest rules | `gpt-5.6-sol` | xhigh |
| 12 | REST/WebSocket/ROI/calibration API | `gpt-5.6-terra` | high |
| 13 | Connected dashboard and calibration UI | `gpt-5.6-terra` | high |
| 14 | Real PostgreSQL/Mosquitto end-to-end gate | `gpt-5.6-sol` | max |
| 15 | Responsive, accessibility, and pixel QA | `gpt-5.6-terra` | high |
| 16 | GitHub Actions | `gpt-5.6-luna` | medium |
| 17 | Owner-only Sites publication | `gpt-5.6-sol` | max |
| 18 | Setup, wiring, privacy, and hardware docs | `gpt-5.6-luna` | medium |
| F1-F4 | Final compliance, code, manual QA, and scope gates | `gpt-5.6-sol` | max |

Planning uses `gpt-5.6-sol max`. Each high-accuracy plan-review round uses two fresh isolated `gpt-5.6-sol` reviewers, one at `xhigh` and one at `max`, and requires both to approve. Repository discovery uses `gpt-5.6-luna medium`. `ultra` is not used because the coordinator, rather than a model's automatic delegation, owns task boundaries and dependency order. No durable team is created: the implementation plan already provides isolated task boundaries, so fresh task workers have lower coordination overhead.

## Scope Guardrails

- No Raspberry Pi camera path, bed weight claim, automatic diagnosis, public camera/backend, raw-frame storage, multi-camera work, custom rule DSL, or speculative CRUD.
- No third Pico node unless a third monitored room becomes an explicit requirement.
- No automatic FSR drift compensation until hardware evidence shows manual recalibration is insufficient.
- The spreadsheet's formula and FSR-purpose label errors are documented separately; implementation uses the actual purchased quantities, not the erroneous total.
