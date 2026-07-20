# PetCare Remote Dashboard Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (\`- [ ]\`) syntax for tracking.

**Goal:** Add the authenticated PetCare remote dashboard UI through same-origin BFF contracts while keeping /demo an exact static, no-client, no-network showcase.

**Architecture:** Keep the existing warm-homecare Dashboard as a presentation component and inject its camera source instead of adding networking to it. A small client-only RemoteDashboard owns the four approved remote-client methods, two-second polling, enrollment display, and clip controls. Authentication UI/routes, Supabase PKCE/cookies, callback/logout, and root redirects are exclusively owned by docs/superpowers/plans/2026-07-20-petcare-auth-tenancy.md and are consumed here as an existing boundary.

**Tech Stack:** Next 16/Vinext, React 19, TypeScript, Vitest, Testing Library, existing warm-homecare CSS, same-origin PetCare BFF, auth/tenancy plan session proxy.

## Global Constraints

- Preserve current warm-homecare tokens, typography, layout, responsive breakpoints, focus treatment, and reduced-motion behavior.
- /demo stays dashboard/app/demo/page.tsx returning exactly <Dashboard data={demoDashboardData} />; it imports no client component and makes no PetCare, Supabase, loopback, tunnel, WebSocket, or remote media request.
- Auth ownership is exclusive to the auth/tenancy plan: its public pages are /login, /signup, /forgot-password, /reset-password and its protocol handlers are /auth/login, /auth/signup, /auth/forgot-password, /auth/reset-password, /auth/callback, /auth/logout. Do not create, change, or test them here.
- Consume that plan's dashboard/proxy.ts contract: an anonymous GET / redirects to /login before React renders. Public auth pages and /demo are excluded. Protected BFF APIs return 401 for invalid sessions and 404 for foreign opaque resources.
- Do not add browser Supabase SDK/persistence, auth/session logic, storage logic, tunnel URLs, Access credentials, or device credentials to UI code.
- Mock interfaces are exactly PetCareRemoteClient.enroll(), .getStatus(signal?), .getClips(), .deleteClip(id), PetCareRemoteMedia.videoFeedUrl(cameraId), and .clipUrl(clipId). PetCareAccountClient has only deleteAccount(currentPassword). The Server Component passes no function-bearing props: RemoteDashboard creates clients within its client boundary, while test-only views accept mocks in jsdom.
- Poll with self-scheduled setTimeout only after each status request settles; never use overlapping setInterval. Abort the active request and clear its pending timeout on unmount. A 503 { code: "agent_offline" } is explicit; 401 returns to login/session state; network and other 5xx failures render an explicit retrying error. Retain only last successful real status in React memory and never show demoDashboardData as live fallback.
- One active home/agent/camera is the MVP limit. enroll() returns one code displayed with a ten-minute expiry.
- Keep existing DashboardData and behavior/anomaly contracts unchanged. Clip UI displays reasons, native playback, expiry, and deletion; BFF owns R2/retention/deletion.
- Do not stage, commit, deploy, create external resources, or set secrets without separate authorization.

---

## File Structure

| Path | Responsibility |
| --- | --- |
| dashboard/lib/petcare-remote.ts | Only browser BFF adapter; exact device/media interfaces and typed same-origin fetch factories. |
| dashboard/components/dashboard.tsx | Existing warm-homecare presentation with optional injected camera source and static demo default. |
| dashboard/components/remote-dashboard.tsx | Polling, offline state, enrollment, and Dashboard composition. |
| dashboard/components/event-clips.tsx | Private clip list, native playback, expiry, and BFF-confirmed deletion. |
| dashboard/components/account-deletion.tsx | Protected PetCare-data deletion form with current-password reauthentication, double confirmation, pending state, and local logout handoff. |
| dashboard/app/page.tsx | Auth-plan-protected operational root; composes RemoteDashboard. |
| dashboard/app/globals.css | Minimal remote/enrollment/clip styling using existing tokens and breakpoints. |
| dashboard/tests/petcare-remote.test.ts | BFF adapter contract tests. |
| dashboard/tests/dashboard.test.tsx | Injected live-media and exact /demo regressions. |
| dashboard/tests/remote-dashboard.test.tsx | Exact mocks for polling, enrollment, offline, clips, deletion, media, and keyboard access. |

## Consumed Contracts

### Auth/protected-root contract

The auth/tenancy plan owns its forms, pages, handlers, Supabase SSR client, and dashboard/proxy.ts. Before Task 3, its proxy must protect / and leave its public pages plus /demo public. RemoteDashboard assumes it mounts only after server-side session verification; it never reads or derives an owner, home, or redirect target.

### Remote BFF contract

All endpoints are same-origin, tenant-owned from the verified auth-plan session, and send Cache-Control: private, no-store, no-transform.

~~~ts
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
  id: string; camera_id: string;
  event_types: Array<"eating" | "resting" | "bed_sensor_mismatch">;
  started_at: string; ended_at: string; expires_at: string;
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
export type AccountDeletionAccepted = { status: "cleanup_pending" | "complete" };
export interface PetCareAccountClient {
  deleteAccount(currentPassword: string): Promise<AccountDeletionAccepted>;
}
~~~

| Request | Success | Required failure |
| --- | --- | --- |
| POST /api/petcare/enrollment (auth/tenancy-plan owned) | 201 Enrollment | 409 existing active agent; expiry/reuse/provision error makes no mutation. |
| GET /api/petcare/status | 200 PetCareStatus | 503 AgentOffline when unavailable, revoked, or timed out; never demo data. |
| GET /api/petcare/clips | 200 { clips: PetCareClip[] }, newest first | Stored private clips stay readable when agent is offline. |
| DELETE /api/petcare/clips/:id | 204 | 404 foreign, absent, or expired; BFF deletes metadata/object. |
| GET /api/petcare/cameras/:id/stream.mjpeg | private authenticated MJPEG | 503 agent_offline; no direct tunnel origin. |
| GET /api/petcare/clips/:id.mp4 | private authenticated MP4 | 404 foreign or expired. |
| DELETE /api/petcare/account | 202 { status: "cleanup_pending" } or idempotent 204 empty | Body is exactly { currentPassword }; both results locally log out and redirect to /login; PetCare tenant/data only, never Supabase identity. |

