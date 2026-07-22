import { existsSync, readFileSync, statSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

import AxeBuilder from "@axe-core/playwright";
import {
  expect,
  test,
  type Page,
  type Request,
  type WebSocketRoute,
} from "@playwright/test";

import {
  validatePlaywrightRuntimeRecord,
  type PlaywrightRuntimeExpectation,
  type PlaywrightRuntimeRecord,
} from "../playwright.config";
import { demoDashboardData } from "../lib/demo-data";
import type {
  BedStatus,
  DashboardData,
  DashboardSummary,
  ZoneIn,
  ZoneOut,
} from "../lib/types";

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

function jsonHeaders(origin = "*") {
  return {
    "access-control-allow-origin": origin,
    "access-control-allow-methods": "GET, POST, PUT, OPTIONS",
    "access-control-allow-headers": "Content-Type",
    "content-type": "application/json",
  };
}

type CalibrationReply = "success" | "occupied" | "unavailable";

interface ConnectedState {
  summary: DashboardSummary;
  zones: [ZoneOut, ZoneOut];
  calibration: CalibrationReply;
  holdCalibration: boolean;
  releaseCalibration?: () => void;
}

function positiveAreaOverlap(left: ZoneIn, right: ZoneIn): boolean {
  return (
    Math.min(left.x2, right.x2) > Math.max(left.x1, right.x1) &&
    Math.min(left.y2, right.y2) > Math.max(left.y1, right.y1)
  );
}

async function mockConnectedBackend(page: Page, state: ConnectedState) {
  let socket: WebSocketRoute | undefined;
  const requests: string[] = [];

  await page.routeWebSocket("ws://127.0.0.1:8000/ws/dashboard", (route) => {
    socket = route;
  });
  await page.route("http://127.0.0.1:8000/**", async (route) => {
    const request = route.request();
    const url = new URL(request.url());
    requests.push(`${request.method()} ${url.pathname}`);
    const origin = request.headers().origin ?? "*";

    if (request.method() === "OPTIONS") {
      await route.fulfill({ status: 204, headers: jsonHeaders(origin) });
      return;
    }
    if (url.pathname === "/api/dashboard/summary" && request.method() === "GET") {
      await route.fulfill({ status: 200, headers: jsonHeaders(origin), json: state.summary });
      return;
    }
    if (url.pathname === "/api/zones" && request.method() === "GET") {
      await route.fulfill({ status: 200, headers: jsonHeaders(origin), json: state.zones });
      return;
    }
    if (url.pathname === "/api/video_feed" && request.method() === "GET") {
      await route.fulfill({
        status: 200,
        headers: { "access-control-allow-origin": origin },
        contentType: "image/webp",
        body: demoCamera,
      });
      return;
    }
    if (url.pathname === "/api/bed/calibration" && request.method() === "POST") {
      if (state.holdCalibration) {
        await new Promise<void>((resolveRequest) => {
          state.releaseCalibration = resolveRequest;
        });
        state.releaseCalibration = undefined;
        state.holdCalibration = false;
      }
      if (state.calibration === "success") {
        await route.fulfill({
          status: 200,
          headers: jsonHeaders(origin),
          json: {
            device_id: "petzone-01",
            calibrated_at: "2026-07-15T01:43:01Z",
            window_start: "2026-07-15T01:42:00Z",
            window_end: "2026-07-15T01:43:00Z",
            channels: [
              { channel: "left", sample_count: 60, baseline: 812, polarity: 1 },
              { channel: "center", sample_count: 60, baseline: 905, polarity: 1 },
              { channel: "right", sample_count: 60, baseline: 844, polarity: 1 },
            ],
          },
        });
        return;
      }
      if (state.calibration === "occupied") {
        await route.fulfill({
          status: 409,
          headers: jsonHeaders(origin),
          json: { code: "occupied", message: "Bed is occupied", channels: [] },
        });
        return;
      }
      await route.fulfill({
        status: 503,
        headers: jsonHeaders(origin),
        json: { code: "worker_unavailable", message: "Rule worker is unavailable" },
      });
      return;
    }
    if (url.pathname.startsWith("/api/zones/") && request.method() === "PUT") {
      const name = url.pathname.endsWith("food_bowl") ? "food_bowl" : "pet_bed";
      const input = request.postDataJSON() as Omit<ZoneOut, "zone_name" | "updated_at">;
      const other = state.zones.find((zone) => zone.zone_name !== name);
      if (input.enabled && other?.enabled && positiveAreaOverlap(input, other)) {
        await route.fulfill({
          status: 409,
          headers: jsonHeaders(origin),
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
      await route.fulfill({ status: 200, headers: jsonHeaders(origin), json: updated });
      return;
    }
    await route.fulfill({ status: 404, headers: jsonHeaders(origin), json: { code: "not_found" } });
  });

  return {
    requests,
    send(message: unknown) {
      if (!socket) throw new Error("Dashboard WebSocket is not connected");
      socket.send(JSON.stringify(message));
    },
    connected: () => socket !== undefined,
  };
}

async function gotoConnected(page: Page, state?: Partial<ConnectedState>) {
  const connectedState: ConnectedState = {
    summary: structuredClone(summaryOf(demoDashboardData)),
    zones: structuredClone(demoDashboardData.zones),
    calibration: "success",
    holdCalibration: false,
    ...state,
  };
  const backend = await mockConnectedBackend(page, connectedState);
  const response = await page.goto("/", { waitUntil: "domcontentloaded" });
  expect(response?.ok()).toBe(true);
  await expect(page.locator("[data-dashboard-mode=connected]")).toBeVisible();
  await expect.poll(backend.connected).toBe(true);
  return { backend, state: connectedState };
}

async function installDemoClientProbe(page: Page) {
  await page.addInitScript(() => {
    const target = window as typeof window & {
      __petcareDemoProbe?: { fetches: string[]; webSockets: string[] };
    };
    const probe = { fetches: [] as string[], webSockets: [] as string[] };
    Object.defineProperty(target, "__petcareDemoProbe", { value: probe });

    const nativeFetch = window.fetch.bind(window);
    window.fetch = ((input: RequestInfo | URL, init?: RequestInit) => {
      probe.fetches.push(input instanceof globalThis.Request ? input.url : String(input));
      return nativeFetch(input, init);
    }) as typeof window.fetch;

    const NativeWebSocket = window.WebSocket;
    window.WebSocket = new Proxy(NativeWebSocket, {
      construct(targetConstructor, args, newTarget) {
        probe.webSockets.push(String(args[0]));
        return Reflect.construct(targetConstructor, args, newTarget);
      },
    });
  });
}

function forbiddenDemoRequests(requests: Request[], pageUrl: string) {
  const pageOrigin = new URL(pageUrl).origin;
  const staticTypes = new Set(["document", "stylesheet", "script", "font", "image"]);
  return requests
    .filter((request) => {
      const url = new URL(request.url());
      const sameOriginStatic = staticTypes.has(request.resourceType()) ||
        (request.resourceType() === "other" && url.pathname === "/favicon.svg");
      return (
        url.origin !== pageOrigin ||
        !sameOriginStatic ||
        url.port === "8000" ||
        url.pathname.startsWith("/api/") ||
        url.pathname === "/ws/dashboard"
      );
    })
    .map((request) => `${request.resourceType()} ${request.url()}`);
}

function resolveTypescriptImport(importer: string, specifier: string): string | null {
  if (!specifier.startsWith(".")) return null;
  const base = resolve(dirname(importer), specifier);
  for (const candidate of [
    base,
    `${base}.ts`,
    `${base}.tsx`,
    resolve(base, "index.ts"),
    resolve(base, "index.tsx"),
  ]) {
    if (existsSync(candidate) && statSync(candidate).isFile()) return candidate;
  }
  throw new Error(`Cannot resolve ${specifier} from ${importer}`);
}

function transitiveTypescriptSources(entry: string): Map<string, string> {
  const sources = new Map<string, string>();
  const visit = (path: string) => {
    if (sources.has(path)) return;
    const source = readFileSync(path, "utf8");
    sources.set(path, source);
    const specifiers = new Set(
      [...source.matchAll(/(?:from\s*|import\s*\(\s*|import\s*)["'](\.[^"']+)["']/g)]
        .map((match) => match[1]),
    );
    for (const specifier of specifiers) {
      const imported = resolveTypescriptImport(path, specifier);
      if (imported) visit(imported);
    }
  };
  visit(entry);
  return sources;
}

async function expectNoSectionOverlap(page: Page) {
  const overlaps = await page.locator("[data-dashboard-section]").evaluateAll((elements) => {
    const boxes = elements.map((element) => ({
      name: element.getAttribute("data-dashboard-section") ?? "unknown",
      rect: element.getBoundingClientRect().toJSON(),
    }));
    const collisions: string[] = [];
    for (let left = 0; left < boxes.length; left += 1) {
      for (let right = left + 1; right < boxes.length; right += 1) {
        const a = boxes[left];
        const b = boxes[right];
        const x = Math.min(a.rect.right, b.rect.right) - Math.max(a.rect.left, b.rect.left);
        const y = Math.min(a.rect.bottom, b.rect.bottom) - Math.max(a.rect.top, b.rect.top);
        if (x > 0.5 && y > 0.5) collisions.push(`${a.name}/${b.name}`);
      }
    }
    return collisions;
  });
  expect(overlaps).toEqual([]);
}

async function expectViewportIntegrity(page: Page, viewportName: string) {
  const overflow = await page.evaluate(() => ({
    document: document.documentElement.scrollWidth - window.innerWidth,
    body: document.body.scrollWidth - window.innerWidth,
  }));
  expect(overflow.document, `${viewportName} document overflow`).toBeLessThanOrEqual(1);
  expect(overflow.body, `${viewportName} body overflow`).toBeLessThanOrEqual(1);
  await expectNoSectionOverlap(page);
  const results = await new AxeBuilder({ page }).analyze();
  expect(results.violations, JSON.stringify(results.violations, null, 2)).toEqual([]);
}

function fusionBed(fusionState: BedStatus["fusion_state"]): BedStatus {
  const bed = structuredClone(demoDashboardData.bed);
  bed.fusion_state = fusionState;
  bed.camera_confirmed = false;
  bed.current_rest_seconds = 0;
  bed.pressure_state = fusionState === "unconfirmed_pressure" ? "occupied" : "empty";
  return bed;
}

test.describe("Playwright runtime identity", () => {
  test("rejects missing, mismatched, and PATH-only browser records", async ({}, testInfo) => {
    test.skip(testInfo.project.name !== "connected", "Run the identity contract once");
    const executablePath = resolve(dashboardRoot, "fixture-chrome");
    const expected: PlaywrightRuntimeExpectation = {
      manifestSha256: "A".repeat(64),
      packageVersion: "1.61.1",
      browserRevision: "1234",
      executablePath,
      executableSha256: "B".repeat(64),
    };
    const valid: PlaywrightRuntimeRecord = {
      manifest_sha256: expected.manifestSha256,
      package_version: expected.packageVersion,
      browser_revision: expected.browserRevision,
      executable_path: expected.executablePath,
      executable_sha256: expected.executableSha256,
    };

    expect(() => validatePlaywrightRuntimeRecord({}, expected)).toThrow(/Missing Playwright runtime field/);
    expect(() => validatePlaywrightRuntimeRecord({ ...valid, package_version: "1.61.0" }, expected)).toThrow(/package version mismatch/);
    expect(() => validatePlaywrightRuntimeRecord({ ...valid, browser_revision: "9999" }, expected)).toThrow(/revision mismatch/);
    expect(() => validatePlaywrightRuntimeRecord({ ...valid, executable_path: "chrome" }, expected)).toThrow(/must be absolute/);
    expect(() => validatePlaywrightRuntimeRecord({ ...valid, executable_sha256: "C".repeat(64) }, expected)).toThrow(/hash mismatch/);
  });
});

test.describe("real /demo route", () => {
  test.beforeEach(async ({}, testInfo) => {
    test.skip(testInfo.project.name === "connected", "Demo QA runs against dev and production builds");
  });

  test("allows only its document and same-origin static assets", async ({ page }) => {
    const demoEntry = resolve(dashboardRoot, "app/demo/page.tsx");
    const source = readFileSync(demoEntry, "utf8");
    expect(source).toMatch(/<Dashboard data=\{demoDashboardData\}/);
    expect(source).not.toMatch(/PetCareClient|ConnectedDashboard|useDashboard|\bfetch\b|WebSocket|127\.0\.0\.1|localhost/);

    const graph = transitiveTypescriptSources(demoEntry);
    const constructorOwners = [...graph.entries()]
      .filter(([, importedSource]) => importedSource.includes("new PetCareClient"))
      .map(([path]) => path.slice(dashboardRoot.length + 1).replaceAll("\\", "/"));
    expect(constructorOwners).toEqual(["lib/use-dashboard.ts"]);
    const dashboardSource = graph.get(resolve(dashboardRoot, "components/dashboard.tsx"));
    expect(dashboardSource).toBeDefined();
    const sharedDashboard = dashboardSource!.slice(
      dashboardSource!.indexOf("export function Dashboard"),
      dashboardSource!.indexOf("const unavailableDashboardData"),
    );
    const connectedDashboard = dashboardSource!.slice(
      dashboardSource!.indexOf("export function ConnectedDashboard"),
    );
    expect(sharedDashboard).not.toMatch(/useDashboard\(|new PetCareClient/);
    expect(connectedDashboard).toMatch(/useDashboard\(\)/);

    await installDemoClientProbe(page);
    const requests: Request[] = [];
    const sockets: string[] = [];
    page.on("request", (request) => requests.push(request));
    page.on("websocket", (socket) => sockets.push(socket.url()));

    const response = await page.goto("/demo", { waitUntil: "networkidle" });
    expect(response?.ok()).toBe(true);
    await expect(page.getByRole("heading", { level: 1, name: "PetCare 운영 현황" })).toBeVisible();
    await expect(page.locator("[data-dashboard-mode=demo]")).toBeVisible();

    const probe = await page.evaluate(() => (
      window as typeof window & {
        __petcareDemoProbe: { fetches: string[]; webSockets: string[] };
      }
    ).__petcareDemoProbe);
    expect(probe.fetches, "transitive PetCareClient construction must stay inactive on /demo").toEqual([]);
    expect(probe.webSockets.filter((value) => /127\.0\.0\.1:8000|localhost:8000|\/ws\/dashboard/.test(value))).toEqual([]);
    expect(sockets.filter((value) => /127\.0\.0\.1:8000|localhost:8000|\/ws\/dashboard/.test(value))).toEqual([]);
    expect(forbiddenDemoRequests(requests, page.url())).toEqual([]);

    const crossOriginImages = await page.locator("img").evaluateAll((images) => {
      const origin = window.location.origin;
      return images
        .map((image) => (image as HTMLImageElement).currentSrc || (image as HTMLImageElement).src)
        .filter((source) => new URL(source, window.location.href).origin !== origin);
    });
    expect(crossOriginImages).toEqual([]);
  });

  test("renders the full state without overflow or overlap at every target viewport", async ({ page }) => {
    for (const viewport of viewports) {
      await page.setViewportSize(viewport);
      await page.goto("/demo", { waitUntil: "networkidle" });

      await expect(page.getByText("현재 휴식")).toBeVisible();
      await expect(page.getByText("오늘 휴식 추정")).toBeVisible();
      await expect(page.getByText("야간 침대 이탈")).toBeVisible();
      await expect(page.getByText("42분", { exact: true }).first()).toBeVisible();
      await expect(page.getByText("3시간 59분", { exact: true })).toBeVisible();
      await expect(page.getByText("1회", { exact: true })).toBeVisible();
      await expect(page.getByRole("row", { name: /왼쪽 1042 ADC 기준 812 변화 230/ })).toBeVisible();
      await expect(page.getByRole("row", { name: /가운데 1398 ADC 기준 905 변화 493/ })).toBeVisible();
      await expect(page.getByRole("row", { name: /오른쪽 1156 ADC 기준 844 변화 312/ })).toBeVisible();
      await expect(page.getByText("12시간 식사 기록 없음", { exact: true }).first()).toBeVisible();
      await expect(page.getByText("카메라 확인 대기", { exact: true }).first()).toBeVisible();
      await expect(page.getByText("침대 센서 확인 필요", { exact: true }).first()).toBeVisible();
      await expect(page.getByText("dog_001", { exact: true }).first()).toBeVisible();
      await expect(page.getByText("cat_001", { exact: true }).first()).toBeVisible();

      const media = await page.getByRole("img", { name: "반려동물 침대와 급식 구역 데모 카메라" }).evaluate(async (image) => {
        const element = image as HTMLImageElement;
        await element.decode();
        const canvas = document.createElement("canvas");
        canvas.width = 32;
        canvas.height = 24;
        const context = canvas.getContext("2d", { willReadFrequently: true });
        if (!context) return { width: element.naturalWidth, height: element.naturalHeight, colors: 0 };
        context.drawImage(element, 0, 0, canvas.width, canvas.height);
        const pixels = context.getImageData(0, 0, canvas.width, canvas.height).data;
        const colors = new Set<string>();
        for (let index = 0; index < pixels.length; index += 16) {
          colors.add(`${pixels[index]},${pixels[index + 1]},${pixels[index + 2]}`);
        }
        return { width: element.naturalWidth, height: element.naturalHeight, colors: colors.size };
      });
      expect(media.width).toBeGreaterThan(0);
      expect(media.height).toBeGreaterThan(0);
      expect(media.colors).toBeGreaterThan(8);

      const overflow = await page.evaluate(() => ({
        document: document.documentElement.scrollWidth - window.innerWidth,
        body: document.body.scrollWidth - window.innerWidth,
      }));
      expect(overflow.document, `${viewport.name} document overflow`).toBeLessThanOrEqual(1);
      expect(overflow.body, `${viewport.name} body overflow`).toBeLessThanOrEqual(1);
      await expectNoSectionOverlap(page);

      if (viewport.name === "mobile") {
        const order = await page.locator("[data-dashboard-section]").evaluateAll((sections) =>
          sections.map((section) => section.getAttribute("data-dashboard-section")),
        );
        expect(order).toEqual([
          "summary",
          "camera",
          "confirmed-rest",
          "warnings",
          "environment-food",
          "timeline",
          "roi",
          "device-health",
        ]);
      }

      const results = await new AxeBuilder({ page }).analyze();
      expect(results.violations, JSON.stringify(results.violations, null, 2)).toEqual([]);
    }

    await expect(page.getByText(/Discord|SMS|이메일 알림|알림 설정|위험|의료 진단/)).toHaveCount(0);
  });
});

test.describe("connected dashboard states", () => {
  test.beforeEach(async ({}, testInfo) => {
    test.skip(testInfo.project.name !== "connected", "Connected QA uses fixed loopback mocks");
  });

  for (const viewport of viewports) {
    test(`covers connected calibration, fusion, handoff, and offline states at ${viewport.name}`, async ({ page }) => {
      await page.setViewportSize(viewport);
      const { backend, state } = await gotoConnected(page);
      await expect(page.getByText("dog_001", { exact: true }).first()).toBeVisible();
      await expect(page.getByText("12시간 식사 기록 없음", { exact: true }).first()).toBeVisible();
      await expect(page.getByText("카메라 확인 대기", { exact: true }).first()).toBeVisible();
      await expect(page.getByText("침대 센서 확인 필요", { exact: true }).first()).toBeVisible();
      await expect(page.getByRole("row", { name: /왼쪽 1042 ADC 기준 812 변화 230/ })).toBeVisible();

      const button = page.getByRole("button", { name: "침대 영점 재설정" });
      const status = page.getByRole("status");
      state.calibration = "success";
      state.holdCalibration = true;
      await button.click();
      await expect(button).toBeDisabled();
      await expect(button).toHaveAttribute("aria-busy", "true");
      await expect(status).toHaveText("60초 영점 보정을 진행하고 있습니다.");
      await expect.poll(() => typeof state.releaseCalibration).toBe("function");
      state.releaseCalibration?.();
      await expect(status).toHaveText("침대 영점 보정이 완료되었습니다.");
      await expect(button).toBeEnabled();

      state.calibration = "occupied";
      await button.click();
      await expect(status).toHaveText("Bed is occupied");

      state.calibration = "unavailable";
      await button.click();
      await expect(status).toHaveText("Rule worker is unavailable");

      const restPanel = page.locator("[data-dashboard-section=confirmed-rest]");
      for (const [fusionState, copy] of [
        ["empty", "침대 비어 있음"],
        ["unconfirmed_pressure", "카메라 확인 대기"],
        ["sensor_check", "침대 센서 확인 필요"],
      ] as const) {
        backend.send({ type: "bed_status", payload: fusionBed(fusionState) });
        await expect(restPanel.getByText(copy, { exact: true })).toBeVisible();
      }

      const catSummary = structuredClone(summaryOf(demoDashboardData));
      catSummary.behaviors = [
        { id: 5, subject_id: "cat_001", behavior_type: "resting", started_at: "2026-07-15T01:41:00Z", ended_at: null, duration_seconds: null },
        { id: 4, subject_id: "dog_001", behavior_type: "resting", started_at: "2026-07-15T01:00:00Z", ended_at: "2026-07-15T01:41:00Z", duration_seconds: 2460 },
        ...catSummary.behaviors.filter((behavior) => behavior.id !== 4),
      ];
      backend.send({ type: "dashboard_update", payload: catSummary });
      await expect(restPanel.getByText("cat_001", { exact: true })).toBeVisible();

      const offline = structuredClone(catSummary);
      offline.health = { ...offline.health, status: "degraded", camera: "offline" };
      offline.camera = { state: "offline", fps: 0, inference_ms: 0, last_frame_at: null, reason: "camera_unavailable" };
      offline.bed = { ...offline.bed, fusion_state: "unavailable", camera_confirmed: false, current_rest_seconds: 0 };
      offline.behaviors = [];
      offline.anomalies = [];
      backend.send({ type: "dashboard_update", payload: offline });

      await expect(page.getByText("카메라 연결 끊김")).toBeVisible();
      await expect(page.getByText("현재 확인할 경고가 없습니다.")).toBeVisible();
      await expect(page.getByText("아직 기록된 행동이 없습니다.")).toBeVisible();
      await expect(page.getByText("센서와 카메라가 함께 확인하면 여기에 표시됩니다.")).toBeVisible();
      await expect(page.getByRole("row", { name: /왼쪽 1042 ADC 기준 812 변화 230/ })).toBeVisible();
      await expectViewportIntegrity(page, viewport.name);
    });
  }
});
