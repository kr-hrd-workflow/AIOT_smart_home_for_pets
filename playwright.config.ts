import { createHash } from "node:crypto";
import { readFileSync, realpathSync, statSync } from "node:fs";
import { dirname, isAbsolute, resolve } from "node:path";
import { fileURLToPath } from "node:url";

import { chromium, defineConfig } from "@playwright/test";

const dashboardRoot = dirname(fileURLToPath(import.meta.url));
const repositoryRoot = resolve(dashboardRoot, "..");
const platformManifestPath = resolve(repositoryRoot, "tools/platform-manifest.json");
const runtimeManifestPath = resolve(repositoryRoot, ".runtime/playwright.json");
const toolchainManifestPath = resolve(repositoryRoot, ".runtime/toolchain.json");

type E2ETarget = "demo-dev" | "demo-production" | "connected";

const targetSettings: Record<E2ETarget, { baseURL: string; serverMode: "dev" | "preview" }> = {
  "demo-dev": { baseURL: "http://127.0.0.1:4173", serverMode: "dev" },
  "demo-production": { baseURL: "http://127.0.0.1:4174", serverMode: "preview" },
  connected: { baseURL: "http://127.0.0.1:4175", serverMode: "dev" },
};

const expectedE2EScripts = {
  "test:e2e:demo:dev": "PETCARE_E2E_TARGET=demo-dev playwright test --project=demo-dev",
  "test:e2e:demo:production": "npm run build && PETCARE_E2E_TARGET=demo-production playwright test --project=demo-production",
  "test:e2e:connected": "PETCARE_E2E_TARGET=connected playwright test --project=connected",
};

export interface PlaywrightRuntimeRecord {
  manifest_sha256: string;
  package_version: string;
  browser_revision: string;
  executable_path: string;
  executable_sha256: string;
}

export interface PlaywrightRuntimeExpectation {
  manifestSha256: string;
  packageVersion: string;
  browserRevision: string;
  executablePath: string;
  executableSha256: string;
}

function digest(value: string | Buffer): string {
  return createHash("sha256").update(value).digest("hex").toUpperCase();
}

function samePath(left: string, right: string): boolean {
  const normalizedLeft = resolve(left);
  const normalizedRight = resolve(right);
  return process.platform === "win32"
    ? normalizedLeft.toLowerCase() === normalizedRight.toLowerCase()
    : normalizedLeft === normalizedRight;
}

function runtimeRecord(value: unknown): PlaywrightRuntimeRecord {
  if (!value || typeof value !== "object") {
    throw new Error("Invalid Playwright runtime manifest");
  }
  const record = value as Record<string, unknown>;
  for (const field of [
    "manifest_sha256",
    "package_version",
    "browser_revision",
    "executable_path",
    "executable_sha256",
  ] as const) {
    if (typeof record[field] !== "string" || record[field].length === 0) {
      throw new Error(`Missing Playwright runtime field: ${field}`);
    }
  }
  return record as unknown as PlaywrightRuntimeRecord;
}

export function validatePlaywrightRuntimeRecord(
  value: unknown,
  expected: PlaywrightRuntimeExpectation,
): PlaywrightRuntimeRecord {
  const record = runtimeRecord(value);
  if (!/^[A-Fa-f0-9]{64}$/.test(record.manifest_sha256)) {
    throw new Error("Invalid Playwright manifest SHA-256");
  }
  if (!/^[A-Fa-f0-9]{64}$/.test(record.executable_sha256)) {
    throw new Error("Invalid Chromium executable SHA-256");
  }
  if (record.manifest_sha256.toUpperCase() !== expected.manifestSha256.toUpperCase()) {
    throw new Error("Playwright central manifest hash mismatch");
  }
  if (record.package_version !== expected.packageVersion) {
    throw new Error("Playwright package version mismatch");
  }
  if (record.browser_revision !== expected.browserRevision) {
    throw new Error("Playwright Chromium revision mismatch");
  }
  if (!isAbsolute(record.executable_path)) {
    throw new Error("Playwright executable path must be absolute");
  }
  if (!samePath(record.executable_path, expected.executablePath)) {
    throw new Error("Playwright package executable path mismatch");
  }
  if (record.executable_sha256.toUpperCase() !== expected.executableSha256.toUpperCase()) {
    throw new Error("Playwright executable hash mismatch");
  }
  return record;
}