### Task 1: Lock the same-origin remote adapter contract

**Files:**
- Create: dashboard/lib/petcare-remote.ts
- Create: dashboard/tests/petcare-remote.test.ts

**Interfaces:**
- Produces exactly the types and two interfaces above.
- Consumes unchanged DashboardData from dashboard/lib/types.ts.

- [ ] **Step 1: Write failing adapter tests**

~~~ts
import { afterEach, describe, expect, it, vi } from "vitest";
import { createPetCareAccountClient, createPetCareRemoteClient, createPetCareRemoteMedia } from "../lib/petcare-remote";

afterEach(() => vi.unstubAllGlobals());

describe("createPetCareRemote", () => {
  it("uses same-origin BFF routes and cookies", async () => {
    const fetchMock = vi.fn().mockResolvedValue(new Response(JSON.stringify({ clips: [] }), { status: 200 }));
    vi.stubGlobal("fetch", fetchMock);
    const client = createPetCareRemoteClient();
    const media = createPetCareRemoteMedia();
    await client.getClips();
    expect(fetchMock).toHaveBeenCalledWith("/api/petcare/clips", {
      credentials: "same-origin", headers: { accept: "application/json" },
    });
    expect(media.videoFeedUrl("camera/one")).toBe("/api/petcare/cameras/camera%2Fone/stream.mjpeg");
    expect(media.clipUrl("clip/one")).toBe("/api/petcare/clips/clip%2Fone.mp4");
  });

  it("maps BFF agent_offline to structured error", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(JSON.stringify({
      code: "agent_offline", agent_id: "agent-1", camera_id: "camera-1", last_seen_at: "2026-07-20T01:00:00Z",
    }), { status: 503 })));
    await expect(createPetCareRemoteClient().getStatus()).rejects.toMatchObject({
      status: 503, offline: { code: "agent_offline", agent_id: "agent-1", camera_id: "camera-1" },
    });
  });

  it("forwards polling abort and sends the password only to account deletion", async () => {
    const fetchMock = vi.fn().mockResolvedValue(new Response(JSON.stringify({ status: "cleanup_pending" }), { status: 202 }));
    vi.stubGlobal("fetch", fetchMock);
    const controller = new AbortController();
    await createPetCareRemoteClient().getStatus(controller.signal).catch(() => undefined);
    await createPetCareAccountClient().deleteAccount("current-password");
    expect(fetchMock).toHaveBeenLastCalledWith("/api/petcare/account", expect.objectContaining({
      method: "DELETE", body: JSON.stringify({ currentPassword: "current-password" }), credentials: "same-origin",
    }));
  });

  it("normalizes idempotent empty account deletion to complete", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(null, { status: 204 })));
    await expect(createPetCareAccountClient().deleteAccount("current-password")).resolves.toEqual({ status: "complete" });
  });
});
~~~

- [ ] **Step 2: Run red test**

Run: npm test -- tests/petcare-remote.test.ts

Expected: FAIL because ../lib/petcare-remote does not exist.

- [ ] **Step 3: Implement only the BFF adapter**

~~~ts
import type { DashboardData } from "./types";

export type AgentOffline = { code: "agent_offline"; agent_id: string | null; camera_id: string | null; last_seen_at: string | null };
export type PetCareStatus = { home: { id: string; state: "ready" | "needs_enrollment" }; agent: { id: string; state: "online"; last_seen_at: string } | null; camera: { id: string; state: "online"; last_seen_at: string } | null; dashboard: DashboardData | null };
export type Enrollment = { code: string; expiresAt: string };
export type PetCareClip = { id: string; camera_id: string; event_types: Array<"eating" | "resting" | "bed_sensor_mismatch">; started_at: string; ended_at: string; expires_at: string };
export interface PetCareRemoteClient { enroll(): Promise<Enrollment>; getStatus(signal?: AbortSignal): Promise<PetCareStatus>; getClips(): Promise<PetCareClip[]>; deleteClip(id: string): Promise<void>; }
export interface PetCareRemoteMedia { videoFeedUrl(cameraId: string): string; clipUrl(clipId: string): string; }
export type AccountDeletionAccepted = { status: "cleanup_pending" | "complete" };
export interface PetCareAccountClient { deleteAccount(currentPassword: string): Promise<AccountDeletionAccepted>; }

class PetCareRemoteError extends Error {
  constructor(readonly status: number, readonly offline?: AgentOffline) { super(offline?.code ?? "petcare_request_" + status); }
}
async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(path, { credentials: "same-origin", headers: { accept: "application/json" }, ...init });
  if (response.ok) return response.status === 204 ? undefined as T : response.json() as Promise<T>;
  const body = await response.json().catch(() => undefined) as AgentOffline | undefined;
  throw new PetCareRemoteError(response.status, body?.code === "agent_offline" ? body : undefined);
}
export function createPetCareRemoteClient(): PetCareRemoteClient {
  return {
    enroll: () => request<Enrollment>("/api/petcare/enrollment", { method: "POST" }),
    getStatus: (signal) => request<PetCareStatus>("/api/petcare/status", { signal }),
    getClips: async () => (await request<{ clips: PetCareClip[] }>("/api/petcare/clips")).clips,
    deleteClip: (id) => request<void>("/api/petcare/clips/" + encodeURIComponent(id), { method: "DELETE" }),
  };
}

