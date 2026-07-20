import { afterEach, describe, expect, it, vi } from "vitest";

import {
  createPetCareAccountClient,
  createPetCareRemoteClient,
  createPetCareRemoteMedia,
} from "../lib/petcare-remote";
import { demoDashboardData } from "../lib/demo-data";

afterEach(() => vi.unstubAllGlobals());

describe("createPetCareRemote", () => {
  it("uses same-origin BFF routes and cookies", async () => {
    const clip = {
      id: "clip-1",
      camera_id: "camera-1",
      event_types: ["resting"],
      started_at: "2026-07-20T01:00:00Z",
      ended_at: "2026-07-20T01:00:30Z",
      expires_at: "2026-07-27T01:00:00Z",
    };
    const fetchMock = vi
      .fn()
      .mockResolvedValue(
        new Response(JSON.stringify({ clips: [clip] }), { status: 200 }),
      );
    vi.stubGlobal("fetch", fetchMock);

    const client = createPetCareRemoteClient();
    const media = createPetCareRemoteMedia();
    await expect(client.getClips()).resolves.toEqual([clip]);

    expect(fetchMock).toHaveBeenCalledWith("/api/petcare/clips", {
      credentials: "same-origin",
      headers: { accept: "application/json" },
    });
    expect(media.videoFeedUrl("camera/one")).toBe(
      "/api/petcare/cameras/camera%2Fone/stream.mjpeg",
    );
    expect(media.clipUrl("clip/one")).toBe(
      "/api/petcare/clips/clip%2Fone.mp4",
    );
  });

  it("uses exact same-origin mutation routes", async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(
        new Response(
          JSON.stringify({
            code: "enrollment-code",
            expiresAt: "2026-07-20T01:10:00Z",
          }),
          { status: 201 },
        ),
      )
      .mockResolvedValueOnce(new Response(null, { status: 204 }));
    vi.stubGlobal("fetch", fetchMock);
    const client = createPetCareRemoteClient();

    await client.enroll();
    await client.deleteClip("clip/one");

    expect(fetchMock).toHaveBeenNthCalledWith(1, "/api/petcare/enrollment", {
      credentials: "same-origin",
      headers: { accept: "application/json" },
      method: "POST",
    });
    expect(fetchMock).toHaveBeenNthCalledWith(
      2,
      "/api/petcare/clips/clip%2Fone",
      {
        credentials: "same-origin",
        headers: { accept: "application/json" },
        method: "DELETE",
      },
    );
  });

  it("maps BFF agent_offline to structured error", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        new Response(
          JSON.stringify({
            code: "agent_offline",
            agent_id: "agent-1",
            camera_id: "camera-1",
            last_seen_at: "2026-07-20T01:00:00Z",
          }),
          { status: 503 },
        ),
      ),
    );

    await expect(
      createPetCareRemoteClient().getStatus(),
    ).rejects.toMatchObject({
      status: 503,
      offline: {
        code: "agent_offline",
        agent_id: "agent-1",
        camera_id: "camera-1",
      },
    });
  });

  it("rejects malformed status, clips, and enrollment success bodies", async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(
        new Response(JSON.stringify({ home: null }), { status: 200 }),
      )
      .mockResolvedValueOnce(
        new Response(JSON.stringify({ clips: [{}] }), { status: 200 }),
      )
      .mockResolvedValueOnce(
        new Response(JSON.stringify({ code: "enrollment-code" }), {
          status: 201,
        }),
      );
    vi.stubGlobal("fetch", fetchMock);
    const client = createPetCareRemoteClient();

    await expect(client.getStatus()).rejects.toMatchObject({ status: 200 });
    await expect(client.getClips()).rejects.toMatchObject({ status: 200 });
    await expect(client.enroll()).rejects.toMatchObject({ status: 201 });
  });

  it("rejects otherwise valid bodies returned with the wrong success status", async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(
        new Response(
          JSON.stringify({
            home: { id: "home-1", state: "needs_enrollment" },
            agent: null,
            camera: null,
            dashboard: null,
          }),
          { status: 201 },
        ),
      )
      .mockResolvedValueOnce(
        new Response(JSON.stringify({ clips: [] }), { status: 201 }),
      )
      .mockResolvedValueOnce(
        new Response(
          JSON.stringify({
            code: "enrollment-code",
            expiresAt: "2026-07-20T01:10:00Z",
          }),
          { status: 200 },
        ),
      )
      .mockResolvedValueOnce(
        new Response(JSON.stringify({ status: "deleted" }), { status: 200 }),
      );
    vi.stubGlobal("fetch", fetchMock);
    const client = createPetCareRemoteClient();

    await expect(client.getStatus()).rejects.toMatchObject({ status: 201 });
    await expect(client.getClips()).rejects.toMatchObject({ status: 201 });
    await expect(client.enroll()).rejects.toMatchObject({ status: 200 });
    await expect(client.deleteClip("clip-1")).rejects.toMatchObject({
      status: 200,
    });
  });

  it("does not trust a malformed agent_offline body", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        new Response(
          JSON.stringify({
            code: "agent_offline",
            agent_id: 7,
            camera_id: "camera-1",
            last_seen_at: null,
          }),
          { status: 503 },
        ),
      ),
    );

    await expect(
      createPetCareRemoteClient().getStatus(),
    ).rejects.toMatchObject({ status: 503, offline: undefined });
  });

  it("accepts a complete valid dashboard status", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        new Response(
          JSON.stringify({
            home: { id: "home-1", state: "ready" },
            agent: {
              id: "agent-1",
              state: "online",
              last_seen_at: "2026-07-20T01:00:00Z",
            },
            camera: {
              id: "camera-1",
              state: "online",
              last_seen_at: "2026-07-20T01:00:00Z",
            },
            dashboard: demoDashboardData,
          }),
          { status: 200 },
        ),
      ),
    );

    await expect(createPetCareRemoteClient().getStatus()).resolves.toMatchObject({
      home: { id: "home-1", state: "ready" },
      dashboard: { generated_at: demoDashboardData.generated_at },
    });
  });

  const validStatus = (dashboard: unknown = demoDashboardData) => ({
    home: { id: "home-1", state: "ready" },
    agent: {
      id: "agent-1",
      state: "online",
      last_seen_at: "2026-07-20T01:00:00Z",
    },
    camera: {
      id: "camera-1",
      state: "online",
      last_seen_at: "2026-07-20T01:00:00Z",
    },
    dashboard,
  });

  const malformedStatuses: Array<[string, unknown]> = [
    [
      "device",
      validStatus({
        ...demoDashboardData,
        devices: [
          { ...demoDashboardData.devices[0], device_id: {} },
          demoDashboardData.devices[1],
        ],
      }),
    ],
    [
      "sensor",
      validStatus({
        ...demoDashboardData,
        latest_sensors: [
          { ...demoDashboardData.latest_sensors[0], value: "23.8" },
          ...demoDashboardData.latest_sensors.slice(1),
        ],
      }),
    ],
    [
      "behavior",
      validStatus({
        ...demoDashboardData,
        behaviors: [
          { ...demoDashboardData.behaviors[0], duration_seconds: "42" },
          ...demoDashboardData.behaviors.slice(1),
        ],
      }),
    ],
    [
      "anomaly",
      validStatus({
        ...demoDashboardData,
        anomalies: [
          { ...demoDashboardData.anomalies[0], severity: "critical" },
          ...demoDashboardData.anomalies.slice(1),
        ],
      }),
    ],
    [
      "zone",
      validStatus({
        ...demoDashboardData,
        zones: [
          { ...demoDashboardData.zones[0], x1: "0" },
          demoDashboardData.zones[1],
        ],
      }),
    ],
    [
      "bed channel",
      validStatus({
        ...demoDashboardData,
        bed: {
          ...demoDashboardData.bed,
          channels: [
            { ...demoDashboardData.bed.channels[0], available: "yes" },
            ...demoDashboardData.bed.channels.slice(1),
          ],
        },
      }),
    ],
    [
      "health",
      validStatus({
        ...demoDashboardData,
        health: { ...demoDashboardData.health, database: "unknown" },
      }),
    ],
    [
      "camera",
      validStatus({
        ...demoDashboardData,
        camera: { ...demoDashboardData.camera, fps: "11.8" },
      }),
    ],
    [
      "seven day comparison",
      validStatus({
        ...demoDashboardData,
        bed: {
          ...demoDashboardData.bed,
          seven_day: {
            ...demoDashboardData.bed.seven_day,
            complete_days: "7",
          },
        },
      }),
    ],
    [
      "calibration",
      validStatus({
        ...demoDashboardData,
        calibration: { ...demoDashboardData.calibration, channels: ["up"] },
      }),
    ],
    [
      "extra owner key",
      validStatus({ ...demoDashboardData, owner_id: "owner-1" }),
    ],
    ["extra secret key", { ...validStatus(), secret: "must-not-pass" }],
  ];

  it.each(malformedStatuses)(
    "rejects malformed nested dashboard %s",
    async (_name, body) => {
      vi.stubGlobal(
        "fetch",
        vi.fn().mockResolvedValue(
          new Response(JSON.stringify(body), { status: 200 }),
        ),
      );

      await expect(createPetCareRemoteClient().getStatus()).rejects.toMatchObject(
        { status: 200 },
      );
    },
  );

  it("forwards polling abort and sends the password only to account deletion", async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(new Response(null, { status: 204 }))
      .mockResolvedValueOnce(
        new Response(JSON.stringify({ status: "cleanup_pending" }), {
          status: 202,
        }),
      );
    vi.stubGlobal("fetch", fetchMock);
    const controller = new AbortController();

    await createPetCareRemoteClient()
      .getStatus(controller.signal)
      .catch(() => undefined);
    await createPetCareAccountClient().deleteAccount("current-password");

    expect(fetchMock).toHaveBeenNthCalledWith(
      1,
      "/api/petcare/status",
      expect.objectContaining({ signal: controller.signal }),
    );
    expect(fetchMock).toHaveBeenLastCalledWith(
      "/api/petcare/account",
      expect.objectContaining({
        method: "DELETE",
        body: JSON.stringify({ currentPassword: "current-password" }),
        credentials: "same-origin",
      }),
    );
  });

  it("normalizes idempotent empty account deletion to complete", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(new Response(null, { status: 204 })),
    );

    await expect(
      createPetCareAccountClient().deleteAccount("current-password"),
    ).resolves.toEqual({ status: "complete" });
  });

  it("accepts only cleanup_pending at 202 for account deletion", async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(
        new Response(JSON.stringify({ status: "complete" }), { status: 202 }),
      )
      .mockResolvedValueOnce(
        new Response(JSON.stringify({ status: "cleanup_pending" }), {
          status: 200,
        }),
      );
    vi.stubGlobal("fetch", fetchMock);
    const account = createPetCareAccountClient();

    await expect(account.deleteAccount("current-password")).rejects.toMatchObject(
      { status: 202 },
    );
    await expect(account.deleteAccount("current-password")).rejects.toMatchObject(
      { status: 200 },
    );
  });
});