function readJson(path: string): unknown {
  try {
    return JSON.parse(readFileSync(path, "utf8")) as unknown;
  } catch (error) {
    throw new Error(`Cannot read ${path}: ${error instanceof Error ? error.message : String(error)}`);
  }
}

function loadPlaywrightRuntime(): PlaywrightRuntimeRecord {
  const platformBytes = readFileSync(platformManifestPath);
  const platform = readJson(platformManifestPath) as {
    managed_exact?: {
      test_dependencies?: Record<string, string>;
      chromium?: { version?: string };
    };
  };
  const pinnedVersion = platform.managed_exact?.test_dependencies?.["@playwright/test"];
  const pinnedAxeVersion = platform.managed_exact?.test_dependencies?.["@axe-core/playwright"];
  if (
    pinnedVersion !== "1.61.1" ||
    pinnedAxeVersion !== "4.12.1" ||
    platform.managed_exact?.chromium?.version !== pinnedVersion
  ) {
    throw new Error("Central manifest must pin Playwright 1.61.1 and axe 4.12.1");
  }

  const dashboardPackage = readJson(resolve(dashboardRoot, "package.json")) as {
    devDependencies?: Record<string, string>;
    scripts?: Record<string, string>;
  };
  const dashboardLock = readJson(resolve(dashboardRoot, "package-lock.json")) as {
    packages?: Record<string, { version?: string; devDependencies?: Record<string, string> }>;
  };
  if (
    dashboardPackage.devDependencies?.["@playwright/test"] !== pinnedVersion ||
    dashboardPackage.devDependencies?.["@axe-core/playwright"] !== pinnedAxeVersion ||
    dashboardLock.packages?.[""]?.devDependencies?.["@playwright/test"] !== pinnedVersion ||
    dashboardLock.packages?.[""]?.devDependencies?.["@axe-core/playwright"] !== pinnedAxeVersion ||
    dashboardLock.packages?.["node_modules/@playwright/test"]?.version !== pinnedVersion ||
    dashboardLock.packages?.["node_modules/@axe-core/playwright"]?.version !== pinnedAxeVersion
  ) {
    throw new Error("Dashboard package and lock must carry the exact Playwright and axe pins");
  }
  for (const [name, script] of Object.entries(expectedE2EScripts)) {
    if (dashboardPackage.scripts?.[name] !== script) {
      throw new Error(`Dashboard package must bind ${name} to its exact server lifecycle`);
    }
  }

  const installedPackage = readJson(
    resolve(dashboardRoot, "node_modules/@playwright/test/package.json"),
  ) as { version?: string };
  const installedAxePackage = readJson(
    resolve(dashboardRoot, "node_modules/@axe-core/playwright/package.json"),
  ) as { version?: string };
  if (installedPackage.version !== pinnedVersion || installedAxePackage.version !== pinnedAxeVersion) {
    throw new Error("Installed Playwright or axe package does not match the central manifest");
  }

  const browsers = readJson(resolve(dashboardRoot, "node_modules/playwright-core/browsers.json")) as {
    browsers?: Array<{ name?: string; revision?: string }>;
  };
  const browserRevision = browsers.browsers?.find((entry) => entry.name === "chromium")?.revision;
  if (!browserRevision) throw new Error("Installed Playwright Chromium revision is missing");

  const browserRoot = process.env.PLAYWRIGHT_BROWSERS_PATH;
  if (!browserRoot || !samePath(resolve(process.cwd(), browserRoot), resolve(repositoryRoot, ".runtime/ms-playwright"))) {
    throw new Error("PLAYWRIGHT_BROWSERS_PATH must resolve to repository .runtime/ms-playwright");
  }

  const packageExecutablePath = chromium.executablePath();
  let executableSha256: string;
  try {
    if (!statSync(packageExecutablePath).isFile()) throw new Error("not a regular file");
    executableSha256 = digest(readFileSync(packageExecutablePath));
  } catch (error) {
    throw new Error(`Chromium executable is unavailable: ${error instanceof Error ? error.message : String(error)}`);
  }

  const record = validatePlaywrightRuntimeRecord(readJson(runtimeManifestPath), {
    manifestSha256: digest(platformBytes),
    packageVersion: pinnedVersion,
    browserRevision,
    executablePath: realpathSync.native(packageExecutablePath),
    executableSha256,
  });
  return { ...record, executable_path: realpathSync.native(record.executable_path) };
}

