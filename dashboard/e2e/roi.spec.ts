import { readFileSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

import AxeBuilder from "@axe-core/playwright";
import { expect, test, type Page, type Request, type WebSocketRoute } from "@playwright/test";

import { demoDashboardData } from "../lib/demo-data";
import type { DashboardData, DashboardSummary, ZoneIn, ZoneOut } from "../lib/types";

const dashboardRoot = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const demoCamera = readFileSync(resolve(dashboardRoot, "public/demo-camera.webp"));
const viewports = [
  { name: "desktop", width: 1440, height: 900 },
  { name: "tablet", width: 768, height: 1024 },
  { name: "mobile", width: 375, height: 812 },
] as const;

function summaryOf(data: DashboardData): DashboardSummary {
  return {
    generated_at: data.generated_at,
    health: data.health,
    devices: data.devices,
    latest_sensors: data.latest_sensors,
    camera: data.camera,
    bed: data.bed,
    behaviors: data.behaviors,
    anomalies: data.anomalies,
  };
}

function cors(origin = "*") {
  return {
    "access-control-allow-origin": origin,
    "access-control-allow-methods": "GET, POST, PUT, OPTIONS",
    "access-control-allow-headers": "Content-Type",
  };
}

interface RoiBackend {
  zones: [ZoneOut, ZoneOut];
  updates: Array<{ name: string; input: Omit<ZoneOut, "zone_name" | "updated_at"> }>;
}

function positiveAreaOverlap(left: ZoneIn, right: ZoneIn): boolean {
  return (
    Math.min(left.x2, right.x2) > Math.max(left.x1, right.x1) &&
    Math.min(left.y2, right.y2) > Math.max(left.y1, right.y1)
  );
}

async function mockRoiBackend(page: Page, state: RoiBackend) {
  let socket: WebSocketRoute | undefined;
  await page.routeWebSocket("ws://127.0.0.1:8000/ws/dashboard", (route) => {
    socket = route;
  });
  await page.route("http://127.0.0.1:8000/**", async (route) => {
    const request: Request = route.request();
    const url = new URL(request.url());
    const headers = cors(request.headers().origin);
    if (request.method() === "OPTIONS") {
      await route.fulfill({ status: 204, headers });
      return;
    }
    if (url.pathname === "/api/dashboard/summary" && request.method() === "GET") {
      await route.fulfill({ status: 200, headers, json: summaryOf(demoDashboardData) });
      return;
    }
    if (url.pathname === "/api/zones" && request.method() === "GET") {
      await route.fulfill({ status: 200, headers, json: state.zones });
      return;
    }
    if (url.pathname === "/api/video_feed" && request.method() === "GET") {
      await route.fulfill({ status: 200, headers, contentType: "image/webp", body: demoCamera });
      return;
    }
    if (url.pathname.startsWith("/api/zones/") && request.method() === "PUT") {
      const name = url.pathname.endsWith("food_bowl") ? "food_bowl" : "pet_bed";
      const input = request.postDataJSON() as Omit<ZoneOut, "zone_name" | "updated_at">;
      state.updates.push({ name, input });
      const other = state.zones.find((zone) => zone.zone_name !== name);
      if (input.enabled && other?.enabled && positiveAreaOverlap(input, other)) {
        await route.fulfill({
          status: 409,
          headers,
          json: { code: "zone_conflict", message: "Enabled zones must not overlap" },
        });
        return;
      }
      const updated: ZoneOut = {
        zone_name: name,
        ...input,
        updated_at: "2026-07-15T01:44:00Z",
      };
      state.zones = state.zones.map((zone) => zone.zone_name === name ? updated : zone) as [ZoneOut, ZoneOut];
      await route.fulfill({ status: 200, headers, json: updated });
      return;
    }
    await route.fulfill({ status: 404, headers, json: { code: "not_found", message: "Not found" } });
  });
  return { connected: () => socket !== undefined };
}

async function gotoRoi(page: Page, overrides?: Partial<RoiBackend>) {
  const state: RoiBackend = {
    zones: structuredClone(demoDashboardData.zones),
    updates: [],
    ...overrides,
  };
  const backend = await mockRoiBackend(page, state);
  const response = await page.goto("/", { waitUntil: "domcontentloaded" });
  expect(response?.ok()).toBe(true);
  await expect(page.locator("[data-dashboard-mode=connected]")).toBeVisible();
  await expect.poll(backend.connected).toBe(true);
  return state;
}

async function expectOverlay(
  page: Page,
  label: string,
  expected: { x1: number; y1: number; x2: number; y2: number },
) {
  const frame = await page.locator(".camera-frame").boundingBox();
  const overlay = await page.getByLabel(label).boundingBox();
  expect(frame).not.toBeNull();
  expect(overlay).not.toBeNull();
  if (!frame || !overlay) return;
  const tolerance = 2.5;
  expect(Math.abs(overlay.x - (frame.x + frame.width * expected.x1 / 640))).toBeLessThanOrEqual(tolerance);
  expect(Math.abs(overlay.y - (frame.y + frame.height * expected.y1 / 480))).toBeLessThanOrEqual(tolerance);
  expect(Math.abs(overlay.width - frame.width * (expected.x2 - expected.x1) / 640)).toBeLessThanOrEqual(tolerance);
  expect(Math.abs(overlay.height - frame.height * (expected.y2 - expected.y1) / 480)).toBeLessThanOrEqual(tolerance);
}

async function replaceNumber(page: Page, label: string, value: number) {
  const input = page.getByRole("spinbutton", { name: label });
  await input.focus();
  await page.keyboard.press("Control+A");
  await page.keyboard.type(String(value));
  await expect(input).toHaveValue(String(value));
}

async function expectViewportIntegrity(page: Page, viewportName: string) {
  const overflow = await page.evaluate(() => ({
    document: document.documentElement.scrollWidth - window.innerWidth,
    body: document.body.scrollWidth - window.innerWidth,
  }));
  expect(overflow.document, `${viewportName} document overflow`).toBeLessThanOrEqual(1);
  expect(overflow.body, `${viewportName} body overflow`).toBeLessThanOrEqual(1);
  const results = await new AxeBuilder({ page }).analyze();
  expect(results.violations, JSON.stringify(results.violations, null, 2)).toEqual([]);
}

test.describe("connected ROI pixel and keyboard contract", () => {
  test.beforeEach(async ({}, testInfo) => {
    test.skip(testInfo.project.name !== "connected", "ROI editing is connected-mode only");
  });

  for (const viewport of viewports) {
    test(`aligns and saves a positive-area-free edge-touch ROI at ${viewport.name}`, async ({ page }) => {
      await page.setViewportSize(viewport);
      const state = await gotoRoi(page);
      await expectOverlay(page, "food_bowl 감시 영역", { x1: 40, y1: 260, x2: 260, y2: 470 });
      await expectOverlay(page, "pet_bed 감시 영역", { x1: 320, y1: 180, x2: 630, y2: 470 });

      await replaceNumber(page, "침대 구역 x1", 260);
      await replaceNumber(page, "침대 구역 y1", 0);
      await replaceNumber(page, "침대 구역 x2", 640);
      await replaceNumber(page, "침대 구역 y2", 480);
      await page.getByRole("button", { name: "침대 구역 저장" }).click();

      await expect.poll(() => state.updates.length).toBe(1);
      expect(state.updates[0]).toEqual({
        name: "pet_bed",
        input: { x1: 260, y1: 0, x2: 640, y2: 480, enabled: true },
      });
      await expectOverlay(page, "pet_bed 감시 영역", { x1: 260, y1: 0, x2: 640, y2: 480 });
      await expectViewportIntegrity(page, viewport.name);
    });
  }

  test("blocks invalid geometry locally and renders a 409 zone conflict", async ({ page }) => {
    const state = await gotoRoi(page);
    await replaceNumber(page, "급식 구역 x1", 260);
    await page.getByRole("button", { name: "급식 구역 저장" }).click();
    await expect(page.getByRole("alert")).toHaveText("영역은 0–640 × 0–480 정수 범위 안에서 양의 크기여야 합니다.");
    expect(state.updates).toEqual([]);

    await replaceNumber(page, "침대 구역 x1", 259);
    await page.getByRole("button", { name: "침대 구역 저장" }).click();
    await expect(page.getByRole("alert")).toHaveText("Enabled zones must not overlap");
    expect(state.updates).toHaveLength(1);
    expect(state.updates[0]).toEqual({
      name: "pet_bed",
      input: { x1: 259, y1: 180, x2: 630, y2: 470, enabled: true },
    });
  });
});
