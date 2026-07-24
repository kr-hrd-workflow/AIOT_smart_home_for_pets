import { existsSync, readFileSync, statSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

import AxeBuilder from "@axe-core/playwright";
import {
  expect,
  test,
  type Page,
  type Request,
} from "@playwright/test";

import {
  validatePlaywrightRuntimeRecord,
  type PlaywrightRuntimeExpectation,
  type PlaywrightRuntimeRecord,
} from "../playwright.config";
import { demoDashboardData } from "../lib/demo-data";
import type {
  DashboardData,
  DashboardSummary,
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

interface ConnectedState {
  summary: DashboardSummary;
  cameraAvailable: boolean;
  statusRequests: number;
}

async function mockConnectedBackend(page: Page, state: ConnectedState) {
  const requests: string[] = [];
  await page.route("**/api/petcare/**", async (route) => {
    const request = route.request();
    const url = new URL(request.url());
    requests.push(`${request.method()} ${url.pathname}`);

    if (
      url.pathname === "/api/petcare/status" &&
      request.method() === "GET"
    ) {
      state.statusRequests += 1;
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        json: {
          home: { id: "home-1", state: "ready" },
          agent: {
            id: "agent-1",
            state: "online",
            last_seen_at: state.summary.generated_at,
          },
          camera: state.cameraAvailable
            ? {
                id: "camera-1",
                state: "online",
                last_seen_at: state.summary.generated_at,
              }
            : null,
          dashboard: state.summary,
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

  return {
    requests,
    connected: () => state.statusRequests > 0,
  };
}

async function gotoConnected(page: Page, state?: Partial<ConnectedState>) {
  const connectedState: ConnectedState = {
    summary: structuredClone(summaryOf(demoDashboardData)),
    cameraAvailable: true,
    statusRequests: 0,
    ...state,
  };
  const backend = await mockConnectedBackend(page, connectedState);
  const response = await page.goto("/dashboard", {
    waitUntil: "domcontentloaded",
  });
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

test.describe("animated public landing", () => {
  test.use({ reducedMotion: "no-preference" });

  test.beforeEach(async ({}, testInfo) => {
    test.skip(testInfo.project.name !== "demo-dev", "Animated landing QA runs once against dev");
  });

  test("scrubs the accepted arrival video and copy together", async ({ page }) => {
    const response = await page.goto("/", { waitUntil: "domcontentloaded" });
    expect(response?.ok()).toBe(true);

    const landing = page.locator("#petcare-story");
    const scene = landing.locator(".scroll-world-scene");
    const video = scene.locator("video");
    await expect(landing).toHaveAttribute("data-scroll-world-active", "true");
    await page.evaluate(() => {
      document.documentElement.style.scrollBehavior = "auto";
    });
    await expect(landing).toHaveAttribute("data-landing-scene", "hero");
    await expect(video).toHaveCount(1);
    await expect(video).toHaveAttribute("src", /scene-01-arrival\.mp4$/);
    await expect.poll(() => video.evaluate((element) => element.readyState)).toBeGreaterThanOrEqual(1);
    await expect(scene).toHaveClass(/has-video-frame/);

    const scrollLandingTo = async (progress: number) => {
      await page.evaluate((value) => {
        const root = document.getElementById("petcare-story");
        if (!root) throw new Error("Landing root is missing");
        const start = root.getBoundingClientRect().top + window.scrollY;
        const range = Math.max(1, root.scrollHeight - window.innerHeight);
        window.scrollTo(0, start + range * value);
      }, progress);
      await page.evaluate(() => new Promise<void>((resolve) => {
        requestAnimationFrame(() => requestAnimationFrame(() => resolve()));
      }));
    };

    const topTime = await video.evaluate((element) => element.currentTime);
    await scrollLandingTo(0.5);
    await expect(landing).toHaveAttribute("data-landing-scene", "events");
    await expect.poll(() => video.evaluate((element) => element.currentTime)).toBeGreaterThan(topTime);
    const middleTime = await video.evaluate((element) => element.currentTime);
    await expect(scene).toHaveClass(/has-video-frame/);

    await scrollLandingTo(0.9);
    await expect(landing).toHaveAttribute("data-landing-scene", "final");
    await expect.poll(() => video.evaluate((element) => element.currentTime)).toBeGreaterThan(middleTime);
    await expect(scene).toHaveClass(/has-video-frame/);
  });
});

test.describe("remote dashboard states", () => {
  test.beforeEach(async ({}, testInfo) => {
    test.skip(
      testInfo.project.name !== "connected",
      "Remote dashboard QA uses same-origin PetCare mocks",
    );
  });

  for (const viewport of viewports) {
    test(`renders the remote polling dashboard at ${viewport.name}`, async ({
      page,
    }) => {
      await page.setViewportSize(viewport);
      const { backend, state } = await gotoConnected(page);
      await expect(page.getByText("필수 연결 완료")).toBeVisible();
      await expect(
        page.getByRole("list", { name: "우리 집 연결" }),
      ).toBeVisible();
      await expect(
        page.getByText("dog_001", { exact: true }).first(),
      ).toBeVisible();
      await expect(
        page.getByRole("img", { name: "실시간 반려동물 카메라" }),
      ).toBeVisible();
      await expect(
        page.locator(".remote-operational .roi-section"),
      ).not.toBeVisible();
      await expect(page.getByRole("spinbutton")).toHaveCount(0);

      const previousRequests = state.statusRequests;
      const offline = structuredClone(state.summary);
      offline.health = {
        ...offline.health,
        status: "degraded",
        camera: "offline",
      };
      offline.camera = {
        state: "offline",
        fps: 0,
        inference_ms: 0,
        last_frame_at: null,
        reason: "camera_unavailable",
      };
      offline.bed = {
        ...offline.bed,
        fusion_state: "unavailable",
        camera_confirmed: false,
        current_rest_seconds: 0,
      };
      offline.behaviors = [];
      offline.anomalies = [];
      state.summary = offline;
      state.cameraAvailable = false;

      await expect
        .poll(() => state.statusRequests, { timeout: 5_000 })
        .toBeGreaterThan(previousRequests);
      await expect(
        page.getByRole("img", { name: "실시간 반려동물 카메라" }),
      ).toHaveCount(0);
      await expect(page.getByText("카메라 연결 끊김")).toBeVisible();
      await expect(
        page.getByText("현재 확인할 경고가 없습니다."),
      ).toBeVisible();
      await expect(
        page.getByText("아직 기록된 행동이 없습니다."),
      ).toBeVisible();
      expect(backend.requests).toContain("GET /api/petcare/status");
      await expectViewportIntegrity(page, viewport.name);
    });
  }
});
