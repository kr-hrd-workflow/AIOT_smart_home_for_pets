# PetCare Consumer Device Onboarding Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let a consumer enroll the Home Agent, provision the two fixed Pico 2 W products once over USB/Web Serial, operate them only over Wi-Fi/MQTT, and optionally add the Jetson without ever replacing the Higgsfield landing.

**Architecture:** Keep `/` and the existing Higgsfield scroll-world untouched. Add one host-testable bounded Pico provisioning protocol, persist it in two flash slots, serve a first-party setup page from the existing loopback FastAPI process, and derive the authenticated checklist from the existing tenant-scoped dashboard status. Reuse `pair_jetson()` locally and keep the camera optional.

**Tech Stack:** C++17, Pico SDK USB CDC and flash APIs, Python 3.12/FastAPI, native Web Serial, Next.js 16/React 19/TypeScript, Vitest, pytest, CTest.

## Global Constraints

- Do not add dependencies, a second web application, a cloud provisioning API, or a new database table.
- `/` always renders the existing approved Higgsfield landing. Device setup lives under authenticated `/dashboard` and loopback-only `http://127.0.0.1:8000/setup`.
- USB is setup/recovery only. All operational Pico data remains Wi-Fi/MQTT.
- Fixed product IDs are exactly `entrance-01` and `petzone-01`; no rename or fleet model.
- Wi-Fi/MQTT secrets never reach Sites, Supabase, URL parameters, logs, analytics, or browser storage.
- Web Serial support is Windows Chrome/Edge only and always requires an explicit user click.
- Jetson is optional. Pico-only sensing and rules must start and remain usable without it.
- The campus public-network exception remains operator-only and must not appear in consumer UI.
- Preserve all existing dirty worktree changes. Do not commit, push, deploy, install, or change external state without the applicable approval gate.
- Follow the same-SHA test policy: focused tests during edits, one component run per completed feature bundle, and one final full run on the final candidate only.
- Run Python, Node, CMake, and browser checks BelowNormal with redirected stdio; poll long commands every 30 seconds.

---

### Task 1: Bounded Pico Provisioning Protocol

**Files:**
- Create: `firmware/pico_pet_node/include/provisioning.hpp`
- Create: `firmware/pico_pet_node/src/provisioning.cpp`
- Create: `firmware/pico_pet_node/tests/test_provisioning.cpp`
- Modify: `firmware/pico_pet_node/CMakeLists.txt`

**Interfaces:**
- Consumes: compile-time `petcare::config::device_id`.
- Produces:
  - `petcare::ProvisioningConfig`
  - `petcare::ProvisioningRecord`
  - `petcare::ProvisioningResult parse_provisioning_frame(const std::uint8_t*, std::size_t, std::string_view expected_device_id)`
  - `std::size_t encode_provisioning_frame(...)`
  - `const ProvisioningRecord* newest_valid_record(const ProvisioningRecord&, const ProvisioningRecord&)`

- [ ] **Step 1: Write the failing host tests**

Test exact framing and bounds:

```cpp
static_assert(petcare::max_provisioning_frame_bytes <= 768);

TEST(parse_valid_config) {
    const auto frame = config_frame(
        "entrance-01", "Home WiFi", "wifi-secret",
        "192.168.1.20", 18883, "entrance-01", "mqtt-secret");
    const auto result = petcare::parse_provisioning_frame(
        frame.data(), frame.size(), "entrance-01");
    CHECK(result.error == petcare::ProvisioningError::none);
    CHECK(result.config.mqtt_port == 18883);
}

TEST(reject_wrong_profile_crc_and_oversize) {
    CHECK(parse_for("petzone-01", frame_for("entrance-01")).error ==
          petcare::ProvisioningError::wrong_product);
    CHECK(parse_for("entrance-01", corrupt_crc(frame)).error ==
          petcare::ProvisioningError::bad_crc);
    CHECK(parse_for("entrance-01", oversize_frame()).error ==
          petcare::ProvisioningError::too_large);
}

TEST(select_newest_valid_slot_and_preserve_previous) {
    CHECK(newest_valid_record(valid_record(8), valid_record(9))->generation == 9);
    CHECK(newest_valid_record(valid_record(8), interrupted_record(9))->generation == 8);
}

TEST(ack_is_secret_free) {
    const auto ack = encode_ack("entrance-01", digest);
    CHECK(contains(ack, "entrance-01"));
    CHECK(!contains(ack, "wifi-secret"));
    CHECK(!contains(ack, "mqtt-secret"));
}
```