export function createPetCareRemoteMedia(): PetCareRemoteMedia {
  return {
    videoFeedUrl: (id) => "/api/petcare/cameras/" + encodeURIComponent(id) + "/stream.mjpeg",
    clipUrl: (id) => "/api/petcare/clips/" + encodeURIComponent(id) + ".mp4",
  };
}

export function createPetCareAccountClient(): PetCareAccountClient {
  return {
    deleteAccount: async (currentPassword) => {
      const response = await fetch("/api/petcare/account", {
        method: "DELETE", credentials: "same-origin",
        headers: { accept: "application/json", "content-type": "application/json" },
        body: JSON.stringify({ currentPassword }),
      });
      if (response.status === 204) return { status: "complete" };
      if (response.status === 202) return response.json() as Promise<AccountDeletionAccepted>;
      throw new PetCareRemoteError(response.status);
    },
  };
}
~~~

- [ ] **Step 4: Run green adapter tests**

Run: npm test -- tests/petcare-remote.test.ts

Expected: PASS; every adapter URL is relative/same-origin and 503 preserves last-seen details.

- [ ] **Step 5: Commit**

~~~bash
git add dashboard/lib/petcare-remote.ts dashboard/tests/petcare-remote.test.ts
git commit -m "feat(dashboard): add remote BFF adapter"
~~~

### Task 2: Inject live camera media without changing /demo

**Files:**
- Modify: dashboard/components/dashboard.tsx
- Modify: dashboard/tests/dashboard.test.tsx

**Interfaces:**
- Consumes: DashboardData and optional camera { src: string; alt: string }.
- Produces: static /demo-camera.webp default and an injectable same-origin MJPEG source.

- [ ] **Step 1: Write failing presentation test**

~~~tsx
it("renders injected authenticated camera source without fetching", () => {
  render(<Dashboard data={demoDashboardData} mode="connected" camera={{
    src: "/api/petcare/cameras/camera-1/stream.mjpeg", alt: "실시간 반려동물 카메라",
  }} />);
  expect(screen.getByRole("img", { name: "실시간 반려동물 카메라" })).toHaveAttribute(
    "src", "/api/petcare/cameras/camera-1/stream.mjpeg",
  );
});
~~~

- [ ] **Step 2: Run red test**

Run: npm test -- tests/dashboard.test.tsx -t "injected authenticated camera source"

Expected: FAIL because Dashboard has no camera prop.

- [ ] **Step 3: Add the smallest presentation seam**

~~~tsx
type DashboardCamera = { src: string; alt: string };
const demoCamera: DashboardCamera = {
  src: "/demo-camera.webp", alt: "반려동물 카메라와 급식 구역 오버레이 카메라",
};

export function Dashboard({ data, mode = "demo", camera = demoCamera }: {
  data: DashboardData; mode?: DashboardMode; camera?: DashboardCamera;
}) {
  // Retain existing body; replace only fixed image attributes:
  // <img src={camera.src} width="640" height="480" alt={camera.alt} />
}
~~~

Do not import petcare-remote, call fetch, or branch to demo data in Dashboard. Leave dashboard/app/demo/page.tsx unchanged.

- [ ] **Step 4: Run presentation and demo tests**

Run: npm test -- tests/dashboard.test.tsx

Expected: PASS; injected MJPEG renders while /demo still uses the local WebP and existing no-network checks remain green.

- [ ] **Step 5: Commit**

~~~bash
git add dashboard/components/dashboard.tsx dashboard/tests/dashboard.test.tsx
git commit -m "feat(dashboard): allow injected camera media"
~~~

### Task 3: Add polling, one-home enrollment, and explicit offline state

**Files:**
- Create: dashboard/components/remote-dashboard.tsx
- Create: dashboard/tests/remote-dashboard.test.tsx
- Modify: dashboard/app/page.tsx
- Modify: dashboard/app/globals.css

**Interfaces:**
- Consumes only the exact Task 1 interfaces and Task 2 camera prop.
- Produces server-safe RemoteDashboard() with no props and test-only RemoteDashboardView({ client, media, accountClient }); neither accepts owner/home ID, credential, tunnel origin, Supabase session, or demo data.

- [ ] **Step 1: Write failing operational tests with exact mocks**

~~~tsx
import { act, render, screen } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { RemoteDashboardView } from "../components/remote-dashboard";
import { demoDashboardData } from "../lib/demo-data";
import type { PetCareAccountClient, PetCareRemoteClient, PetCareRemoteMedia } from "../lib/petcare-remote";

afterEach(() => vi.useRealTimers());

function mockRemote(overrides: Partial<PetCareRemoteClient> = {}) {
  const client: PetCareRemoteClient = {
    enroll: vi.fn(),
    getStatus: vi.fn().mockResolvedValue({
      home: { id: "home-1", state: "ready" },
      agent: { id: "agent-1", state: "online", last_seen_at: "2026-07-20T01:00:00Z" },
      camera: { id: "camera-1", state: "online", last_seen_at: "2026-07-20T01:00:00Z" },
      dashboard: demoDashboardData,
    }),
    getClips: vi.fn().mockResolvedValue([]), deleteClip: vi.fn().mockResolvedValue(undefined), ...overrides,
  };
  const media: PetCareRemoteMedia = {
    videoFeedUrl: vi.fn(() => "/api/petcare/cameras/camera-1/stream.mjpeg"),
    clipUrl: vi.fn((id) => "/api/petcare/clips/" + id + ".mp4"),
  };
  const accountClient: PetCareAccountClient = { deleteAccount: vi.fn().mockResolvedValue({ status: "cleanup_pending" }) };
  return { client, media, accountClient };
}

