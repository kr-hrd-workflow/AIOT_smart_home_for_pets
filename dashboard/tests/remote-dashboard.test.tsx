import "@testing-library/jest-dom/vitest";

import {
  act,
  cleanup,
  render,
  screen,
  waitFor,
  within,
} from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, expect, it, vi } from "vitest";

import { RemoteDashboardView } from "../components/remote-dashboard";
import { demoDashboardData } from "../lib/demo-data";
import type {
  PetCareAccountClient,
  PetCareClip,
  PetCareRemoteClient,
  PetCareRemoteMedia,
  PetCareStatus,
} from "../lib/petcare-remote";
import type { DashboardSummary } from "../lib/types";

const dashboardSummary = Object.fromEntries(
  Object.entries(demoDashboardData).filter(
    ([key]) => key !== "zones" && key !== "calibration",
  ),
) as unknown as DashboardSummary;

afterEach(() => {
  cleanup();
  vi.useRealTimers();
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

function mockRemote(overrides: Partial<PetCareRemoteClient> = {}) {
  const client: PetCareRemoteClient = {
    enroll: vi.fn(),
    getStatus: vi.fn().mockResolvedValue({
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
      dashboard: dashboardSummary,
    }),
    getClips: vi.fn().mockResolvedValue([]),
    deleteClip: vi.fn().mockResolvedValue(undefined),
    ...overrides,
  };
  const media: PetCareRemoteMedia = {
    videoFeedUrl: vi.fn(() => "/api/petcare/cameras/camera-1/stream.mjpeg"),
    clipUrl: vi.fn((id) => `/api/petcare/clips/${id}.mp4`),
  };
  const accountClient: PetCareAccountClient = {
    deleteAccount: vi.fn().mockResolvedValue({ status: "cleanup_pending" }),
  };
  return { client, media, accountClient };
}

function stubNavigation() {
  const assign = vi.fn();
  const testWindow = Object.create(window) as Window;
  Object.defineProperty(testWindow, "location", {
    configurable: true,
    value: { assign },
  });
  vi.stubGlobal("window", testWindow);
  return assign;
}

const clip: PetCareClip = {
  id: "clip-1",
  camera_id: "camera-1",
  event_types: ["eating", "bed_sensor_mismatch"],
  started_at: "2026-07-20T00:00:00Z",
  ended_at: "2026-07-20T00:00:30Z",
  expires_at: "2026-07-27T00:00:00Z",
};

it("polls every two seconds and uses authenticated MJPEG", async () => {
  vi.useFakeTimers();
  const { client, media, accountClient } = mockRemote();
  render(
    <RemoteDashboardView
      client={client}
      media={media}
      accountClient={accountClient}
    />,
  );

  await act(async () => Promise.resolve());
  expect(client.getStatus).toHaveBeenCalledTimes(1);
  expect(media.videoFeedUrl).toHaveBeenCalledWith("camera-1");
  await act(async () => vi.advanceTimersByTimeAsync(2_000));
  expect(client.getStatus).toHaveBeenCalledTimes(2);
});

it("self-schedules only after status settles and aborts on unmount", async () => {
  vi.useFakeTimers();
  let signal: AbortSignal | undefined;
  const pending = new Promise<PetCareStatus>(() => undefined);
  const { client, media, accountClient } = mockRemote({
    getStatus: vi.fn((nextSignal?: AbortSignal) => {
      signal = nextSignal;
      return pending;
    }),
  });
  const { unmount } = render(
    <RemoteDashboardView
      client={client}
      media={media}
      accountClient={accountClient}
    />,
  );

  await act(async () => vi.advanceTimersByTimeAsync(10_000));
  expect(client.getStatus).toHaveBeenCalledTimes(1);
  unmount();
  expect(signal?.aborted).toBe(true);
});

it("shows agent_offline with clips and without demo fallback", async () => {
  const { client, media, accountClient } = mockRemote({
    getStatus: vi.fn().mockRejectedValue({
      status: 503,
      offline: {
        code: "agent_offline",
        agent_id: "agent-1",
        camera_id: "camera-1",
        last_seen_at: "2026-07-20T00:58:00Z",
      },
    }),
    getClips: vi.fn().mockResolvedValue([clip]),
  });
  render(
    <RemoteDashboardView
      client={client}
      media={media}
      accountClient={accountClient}
    />,
  );

  expect(await screen.findByRole("alert")).toHaveTextContent(
    "에이전트가 오프라인입니다",
  );
  expect(screen.getByText("2026-07-20T00:58:00Z")).toBeInTheDocument();
  expect(
    await screen.findByLabelText("이벤트 클립 2026-07-20T00:00:00Z"),
  ).toBeInTheDocument();
  expect(screen.queryByText("742 g")).not.toBeInTheDocument();
});

it("prevents duplicate enrollment, reports failure, and permits retry", async () => {
  const enroll = vi
    .fn()
    .mockRejectedValueOnce(new Error("offline"))
    .mockResolvedValueOnce({
      code: "AQEBAQEBAQEBAQEBAQEBAQ",
      expiresAt: "2026-07-20T01:10:00Z",
    });
  const { client, media, accountClient } = mockRemote({
    getStatus: vi.fn().mockResolvedValue({
      home: { id: "home-1", state: "needs_enrollment" },
      agent: null,
      camera: null,
      dashboard: null,
    }),
    enroll,
  });
  render(
    <RemoteDashboardView
      client={client}
      media={media}
      accountClient={accountClient}
    />,
  );

  const checklist = await screen.findByRole("list", { name: "우리 집 연결" });
  const steps = within(checklist).getAllByRole("listitem");
  expect(steps).toHaveLength(4);
  expect(steps[0]).toHaveTextContent("홈 에이전트");
  expect(steps[1]).toHaveTextContent("현관 Pico");
  expect(steps[2]).toHaveTextContent("생활공간 Pico");
  expect(steps[3]).toHaveTextContent("Jetson 카메라선택");
  const button = await screen.findByRole("button", { name: "10분 코드 만들기" });
  expect(steps[0]).toContainElement(button);
  button.click();
  button.click();
  await waitFor(() => expect(enroll).toHaveBeenCalledTimes(1));
  expect(await screen.findByRole("alert")).toHaveTextContent(
    "코드를 만들지 못했습니다",
  );
  expect(button).toBeEnabled();
  button.click();
  expect(
    await screen.findByText("AQEBAQEBAQEBAQEBAQEBAQ"),
  ).toBeInTheDocument();
  expect(screen.getByText("2026-07-20T01:10:00Z")).toBeInTheDocument();
});

it("keeps the setup checklist visible when optional runtime data is absent", async () => {
  const { client, media, accountClient } = mockRemote({
    getStatus: vi.fn().mockResolvedValue({
      home: { id: "home-1", state: "ready" },
      agent: { id: "agent-1", state: "online", last_seen_at: "2026-07-20T01:00:00Z" },
      camera: null,
      dashboard: null,
    }),
  });
  render(
    <RemoteDashboardView
      client={client}
      media={media}
      accountClient={accountClient}
    />,
  );

  const checklist = await screen.findByRole("list", { name: "우리 집 연결" });
  expect(within(checklist).getAllByRole("listitem")).toHaveLength(4);
  expect(
    screen.getByRole("link", { name: "Pico Wi-Fi 설정 열기" }),
  ).toHaveAttribute("href", "http://127.0.0.1:8000/setup");
  expect(screen.queryByText("필수 연결 완료")).not.toBeInTheDocument();
  expect(screen.getByText("Jetson 카메라는 선택 사항입니다.")).toBeInTheDocument();
});

it("marks fixed Pico IDs independently and completes without a Jetson camera", async () => {
  const { client, media, accountClient } = mockRemote({
    getStatus: vi.fn().mockResolvedValue({
      home: { id: "home-1", state: "ready" },
      agent: { id: "agent-1", state: "online", last_seen_at: "2026-07-20T01:00:00Z" },
      camera: null,
      dashboard: {
        ...dashboardSummary,
        devices: [
          { device_id: "entrance-01", status: "online", last_seen_at: "2026-07-20T01:00:00Z" },
          { device_id: "petzone-01", status: "offline", last_seen_at: null },
        ],
      },
    }),
  });
  const view = render(
    <RemoteDashboardView
      client={client}
      media={media}
      accountClient={accountClient}
    />,
  );

  const firstChecklist = await screen.findByRole("list", { name: "우리 집 연결" });
  const firstSteps = within(firstChecklist).getAllByRole("listitem");
  expect(firstSteps[1]).toHaveTextContent("현관 Pico연결됨");
  expect(firstSteps[2]).toHaveTextContent("생활공간 Pico연결 필요");
  expect(screen.queryByText("필수 연결 완료")).not.toBeInTheDocument();

  view.unmount();
  const complete = mockRemote({
    getStatus: vi.fn().mockResolvedValue({
      home: { id: "home-1", state: "ready" },
      agent: { id: "agent-1", state: "online", last_seen_at: "2026-07-20T01:00:00Z" },
      camera: null,
      dashboard: dashboardSummary,
    }),
  });
  render(
    <RemoteDashboardView
      client={complete.client}
      media={complete.media}
      accountClient={complete.accountClient}
    />,
  );

  expect(await screen.findByText("필수 연결 완료")).toBeInTheDocument();
  expect(
    screen.getByRole("heading", { name: "PetCare 운영 현황", level: 1 }),
  ).toBeInTheDocument();
  const completeChecklist = screen.getByRole("list", { name: "우리 집 연결" });
  expect(within(completeChecklist).getAllByRole("listitem")[3]).toHaveTextContent(
    "Jetson 카메라선택",
  );
});

it("marks the optional Jetson camera connected from remote status", async () => {
  const { client, media, accountClient } = mockRemote();
  render(
    <RemoteDashboardView
      client={client}
      media={media}
      accountClient={accountClient}
    />,
  );

  const checklist = await screen.findByRole("list", { name: "우리 집 연결" });
  expect(within(checklist).getAllByRole("listitem")[3]).toHaveTextContent(
    "Jetson 카메라선택연결됨",
  );
});

it("lists private clips through media adapter and reloads after deletion", async () => {
  vi.spyOn(window, "confirm").mockReturnValue(true);
  const { client, media, accountClient } = mockRemote({
    getClips: vi.fn().mockResolvedValue([clip]),
  });
  render(
    <RemoteDashboardView
      client={client}
      media={media}
      accountClient={accountClient}
    />,
  );

  expect(
    await screen.findByLabelText("이벤트 클립 2026-07-20T00:00:00Z"),
  ).toHaveAttribute("src", "/api/petcare/clips/clip-1.mp4");
  expect(media.clipUrl).toHaveBeenCalledWith("clip-1");
  expect(screen.getByText("eating, bed_sensor_mismatch")).toBeInTheDocument();
  screen
    .getByRole("button", {
      name: "이벤트 클립 삭제 2026-07-20T00:00:00Z",
    })
    .click();
  await waitFor(() => expect(client.deleteClip).toHaveBeenCalledWith("clip-1"));
  await waitFor(() => expect(client.getClips).toHaveBeenCalledTimes(2));
});

it("guards duplicate clip deletion and permits retry after failure", async () => {
  vi.spyOn(window, "confirm").mockReturnValue(true);
  const deleteClip = vi
    .fn()
    .mockRejectedValueOnce(new Error("petcare_request_500"))
    .mockResolvedValueOnce(undefined);
  const { client, media, accountClient } = mockRemote({
    getClips: vi.fn().mockResolvedValue([clip]),
    deleteClip,
  });
  render(
    <RemoteDashboardView
      client={client}
      media={media}
      accountClient={accountClient}
    />,
  );

  const button = await screen.findByRole("button", {
    name: "이벤트 클립 삭제 2026-07-20T00:00:00Z",
  });
  button.click();
  button.click();
  await waitFor(() => expect(deleteClip).toHaveBeenCalledTimes(1));
  expect(await screen.findByRole("alert")).toHaveTextContent(
    "클립을 삭제하지 못했습니다",
  );
  expect(button).toBeEnabled();
  expect(
    screen.getByLabelText("이벤트 클립 2026-07-20T00:00:00Z"),
  ).toBeInTheDocument();
  button.click();
  await waitFor(() => expect(deleteClip).toHaveBeenCalledTimes(2));
});

it("keeps a clip when deletion is not confirmed", async () => {
  const user = userEvent.setup();
  vi.spyOn(window, "confirm").mockReturnValue(false);
  const { client, media, accountClient } = mockRemote({
    getClips: vi.fn().mockResolvedValue([clip]),
  });
  render(
    <RemoteDashboardView
      client={client}
      media={media}
      accountClient={accountClient}
    />,
  );

  const button = await screen.findByRole("button", {
    name: "이벤트 클립 삭제 2026-07-20T00:00:00Z",
  });
  await user.click(button);
  expect(client.deleteClip).not.toHaveBeenCalled();
  expect(
    screen.getByLabelText("이벤트 클립 2026-07-20T00:00:00Z"),
  ).toBeInTheDocument();
});

it("treats a generic response error with offline undefined as retryable", async () => {
  const { client, media, accountClient } = mockRemote({
    getStatus: vi.fn().mockRejectedValue({
      status: 500,
      offline: undefined,
    }),
  });
  render(
    <RemoteDashboardView
      client={client}
      media={media}
      accountClient={accountClient}
    />,
  );

  expect(await screen.findByRole("alert")).toHaveTextContent(
    "원격 상태를 확인하지 못했습니다",
  );
});

it("redirects expired sessions and shows initial network failure without demo data", async () => {
  const assign = stubNavigation();
  const expired = mockRemote({
    getStatus: vi.fn().mockRejectedValue({ status: 401 }),
  });
  const { unmount } = render(
    <RemoteDashboardView
      client={expired.client}
      media={expired.media}
      accountClient={expired.accountClient}
    />,
  );
  await waitFor(() =>
    expect(assign).toHaveBeenCalledWith("/login?error=session"),
  );
  unmount();

  const failed = mockRemote({
    getStatus: vi.fn().mockRejectedValue(new TypeError("network")),
  });
  render(
    <RemoteDashboardView
      client={failed.client}
      media={failed.media}
      accountClient={failed.accountClient}
    />,
  );
  expect(await screen.findByRole("alert")).toHaveTextContent(
    "원격 상태를 확인하지 못했습니다",
  );
  expect(screen.queryByText("742 g")).not.toBeInTheDocument();
});

it("keeps enrollment and destructive controls in keyboard order", async () => {
  const user = userEvent.setup();
  const { client, media, accountClient } = mockRemote({
    getStatus: vi.fn().mockResolvedValue({
      home: { id: "home-1", state: "needs_enrollment" },
      agent: null,
      camera: null,
      dashboard: null,
    }),
  });
  render(
    <RemoteDashboardView
      client={client}
      media={media}
      accountClient={accountClient}
    />,
  );

  const enrollment = await screen.findByRole("button", {
    name: "10분 코드 만들기",
  });
  await user.tab();
  expect(enrollment).toHaveFocus();
  await user.tab();
  expect(screen.getByLabelText("현재 비밀번호")).toHaveFocus();
});