Wire format is little-endian:

```text
magic[4]="PET1" | version:u8=1 | kind:u8 | payload_length:u16 |
payload | crc32:u32(header+payload)
```

`kind` is `1=hello`, `2=config`, `3=ack`, `4=error`. Config payload begins with the fixed product ID followed by `ssid`, `wifi_password`, `mqtt_host`, `mqtt_username`, and `mqtt_password` as `u16 length + UTF-8 bytes`, then `mqtt_port:u16`. Maximum byte lengths are 32, 63, 253, 64, and 128 respectively.

- [ ] **Step 2: Run the focused host test and confirm RED**

Run from a detached BelowNormal process:

```powershell
cmake --build .runtime/pico-build --target test_provisioning
ctest --test-dir .runtime/pico-build -R provisioning --output-on-failure
```

Expected: target/source missing before implementation.

- [ ] **Step 3: Implement only the fixed-capacity parser, encoder, CRC-32, and slot selection**

Use `std::array<char, N>` and explicit lengths. Do not allocate, parse JSON, add templates, or create a generic protocol framework. Validation must reject unknown kinds, non-canonical lengths, embedded NUL, invalid UTF-8, zero port, trailing bytes, and a CRC mismatch before exposing any field.

- [ ] **Step 4: Run the focused test and confirm GREEN**

Expected: the new provisioning CTest target passes with no secret text in failure output.

- [ ] **Step 5: Inspect the diff; do not commit yet**

Confirm only the four task files changed and no real credentials were added.

---

### Task 2: Pico A/B Flash Persistence and USB Recovery

**Files:**
- Create: `firmware/pico_pet_node/pico/include/provisioning_store.hpp`
- Create: `firmware/pico_pet_node/pico/src/provisioning_store.cpp`
- Modify: `firmware/pico_pet_node/pico/src/main.cpp`
- Modify: `firmware/pico_pet_node/pico/CMakeLists.txt`
- Modify: `firmware/pico_pet_node/tests/test_provisioning.cpp`

**Interfaces:**
- Consumes: Task 1 `ProvisioningConfig`, frame parser, ACK encoder, and record selection.
- Produces:
  - `bool petcare::load_provisioning(ProvisioningConfig&)`
  - `ProvisioningError petcare::poll_usb_provisioning(std::string_view device_id, ProvisioningConfig&)`

- [ ] **Step 1: Extend the host test with persistence state transitions**

```cpp
TEST(interrupted_write_keeps_last_known_good) {
    FakeSlots slots{valid_record(3), erased_record()};
    CHECK(simulate_store(slots, config_b, StopAfter::erase) ==
          ProvisioningError::flash_write);
    CHECK(newest_valid_record(slots.a, slots.b)->generation == 3);
}

TEST(successful_write_advances_generation) {
    FakeSlots slots{valid_record(3), erased_record()};
    CHECK(simulate_store(slots, config_b, StopAfter::none) ==
          ProvisioningError::none);
    CHECK(newest_valid_record(slots.a, slots.b)->generation == 4);
}
```

- [ ] **Step 2: Confirm RED on the focused provisioning target**

Expected: the storage transition functions are absent.

- [ ] **Step 3: Implement the two reserved flash sectors**

Use the final two `FLASH_SECTOR_SIZE` sectors. Each record contains magic, generation, bounded `ProvisioningConfig`, and CRC. Erase/program the inactive sector under `save_and_disable_interrupts()`/`restore_interrupts()`, read it back, validate it, and only then use its higher generation. Never erase the current valid sector during the same update.

Add `hardware_flash` and `hardware_sync` to both Pico targets. Do not add a filesystem or wear-leveling layer.

- [ ] **Step 4: Replace build-time runtime credentials in `main.cpp`**

At boot:

```cpp
petcare::ProvisioningConfig runtime{};
while (!petcare::load_provisioning(runtime)) {
    petcare::poll_usb_provisioning(petcare::config::device_id, runtime);
    sleep_ms(10);
}
```

Use `runtime` for Wi-Fi and MQTT. During Wi-Fi/MQTT retry waits, poll USB in short bounded slices so recovery remains available. On a valid new config, ACK, reboot with `watchdog_reboot(0, 0, 50)`, and never echo a password.

- [ ] **Step 5: Build the two UF2 targets once**

Run one component build after Task 1–2 are both green:

```powershell
cmake --build .runtime/pico-sdk-build --target entrance-01 petzone-01 --parallel 1
```

