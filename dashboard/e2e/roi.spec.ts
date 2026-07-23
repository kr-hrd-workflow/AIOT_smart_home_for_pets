import { readFileSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

import AxeBuilder from "@axe-core/playwright";
import { expect, test, type Page } from "@playwright/test";

import { demoDashboardData } from "../lib/demo-data";
import type { DashboardData, DashboardSummary } from "../lib/types";

const dashboardRoot = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const demoCamera = readFileSync(
  resolve(dashboardRoot, "public/demo-camera.webp"),
);
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

async function mockRemoteDashboard(page: Page) {
  const summary = structuredClone(summaryOf(demoDashboardData));
  await page.route("**/api/petcare/**", async (route) => {
    const request = route.request();
    const url = new URL(request.url());
    if (
      url.pathname === "/api/petcare/status" &&
      request.method() === "GET"
    ) {
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        json: {
          home: { id: "home-1", state: "ready" },
          agent: {
            id: "agent-1",
            state: "online",
            last_seen_at: summary.generated_at,
          },
          camera: {
            id: "camera-1",
            state: "online",
            last_seen_at: summary.generated_at,
          },
          dashboard: summary,
        },
      });
      return;
    }
    if (
      url.pathname === "/api/petcare/clips" &&
      request.method() === "GET"
    ) {
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        json: { clips: [] },
      });
      return;
    }
    if (
      url.pathname === "/api/petcare/cameras/camera-1/stream.mjpeg" &&
      request.method() === "GET"
    ) {
      await route.fulfill({
        status: 200,
        contentType: "image/webp",
        body: demoCamera,
      });
      return;
    }
    await route.fulfill({
      status: 404,
      contentType: "application/json",
      json: { code: "not_found" },
    });
  });
}

async function gotoRemoteDashboard(page: Page) {
  await mockRemoteDashboard(page);
  const response = await page.goto("/dashboard", {
    waitUntil: "domcontentloaded",
  });
  expect(response?.ok()).toBe(true);
  await expect(page.locator("[data-dashboard-mode=connected]")).toBeVisible();
}

async function expectViewportIntegrity(page: Page, viewportName: string) {
  const overflow = await page.evaluate(() => ({
    document: document.documentElement.scrollWidth - window.innerWidth,
    body: document.body.scrollWidth - window.innerWidth,
  }));
  expect(
    overflow.document,
    `${viewportName} document overflow`,
  ).toBeLessThanOrEqual(1);
  expect(
    overflow.body,
    `${viewportName} body overflow`,
  ).toBeLessThanOrEqual(1);
  const results = await new AxeBuilder({ page }).analyze();
  expect(
    results.violations,
    JSON.stringify(results.violations, null, 2),
  ).toEqual([]);
}

test.describe("remote ROI privacy contract", () => {
  test.beforeEach(async ({}, testInfo) => {
    test.skip(
      testInfo.project.name !== "connected",
      "Remote ROI QA is connected-mode only",
    );
  });

  for (const viewport of viewports) {
    test(`hides local ROI details and edit controls at ${viewport.name}`, async ({
      page,
    }) => {
      await page.setViewportSize(viewport);
      await gotoRemoteDashboard(page);
      await expect(
        page.locator(".remote-operational .roi-section"),
      ).not.toBeVisible();
      await expect(page.locator(".remote-operational .zone")).toHaveCount(2);
      await expect(
        page.locator(".remote-operational .zone").first(),
      ).not.toBeVisible();
      await expect(page.getByRole("spinbutton")).toHaveCount(0);
      await expect(
        page.getByRole("button", { name: "침대 영점 재설정" }),
      ).toHaveCount(0);
      await expectViewportIntegrity(page, viewport.name);
    });
  }
});
