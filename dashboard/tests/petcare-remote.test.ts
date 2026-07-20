import { afterEach, describe, expect, it, vi } from "vitest";

import {
  createPetCareAccountClient,
  createPetCareRemoteClient,
  createPetCareRemoteMedia,
} from "../lib/petcare-remote";

afterEach(() => vi.unstubAllGlobals());

describe("createPetCareRemote", () => {
  it("uses same-origin BFF routes and cookies", async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValue(
        new Response(JSON.stringify({ clips: [] }), { status: 200 }),
      );
    vi.stubGlobal("fetch", fetchMock);

    const client = createPetCareRemoteClient();
    const media = createPetCareRemoteMedia();
    await client.getClips();

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
});