Expected: two UF2 files with USB CDC still enabled.

- [ ] **Step 6: Inspect the diff; do not flash or commit yet**

Physical flashing is deferred to the dedicated acceptance task because the Pico SHA changes here.

---

### Task 3: Loopback Setup Page and Web Serial

**Files:**
- Create: `backend/app/setup.py`
- Modify: `backend/app/main.py`
- Modify: `backend/app/api.py`
- Create: `backend/tests/test_setup.py`
- Create: `dashboard/tests/e2e/local-setup.spec.ts`

**Interfaces:**
- Consumes:
  - validated `AppConfig` and `load_mqtt_endpoint()`
  - `PETCARE_AGENT_CONFIG`
  - optional `PETCARE_JETSON_CONFIG`
  - existing `pair_jetson()`
- Produces:
  - `install_setup(application: FastAPI) -> None`
  - `GET /setup`
  - `POST /setup/api/bootstrap`
  - `DELETE /setup/api/session`
  - `POST /setup/api/jetson`

- [ ] **Step 1: Write failing FastAPI security tests**

```python
def test_setup_is_loopback_session_only(client):
    response = client.get("/setup", headers={"host": "127.0.0.1:8000"})
    assert response.status_code == 200
    cookie = response.headers["set-cookie"]
    assert "HttpOnly" in cookie
    assert "SameSite=strict" in cookie
    assert "Max-Age=600" in cookie
    assert "access-control-allow-origin" not in response.headers

def test_bootstrap_rejects_missing_expired_and_cross_origin_session(client):
    assert client.post("/setup/api/bootstrap").status_code == 401
    assert client.post(
        "/setup/api/bootstrap",
        headers={"origin": "https://example.invalid"},
    ).status_code == 403

def test_bootstrap_returns_only_local_mqtt_material(client, setup_cookie):
    body = client.post("/setup/api/bootstrap", cookies=setup_cookie).json()
    assert set(body) == {"mqtt_host", "mqtt_port", "mqtt_username", "mqtt_password"}
    assert "wifi" not in repr(body).lower()
```

Capture logs with sentinel Wi-Fi/MQTT values and assert the sentinel never appears.

- [ ] **Step 2: Confirm RED on `backend/tests/test_setup.py`**

Run only this file in a detached BelowNormal Python process. Expected: route/module missing.

- [ ] **Step 3: Implement the loopback session and page**

`install_setup()` must:

- reject any Host other than `127.0.0.1:8000` or `localhost:8000`;
- issue a random 32-byte URL-safe token in an `HttpOnly`, `SameSite=Strict`, `Max-Age=600`, `Path=/setup` cookie;
- keep only a SHA-256 token digest and monotonic expiry in process memory;
- set `Cache-Control: no-store`, `Referrer-Policy: no-referrer`, and a CSP allowing only same-origin scripts/styles;
- never add CORS headers;
- render a first-party page with two fixed steps: `현관 Pico 연결`, `생활공간 Pico 연결`.

The bootstrap endpoint reads the already-validated MQTT profile and returns it only to a valid setup session. It never accepts or stores Wi-Fi credentials.

- [ ] **Step 4: Implement the native Web Serial client inline**

On an explicit button click:

```js
const port = await navigator.serial.requestPort({
  filters: [{ usbVendorId: 0x2e8a }],
});
await port.open({ baudRate: 115200, bufferSize: 1024 });
```

Send hello, verify protocol version and exact fixed product ID, request loopback MQTT material, build the bounded `PET1` config frame with the in-memory Wi-Fi fields, write it, verify the secret-free ACK digest, then clear both password inputs and close the port. Do not use `localStorage`, `sessionStorage`, URL parameters, analytics, external fonts, or third-party scripts.

Unsupported browsers and denied permission keep the current step and show the exact Chrome/Edge retry action. Disconnect, wrong profile, CRC error, and timeout are distinct Korean messages.

- [ ] **Step 5: Add one browser contract test with mocked Web Serial**

Mock `navigator.serial.requestPort()` and cover:

- explicit-click permission;
- wrong profile rejected before bootstrap/credential write;
- entrance succeeds, then petzone succeeds;
- disconnect retains the incomplete step;
- unsupported browser displays the Chrome/Edge requirement;
- password inputs are empty after success.

- [ ] **Step 6: Run focused GREEN checks once**

Run `backend/tests/test_setup.py` and only `dashboard/tests/e2e/local-setup.spec.ts`. Do not run the full backend/dashboard suites on this intermediate SHA.

