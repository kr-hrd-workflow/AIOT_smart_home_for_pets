from __future__ import annotations

import hashlib
import json
import secrets
import threading
import time
from collections.abc import Mapping
from pathlib import Path

from fastapi import APIRouter, FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse, Response
from starlette.concurrency import run_in_threadpool
from starlette.datastructures import Headers, MutableHeaders

from .agent_runtime import _write_private_file, pair_jetson
from .mqtt_ingest import MqttEndpoint
from .pico_provision import PicoProvisioningError, provision_pico


COOKIE_NAME = "petcare_setup"
SESSION_SECONDS = 600
SESSION_LIMIT = 64
MAX_JETSON_BUNDLE_BYTES = 65_536
MAX_PICO_REQUEST_BYTES = 256
PICO_PRODUCTS = frozenset({"entrance-01", "petzone-01"})
ALLOWED_HOSTS = frozenset({"127.0.0.1:8000", "localhost:8000"})
SECURITY_HEADERS: Mapping[str, str] = {
    "Cache-Control": "no-store",
    "Referrer-Policy": "no-referrer",
    "Content-Security-Policy": (
        "default-src 'none'; script-src 'self'; style-src 'self'; "
        "connect-src 'self'; base-uri 'none'; frame-ancestors 'none'; form-action 'self'"
    ),
}
SETUP_HTML = """<!doctype html>
<html lang="ko">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>PetCare 기기 연결</title>
  <style nonce="__NONCE__">
    :root {
      color-scheme: light;
      font-family: Inter, Pretendard, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
      background: #f3f5f1;
      color: #17322b;
    }
    * { box-sizing: border-box; }
    body {
      margin: 0;
      min-height: 100vh;
      background:
        radial-gradient(circle at 8% 10%, rgba(133, 185, 158, .26), transparent 34rem),
        radial-gradient(circle at 92% 78%, rgba(54, 105, 89, .16), transparent 30rem),
        #f3f5f1;
    }
    button, input { font: inherit; }
    .shell {
      width: min(100% - 32px, 980px);
      margin: 0 auto;
      padding: 56px 0 72px;
    }
    .eyebrow {
      display: inline-flex;
      align-items: center;
      gap: 8px;
      margin: 0 0 18px;
      color: #3a6c5c;
      font-size: 12px;
      font-weight: 750;
      letter-spacing: .14em;
    }
    .eyebrow::before {
      width: 8px;
      height: 8px;
      border-radius: 50%;
      background: #57a681;
      box-shadow: 0 0 0 5px rgba(87, 166, 129, .14);
      content: "";
    }
    h1 {
      max-width: 720px;
      margin: 0;
      font-size: clamp(40px, 7vw, 76px);
      font-weight: 650;
      letter-spacing: -.055em;
      line-height: .98;
      text-wrap: balance;
    }
    .intro {
      max-width: 610px;
      margin: 24px 0 38px;
      color: #567068;
      font-size: 17px;
      line-height: 1.7;
      word-break: keep-all;
    }
    .panel {
      border: 1px solid rgba(41, 75, 65, .13);
      border-radius: 28px;
      background: rgba(255, 255, 255, .82);
      box-shadow: 0 30px 80px rgba(42, 68, 58, .10);
      backdrop-filter: blur(18px);
    }
    .wifi {
      display: grid;
      grid-template-columns: 1fr 1fr;
      gap: 18px;
      padding: 28px;
    }
    .field { display: grid; gap: 9px; }
    .field label {
      color: #294c41;
      font-size: 13px;
      font-weight: 700;
    }
    .field input {
      width: 100%;
      min-height: 54px;
      border: 1px solid #cbd7d1;
      border-radius: 14px;
      outline: none;
      padding: 0 16px;
      background: #fbfcfa;
      color: #17322b;
      transition: border-color .2s ease, box-shadow .2s ease;
    }
    .field input:focus {
      border-color: #4b9776;
      box-shadow: 0 0 0 4px rgba(75, 151, 118, .13);
    }
    .browser-status {
      grid-column: 1 / -1;
      min-height: 24px;
      margin: 0;
      color: #647a72;
      font-size: 13px;
      line-height: 1.6;
    }
    .steps {
      display: grid;
      grid-template-columns: 1fr 1fr;
      gap: 18px;
      margin-top: 18px;
    }
    .device {
      position: relative;
      overflow: hidden;
      min-height: 310px;
      padding: 30px;
    }
    .device::after {
      position: absolute;
      right: -48px;
      bottom: -72px;
      width: 190px;
      height: 190px;
      border: 1px solid rgba(60, 113, 93, .15);
      border-radius: 50%;
      box-shadow: 0 0 0 28px rgba(72, 140, 113, .05);
      content: "";
      pointer-events: none;
    }
    .step-number {
      display: grid;
      width: 36px;
      height: 36px;
      place-items: center;
      border-radius: 50%;
      background: #e3eee8;
      color: #356d59;
      font-size: 13px;
      font-weight: 800;
    }
    h2 {
      margin: 48px 0 8px;
      font-size: 28px;
      letter-spacing: -.035em;
    }
    .device-copy {
      min-height: 52px;
      margin: 0 0 24px;
      color: #647a72;
      font-size: 14px;
      line-height: 1.65;
      word-break: keep-all;
    }
    .connect {
      position: relative;
      z-index: 1;
      width: 100%;
      min-height: 52px;
      border: 0;
      border-radius: 14px;
      background: #1e5b48;
      color: white;
      cursor: pointer;
      font-weight: 750;
      transition: transform .18s ease, background .18s ease, opacity .18s ease;
    }
    .connect:hover:not(:disabled) { transform: translateY(-2px); background: #164939; }
    .connect:disabled { cursor: not-allowed; opacity: .42; }
    .status {
      position: relative;
      z-index: 1;
      min-height: 42px;
      margin: 14px 0 0;
      color: #657a72;
      font-size: 13px;
      line-height: 1.55;
    }
    .status[data-state="working"] { color: #356d59; }
    .status[data-state="success"] { color: #16714d; font-weight: 750; }
    .status[data-state="error"] { color: #a13e32; }
    .complete {
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 18px;
      margin-top: 18px;
      border-radius: 22px;
      padding: 22px 26px;
      background: #173f32;
      color: white;
      font-weight: 700;
    }
    .complete[hidden] { display: none; }
    .finish {
      flex: 0 0 auto;
      border: 1px solid rgba(255, 255, 255, .38);
      border-radius: 12px;
      padding: 10px 14px;
      background: transparent;
      color: white;
      cursor: pointer;
      font-weight: 750;
    }
    .jetson {
      display: grid;
      grid-template-columns: 1fr auto;
      gap: 20px;
      align-items: end;
      margin-top: 18px;
      padding: 28px;
    }
    .jetson h2 { margin: 0 0 8px; }
    .jetson-copy {
      max-width: 640px;
      margin: 0;
      color: #647a72;
      font-size: 14px;
      line-height: 1.65;
      word-break: keep-all;
    }
    .jetson-actions {
      display: grid;
      min-width: min(100%, 280px);
      gap: 10px;
    }
    .file {
      min-height: 44px;
      max-width: 280px;
      color: #49645b;
      font-size: 12px;
    }
    .file::file-selector-button {
      margin-right: 10px;
      border: 1px solid #c6d5ce;
      border-radius: 10px;
      padding: 9px 11px;
      background: #f7faf7;
      color: #294c41;
      cursor: pointer;
      font-weight: 700;
    }
    .jetson-status {
      grid-column: 1 / -1;
      min-height: 22px;
      margin: 0;
      color: #647a72;
      font-size: 13px;
    }
    .privacy {
      margin: 24px 4px 0;
      color: #71837d;
      font-size: 12px;
      line-height: 1.7;
      word-break: keep-all;
    }
    @media (max-width: 720px) {
      .shell { padding-top: 32px; }
      .wifi, .steps { grid-template-columns: 1fr; }
      .wifi, .device { padding: 22px; }
      .device { min-height: 280px; }
      h2 { margin-top: 36px; }
      .complete, .jetson { align-items: stretch; grid-template-columns: 1fr; }
      .complete { flex-direction: column; }
      .finish { width: 100%; }
    }
    @media (prefers-reduced-motion: reduce) {
      *, *::before, *::after { scroll-behavior: auto !important; transition: none !important; }
    }
  </style>
</head>
<body>
  <main class="shell">
    <p class="eyebrow">LOCAL · PRIVATE · 10 MINUTES</p>
    <h1>두 개의 센서를<br>우리 집 Wi-Fi에.</h1>
    <p class="intro">
      이 페이지는 이 PC 안에서만 열립니다. Wi-Fi 비밀번호는 클라우드로 전송되지 않으며,
      선택한 Pico에 한 번만 전달됩니다.
    </p>

    <section class="panel wifi" aria-label="Wi-Fi 설정">
      <div class="field">
        <label for="wifi-ssid">Wi-Fi 이름</label>
        <input id="wifi-ssid" name="wifi-ssid" autocomplete="off" maxlength="32" required>
      </div>
      <div class="field">
        <label for="wifi-password">Wi-Fi 비밀번호</label>
        <input id="wifi-password" name="wifi-password" type="password"
               autocomplete="new-password" minlength="8" maxlength="63" required>
      </div>
      <p class="browser-status" data-testid="browser-status" aria-live="polite"></p>
    </section>

    <section class="steps" aria-label="Pico 연결 단계">
      <article class="panel device" data-device="entrance-01">
        <span class="step-number">01</span>
        <h2>현관 센서</h2>
        <p class="device-copy">현관문 근처의 움직임과 온습도를 확인하는 Pico를 USB로 연결하세요.</p>
        <button class="connect" type="button" data-product="entrance-01">현관 Pico 연결</button>
        <p class="status" data-testid="entrance-status" data-state="idle" aria-live="polite">연결 대기</p>
      </article>

      <article class="panel device" data-device="petzone-01">
        <span class="step-number">02</span>
        <h2>생활공간 센서</h2>
        <p class="device-copy">식기와 침대가 있는 생활공간의 Pico를 USB로 연결하세요.</p>
        <button class="connect" type="button" data-product="petzone-01">생활공간 Pico 연결</button>
        <p class="status" data-testid="petzone-status" data-state="idle" aria-live="polite">연결 대기</p>
      </article>
    </section>

    <div class="complete" id="complete" hidden>
      <span>두 센서의 설정이 끝났습니다. 이제 USB를 분리해도 Wi-Fi로 동작합니다.</span>
      <button class="finish" id="finish" type="button">설정 완료</button>
    </div>

    <section class="panel jetson" aria-label="선택형 Jetson 연결">
      <div>
        <p class="eyebrow">OPTIONAL CAMERA</p>
        <h2>Jetson 카메라 연결</h2>
        <p class="jetson-copy">
          카메라를 사용할 때만 Jetson에서 받은 pairing.json을 선택하세요.
          파일 내용은 이 PC의 Home Agent로만 전달됩니다.
        </p>
      </div>
      <div class="jetson-actions">
        <label class="field" for="jetson-bundle">Jetson 연결 파일</label>
        <input class="file" id="jetson-bundle" type="file"
               accept=".json,application/json">
        <button class="connect" id="pair-jetson" type="button" disabled>Jetson 연결</button>
      </div>
      <p class="jetson-status" data-testid="jetson-status" aria-live="polite">
        선택 사항입니다. Pico만 연결하고 완료해도 됩니다.
      </p>
    </section>
    <p class="privacy">
      지원 환경: Windows의 최신 브라우저 · Home Agent가 Raspberry Pi Pico USB VID 0x2e8a만 자동 선택 ·
      센서 운영 데이터는 USB가 아닌 Wi-Fi/MQTT를 사용합니다.
    </p>
  </main>
  <script nonce="__NONCE__">
    (() => {
      "use strict";

      const encoder = new TextEncoder();
      const ssidInput = document.querySelector("#wifi-ssid");
      const wifiPasswordInput = document.querySelector("#wifi-password");
      const browserStatus = document.querySelector("[data-testid='browser-status']");
      const completeMessage = document.querySelector("#complete");
      const finishButton = document.querySelector("#finish");
      const jetsonInput = document.querySelector("#jetson-bundle");
      const jetsonButton = document.querySelector("#pair-jetson");
      const jetsonStatus = document.querySelector("[data-testid='jetson-status']");
      const buttons = [...document.querySelectorAll("[data-product]")];
      const completed = new Set();
      const productLabels = {
        "entrance-01": "현관",
        "petzone-01": "생활공간",
      };
      const statuses = {
        "entrance-01": document.querySelector("[data-testid='entrance-status']"),
        "petzone-01": document.querySelector("[data-testid='petzone-status']"),
      };

      class SetupError extends Error {
        constructor(code, message) {
          super(message);
          this.name = "SetupError";
          this.code = code;
        }
      }

      function validateField(value, minimum, maximum, label) {
        if (typeof value !== "string" || value.includes("\0")) {
          throw new SetupError("validation", `${label} 값이 올바르지 않습니다.`);
        }
        const bytes = encoder.encode(value);
        if (bytes.length < minimum || bytes.length > maximum) {
          throw new SetupError(
            "validation",
            `${label} 길이를 확인해 주세요. 현재 ${bytes.length}바이트입니다.`,
          );
        }
      }

      async function closeSession() {
        try {
          await fetch("/setup/api/session", {
            method: "DELETE",
            credentials: "same-origin",
            keepalive: true,
          });
        } catch {
          // The page is already safe to close when loopback disappears.
        }
      }

      async function pairJetson() {
        const file = jetsonInput.files?.[0];
        if (!file) return;
        if (file.size < 1 || file.size > 65_536) {
          jetsonStatus.textContent = "Jetson 연결 파일은 64KB 이하의 JSON이어야 합니다.";
          jetsonStatus.dataset.state = "error";
          return;
        }
        let bytes;
        jetsonButton.disabled = true;
        jetsonStatus.textContent = "Jetson을 확인하고 안전하게 연결하고 있습니다…";
        jetsonStatus.dataset.state = "working";
        try {
          bytes = new Uint8Array(await file.arrayBuffer());
          const response = await fetch("/setup/api/jetson", {
            method: "POST",
            credentials: "same-origin",
            headers: { "Content-Type": "application/json" },
            body: bytes,
          });
          if (!response.ok) {
            throw new SetupError(
              "jetson",
              "Jetson 연결 파일을 확인할 수 없습니다. 기존 카메라 설정은 유지됩니다.",
            );
          }
          const result = await response.json();
          if (result.status !== "paired" || result.restart_required !== true) {
            throw new SetupError("jetson", "Jetson 연결 응답이 올바르지 않습니다.");
          }
          jetsonStatus.textContent =
            "Jetson 연결 완료 · Home Agent를 재시작하면 카메라가 켜집니다.";
          jetsonStatus.dataset.state = "success";
        } catch (error) {
          jetsonStatus.textContent = friendlyError(error);
          jetsonStatus.dataset.state = "error";
          jetsonButton.disabled = false;
        } finally {
          if (bytes) bytes.fill(0);
          jetsonInput.value = "";
        }
      }

      function setStatus(product, message, state) {
        const status = statuses[product];
        status.textContent = message;
        status.dataset.state = state;
      }

      function friendlyError(error) {
        if (error instanceof SetupError) return error.message;
        return "Pico 연결 중 문제가 발생했습니다. USB 케이블을 확인하고 다시 시도하세요.";
      }

      async function connectProduct(product, button) {
        const ssid = ssidInput.value;
        const wifiPassword = wifiPasswordInput.value;

        button.disabled = true;
        setStatus(product, "Pico를 확인하고 있습니다…", "working");
        try {
          validateField(ssid, 1, 32, "Wi-Fi 이름");
          validateField(wifiPassword, 8, 63, "Wi-Fi 비밀번호");
          const response = await fetch(`/setup/api/pico/${product}`, {
            method: "POST",
            credentials: "same-origin",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
              wifi_ssid: ssid,
              wifi_password: wifiPassword,
            }),
          });
          let result;
          try {
            result = await response.json();
          } catch {
            throw new SetupError(
              "protocol",
              "Home Agent 응답을 확인할 수 없습니다. 페이지를 새로고침해 주세요.",
            );
          }
          if (!response.ok) {
            const code = result?.error?.code;
            const messages = {
              pico_busy: "다른 Pico를 설정 중입니다. 잠시 후 다시 시도하세요.",
              pico_wrong_product:
                `다른 Pico가 연결되었습니다. ${productLabels[product]} Pico를 확인해 주세요.`,
              pico_timeout:
                "Pico 응답 시간이 초과되었습니다. USB 케이블을 확인하고 다시 시도하세요.",
              pico_unavailable:
                "연결된 Pico를 찾지 못했습니다. USB 데이터 케이블과 전원을 확인해 주세요.",
              pico_uncertain:
                "Pico가 설정을 저장한 직후 응답이 끊겼습니다. 전원을 다시 연결한 뒤 상태를 확인해 주세요.",
            };
            throw new SetupError(
              code || "pico",
              messages[code]
                || "Pico 설정을 확인하지 못했습니다. USB 연결을 확인하고 다시 시도하세요.",
            );
          }
          if (result?.status !== "provisioned" || result?.product !== product) {
            throw new SetupError(
              "protocol",
              "Home Agent의 Pico 설정 응답이 올바르지 않습니다.",
            );
          }
          setStatus(product, "Wi-Fi 설정을 안전하게 저장하고 있습니다…", "working");

          completed.add(product);
          setStatus(product, "연결 완료", "success");
          if (completed.size === 2) {
            ssidInput.value = "";
            wifiPasswordInput.value = "";
            completeMessage.hidden = false;
          }
        } catch (error) {
          setStatus(product, friendlyError(error), "error");
          button.disabled = false;
        }
      }

      jetsonInput.addEventListener("change", () => {
        jetsonButton.disabled = jetsonInput.files.length !== 1;
      });
      jetsonButton.addEventListener("click", pairJetson);
      finishButton.addEventListener("click", async () => {
        ssidInput.value = "";
        wifiPasswordInput.value = "";
        jetsonInput.value = "";
        await closeSession();
        for (const button of [...buttons, jetsonButton, finishButton]) {
          button.disabled = true;
        }
        completeMessage.querySelector("span").textContent =
          "설정이 안전하게 종료되었습니다. 이 창을 닫아도 됩니다.";
      });

      browserStatus.textContent =
        "Home Agent가 USB로 연결된 Pico를 자동으로 찾습니다.";
      for (const button of buttons) {
        button.addEventListener("click", () => connectProduct(button.dataset.product, button));
      }

      window.addEventListener("pagehide", () => {
        ssidInput.value = "";
        wifiPasswordInput.value = "";
        jetsonInput.value = "";
      });
    })();
  </script>
</body>
</html>
"""


