import { spawn, type ChildProcess } from "node:child_process";
import { readFileSync, realpathSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

import { expect, test } from "@playwright/test";


const dashboardRoot = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const repositoryRoot = resolve(dashboardRoot, "..");
const backendRoot = resolve(repositoryRoot, "backend");
const setupUrl = "http://127.0.0.1:8000/setup";
let backend: ChildProcess | undefined;
let backendError = "";

const serverCode = `
from types import SimpleNamespace
from fastapi import FastAPI
from pydantic import SecretStr
from app.api import install_api
from app.mqtt_ingest import MqttEndpoint
from app.setup import install_setup
import uvicorn

app = FastAPI()
app.state.config = SimpleNamespace(
    mqtt_enabled=True,
    mqtt_username=SecretStr("test-mqtt-user"),
    mqtt_password=SecretStr("test-mqtt-password"),
)
app.state.mqtt_endpoint = MqttEndpoint("192.168.0.20", 18883)
app.state.pico_provisioner = lambda **_kwargs: None
install_api(app)
install_setup(app)
uvicorn.run(app, host="127.0.0.1", port=8000, log_level="error", access_log=False)
`;

async function waitForBackend(): Promise<void> {
  for (let attempt = 0; attempt < 50; attempt += 1) {
    if (backend?.exitCode !== null) {
      throw new Error(`Local setup server exited early: ${backend?.exitCode}\n${backendError}`);
    }
    try {
      const response = await fetch(setupUrl);
      if (response.ok) return;
    } catch {
      // The loopback listener is still starting.
    }
    await new Promise((resolveWait) => setTimeout(resolveWait, 100));
  }
  throw new Error("Local setup server did not become ready");
}

test.beforeAll(async () => {
  const toolchain = JSON.parse(
    readFileSync(resolve(repositoryRoot, ".runtime/toolchain.json"), "utf8"),
  ) as { paths?: { python_path?: string } };
  if (!toolchain.paths?.python_path) throw new Error("Managed Python path is missing");
  const managedPython = realpathSync.native(toolchain.paths.python_path);
  const pythonPath = resolve(backendRoot, ".venv/Lib/site-packages");
  backend = spawn(managedPython, ["-c", serverCode], {
    cwd: backendRoot,
    env: {
      ...process.env,
      PYTHONPATH: pythonPath,
      PYTHONUNBUFFERED: "1",
    },
    stdio: ["ignore", "ignore", "pipe"],
    windowsHide: true,
  });
  backend.stderr?.on("data", (chunk) => {
    backendError += String(chunk);
  });
  await waitForBackend();
});

test.afterAll(async () => {
  if (!backend || backend.exitCode !== null) return;
  backend.kill();
  await Promise.race([
    new Promise<void>((resolveExit) => backend?.once("exit", () => resolveExit())),
    new Promise<void>((resolveWait) => setTimeout(resolveWait, 2_000)),
  ]);
});

test("provisions both fixed Pico products through the Home Agent API", async ({ page }) => {
  const requests: Array<{ url: string; body: string | null }> = [];
  page.on("request", (request) => {
    if (request.url().includes("/setup/api/pico/")) {
      requests.push({ url: request.url(), body: request.postData() });
    }
  });
  await page.goto(setupUrl);

  await page.getByLabel("Wi-Fi 이름").fill("test-network");
  await page.getByLabel("Wi-Fi 비밀번호").fill("password-for-test");
  expect(requests).toHaveLength(0);

  await page.getByRole("button", { name: "현관 Pico 연결" }).click();
  await expect(page.getByTestId("entrance-status")).toHaveText("연결 완료");
  expect(requests).toHaveLength(1);
  expect(requests[0].url).toBe(`${setupUrl}/api/pico/entrance-01`);
  expect(JSON.parse(requests[0].body ?? "")).toEqual({
    wifi_ssid: "test-network",
    wifi_password: "password-for-test",
  });

  await page.getByRole("button", { name: "생활공간 Pico 연결" }).click();
  await expect(page.getByTestId("petzone-status")).toHaveText("연결 완료");
  expect(requests).toHaveLength(2);
  expect(requests[1].url).toBe(`${setupUrl}/api/pico/petzone-01`);
  await expect(page.getByLabel("Wi-Fi 이름")).toHaveValue("");
  await expect(page.getByLabel("Wi-Fi 비밀번호")).toHaveValue("");
});

test("keeps the first step active when the server detects the other Pico", async ({ page }) => {
  await page.route("**/setup/api/pico/entrance-01", async (route) => {
    await route.fulfill({
      status: 409,
      contentType: "application/json",
      json: {
        error: {
          code: "pico_wrong_product",
          message: "Setup request was rejected",
        },
      },
    });
  });
  await page.goto(setupUrl);
  await page.getByLabel("Wi-Fi 이름").fill("test-network");
  await page.getByLabel("Wi-Fi 비밀번호").fill("password-for-test");

  await page.getByRole("button", { name: "현관 Pico 연결" }).click();

  await expect(page.getByTestId("entrance-status")).toContainText("다른 Pico");
  await expect(page.getByRole("button", { name: "현관 Pico 연결" })).toBeEnabled();
  await expect(page.getByRole("button", { name: "생활공간 Pico 연결" })).toBeDisabled();
});

test("permits retry when the Home Agent cannot find a connected Pico", async ({ page }) => {
  await page.route("**/setup/api/pico/entrance-01", async (route) => {
    await route.fulfill({
      status: 503,
      contentType: "application/json",
      json: {
        error: {
          code: "pico_unavailable",
          message: "Setup request was rejected",
        },
      },
    });
  });
  await page.goto(setupUrl);
  await page.getByLabel("Wi-Fi 이름").fill("test-network");
  await page.getByLabel("Wi-Fi 비밀번호").fill("password-for-test");

  await page.getByRole("button", { name: "현관 Pico 연결" }).click();

  await expect(page.getByTestId("entrance-status")).toContainText(
    "연결된 Pico를 찾지 못했습니다",
  );
  await expect(page.getByRole("button", { name: "현관 Pico 연결" })).toBeEnabled();
});

test("pairs an optional Jetson bundle through the local web page", async ({ page }) => {
  let body = "";
  await page.route("**/setup/api/jetson", async (route) => {
    body = route.request().postData() ?? "";
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      json: { status: "paired", restart_required: true },
    });
  });
  await page.goto(setupUrl);
  await page.getByLabel("Jetson 연결 파일").setInputFiles({
    name: "pairing.json",
    mimeType: "application/json",
    buffer: Buffer.from('{"pairing":"test-only"}'),
  });

  await page.getByRole("button", { name: "Jetson 연결", exact: true }).click();

  await expect(page.getByTestId("jetson-status")).toContainText("Jetson 연결 완료");
  expect(body).toBe('{"pairing":"test-only"}');
  await expect(page.getByLabel("Jetson 연결 파일")).toHaveValue("");
});

test("uses the web page without exposing or requiring Web Serial", async ({ page }) => {
  await page.addInitScript(() => {
    Object.defineProperty(navigator, "serial", {
      configurable: true,
      value: undefined,
    });
  });
  await page.goto(setupUrl);

  await expect(page.getByTestId("browser-status")).toContainText(
    "Home Agent가 USB로 연결된 Pico를 자동으로 찾습니다",
  );
  await expect(page.getByRole("button", { name: "현관 Pico 연결" })).toBeEnabled();
  await expect(page.getByRole("button", { name: "생활공간 Pico 연결" })).toBeDisabled();
});