---

### Task 4: Optional Jetson and Pico-Only Home Agent Lifecycle

**Files:**
- Modify: `backend/app/agent_lifecycle.py`
- Modify: `backend/app/agent_runtime.py`
- Modify: `backend/app/windows_service.py`
- Modify: `backend/tests/test_agent_lifecycle.py`
- Modify: `backend/tests/test_agent_runtime.py`
- Modify: `backend/tests/test_windows_service.py`
- Modify: `backend/app/setup.py`
- Modify: `backend/tests/test_setup.py`

**Interfaces:**
- Consumes: existing optional `AgentSupervisor(..., jetson_config_path: Path | None)` work and `pair_jetson()`.
- Produces: a running Home Agent when Jetson is absent; local pairing returns `{"status":"paired","restart_required":true}` without replacing an existing valid Jetson config.

- [ ] **Step 1: Write failing lifecycle tests**

```python
def test_build_agent_components_without_jetson_keeps_pico_services_available(...):
    components = build_agent_components(config_path, tools_path, session_factory)
    assert components.jetson_client is None
    assert components.clip_admission is None
    assert components.clip_delivery is None

def test_windows_service_treats_missing_reserved_jetson_path_as_optional(...):
    supervisor = captured_supervisor()
    service.SvcDoRun()
    assert supervisor.jetson_config_path is None
```

Add a setup endpoint test proving a bad new bundle preserves the previous valid files and a good first bundle reports `restart_required`.

- [ ] **Step 2: Confirm RED on the three focused test files**

Expected: `build_agent_components()` still raises `Jetson camera source is required`, and the Windows service always passes a Jetson path.

- [ ] **Step 3: Make lifecycle members optional, not the Home Agent**

Keep MQTT ingest, rules, database, tunnel, and dashboard alive with `PETCARE_CAMERA_SOURCE=disabled`. Only construct/start/stop clip admission and delivery when a valid Jetson config exists. Do not create a fake camera.

In the Windows service, pass the reserved Jetson path only when it is an owner-only regular file; otherwise pass `None`. Keep the registry schema stable.

- [ ] **Step 4: Reuse `pair_jetson()` from the local setup endpoint**

Write the uploaded bundle to an owner-only temporary file beside the agent config, call `pair_jetson()`, delete the temporary in `finally`, and return only status plus restart requirement. Do not return URL, certificate, PSK, home IP, or file paths.

- [ ] **Step 5: Run focused GREEN checks once**

Run only `test_agent_lifecycle.py`, `test_agent_runtime.py`, `test_windows_service.py`, and `test_setup.py`. Because `test_agent_runtime.py` previously destabilized Codex stdio, run detached with redirected output and poll every 30 seconds.

---

### Task 5: Authenticated Consumer Checklist

**Files:**
- Modify: `dashboard/components/remote-dashboard.tsx`
- Modify: `dashboard/tests/remote-dashboard.test.tsx`
- Modify: `dashboard/app/globals.css`
- Modify: `dashboard/tests/landing/landing-page.test.tsx`

**Interfaces:**
- Consumes:
  - existing `PetCareStatus.home`, `agent`, and `dashboard.devices`
  - existing enrollment action
  - local setup URL constant `http://127.0.0.1:8000/setup`
  - actual camera state from `status.dashboard.camera.state`
- Produces: a single ordered consumer checklist without changing the remote wire schema.

- [ ] **Step 1: Write failing component tests**

```tsx
it("keeps the Higgsfield landing first and setup inside dashboard", async () => {
  render(<LandingPage />);
  expect(screen.getByRole("heading", { level: 1 })).toBeVisible();
  expect(screen.queryByText("10분 코드 만들기")).not.toBeInTheDocument();
});

it("shows Pico-only completion and optional Jetson", async () => {
  renderDashboard(statusWithDevices({
    "entrance-01": "online",
    "petzone-01": "online",
    camera: "offline",
  }));
  expect(screen.getByText("현관 연결됨")).toBeVisible();
  expect(screen.getByText("생활공간 연결됨")).toBeVisible();
  expect(screen.getByText("기본 설정 완료")).toBeVisible();
  expect(screen.getByText("카메라 추가 필요")).toBeVisible();
  expect(screen.getByRole("link", { name: "나중에 연결" })).toHaveAttribute(
    "href", "http://127.0.0.1:8000/setup");
});
```

Also cover agent missing, agent offline with last seen, one Pico online, both offline, and enrollment retry without duplicate requests.