def _response_headers(response: Response) -> Response:
    for name, value in SECURITY_HEADERS.items():
        response.headers[name] = value
    return response


def _error(status_code: int, code: str) -> JSONResponse:
    return _response_headers(
        JSONResponse(
            status_code=status_code,
            content={"error": {"code": code, "message": "Setup request was rejected"}},
        )
    )


class SetupSecurityHeadersMiddleware:
    def __init__(self, app: object) -> None:
        self.app = app

    async def __call__(self, scope: dict[str, object], receive, send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        path = str(scope.get("path", ""))
        if path != "/setup" and not path.startswith("/setup/"):
            await self.app(scope, receive, send)
            return
        hosts = Headers(scope=scope).getlist("host")
        if len(hosts) != 1 or hosts[0] not in ALLOWED_HOSTS:
            await _error(403, "host_forbidden")(scope, receive, send)
            return

        async def send_with_security_headers(message: dict[str, object]) -> None:
            if message["type"] == "http.response.start":
                headers = MutableHeaders(scope=message)
                for name, value in SECURITY_HEADERS.items():
                    if name not in headers:
                        headers[name] = value
            await send(message)

        await self.app(scope, receive, send_with_security_headers)


def _host(request: Request) -> str | None:
    hosts = request.headers.getlist("host")
    if len(hosts) != 1 or hosts[0] not in ALLOWED_HOSTS:
        return None
    return hosts[0]


def install_setup(application: FastAPI) -> None:
    sessions: dict[bytes, float] = {}
    pico_lock = threading.Lock()
    router = APIRouter()
    application.add_middleware(SetupSecurityHeadersMiddleware)

    def prune_sessions(now: float) -> None:
        for digest, expires_at in tuple(sessions.items()):
            if expires_at <= now:
                sessions.pop(digest, None)

    def require_session(request: Request) -> tuple[bytes, JSONResponse | None]:
        host = _host(request)
        if host is None:
            return b"", _error(403, "host_forbidden")
        if request.headers.get("origin") != f"http://{host}":
            return b"", _error(403, "origin_forbidden")
        token = request.cookies.get(COOKIE_NAME)
        if not token:
            return b"", _error(401, "session_required")
        digest = hashlib.sha256(token.encode("utf-8")).digest()
        now = time.monotonic()
        expires_at = sessions.get(digest)
        if expires_at is None or expires_at <= now:
            sessions.pop(digest, None)
            return b"", _error(401, "session_expired")
        prune_sessions(now)
        return digest, None

    @router.get("/setup", response_model=None)
    async def setup_page(request: Request) -> Response:
        if _host(request) is None:
            return _error(403, "host_forbidden")
        now = time.monotonic()
        prune_sessions(now)
        previous = request.cookies.get(COOKIE_NAME)
        if previous:
            sessions.pop(hashlib.sha256(previous.encode("utf-8")).digest(), None)
        if len(sessions) >= SESSION_LIMIT:
            oldest = min(sessions, key=sessions.get)
            sessions.pop(oldest, None)
        token = secrets.token_urlsafe(32)
        digest = hashlib.sha256(token.encode("utf-8")).digest()
        sessions[digest] = now + SESSION_SECONDS
        nonce = secrets.token_urlsafe(18)
        response = _response_headers(
            HTMLResponse(SETUP_HTML.replace("__NONCE__", nonce))
        )
        response.headers["Content-Security-Policy"] = (
            "default-src 'none'; "
            f"script-src 'nonce-{nonce}'; style-src 'nonce-{nonce}'; "
            "connect-src 'self'; base-uri 'none'; frame-ancestors 'none'; "
            "form-action 'self'"
        )
        response.set_cookie(
            COOKIE_NAME,
            token,
            max_age=SESSION_SECONDS,
            path="/setup",
            httponly=True,
            samesite="strict",
        )
        return response

    @router.post("/setup/api/pico/{product}", response_model=None)
    async def configure_pico(product: str, request: Request) -> Response:
        _digest, error = require_session(request)
        if error is not None:
            return error
        if product not in PICO_PRODUCTS:
            return _error(404, "product_not_found")
        media_type = request.headers.get("content-type", "").partition(";")[0].strip().lower()
        if media_type != "application/json":
            return _error(415, "unsupported_media_type")
        content_lengths = request.headers.getlist("content-length")
        if len(content_lengths) > 1:
            return _error(400, "invalid_request")
        if content_lengths:
            try:
                declared_size = int(content_lengths[0])
            except ValueError:
                return _error(400, "invalid_request")
            if declared_size < 1 or declared_size > MAX_PICO_REQUEST_BYTES:
                return _error(413, "request_too_large")

        body = bytearray()
        try:
            async for chunk in request.stream():
                if len(body) + len(chunk) > MAX_PICO_REQUEST_BYTES:
                    return _error(413, "request_too_large")
                body.extend(chunk)
            try:
                data = json.loads(body.decode("utf-8", errors="strict"))
            except (UnicodeDecodeError, json.JSONDecodeError):
                return _error(400, "invalid_request")
            if not isinstance(data, dict) or set(data) != {
                "wifi_ssid",
                "wifi_password",
            }:
                return _error(400, "invalid_request")
            wifi_ssid = data["wifi_ssid"]
            wifi_password = data["wifi_password"]
            if not isinstance(wifi_ssid, str) or not isinstance(wifi_password, str):
                return _error(400, "invalid_request")
            if "\0" in wifi_ssid or "\0" in wifi_password:
                return _error(400, "invalid_request")
            if not 1 <= len(wifi_ssid.encode("utf-8")) <= 32:
                return _error(400, "invalid_request")
            if not 8 <= len(wifi_password.encode("utf-8")) <= 63:
                return _error(400, "invalid_request")

            config = getattr(request.app.state, "config", None)
            endpoint = getattr(request.app.state, "mqtt_endpoint", None)
            if (
                not getattr(config, "mqtt_enabled", False)
                or not isinstance(endpoint, MqttEndpoint)
            ):
                return _error(503, "mqtt_unavailable")
            username = getattr(config, "mqtt_username", None)
            password = getattr(config, "mqtt_password", None)
            if username is None or password is None:
                return _error(503, "mqtt_unavailable")
            if not pico_lock.acquire(blocking=False):
                return _error(409, "pico_busy")

            provisioner = getattr(request.app.state, "pico_provisioner", provision_pico)
            try:
                await run_in_threadpool(
                    lambda: provisioner(
                        product=product,
                        wifi_ssid=wifi_ssid,
                        wifi_password=wifi_password,
                        mqtt_host=endpoint.host,
                        mqtt_port=endpoint.port,
                        mqtt_username=username.get_secret_value(),
                        mqtt_password=password.get_secret_value(),
                    )
                )
            except PicoProvisioningError as provision_error:
                status_by_code = {
                    "validation": 400,
                    "wrong_product": 409,
                    "unavailable": 503,
                    "disconnect": 503,
                    "timeout": 504,
                    "uncertain": 502,
                }
                return _error(
                    status_by_code.get(provision_error.code, 502),
                    f"pico_{provision_error.code}",
                )
            except (OSError, RuntimeError, ValueError):
                return _error(503, "pico_unavailable")
            finally:
                pico_lock.release()
            return _response_headers(
                JSONResponse({"status": "provisioned", "product": product})
            )
        finally:
            body[:] = b"\0" * len(body)

    @router.delete("/setup/api/session", response_model=None)
    async def delete_session(request: Request) -> Response:
        digest, error = require_session(request)
        if error is not None:
            return error
        sessions.pop(digest, None)
        response = _response_headers(Response(status_code=204))
        response.delete_cookie(COOKIE_NAME, path="/setup", httponly=True, samesite="strict")
        return response

    @router.post("/setup/api/jetson", response_model=None)
    async def pair_optional_jetson(request: Request) -> Response:
        _digest, error = require_session(request)
        if error is not None:
            return error
        media_type = request.headers.get("content-type", "").partition(";")[0].strip().lower()
        if media_type != "application/json":
            return _error(415, "unsupported_media_type")
        content_lengths = request.headers.getlist("content-length")
        if len(content_lengths) > 1:
            return _error(400, "invalid_request")
        if content_lengths:
            try:
                declared_size = int(content_lengths[0])
            except ValueError:
                return _error(400, "invalid_request")
            if declared_size < 1 or declared_size > MAX_JETSON_BUNDLE_BYTES:
                return _error(413, "pairing_bundle_too_large")

        bundle = bytearray()
        async for chunk in request.stream():
            if len(bundle) + len(chunk) > MAX_JETSON_BUNDLE_BYTES:
                return _error(413, "pairing_bundle_too_large")
            bundle.extend(chunk)
        if not bundle:
            return _error(400, "pairing_rejected")

        agent_config_path = getattr(request.app.state, "agent_config_path", None)
        jetson_config_path = getattr(request.app.state, "jetson_config_path", None)
        if not isinstance(agent_config_path, Path) or not isinstance(jetson_config_path, Path):
            return _error(503, "agent_unavailable")
        if not agent_config_path.is_absolute() or not jetson_config_path.is_absolute():
            return _error(503, "agent_unavailable")

        temporary = agent_config_path.with_name(
            f".jetson-pairing.{secrets.token_hex(8)}.tmp"
        )

        def import_bundle() -> None:
            try:
                _write_private_file(temporary, bundle)
                pair_jetson(agent_config_path, temporary, jetson_config_path)
            finally:
                temporary.unlink(missing_ok=True)

        try:
            await run_in_threadpool(import_bundle)
        except (OSError, RuntimeError, ValueError):
            return _error(400, "pairing_rejected")
        finally:
            bundle[:] = b"\0" * len(bundle)
        return _response_headers(
            JSONResponse({"status": "paired", "restart_required": True})
        )

    application.include_router(router)
