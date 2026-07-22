import "@testing-library/jest-dom/vitest";

import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { render, screen, waitFor } from "@testing-library/react";
import { renderToString } from "react-dom/server";
import { afterEach, describe, expect, it, vi } from "vitest";

import {
  ClientDashboardEntry,
  ConnectedDashboard,
  Dashboard,
  selectDashboardMode,
} from "../components/dashboard";
import { PetCareClient } from "../lib/api-client";
import { demoDashboardData } from "../lib/demo-data";

const root = resolve(import.meta.dirname, "..");

afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
  window.history.replaceState({}, "", "/");
});

function contrastRatio(foreground: string, background: string) {
  const luminance = (hex: string) => {
    const rgb = hex
      .slice(1)
      .match(/.{2}/g)!
      .map((value) => Number.parseInt(value, 16) / 255)
      .map((value) =>
        value <= 0.03928 ? value / 12.92 : ((value + 0.055) / 1.055) ** 2.4,
      );
    return 0.2126 * rgb[0] + 0.7152 * rgb[1] + 0.0722 * rgb[2];
  };
  const light = Math.max(luminance(foreground), luminance(background));
  const dark = Math.min(luminance(foreground), luminance(background));
  return (light + 0.05) / (dark + 0.05);
}

describe("dashboard demo surface", () => {
  it("renders injected authenticated camera source without fetching", () => {
    const fetchSpy = vi.fn();
    vi.stubGlobal("fetch", fetchSpy);

    render(
      <Dashboard
        data={demoDashboardData}
        mode="connected"
        camera={{
          src: "/api/petcare/cameras/camera-1/stream.mjpeg",
          alt: "실시간 반려동물 카메라",
        }}
      />,
    );

    expect(
      screen.getByRole("img", { name: "실시간 반려동물 카메라" }),
    ).toHaveAttribute(
      "src",
      "/api/petcare/cameras/camera-1/stream.mjpeg",
    );
    expect(fetchSpy).not.toHaveBeenCalled();
  });

  it("renders the operational evidence in the required mobile DOM order", () => {
    const { container } = render(<Dashboard data={demoDashboardData} />);

    expect(
      screen.getByRole("heading", { name: "PetCare 운영 현황", level: 1 }),
    ).toBeInTheDocument();
    expect(screen.getByRole("navigation", { name: "주요 화면" })).toHaveTextContent(
      "현황카메라휴식기록설정",
    );
    expect(screen.getByRole("img", { name: "반려동물 침대와 급식 구역 데모 카메라" })).toHaveAttribute(
      "src",
      "/demo-camera.webp",
    );
    expect(screen.getByRole("button", { name: "침대 영점 재설정" })).toBeDisabled();
    expect(screen.getByText("로컬 연결 모드에서 실행할 수 있습니다.")).toBeInTheDocument();
    expect(screen.getAllByText("휴식 추정").length).toBeGreaterThan(0);
    expect(screen.getByText("야간 침대 이탈")).toBeInTheDocument();
    expect(screen.getAllByText("dog_001").length).toBeGreaterThan(0);
    expect(screen.getAllByText("cat_001").length).toBeGreaterThan(0);
    expect(screen.getByText("640 × 480")).toBeInTheDocument();
    expect(screen.getAllByText("742 g").length).toBeGreaterThan(0);

    const order = [
      "summary",
      "camera",
      "confirmed-rest",
      "warnings",
      "environment-food",
      "timeline",
      "roi",
      "device-health",
    ].map((section) =>
      container.querySelector(`[data-dashboard-section="${section}"]`),
    );
    expect(order.every(Boolean)).toBe(true);
    expect(order.map((node) => [...container.querySelectorAll("[data-dashboard-section]")].indexOf(node!))).toEqual(
      [...order.keys()],
    );
  });

  it("keeps unavailable and calibration failure states explicit", () => {
    const unavailable = structuredClone(demoDashboardData);
    unavailable.camera = {
      state: "offline",
      fps: 0,
      inference_ms: 0,
      last_frame_at: null,
      reason: "camera_unavailable",
    };
    unavailable.bed.sensor_state = "unavailable";
    unavailable.bed.pressure_state = "unavailable";
    unavailable.bed.fusion_state = "unavailable";
    unavailable.bed.channels[0] = {
      channel: "left",
      raw: null,
      baseline: null,
      delta: null,
      polarity: null,
      available: false,
      observed_at: null,
    };
    unavailable.bed.seven_day = {
      status: "insufficient_data",
      today_seconds: 0,
      baseline_seconds: null,
      difference_seconds: null,
      percent_change: null,
      complete_days: 2,
    };
    unavailable.calibration = {
      phase: "error",
      code: "sensor_unavailable",
      channels: ["left"],
      message: "왼쪽 센서 입력을 확인하세요.",
    };

    const { container } = render(<Dashboard data={unavailable} />);

    expect(screen.getAllByText("센서 사용 불가").length).toBeGreaterThan(0);
    expect(screen.getByText("카메라 연결 끊김")).toBeInTheDocument();
    expect(screen.getAllByText("기준 데이터 수집 중 · 2/7일").length).toBeGreaterThan(0);
    expect(screen.getByRole("status")).toHaveTextContent("왼쪽 센서 입력을 확인하세요.");
    expect(screen.queryByText(/^0 ADC$/)).not.toBeInTheDocument();
    expect(container.querySelector(".camera-meta")).toHaveTextContent(
      "FPS사용 불가추론사용 불가",
    );
  });

  it("exposes operational recovery and dependency states", () => {
    const { container } = render(<Dashboard data={demoDashboardData} />);

    expect(screen.getByRole("button", { name: "침대 영점 재설정" })).toBeDisabled();
    expect(container.querySelector('[data-dashboard-section="warnings"]')).toHaveAttribute(
      "aria-live",
      "polite",
    );
    expect(screen.getByText("카메라 처리")).toBeInTheDocument();
    expect(screen.getByText("이벤트 큐")).toBeInTheDocument();
    expect(screen.getByText("백그라운드 워커")).toBeInTheDocument();

    const types = readFileSync(resolve(root, "lib/types.ts"), "utf8");
    for (const phase of ["idle", "submitting", "success", "disabled", "error"]) {
      expect(types).toContain(`\"${phase}\"`);
    }
  });

  it("uses the exact warm-homecare tokens and accessible control contrast", () => {
    const css = readFileSync(resolve(root, "app/globals.css"), "utf8");
    const dashboardCss = css.split("/* Public landing and authentication entry surfaces */", 1)[0];
    const tokens = {
      canvas: "#F8F7F3",
      surface: "#FFFFFF",
      "surface-strong": "#EFEDE5",
      text: "#202923",
      muted: "#5C665F",
      "border-decorative": "#D4D3CA",
      "border-control": "#7E847F",
      primary: "#176A55",
      accent: "#994A24",
      warning: "#925000",
      destructive: "#B42318",
    };

    for (const [name, value] of Object.entries(tokens)) {
      expect(css).toContain(`--${name}: ${value}`);
    }
    for (const adjacent of [tokens.surface, tokens.canvas, tokens["surface-strong"]]) {
      expect(contrastRatio(tokens["border-control"], adjacent)).toBeGreaterThanOrEqual(3);
    }
    expect(css).toContain("grid-template-columns: 172px minmax(0, 1fr)");
    expect(css).toContain("grid-template-columns: repeat(12, minmax(0, 1fr))");
    expect(css).toContain("height: 64px");
    expect(css).toContain("outline: 2px solid var(--primary)");
    expect(css).toContain("@media (prefers-reduced-motion: reduce)");
    expect(dashboardCss).not.toMatch(/gradient|backdrop-filter|text-shadow/i);
  });

  it("keeps auth-plan-protected root Flight-serializable and demo server-only", () => {
    const rootPage = readFileSync(resolve(root, "app/page.tsx"), "utf8");
    const demoPage = readFileSync(resolve(root, "app/demo/page.tsx"), "utf8");
    const remoteDashboard = readFileSync(
      resolve(root, "components/remote-dashboard.tsx"),
      "utf8",
    );
    expect(rootPage).toContain("RemoteDashboard");
    expect(rootPage).toContain("LandingPage");
    expect(rootPage).toContain("ClientDashboardEntry");
    expect(rootPage).not.toContain("<ConnectedDashboard />");
    expect(rootPage).toContain('export const dynamic = "force-dynamic"');
    expect(rootPage).toContain('get("x-petcare-authenticated") === "1"');
    expect(rootPage.indexOf('get("x-petcare-authenticated")')).toBeLessThan(
      rootPage.indexOf("return <ClientDashboardEntry"),
    );
    expect(rootPage).not.toMatch(/createPetCareRemote|client=|media=/);
    expect(remoteDashboard).toContain("createPetCareRemoteClient");
    expect(remoteDashboard).toContain("createPetCareRemoteMedia");
    expect(rootPage).not.toMatch(
      /"use client"|demoDashboardData|window\.|localStorage|sessionStorage/,
    );
    expect(demoPage).toMatch(/return <Dashboard data=\{demoDashboardData\} \/>/);
    expect(demoPage).not.toMatch(
      /fetch|WebSocket|localhost|127\.0\.0\.1|useState|useEffect|petcare-remote/,
    );
  });

  it.each([
    ["/", undefined, "demo"],
    ["/", "localhost", "connected"],
    ["/", "127.0.0.1", "connected"],
    ["/", "LOCALHOST", "demo"],
    ["/", "petcare.example", "demo"],
    ["/", "localhost\u0000.example", "demo"],
    ["/demo", "localhost", "demo"],
    ["/unknown", "localhost", "not_found"],
  ] as const)("selects %s / %s as %s before client construction", (pathname, hostname, mode) => {
    expect(selectDashboardMode(pathname, hostname)).toBe(mode);
  });

  it("constructs the local client only after localhost hydration and never on /demo SSR", async () => {
    const getSummary = vi
      .spyOn(PetCareClient.prototype, "getSummary")
      .mockReturnValue(new Promise(() => undefined));
    vi.spyOn(PetCareClient.prototype, "getZones").mockReturnValue(new Promise(() => undefined));
    vi.spyOn(PetCareClient.prototype, "subscribe").mockReturnValue(vi.fn());

    window.history.replaceState({}, "", "/demo");
    const demo = render(<ClientDashboardEntry fallback={<div>landing</div>} />);
    expect(screen.getByText("landing")).toBeInTheDocument();
    expect(getSummary).not.toHaveBeenCalled();
    demo.unmount();

    window.history.replaceState({}, "", "/");
    expect(renderToString(<ClientDashboardEntry fallback={<div>landing</div>} />)).toContain("landing");
    expect(getSummary).not.toHaveBeenCalled();

    render(<ClientDashboardEntry fallback={<div>landing</div>} />);
    await waitFor(() => expect(getSummary).toHaveBeenCalledTimes(1));
  });

  it("keeps the full connected layout mounted while the initial snapshot is pending", () => {
    vi.spyOn(PetCareClient.prototype, "getSummary").mockReturnValue(new Promise(() => undefined));
    vi.spyOn(PetCareClient.prototype, "getZones").mockReturnValue(new Promise(() => undefined));
    vi.spyOn(PetCareClient.prototype, "subscribe").mockReturnValue(vi.fn());

    const { container } = render(<ConnectedDashboard />);
    expect(container.querySelector(".app-shell")).toHaveAttribute("aria-busy", "true");
    expect(container.querySelectorAll("[data-dashboard-section]")).toHaveLength(8);
    expect(container.querySelector(".zone-pet_bed")).toHaveStyle({
      left: "50%",
      top: "37.5%",
    });
  });

  it("styles every ROI control with a visible 44px boundary and a two-column shell", () => {
    const css = readFileSync(resolve(root, "app/globals.css"), "utf8");
    expect(css).toMatch(/\.zone-table fieldset\s*\{[^}]*grid-template-columns:\s*repeat\(2, minmax\(0, 1fr\)\)/);
    expect(css).toMatch(/\.zone-table fieldset button\s*\{[^}]*min-height:\s*44px[^}]*border:\s*1px solid var\(--border-control\)/);
    expect(css).toMatch(/\.zone-toggle\s*\{[^}]*min-height:\s*44px/);
    expect(css).toMatch(/\.zone-toggle input\[type="checkbox"\]\s*\{[^}]*border:\s*1px solid var\(--border-control\)/);
  });

  it("keeps remote controls keyboard-visible and mobile-safe", () => {
    const css = readFileSync(resolve(root, "app/globals.css"), "utf8");
    expect(css).toContain("input:focus-visible");
    expect(css).toContain(".clip-list video");
    expect(css).toContain("@media (max-width: 600px)");
    expect(css).toContain("@media (prefers-reduced-motion: reduce)");
    expect(css).toMatch(/\.remote-operational \.zone,[\s\S]*display: none/);
  });

  it("keeps /demo a thin shared entry with no network or remote image source", () => {
    const demoPage = readFileSync(resolve(root, "app/demo/page.tsx"), "utf8");
    const dashboard = readFileSync(resolve(root, "components/dashboard.tsx"), "utf8");
    const packageJson = JSON.parse(readFileSync(resolve(root, "package.json"), "utf8"));
    const hosting = readFileSync(resolve(root, ".openai/hosting.json"), "utf8");
    const dependencies = { ...packageJson.dependencies, ...packageJson.devDependencies };

    expect(demoPage).toMatch(/return <Dashboard data=\{demoDashboardData\} \/>/);
    expect(demoPage).not.toMatch(/fetch|WebSocket|localhost|127\.0\.0\.1|useState|useEffect/);
    expect(dashboard).not.toMatch(/fetch\(|new WebSocket|<img[^>]+https?:\/\//);
    expect(hosting.replace(/\s/g, "")).toMatch(
      /^\{"d1":"DB","r2":"CLIPS"\}/,
    );
    expect(Object.keys(dependencies)).not.toEqual(
      expect.arrayContaining([
        "lucide-react",
        "recharts",
        "chart.js",
        "framer-motion",
        "motion",
      ]),
    );

    const fetchSpy = vi.fn();
    const websocketSpy = vi.fn();
    vi.stubGlobal("fetch", fetchSpy);
    vi.stubGlobal("WebSocket", websocketSpy);
    const { container } = render(<Dashboard data={demoDashboardData} mode="demo" />);
    expect(fetchSpy).not.toHaveBeenCalled();
    expect(websocketSpy).not.toHaveBeenCalled();
    expect(container.querySelector(".app-shell")).toHaveAttribute(
      "data-dashboard-mode",
      "demo",
    );
    expect(container.querySelector("img")).toHaveAttribute("src", "/demo-camera.webp");
  });

  it("keeps the backend summary key order and every calibration state explicit", () => {
    expect(Object.keys(demoDashboardData).slice(0, 8)).toEqual([
      "generated_at",
      "health",
      "devices",
      "latest_sensors",
      "camera",
      "bed",
      "behaviors",
      "anomalies",
    ]);

    for (const phase of ["idle", "submitting", "success", "disabled", "error"] as const) {
      const state = structuredClone(demoDashboardData);
      state.calibration.phase = phase;
      const { container, unmount } = render(<Dashboard data={state} />);
      expect(container.querySelector(".calibration-control")).toHaveAttribute(
        "data-calibration-phase",
        phase,
      );
      unmount();
    }
  });

  it("ships a nonblank local WebP camera frame", () => {
    const camera = readFileSync(resolve(root, "public/demo-camera.webp"));
    expect(camera.byteLength).toBeGreaterThan(1_000);
    expect(camera.subarray(0, 4).toString("ascii")).toBe("RIFF");
    expect(camera.subarray(8, 12).toString("ascii")).toBe("WEBP");
  });
});