it("polls every two seconds and uses authenticated MJPEG", async () => {
  vi.useFakeTimers();
  const { client, media, accountClient } = mockRemote();
  render(<RemoteDashboardView client={client} media={media} accountClient={accountClient} />);
  await act(async () => { await Promise.resolve(); });
  expect(client.getStatus).toHaveBeenCalledTimes(1);
  expect(media.videoFeedUrl).toHaveBeenCalledWith("camera-1");
  await act(async () => { await vi.advanceTimersByTimeAsync(2_000); });
  expect(client.getStatus).toHaveBeenCalledTimes(2);
});

it("shows agent_offline without demo fallback", async () => {
  const { client, media, accountClient } = mockRemote({ getStatus: vi.fn().mockRejectedValue({
    status: 503, offline: { code: "agent_offline", agent_id: "agent-1", camera_id: "camera-1", last_seen_at: "2026-07-20T00:58:00Z" },
  }) });
  render(<RemoteDashboardView client={client} media={media} accountClient={accountClient} />);
  expect(await screen.findByRole("alert")).toHaveTextContent("에이전트가 오프라인입니다");
  expect(screen.getByText("2026-07-20T00:58:00Z")).toBeInTheDocument();
  expect(screen.queryByText("742 g")).not.toBeInTheDocument();
});

it("displays the one-time ten-minute enrollment code", async () => {
  const { client, media, accountClient } = mockRemote({
    getStatus: vi.fn().mockResolvedValue({ home: { id: "home-1", state: "needs_enrollment" }, agent: null, camera: null, dashboard: null }),
    enroll: vi.fn().mockResolvedValue({ code: "AQEBAQEBAQEBAQEBAQEBAQ", expiresAt: "2026-07-20T01:10:00Z" }),
  });
  render(<RemoteDashboardView client={client} media={media} accountClient={accountClient} />);
  (await screen.findByRole("button", { name: "10분 코드 만들기" })).click();
  expect(await screen.findByText("AQEBAQEBAQEBAQEBAQEBAQ")).toBeInTheDocument();
  expect(screen.getByText("2026-07-20T01:10:00Z")).toBeInTheDocument();
});
~~~

- [ ] **Step 2: Run red test**

Run: npm test -- tests/remote-dashboard.test.tsx -t "polls|agent_offline|enrollment"

Expected: FAIL because RemoteDashboard does not exist.

- [ ] **Step 3: Implement client-only composition**

~~~tsx
"use client";
import { useEffect, useState } from "react";
import { Dashboard } from "./dashboard";
import { AccountDeletion } from "./account-deletion";
import { createPetCareAccountClient, createPetCareRemoteClient, createPetCareRemoteMedia } from "../lib/petcare-remote";
import type { AgentOffline, Enrollment, PetCareAccountClient, PetCareRemoteClient, PetCareRemoteMedia, PetCareStatus } from "../lib/petcare-remote";

export function RemoteDashboard() {
  const [client] = useState(createPetCareRemoteClient);
  const [media] = useState(createPetCareRemoteMedia);
  const [accountClient] = useState(createPetCareAccountClient);
  return <RemoteDashboardView client={client} media={media} accountClient={accountClient} />;
}

export function RemoteDashboardView({ client, media, accountClient }: { client: PetCareRemoteClient; media: PetCareRemoteMedia; accountClient: PetCareAccountClient }) {
  const [status, setStatus] = useState<PetCareStatus | null>(null);
  const [offline, setOffline] = useState<AgentOffline | null>(null);
  const [enrollment, setEnrollment] = useState<Enrollment | null>(null);
  const [statusError, setStatusError] = useState<string | null>(null);
  useEffect(() => {
    let active = true;
    let timeout: number | undefined;
    let controller: AbortController | undefined;
    const refresh = async () => {
      controller = new AbortController();
      try {
        const next = await client.getStatus(controller.signal);
        if (active) { setStatus(next); setOffline(null); setStatusError(null); }
      } catch (error) {
        if (!active || (error instanceof DOMException && error.name === "AbortError")) return;
        if (typeof error === "object" && error !== null && "status" in error && (error as { status?: number }).status === 401) {
          window.location.assign("/login?error=session");
          return;
        }
        if (typeof error === "object" && error !== null && "offline" in error) setOffline((error as { offline?: AgentOffline }).offline ?? null);
        else setStatusError("원격 상태를 확인하지 못했습니다. 2초 후 다시 시도합니다.");
      } finally {
        if (active) timeout = window.setTimeout(() => void refresh(), 2_000);
      }
    };
    void refresh();
    return () => { active = false; controller?.abort(); if (timeout !== undefined) window.clearTimeout(timeout); };
  }, [client]);
  if (offline) return <main className="remote-page"><p className="remote-offline" role="alert">에이전트가 오프라인입니다. 마지막 확인: <time dateTime={offline.last_seen_at ?? undefined}>{offline.last_seen_at ?? "기록 없음"}</time></p></main>;
  if (!status) return <main className="remote-page"><p role="status">운영 상태를 확인하고 있습니다.</p></main>;
  if (status.home.state === "needs_enrollment") return <main className="remote-page"><section className="enrollment-card"><h1>홈 에이전트 연결</h1><p>이 집에는 하나의 활성 에이전트와 카메라만 연결할 수 있습니다.</p><button type="button" onClick={() => void client.enroll().then(setEnrollment)}>10분 코드 만들기</button>{enrollment && <p aria-live="polite"><strong>{enrollment.code}</strong><time dateTime={enrollment.expiresAt}>{enrollment.expiresAt}</time></p>}</section></main>;
  if (!status.dashboard || !status.camera) return <main className="remote-page"><p role="alert">에이전트 상태를 확인할 수 없습니다.</p></main>;
  return <main className="remote-page"><p className="remote-online" role="status">에이전트 온라인 · 카메라 온라인 · 마지막 확인: <time dateTime={status.agent?.last_seen_at}>{status.agent?.last_seen_at}</time></p><Dashboard data={status.dashboard} mode="connected" camera={{ src: media.videoFeedUrl(status.camera.id), alt: "실시간 반려동물 카메라" }} /></main>;
}
~~~

