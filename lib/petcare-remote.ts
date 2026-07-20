import type { DashboardData } from "./types";

export type AgentOffline = {
  code: "agent_offline";
  agent_id: string | null;
  camera_id: string | null;
  last_seen_at: string | null;
};

export type PetCareStatus = {
  home: { id: string; state: "ready" | "needs_enrollment" };
  agent: { id: string; state: "online"; last_seen_at: string } | null;
  camera: { id: string; state: "online"; last_seen_at: string } | null;
  dashboard: DashboardData | null;
};

export type Enrollment = { code: string; expiresAt: string };

export type PetCareClip = {
  id: string;
  camera_id: string;
  event_types: Array<"eating" | "resting" | "bed_sensor_mismatch">;
  started_at: string;
  ended_at: string;
  expires_at: string;
};

export interface PetCareRemoteClient {
  enroll(): Promise<Enrollment>;
  getStatus(signal?: AbortSignal): Promise<PetCareStatus>;
  getClips(): Promise<PetCareClip[]>;
  deleteClip(id: string): Promise<void>;
}

export interface PetCareRemoteMedia {
  videoFeedUrl(cameraId: string): string;
  clipUrl(clipId: string): string;
}

export type AccountDeletionAccepted = {
  status: "cleanup_pending" | "complete";
};

export interface PetCareAccountClient {
  deleteAccount(currentPassword: string): Promise<AccountDeletionAccepted>;
}

class PetCareRemoteError extends Error {
  constructor(
    readonly status: number,
    readonly offline?: AgentOffline,
  ) {
    super(offline?.code ?? `petcare_request_${status}`);
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(path, {
    credentials: "same-origin",
    headers: { accept: "application/json" },
    ...init,
  });
  if (response.ok) {
    return response.status === 204
      ? (undefined as T)
      : (response.json() as Promise<T>);
  }
  const body = (await response.json().catch(() => undefined)) as
    | AgentOffline
    | undefined;
  throw new PetCareRemoteError(
    response.status,
    body?.code === "agent_offline" ? body : undefined,
  );
}

export function createPetCareRemoteClient(): PetCareRemoteClient {
  return {
    enroll: () =>
      request<Enrollment>("/api/petcare/enrollment", { method: "POST" }),
    getStatus: (signal) =>
      request<PetCareStatus>("/api/petcare/status", { signal }),
    getClips: async () =>
      (await request<{ clips: PetCareClip[] }>("/api/petcare/clips")).clips,
    deleteClip: (id) =>
      request<void>(`/api/petcare/clips/${encodeURIComponent(id)}`, {
        method: "DELETE",
      }),
  };
}

export function createPetCareRemoteMedia(): PetCareRemoteMedia {
  return {
    videoFeedUrl: (id) =>
      `/api/petcare/cameras/${encodeURIComponent(id)}/stream.mjpeg`,
    clipUrl: (id) => `/api/petcare/clips/${encodeURIComponent(id)}.mp4`,
  };
}

export function createPetCareAccountClient(): PetCareAccountClient {
  return {
    deleteAccount: async (currentPassword) => {
      const response = await fetch("/api/petcare/account", {
        method: "DELETE",
        credentials: "same-origin",
        headers: {
          accept: "application/json",
          "content-type": "application/json",
        },
        body: JSON.stringify({ currentPassword }),
      });
      if (response.status === 204) return { status: "complete" };
      if (response.status === 202) {
        return response.json() as Promise<AccountDeletionAccepted>;
      }
      throw new PetCareRemoteError(response.status);
    },
  };
}
