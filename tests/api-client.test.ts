import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { act, renderHook, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import {
  API_BASE_URL,
  PetCareClient,
  VIDEO_FEED_URL,
  WEBSOCKET_URL,
  parseDashboardMessage,
} from "../lib/api-client";
import { demoDashboardData } from "../lib/demo-data";
import { useDashboard } from "../lib/use-dashboard";

const root = resolve(import.meta.dirname, "..");
const { zones, calibration: _calibration, ...summary } = demoDashboardData;

function json(body: unknown, status = 200) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

afterEach(() => {
  vi.useRealTimers();
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

describe("PetCareClient", () => {
  it("uses only the fixed loopback contract and keeps /demo network-free", () => {
    expect(API_BASE_URL).toBe("http://127.0.0.1:8000");
    expect(VIDEO_FEED_URL).toBe("http://127.0.0.1:8000/api/video_feed");
    expect(WEBSOCKET_URL).toBe("ws://127.0.0.1:8000/ws/dashboard");

    const source = readFileSync(resolve(root, "lib/api-client.ts"), "utf8");
    const demoPage = readFileSync(resolve(root, "app/demo/page.tsx"), "utf8");
    expect(source).not.toMatch(/process\.env|location\.search|URLSearchParams|NEXT_PUBLIC/);
    expect(demoPage).not.toMatch(
      /PetCareClient|api-client|useDashboard|fetch|WebSocket|localhost|127\.0\.0\.1/,
    );
  });

  it("strictly validates summary and zones without coercing extra fields", async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(json(summary))
      .mockResolvedValueOnce(json(zones));
    vi.stubGlobal("fetch", fetchMock);

    const client = new PetCareClient();
    await expect(client.getSummary()).resolves.toEqual(summary);
    await expect(client.getZones()).resolves.toEqual(zones);
    expect(fetchMock).toHaveBeenNthCalledWith(
      1,
      "http://127.0.0.1:8000/api/dashboard/summary",
      expect.objectContaining({ method: "GET" }),
    );

    fetchMock.mockResolvedValueOnce(json({ ...summary, extra: true }));
    await expect(client.getSummary()).rejects.toThrow("Invalid dashboard summary");
  });

  it("rejects invalid behavior, seven-day, and calibration time relations", async () => {
    const endedBeforeStart = structuredClone(summary);
    endedBeforeStart.behaviors[0].ended_at = "2026-07-15T00:00:00Z";
    endedBeforeStart.behaviors[0].duration_seconds = 1;

    const invalidComparison = structuredClone(summary);
    invalidComparison.bed.seven_day.difference_seconds = 1;

    const invalidCalibration = {
      device_id: "petzone-01",
      calibrated_at: "2026-07-20T12:01:00Z",
      window_start: "2026-07-20T12:00:00Z",
      window_end: "2026-07-20T12:00:59Z",
      channels: ["left", "center", "right"].map((channel) => ({
        channel,
        sample_count: 60,
        baseline: 100,
        polarity: 1,
      })),
    };
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(json(endedBeforeStart))
      .mockResolvedValueOnce(json(invalidComparison))
      .mockResolvedValueOnce(json(invalidCalibration));
    vi.stubGlobal("fetch", fetchMock);

    const client = new PetCareClient();
    await expect(client.getSummary()).rejects.toThrow("Invalid dashboard summary");
    await expect(client.getSummary()).rejects.toThrow("Invalid dashboard summary");
    await expect(client.calibrateBed()).rejects.toThrow("Invalid calibration success");
  });

  it("rejects duplicate or non-canonical summary lists", async () => {
    const reversedDevices = structuredClone(summary);
    reversedDevices.devices.reverse();
    const duplicateSensor = structuredClone(summary);
    duplicateSensor.latest_sensors.push(structuredClone(duplicateSensor.latest_sensors[0]));
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(json(reversedDevices))
      .mockResolvedValueOnce(json(duplicateSensor));
    vi.stubGlobal("fetch", fetchMock);

    const client = new PetCareClient();
    await expect(client.getSummary()).rejects.toThrow("Invalid dashboard summary");
    await expect(client.getSummary()).rejects.toThrow("Invalid dashboard summary");
  });

  it("returns the exact calibration success, 409, and 503 unions", async () => {
    const success = {
      device_id: "petzone-01",
      calibrated_at: "2026-07-20T12:01:00Z",
      window_start: "2026-07-20T12:00:00Z",
      window_end: "2026-07-20T12:01:00Z",
      channels: ["left", "center", "right"].map((channel) => ({
        channel,
        sample_count: 60,
        baseline: 100,
        polarity: 1,
      })),
    };
    const occupied = { code: "occupied", message: "Bed is occupied", channels: [] };
    const unavailable = {
      code: "queue_unavailable",
      message: "Rule queue is unavailable",
    };
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(json(success))
      .mockResolvedValueOnce(json(occupied, 409))
      .mockResolvedValueOnce(json(unavailable, 503));
    vi.stubGlobal("fetch", fetchMock);

    const client = new PetCareClient();
    await expect(client.calibrateBed()).resolves.toEqual({ ok: true, value: success });
    await expect(client.calibrateBed()).resolves.toEqual({
      ok: false,
      status: 409,
      error: occupied,
    });
    await expect(client.calibrateBed()).resolves.toEqual({
      ok: false,
      status: 503,
      error: unavailable,
    });
    expect(fetchMock).toHaveBeenNthCalledWith(
      1,
      "http://127.0.0.1:8000/api/bed/calibration",
      expect.objectContaining({
        method: "POST",
        body: JSON.stringify({ device_id: "petzone-01" }),
      }),
    );
  });

  it("updates only a named zone with an exact PUT body", async () => {
    const updated = { ...zones[0], x1: 20 };
    const fetchMock = vi.fn().mockResolvedValue(json(updated));
    vi.stubGlobal("fetch", fetchMock);

    await expect(
      new PetCareClient().updateZone("food_bowl", {
        x1: 20,
        y1: 260,
        x2: 260,
        y2: 470,
        enabled: true,
      }),
    ).resolves.toEqual(updated);
    expect(fetchMock).toHaveBeenCalledWith(
      "http://127.0.0.1:8000/api/zones/food_bowl",
      expect.objectContaining({
        method: "PUT",
        body: JSON.stringify({ x1: 20, y1: 260, x2: 260, y2: 470, enabled: true }),
      }),
    );

    fetchMock.mockResolvedValueOnce(json({ ...updated, zone_name: "pet_bed" }));
    await expect(
      new PetCareClient().updateZone("food_bowl", {
        x1: 20,
        y1: 260,
        x2: 260,
        y2: 470,
        enabled: true,
      }),
    ).rejects.toThrow("Invalid zone response");
  });

  it("accepts only the three closed WebSocket message variants", () => {
    expect(
      parseDashboardMessage({ type: "dashboard_update", payload: summary }),
    ).toEqual({ type: "dashboard_update", payload: summary });
    expect(
      parseDashboardMessage({ type: "bed_status", payload: summary.bed }),
    ).toEqual({ type: "bed_status", payload: summary.bed });
    expect(
      parseDashboardMessage({ type: "anomaly_alert", payload: summary.anomalies[0] }),
    ).toEqual({ type: "anomaly_alert", payload: summary.anomalies[0] });
    expect(() =>
      parseDashboardMessage({ type: "anomaly_alert", payload: summary.anomalies[0], extra: true }),
    ).toThrow("Invalid dashboard message");
  });

  it("reconnects WebSocket on one fixed delay and stops cleanly", async () => {
    vi.useFakeTimers();
    class FakeWebSocket {
      static instances: FakeWebSocket[] = [];
      onclose: (() => void) | null = null;
      onerror: (() => void) | null = null;
      onmessage: ((event: { data: string }) => void) | null = null;
      onopen: (() => void) | null = null;

      constructor(readonly url: string) {
        FakeWebSocket.instances.push(this);
      }

      close() {}
    }
    vi.stubGlobal("WebSocket", FakeWebSocket);

    const stop = new PetCareClient().subscribe(vi.fn());
    expect(FakeWebSocket.instances[0].url).toBe(WEBSOCKET_URL);
    FakeWebSocket.instances[0].onclose?.();
    await vi.advanceTimersByTimeAsync(999);
    expect(FakeWebSocket.instances).toHaveLength(1);
    await vi.advanceTimersByTimeAsync(1);
    expect(FakeWebSocket.instances).toHaveLength(2);
    stop();
    FakeWebSocket.instances[1].onclose?.();
    await vi.advanceTimersByTimeAsync(1_000);
    expect(FakeWebSocket.instances).toHaveLength(2);
  });
});

describe("useDashboard", () => {
  it("retries a failed initial snapshot on the fixed delay and retains queued updates", async () => {
    vi.useFakeTimers();
    const updated = structuredClone(summary);
    updated.generated_at = "2026-07-15T01:43:00Z";
    vi.spyOn(PetCareClient.prototype, "getSummary")
      .mockRejectedValueOnce(new Error("offline"))
      .mockResolvedValue(summary);
    vi.spyOn(PetCareClient.prototype, "getZones").mockResolvedValue(zones);
    vi.spyOn(PetCareClient.prototype, "subscribe").mockImplementation((onMessage) => {
      onMessage({ type: "dashboard_update", payload: updated });
      return vi.fn();
    });

    const { result } = renderHook(() => useDashboard());
    await act(async () => {
      await Promise.resolve();
      await Promise.resolve();
    });
    expect(result.current.error).toBe("offline");
    expect(result.current.data).toBeNull();

    await act(async () => {
      await vi.advanceTimersByTimeAsync(1_000);
      await Promise.resolve();
    });
    expect(result.current.data?.generated_at).toBe(updated.generated_at);
  });

  it("cancels the initial retry timer when the connected surface unmounts", async () => {
    vi.useFakeTimers();
    const getSummary = vi
      .spyOn(PetCareClient.prototype, "getSummary")
      .mockRejectedValue(new Error("offline"));
    vi.spyOn(PetCareClient.prototype, "getZones").mockResolvedValue(zones);
    const stop = vi.fn();
    vi.spyOn(PetCareClient.prototype, "subscribe").mockReturnValue(stop);

    const { unmount } = renderHook(() => useDashboard());
    await act(async () => {
      await Promise.resolve();
      await Promise.resolve();
    });
    unmount();
    await vi.advanceTimersByTimeAsync(1_000);

    expect(getSummary).toHaveBeenCalledTimes(1);
    expect(stop).toHaveBeenCalledTimes(1);
  });

  it("aborts calibration and ROI requests when the connected surface unmounts", async () => {
    vi.spyOn(PetCareClient.prototype, "getSummary").mockResolvedValue(summary);
    vi.spyOn(PetCareClient.prototype, "getZones").mockResolvedValue(zones);
    vi.spyOn(PetCareClient.prototype, "subscribe").mockReturnValue(vi.fn());
    let calibrationSignal: AbortSignal | undefined;
    let zoneSignal: AbortSignal | undefined;
    vi.spyOn(PetCareClient.prototype, "calibrateBed").mockImplementation((signal) => {
      calibrationSignal = signal;
      return new Promise((_resolve, reject) =>
        signal?.addEventListener("abort", () => reject(new DOMException("Aborted", "AbortError"))),
      );
    });
    vi.spyOn(PetCareClient.prototype, "updateZone").mockImplementation((_name, _input, signal) => {
      zoneSignal = signal;
      return new Promise((_resolve, reject) =>
        signal?.addEventListener("abort", () => reject(new DOMException("Aborted", "AbortError"))),
      );
    });

    const { result, unmount } = renderHook(() => useDashboard());
    await waitFor(() => expect(result.current.data).not.toBeNull());
    let calibration!: Promise<void>;
    let update!: Promise<void>;
    act(() => {
      calibration = result.current.calibrate();
      update = result.current.updateZone("food_bowl", {
        x1: zones[0].x1,
        y1: zones[0].y1,
        x2: zones[0].x2,
        y2: zones[0].y2,
        enabled: zones[0].enabled,
      });
    });
    unmount();

    expect(calibrationSignal?.aborted).toBe(true);
    expect(zoneSignal?.aborted).toBe(true);
    await expect(calibration).resolves.toBeUndefined();
    await expect(update).rejects.toMatchObject({ name: "AbortError" });
  });
});