Replace dashboard/app/page.tsx with only this composition; the auth plan's proxy owns redirect behavior:

~~~tsx
import { RemoteDashboard } from "../components/remote-dashboard";

export const dynamic = "force-dynamic";
export default function Home() {
  return <RemoteDashboard />;
}
~~~

Add remote-page, remote-online, remote-offline, and enrollment-card using existing panel borders/colors, 44px controls, current responsive padding, and --destructive for offline. No offline view contains demo imagery/metrics.

- [ ] **Step 4: Run green operational tests**

Run: npm test -- tests/remote-dashboard.test.tsx -t "polls|agent_offline|enrollment"

Expected: PASS; immediate/2,000 ms polling works, media adapter is sole MJPEG URL source, enrollment calls once, and offline has no demo metric.

- [ ] **Step 5: Commit**

~~~bash
git add dashboard/components/remote-dashboard.tsx dashboard/app/page.tsx dashboard/app/globals.css dashboard/tests/remote-dashboard.test.tsx
git commit -m "feat(dashboard): add remote status and enrollment"
~~~

### Task 4: Add private clip playback, expiry, and deletion

**Files:**
- Create: dashboard/components/event-clips.tsx
- Modify: dashboard/components/remote-dashboard.tsx
- Modify: dashboard/tests/remote-dashboard.test.tsx
- Modify: dashboard/app/globals.css

**Interfaces:**
- Consumes only client.getClips(), client.deleteClip(id), and media.clipUrl(clipId).
- Produces EventClips({ client, media }) with no object-storage, signed URL, or retention implementation.

- [ ] **Step 1: Add failing clip lifecycle tests**

~~~tsx
it("lists private clips, plays through media adapter, and deletes after BFF success", async () => {
  const { client, media, accountClient } = mockRemote({ getClips: vi.fn().mockResolvedValue([{
    id: "clip-1", camera_id: "camera-1", event_types: ["eating", "bed_sensor_mismatch"],
    started_at: "2026-07-20T00:00:00Z", ended_at: "2026-07-20T00:00:30Z", expires_at: "2026-07-27T00:00:00Z",
  }]) });
  render(<RemoteDashboardView client={client} media={media} accountClient={accountClient} />);
  expect(await screen.findByLabelText("이벤트 클립 2026-07-20T00:00:00Z")).toHaveAttribute("src", "/api/petcare/clips/clip-1.mp4");
  expect(screen.getByText("eating, bed_sensor_mismatch")).toBeInTheDocument();
  screen.getByRole("button", { name: "이벤트 클립 삭제 2026-07-20T00:00:00Z" }).click();
  await waitFor(() => expect(client.deleteClip).toHaveBeenCalledWith("clip-1"));
  expect(client.getClips).toHaveBeenCalledTimes(2);
});

it("keeps deletion failure explicit and clip visible", async () => {
  const clip = { id: "clip-1", camera_id: "camera-1", event_types: ["resting"] as const, started_at: "2026-07-20T00:00:00Z", ended_at: "2026-07-20T00:00:30Z", expires_at: "2026-07-27T00:00:00Z" };
  const { client, media, accountClient } = mockRemote({ getClips: vi.fn().mockResolvedValue([clip]), deleteClip: vi.fn().mockRejectedValue(new Error("petcare_request_500")) });
  render(<RemoteDashboardView client={client} media={media} accountClient={accountClient} />);
  (await screen.findByRole("button", { name: "이벤트 클립 삭제 2026-07-20T00:00:00Z" })).click();
  expect(await screen.findByRole("alert")).toHaveTextContent("클립을 삭제하지 못했습니다");
  expect(screen.getByLabelText("이벤트 클립 2026-07-20T00:00:00Z")).toBeInTheDocument();
});
~~~

- [ ] **Step 2: Run red clip tests**

Run: npm test -- tests/remote-dashboard.test.tsx -t "clips|deletion"

Expected: FAIL because event-clips.tsx and clip loading do not exist.

- [ ] **Step 3: Implement native video and server-confirmed deletion**

~~~tsx
"use client";
import { useEffect, useState } from "react";
import type { PetCareClip, PetCareRemoteClient, PetCareRemoteMedia } from "../lib/petcare-remote";

export function EventClips({ client, media }: { client: PetCareRemoteClient; media: PetCareRemoteMedia }) {
  const [clips, setClips] = useState<PetCareClip[]>([]);
  const [error, setError] = useState<string | null>(null);
  const load = () => client.getClips().then(setClips).catch(() => setError("클립 목록을 불러오지 못했습니다."));
  useEffect(() => { void load(); }, [client]);
  const remove = async (clip: PetCareClip) => {
    try { setError(null); await client.deleteClip(clip.id); await load(); }
    catch { setError("클립을 삭제하지 못했습니다."); }
  };
  return <section className="clip-section" aria-labelledby="clips-title"><header className="section-heading"><h2 id="clips-title">이벤트 클립</h2><span>{clips.length}개</span></header>{error && <p role="alert">{error}</p>}<ol className="clip-list">{clips.map((clip) => <li key={clip.id}><video aria-label={"이벤트 클립 " + clip.started_at} controls preload="metadata" src={media.clipUrl(clip.id)} /><div><strong>{clip.event_types.join(", ")}</strong><time dateTime={clip.started_at}>{clip.started_at}</time><p>만료: <time dateTime={clip.expires_at}>{clip.expires_at}</time></p><button type="button" onClick={() => void remove(clip)} aria-label={"이벤트 클립 삭제 " + clip.started_at}>삭제</button></div></li>)}</ol></section>;
}
~~~

Add the EventClips import. Replace RemoteDashboardView's offline return with the same offline paragraph followed by <EventClips client={client} media={media} />. Replace its ready return with the current status paragraph, Dashboard, then <EventClips client={client} media={media} />. RemoteDashboard() remains the prop-free client boundary.