function loadTarget(): E2ETarget {
  const target = process.env.PETCARE_E2E_TARGET;
  if (target !== "demo-dev" && target !== "demo-production" && target !== "connected") {
    throw new Error("PETCARE_E2E_TARGET must be demo-dev, demo-production, or connected");
  }
  return target;
}

function serverCommand(serverMode: "dev" | "preview", manifestSha256: string): string {
  const toolchain = readJson(toolchainManifestPath) as {
    manifest_sha256?: string;
    paths?: { node_path?: string };
  };
  const nodePath = toolchain.paths?.node_path;
  const vinextCli = resolve(dashboardRoot, "node_modules/vinext/dist/cli.js");
  const viteCli = resolve(dashboardRoot, "node_modules/vite/bin/vite.js");
  if (toolchain.manifest_sha256?.toUpperCase() !== manifestSha256.toUpperCase()) {
    throw new Error("Playwright and toolchain runtime manifests must share the central manifest hash");
  }
  const regularFile = (label: string, path: string | undefined) => {
    if (!path || !isAbsolute(path) || path.includes('"')) {
      throw new Error(`${label} server path must be an absolute quote-safe path`);
    }
    if (!statSync(path).isFile()) throw new Error(`${label} server path must be a regular file`);
    return realpathSync.native(path);
  };
  const nodeExecutable = regularFile("Node", nodePath);
  const serverExecutable = regularFile(serverMode === "preview" ? "Vite" : "vinext", serverMode === "preview" ? viteCli : vinextCli);
  return `"${nodeExecutable}" "${serverExecutable}" ${serverMode}`;
}

const runtime = loadPlaywrightRuntime();
const target = loadTarget();
const targetSetting = targetSettings[target];
const baseURL = `${targetSetting.baseURL}/`;
const port = new URL(baseURL).port;
const serverArgs = targetSetting.serverMode === "preview" ? `--host 127.0.0.1 --port ${port}` : `-H 127.0.0.1 -p ${port}`;
const use = {
  baseURL,
  browserName: "chromium" as const,
  colorScheme: "light" as const,
  locale: "ko-KR",
  timezoneId: "Asia/Seoul",
  reducedMotion: "reduce" as const,
  serviceWorkers: "block" as const,
  screenshot: "off" as const,
  trace: "retain-on-failure" as const,
  launchOptions: { executablePath: runtime.executable_path },
};

export default defineConfig({
  testDir: "./e2e",
  outputDir: resolve(repositoryRoot, ".runtime/playwright-results"),
  forbidOnly: true,
  fullyParallel: false,
  workers: 1,
  retries: 0,
  timeout: 45_000,
  expect: { timeout: 8_000 },
  reporter: [["line"]],
  webServer: {
    command: `${serverCommand(targetSetting.serverMode, runtime.manifest_sha256)} ${serverArgs}`,
    cwd: dashboardRoot,
    url: baseURL,
    reuseExistingServer: false,
    timeout: 120_000,
    stdout: "pipe",
    stderr: "pipe",
  },
  projects: [{
    name: target,
    testIgnore: target === "connected" ? undefined : /roi\.spec\.ts$/,
    use,
  }],
});
