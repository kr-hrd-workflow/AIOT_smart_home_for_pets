# PetCare Consumer Device Onboarding Design

**Status:** Approved direction on 2026-07-23
**Depends on:** `2026-07-20-petcare-multitenant-remote-design.md`
**Landing visual authority:** `2026-07-23-petcare-photoreal-scroll-world-design.md`

## Goal

Turn the existing operator-oriented enrollment screen into a consumer flow that connects one Home Agent, the two fixed Pico 2 W products, and an optional Jetson camera without exposing household credentials to Sites.

The public `/` route always starts with the approved Higgsfield scroll-world landing. The authenticated `/dashboard` route owns setup and live household data. A missing Home Agent must never replace the landing page.

## Reused Contracts

- Keep the existing Supabase session and tenant ownership model.
- Keep the existing ten-minute Home Agent enrollment code and Ed25519 enrollment request.
- Keep the fixed Pico products `entrance-01` and `petzone-01`; users do not select or rename firmware profiles.
- Keep Pico operational traffic on Wi-Fi/MQTT only. USB is used only for first setup and recovery.
- Keep the Home Agent as the owner of MQTT credentials, local PostgreSQL, rules, tunnel, and device health.
- Keep the existing owner-only Jetson pairing bundle and `pair-jetson` validation.
- Keep real sensor, camera, and credential data out of the anonymous landing and `/demo`.

No temporary access point, captive portal, BLE provisioning, mobile browser support, device fleet model, or second Home Agent is added.

## Consumer Flow

1. The visitor sees the approved Higgsfield landing at `/`, then signs in.
2. `/dashboard` shows a progress checklist instead of the standalone `10분 코드 만들기` card.
3. If no Home Agent is enrolled, the first step reuses the current ten-minute code and gives the Windows installer command.
4. When the agent is online, the page opens the local Home Agent setup page at `http://127.0.0.1:8000/setup`.
5. The local page asks for Wi-Fi SSID/password, then uses Web Serial to configure the pre-labelled `entrance-01` Pico.
6. The same page repeats the operation for the pre-labelled `petzone-01` Pico.
7. The Home Agent confirms both MQTT status topics. `/dashboard` shows `현관 연결됨` and `생활공간 연결됨`; this is the Pico completion gate.
8. Jetson setup is an optional final step. A consumer can finish with Pico-only sensing and add the camera later.

The local page and Sites dashboard share wording and visual tokens, but secrets never cross between their origins.

## Local Setup Boundary

The Home Agent serves the setup page and its API on loopback only. It does not add a public route or tunnel alias.

- Opening `/setup` requires a local user gesture and creates a ten-minute `HttpOnly`, `SameSite=Strict` setup session.
- The page has no third-party scripts, analytics, external fonts, or CORS.
- It reads the existing Home Agent MQTT endpoint and credential from the validated local runtime config.
- The Wi-Fi password exists only in the local page's memory and the serial write buffer.
- Closing, expiry, successful setup, or navigation clears the setup session and in-memory form values.
- Unsupported browsers show a direct Windows Chrome/Edge requirement; there is no compatibility shim.

## Pico Serial Contract

Use a bounded binary packet instead of adding a JSON parser to firmware:

- magic `PET1`;
- protocol version `1`;
- total payload length;
- UTF-8 SSID, Wi-Fi password, MQTT host, MQTT username, and MQTT password as length-prefixed fields;
- MQTT port as unsigned 16-bit integer;
- CRC-32 over the header and payload.

The browser selects only Raspberry Pi VID `0x2e8a`, then requires the firmware handshake to return the expected protocol version and fixed product ID. A friendly USB name is not trusted.

Firmware validates field lengths and UTF-8, writes the inactive one of two reserved flash slots, verifies CRC, then advances its generation counter. Boot selects the newest valid slot and falls back to the previous slot after an interrupted write. The ACK contains only protocol version, fixed device ID, and configuration digest; it never returns a password.

After ACK, Pico reboots and uses Wi-Fi/MQTT. Failed Wi-Fi or broker connection leaves USB recovery available and does not erase the last valid slot.

## Jetson Boundary

Jetson remains optional.

- The local setup page accepts the existing pairing bundle and passes it to the existing `pair-jetson` implementation.
- Certificate, PSK, Jetson address, and pairing content never pass through Sites or Supabase.
- Without Jetson, camera preview and clip controls show `카메라 추가 필요`; Pico sensor status and rules remain usable.
- Pairing failure preserves the previous valid Jetson configuration.

## Public-Network Exception

Consumer setup defaults to a Private RFC1918 LAN and the existing exact-address/LocalSubnet firewall proof. The explicit public-network mode added for the current campus machine is an operator-only exception and is not offered in the consumer UI.

## Error And Recovery States

- Agent missing: issue or refresh the existing ten-minute code.
- Agent offline: show last seen and retry without creating another home.
- Browser unsupported or serial permission denied: retain the current step and show the exact recovery action.
- Wrong Pico profile: reject before sending credentials.
- Serial disconnect or flash CRC failure: keep the last valid slot and allow retry.
- Wi-Fi/MQTT timeout: show local diagnostics without echoing credentials.
- One Pico online: keep the completed device checked and resume at the other.
- Jetson missing: finish Pico setup normally and offer `나중에 연결`.

## Verification

- Firmware host tests cover packet bounds, CRC, fixed profile handshake, interrupted A/B writes, recovery, and secret-free ACKs.
- Browser component tests mock Web Serial permission, handshake, progress, disconnect, retry, and unsupported browsers.
- Home Agent tests prove loopback-only setup, ten-minute expiry, strict session cookie, no CORS, no secret logging, and reuse of validated MQTT config.
- Remote dashboard tests prove landing-first routing, Home Agent checklist state, each Pico online state, Pico-only completion, optional Jetson, and no demo/live substitution.
- Physical acceptance uses the two connected Pico 2 W boards: configure through Web Serial, unplug USB, and prove fresh MQTT status/sensors through the authenticated dashboard.
- Jetson acceptance is separate and does not block the Pico-only product gate.