Add clip-section, clip-list, clip-list li, clip-list video, and destructive-delete styles using existing tokens. Video width is 100%; at max-width 600px clip list is one column. Do not cache/transform media URLs outside media.clipUrl.

- [ ] **Step 4: Run green remote tests**

Run: npm test -- tests/remote-dashboard.test.tsx

Expected: PASS; playback only calls clipUrl, deletion reloads only after success, and failed deletion remains visible with an alert.

- [ ] **Step 5: Commit**

~~~bash
git add dashboard/components/event-clips.tsx dashboard/components/remote-dashboard.tsx dashboard/app/globals.css dashboard/tests/remote-dashboard.test.tsx
git commit -m "feat(dashboard): add private event clips"
~~~

### Task 5: Harden polling, mutations, and PetCare-data deletion

**Files:**
- Create: dashboard/components/account-deletion.tsx
- Create: dashboard/tests/account-deletion.test.tsx
- Modify: dashboard/lib/petcare-remote.ts
- Modify: dashboard/components/remote-dashboard.tsx
- Modify: dashboard/components/event-clips.tsx
- Modify: dashboard/tests/remote-dashboard.test.tsx
- Modify: dashboard/app/globals.css

**Interfaces:**
- Keeps PetCareRemoteClient at its exact four methods and PetCareRemoteMedia at two. The separate PetCareAccountClient exposes only deleteAccount(currentPassword).
- Account deletion sends currentPassword only to DELETE /api/petcare/account as JSON. It never stores/logs the password or sends it to /auth/logout.

- [ ] **Step 1: Write failing safety tests**

~~~tsx
it("self-schedules only after status settles and aborts on unmount", async () => {
  vi.useFakeTimers();
  let signal: AbortSignal | undefined;
  const pending = new Promise<PetCareStatus>(() => undefined);
  const { client, media, accountClient } = mockRemote({
    getStatus: vi.fn((nextSignal?: AbortSignal) => { signal = nextSignal; return pending; }),
  });
  const { unmount } = render(<RemoteDashboardView client={client} media={media} accountClient={accountClient} />);
  await act(async () => { await vi.advanceTimersByTimeAsync(10_000); });
  expect(client.getStatus).toHaveBeenCalledTimes(1);
  unmount();
  expect(signal?.aborted).toBe(true);
});

it("prevents duplicate enrollment, reports failure, and permits retry", async () => {
  const enroll = vi.fn().mockRejectedValueOnce(new Error("offline")).mockResolvedValueOnce({ code: "AQEBAQEBAQEBAQEBAQEBAQ", expiresAt: "2026-07-20T01:10:00Z" });
  const { client, media, accountClient } = mockRemote({
    getStatus: vi.fn().mockResolvedValue({ home: { id: "home-1", state: "needs_enrollment" }, agent: null, camera: null, dashboard: null }),
    enroll,
  });
  render(<RemoteDashboardView client={client} media={media} accountClient={accountClient} />);
  const button = await screen.findByRole("button", { name: "10분 코드 만들기" });
  button.click(); button.click();
  await waitFor(() => expect(enroll).toHaveBeenCalledTimes(1));
  expect(await screen.findByRole("alert")).toHaveTextContent("코드를 만들지 못했습니다");
  expect(button).toBeEnabled();
  button.click();
  expect(await screen.findByText("AQEBAQEBAQEBAQEBAQEBAQ")).toBeInTheDocument();
});
~~~

Create dashboard/tests/account-deletion.test.tsx:

~~~tsx
it("requires double confirmation, submits password only to deletion, then logs out", async () => {
  const remove = vi.fn().mockResolvedValue({ status: "cleanup_pending" });
  const logout = vi.fn().mockResolvedValue(new Response(null, { status: 200 }));
  vi.stubGlobal("fetch", logout);
  const assign = vi.fn();
  Object.defineProperty(window, "location", { configurable: true, value: { assign } });
  render(<AccountDeletion client={{ deleteAccount: remove }} />);
  await userEvent.type(screen.getByLabelText("현재 비밀번호"), "current-password");
  await userEvent.click(screen.getByLabelText("PetCare 데이터 삭제를 이해합니다"));
  await userEvent.type(screen.getByLabelText("삭제 확인 문구"), "DELETE");
  await userEvent.click(screen.getByRole("button", { name: "PetCare 데이터 삭제" }));
  expect(remove).toHaveBeenCalledWith("current-password");
  expect(await screen.findByRole("status")).toHaveTextContent("cleanup_pending");
  expect(logout).toHaveBeenCalledWith("/auth/logout", { method: "POST", credentials: "same-origin" });
  expect(assign).toHaveBeenCalledWith("/login");
});
~~~

Add EventClips tests that a double-click calls deleteClip once; rejection leaves the clip visible, reenables its button, and a later click retries.
Add a status mock rejecting `{ status: 401 }` and assert `window.location.assign("/login?error=session")`; add an initial generic `TypeError` rejection and assert the explicit retry alert with no demo metric.
Repeat the account-deletion test with deleteAccount resolving `{ status: "complete" }` (the normalized BFF 204): assert the same POST /auth/logout and /login transition.

- [ ] **Step 2: Run the red suite**

Run: npm test -- tests/remote-dashboard.test.tsx tests/account-deletion.test.tsx

Expected: FAIL because polling overlaps, mutations have no pending/retry states, and account-deletion.tsx does not exist.

- [ ] **Step 3: Replace the interim interval/effect and enrollment handler**