- [ ] **Step 2: Confirm RED on the two focused Vitest files**

Run only `remote-dashboard.test.tsx` and `landing-page.test.tsx`.

- [ ] **Step 3: Replace the standalone enrollment card with one checklist**

Order:

1. `홈 에이전트 연결`
2. `현관 Pico 연결`
3. `생활공간 Pico 연결`
4. `Jetson 카메라 연결 (선택)`

Reuse the existing enrollment button/code inside step 1. Enable the local setup link only when the agent is online. Derive each Pico state from `dashboard.devices`; no new API, polling loop, or database field. Treat both Pico devices online as `기본 설정 완료` even when camera is offline.

Use a native `<ol>`, links, buttons, and existing CSS tokens. Preserve keyboard order, focus, reduced motion, and responsive layout.

- [ ] **Step 4: Run focused GREEN checks once**

Expected: both focused files pass; `/` still contains the landing and no setup card.

- [ ] **Step 5: Run the dashboard component bundle once**

After Tasks 3–5 share one candidate SHA, run the dashboard Vitest component set once. Do not repeat it without a dashboard SHA change.

---

### Task 6: Physical Acceptance, Final Gates, and Handoff

**Files:**
- Modify only if evidence requires a fix:
  - `tools/pico_mqtt_smoke.py`
  - `tools/check_all.ps1`
  - `docs/setup.md`
- Create: `.runtime/evidence/pico-web-serial-acceptance.json` (ignored runtime evidence)

**Interfaces:**
- Consumes: final firmware, loopback setup page, existing hardware MQTT profile, two connected Pico 2 W boards, authenticated dashboard, optional Jetson pairing bundle.
- Produces: current-SHA evidence for the product gate.

- [ ] **Step 1: Recheck resources and stop other heavy work**

No Remotion render, browser QA, full suite, or Higgsfield download may overlap this gate.

- [ ] **Step 2: Flash both final UF2 files because the Pico SHA changed**

Record firmware SHA-256 and USB identity only; never record credentials. Confirm:

- first boot exposes provisioning over USB;
- wrong-profile attempt is rejected;
- `entrance-01` and `petzone-01` each accept exactly one matching setup;
- USB can be unplugged after ACK.

- [ ] **Step 3: Prove Wi-Fi/MQTT-only operation**

With both USB cables unplugged, require fresh retained status and fresh sensor timestamps from both product IDs through:

```text
Pico Wi-Fi/MQTT → local broker → backend ingest/PostgreSQL →
Home Agent tunnel → authenticated /api/petcare/status → /dashboard
```

The acceptance JSON records timestamps, device IDs, firmware hashes, and pass/fail only.

- [ ] **Step 4: Verify optional Jetson separately**

If the Jetson is reachable, pair using the local page, restart the Home Agent once, and prove signed status/camera preview. If it is unreachable, record `NOT RUN: jetson unreachable`; this does not fail the Pico-only product gate but does block any claim that Jetson E2E is complete.

- [ ] **Step 5: Run the final candidate checks once**

Run in this order, detached BelowNormal, one heavy process at a time:

1. firmware host CTest;
2. backend full pytest;
3. dashboard full Vitest/type/build;
4. `tools/check_all.ps1`;
5. connected E2E/browser QA;
6. landing visual QA at desktop/mobile/reduced-motion/save-data.

Do not rerun any unchanged-SHA gate merely for review.

- [ ] **Step 6: Review, CI, and deployment approval gates**

Review the exact final diff and evidence. Only after the applicable user approval:

1. stage and commit the scoped files;
2. push the approved branch and wait for same-SHA CI;
3. save a Sites version from the exact pushed source;
4. deploy that saved version with private visibility;
5. verify the production URL, auth, landing-first behavior, dashboard checklist, and no secret exposure.

Do not deploy a local-only, uncommitted, or differently hashed build.

## Self-Review

- Spec coverage: every landing, Home Agent, two-Pico, Web Serial, A/B flash, security, optional Jetson, public-network, recovery, and physical verification requirement maps to Tasks 1–6.
- Placeholder scan: no TBD/TODO/FIXME or “implement later” steps remain.
- Type consistency: the same fixed IDs, `ProvisioningConfig`, `parse_provisioning_frame`, `load_provisioning`, `poll_usb_provisioning`, `install_setup`, and optional `jetson_config_path` names are used throughout.
- Ponytail audit: no dependency, new cloud schema, AP/BLE path, fleet abstraction, or second frontend is introduced.