~~~tsx
const [enrolling, setEnrolling] = useState(false);
const [enrollmentError, setEnrollmentError] = useState<string | null>(null);
useEffect(() => {
  let active = true;
  let timeout: number | undefined;
  let controller: AbortController | undefined;
  const refresh = async () => {
    controller = new AbortController();
    try {
      const next = await client.getStatus(controller.signal);
      if (active) { setStatus(next); setOffline(null); setStatusError(null); }
    } catch (error) {
      if (!active || (error instanceof DOMException && error.name === "AbortError")) return;
      if (typeof error === "object" && error !== null && "status" in error && (error as { status?: number }).status === 401) {
        window.location.assign("/login?error=session");
        return;
      }
      if (typeof error === "object" && error !== null && "offline" in error) setOffline((error as { offline?: AgentOffline }).offline ?? null);
      else setStatusError("원격 상태를 확인하지 못했습니다. 2초 후 다시 시도합니다.");
    } finally {
      if (active) timeout = window.setTimeout(() => void refresh(), 2_000);
    }
  };
  void refresh();
  return () => { active = false; controller?.abort(); if (timeout !== undefined) window.clearTimeout(timeout); };
}, [client]);

const issueEnrollment = async () => {
  if (enrolling) return;
  setEnrolling(true); setEnrollmentError(null);
  try { setEnrollment(await client.enroll()); }
  catch { setEnrollmentError("코드를 만들지 못했습니다. 다시 시도하세요."); }
  finally { setEnrolling(false); }
};
~~~

Replace the bare .then(setEnrollment) control with a disabled={enrolling}, aria-busy={enrolling} button that calls issueEnrollment(), then render enrollmentError with role="alert". Preserve last successful real Dashboard data under an explicit network/5xx error; initial network/5xx failure is only role="alert", never demo data. Render AccountDeletion below both the needs_enrollment and agent_offline surfaces as well as the ready dashboard.

- [ ] **Step 4: Implement the account-deletion form**

~~~tsx
"use client";
import { useState } from "react";
import type { PetCareAccountClient } from "../lib/petcare-remote";

export function AccountDeletion({ client }: { client: PetCareAccountClient }) {
  const [currentPassword, setCurrentPassword] = useState("");
  const [acknowledged, setAcknowledged] = useState(false);
  const [phrase, setPhrase] = useState("");
  const [phase, setPhase] = useState<"idle" | "pending" | "cleanup_pending" | "complete" | "error">("idle");
  const submit = async (event: React.FormEvent) => {
    event.preventDefault();
    if (phase !== "idle" || !acknowledged || phrase !== "DELETE" || !currentPassword) return;
    setPhase("pending");
    try {
      const accepted = await client.deleteAccount(currentPassword);
      if (accepted.status !== "cleanup_pending" && accepted.status !== "complete") throw new Error("unexpected_account_status");
      setCurrentPassword(""); setPhase(accepted.status);
      await fetch("/auth/logout", { method: "POST", credentials: "same-origin" });
      window.location.assign("/login");
    } catch { setPhase("error"); }
  };
  const pending = phase === "pending" || phase === "cleanup_pending" || phase === "complete";
  return <section className="account-deletion" aria-labelledby="account-delete-title"><h2 id="account-delete-title">PetCare 데이터 삭제</h2><p>삭제 cleanup pending 동안 차단되고 완료 후 PetCare tenant data가 제거됩니다. Supabase login identity는 유지되며 나중에 명시적으로 새 home enrollment를 시작할 수 있습니다.</p><form onSubmit={submit}><fieldset disabled={pending} aria-busy={pending}><label>현재 비밀번호<input aria-label="현재 비밀번호" type="password" value={currentPassword} onChange={(event) => setCurrentPassword(event.target.value)} autoComplete="current-password" required /></label><label><input aria-label="PetCare 데이터 삭제를 이해합니다" type="checkbox" checked={acknowledged} onChange={(event) => setAcknowledged(event.target.checked)} /> PetCare 데이터 삭제를 이해합니다</label><label>삭제 확인 문구<input aria-label="삭제 확인 문구" value={phrase} onChange={(event) => setPhrase(event.target.value)} required /></label><button type="submit" disabled={pending || !acknowledged || phrase !== "DELETE" || !currentPassword} aria-busy={pending}>PetCare 데이터 삭제</button></fieldset></form>{(phase === "cleanup_pending" || phase === "complete") && <p role="status">{phase}</p>}{phase === "error" && <p role="alert">PetCare 데이터를 삭제하지 못했습니다. 다시 시도하세요.</p>}</section>;
}
~~~

Add account-deletion styles from existing destructive/control/focus tokens and mobile width. Do not add permanent/no-reactivation copy, Supabase Admin/identity deletion, password persistence, or console logging.

- [ ] **Step 5: Replace clip deletion with guarded retry**

~~~tsx
const [deletingId, setDeletingId] = useState<string | null>(null);
const remove = async (clip: PetCareClip) => {
  if (deletingId) return;
  setDeletingId(clip.id); setError(null);
  try { await client.deleteClip(clip.id); await load(); }
  catch { setError("클립을 삭제하지 못했습니다. 다시 시도하세요."); }
  finally { setDeletingId(null); }
};
// Matching button: disabled={deletingId === clip.id}, aria-busy={deletingId === clip.id}.
~~~

- [ ] **Step 6: Run green suite and commit**

Run: npm test -- tests/remote-dashboard.test.tsx tests/account-deletion.test.tsx

Expected: PASS; no overlap, unmount abort, 401 login transition, duplicate mutation prevention, failure retry, and both cleanup_pending/complete local logout-login transitions.

~~~bash
git add dashboard/lib/petcare-remote.ts dashboard/components/remote-dashboard.tsx dashboard/components/event-clips.tsx dashboard/components/account-deletion.tsx dashboard/app/globals.css dashboard/tests/petcare-remote.test.ts dashboard/tests/remote-dashboard.test.tsx dashboard/tests/account-deletion.test.tsx
git commit -m "feat(dashboard): harden remote mutations and data deletion"
~~~

### Task 6: Verify protected-root consumption, accessibility, and /demo isolation


**Files:**
- Modify: dashboard/tests/dashboard.test.tsx
- Modify: dashboard/tests/remote-dashboard.test.tsx
- Modify: dashboard/app/globals.css

**Interfaces:**
- Consumes Tasks 1-5 and the auth plan's proxy-protected root.
- Produces regression evidence for root composition, keyboard-visible controls, mobile/reduced-motion behavior, and exact /demo isolation; it does not test auth routes/pages.

- [ ] **Step 1: Write final regression tests**

~~~tsx
it("keeps auth-plan-protected root Flight-serializable and demo server-only", () => {
  const rootPage = readFileSync(resolve(root, "app/page.tsx"), "utf8");
  const demoPage = readFileSync(resolve(root, "app/demo/page.tsx"), "utf8");
  const remoteDashboard = readFileSync(resolve(root, "components/remote-dashboard.tsx"), "utf8");
  expect(rootPage).toContain("RemoteDashboard");
  expect(rootPage).toContain('export const dynamic = "force-dynamic"');
  expect(rootPage).toMatch(/return <RemoteDashboard \/>/);
  expect(rootPage).not.toMatch(/createPetCareRemote|client=|media=/);
  expect(remoteDashboard).toContain("createPetCareRemoteClient");
  expect(remoteDashboard).toContain("createPetCareRemoteMedia");
  expect(rootPage).not.toMatch(/"use client"|demoDashboardData|window\.|localStorage|sessionStorage/);
  expect(demoPage).toMatch(/return <Dashboard data=\{demoDashboardData\} \/>/);
  expect(demoPage).not.toMatch(/fetch|WebSocket|localhost|127\.0\.0\.1|useState|useEffect|petcare-remote/);
});

it("keeps remote controls keyboard-visible and mobile-safe", () => {
  const css = readFileSync(resolve(root, "app/globals.css"), "utf8");
  expect(css).toContain("input:focus-visible");
  expect(css).toContain(".clip-list video");
  expect(css).toContain("@media (max-width: 600px)");
  expect(css).toContain("@media (prefers-reduced-motion: reduce)");
});
~~~

Extend remote-dashboard.test.tsx with userEvent.tab() assertions that enrollment/delete controls occur in DOM order and remain visibly focused.

- [ ] **Step 2: Run red regression test**

Run: npm test -- tests/dashboard.test.tsx tests/remote-dashboard.test.tsx

Expected: FAIL only for missing focus/mobile selectors, stale local-root assertions, or a function-bearing client/media prop crossing the React Flight boundary; current /demo invariants stay green.

- [ ] **Step 3: Make only CSS/test corrections**

~~~css
input:focus-visible,
video:focus-visible {
  outline: 2px solid var(--primary);
  outline-offset: 2px;
}
.clip-list video { width: 100%; border: 1px solid var(--border-control); border-radius: 6px; }
@media (max-width: 600px) {
  .clip-list li { grid-template-columns: minmax(0, 1fr); }
}
~~~

Remove obsolete selectDashboardMode root-host tests and loopback assumptions from dashboard/tests/dashboard.test.tsx. Keep the existing /demo no-client/no-network and static WebP assertions verbatim. Do not alter auth-plan files, add dependencies, or add browser automation.

- [ ] **Step 4: Run complete local verification**

Run: npm test

Expected: PASS; dashboard/anomaly tests plus new adapter/remote/clip tests pass alongside auth-plan tests.

Run: npm run build

Expected: exit code 0 with no TypeScript or Vinext error.

- [ ] **Step 5: Inspect and commit verification changes**

Run: git diff --check

Expected: no whitespace errors.

~~~bash
git add dashboard/app/globals.css dashboard/tests/dashboard.test.tsx dashboard/tests/remote-dashboard.test.tsx
git commit -m "test(dashboard): verify remote access boundaries"
~~~

## Authorized Integration Evidence

Run only after the auth/tenancy plan and external resources are complete and authorized.

1. Confirm the auth plan's anonymous GET / redirect and its /demo exception; this plan adds no auth form/route assertion.
2. With two authenticated accounts and separately enrolled homes, each account sees only its own agent, camera, MJPEG, and clips; copied opaque IDs return 404, including deletion.
3. Stop one agent: status returns 503 agent_offline; the dashboard renders real last-seen and never demo readings, while stored clips remain available through BFF MP4.
4. At 320px and keyboard-only, enrollment/video/delete controls have visible focus, deletion failures announce an alert, and reduced-motion has no large movement.
5. Verify MJPEG/MP4 no-store headers and no browser-visible tunnel origin, Access credential, device credential, reset token, or service-role secret.

## Plan Review

- Scope coverage: Task 1 fixes exact BFF/mock interfaces. Task 2 preserves visual system while enabling authenticated MJPEG. Task 3 covers one-home enrollment, ten-minute code, online/last-seen, and offline state. Task 4 covers clips/playback/expiry/delete. Task 5 adds non-overlapping polling, abort/error handling, safe mutation retries, and PetCare-data deletion. Task 6 verifies protected-root consumption, responsive accessibility, and /demo isolation.
- Auth ownership: signup/login/verification/forgot/reset UI, Supabase PKCE/cookies, callback/logout, and redirects are absent and consumed only from 2026-07-20-petcare-auth-tenancy.md.
- Placeholder/type review: Every file, method, wire field, request, test command, expected result, and commit is named. PetCareRemoteClient has exactly four methods and PetCareRemoteMedia exactly two.

## Execution Handoff

Plan complete and saved to docs/superpowers/plans/2026-07-20-petcare-remote-dashboard.md. Two execution options:

1. Subagent-Driven (recommended) - dispatch a fresh subagent per task and review between tasks.
2. Inline Execution - execute task-by-task with checkpoints.

Which approach?
