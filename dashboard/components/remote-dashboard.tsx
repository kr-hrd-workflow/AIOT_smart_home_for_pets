"use client";

import { useEffect, useRef, useState } from "react";

import { AccountDeletion } from "./account-deletion";
import { Dashboard } from "./dashboard";
import { EventClips } from "./event-clips";
import {
  createPetCareAccountClient,
  createPetCareRemoteClient,
  createPetCareRemoteMedia,
} from "../lib/petcare-remote";
import type {
  AgentOffline,
  Enrollment,
  PetCareAccountClient,
  PetCareRemoteClient,
  PetCareRemoteMedia,
  PetCareStatus,
} from "../lib/petcare-remote";
import type { DashboardData, DashboardSummary } from "../lib/types";

const LOCAL_SETUP_URL = "http://127.0.0.1:8000/setup";

function operationalData(summary: DashboardSummary): DashboardData {
  return {
    ...summary,
    zones: [
      {
        zone_name: "food_bowl",
        x1: 40,
        y1: 260,
        x2: 260,
        y2: 470,
        enabled: true,
        updated_at: summary.generated_at,
      },
      {
        zone_name: "pet_bed",
        x1: 320,
        y1: 180,
        x2: 630,
        y2: 470,
        enabled: true,
        updated_at: summary.generated_at,
      },
    ],
    calibration: {
      phase: "disabled",
      code: null,
      channels: [],
      message: "원격 대시보드에서는 보정을 실행할 수 없습니다.",
    },
  };
}

export function RemoteDashboard() {
  const [client] = useState(createPetCareRemoteClient);
  const [media] = useState(createPetCareRemoteMedia);
  const [accountClient] = useState(createPetCareAccountClient);
  return (
    <RemoteDashboardView
      client={client}
      media={media}
      accountClient={accountClient}
    />
  );
}

export function RemoteDashboardView({
  client,
  media,
  accountClient,
}: {
  client: PetCareRemoteClient;
  media: PetCareRemoteMedia;
  accountClient: PetCareAccountClient;
}) {
  const [status, setStatus] = useState<PetCareStatus | null>(null);
  const [offline, setOffline] = useState<AgentOffline | null>(null);
  const [enrollment, setEnrollment] = useState<Enrollment | null>(null);
  const [statusError, setStatusError] = useState<string | null>(null);
  const [enrolling, setEnrolling] = useState(false);
  const [enrollmentError, setEnrollmentError] = useState<string | null>(null);
  const enrollingRef = useRef(false);

  useEffect(() => {
    let active = true;
    let redirected = false;
    let timeout: number | undefined;
    let controller: AbortController | undefined;
    const refresh = async () => {
      controller = new AbortController();
      try {
        const next = await client.getStatus(controller.signal);
        if (active) {
          setStatus(next);
          setOffline(null);
          setStatusError(null);
        }
      } catch (error) {
        if (!active || (error instanceof DOMException && error.name === "AbortError")) {
          return;
        }
        if (
          typeof error === "object" &&
          error !== null &&
          "status" in error &&
          (error as { status?: number }).status === 401
        ) {
          redirected = true;
          window.location.assign("/login?error=session");
          return;
        }
        const nextOffline =
          typeof error === "object" && error !== null && "offline" in error
            ? (error as { offline?: AgentOffline }).offline
            : undefined;
        if (nextOffline?.code === "agent_offline") {
          setOffline(nextOffline);
        } else {
          setStatusError("원격 상태를 확인하지 못했습니다. 2초 후 다시 시도합니다.");
        }
      } finally {
        if (active && !redirected) {
          timeout = window.setTimeout(() => void refresh(), 2_000);
        }
      }
    };
    void refresh();
    return () => {
      active = false;
      controller?.abort();
      if (timeout !== undefined) window.clearTimeout(timeout);
    };
  }, [client]);

  const issueEnrollment = async () => {
    if (enrollingRef.current) return;
    enrollingRef.current = true;
    setEnrolling(true);
    setEnrollmentError(null);
    try {
      setEnrollment(await client.enroll());
    } catch {
      setEnrollmentError("코드를 만들지 못했습니다. 다시 시도하세요.");
    } finally {
      enrollingRef.current = false;
      setEnrolling(false);
    }
  };

  if (offline) {
    return (
      <main className="remote-page">
        <p className="remote-offline" role="alert">
          에이전트가 오프라인입니다. 마지막 확인:{" "}
          <time dateTime={offline.last_seen_at ?? undefined}>
            {offline.last_seen_at ?? "기록 없음"}
          </time>
        </p>
        <EventClips client={client} media={media} />
        <AccountDeletion client={accountClient} />
      </main>
    );
  }

  if (!status) {
    return (
      <main className="remote-page">
        {statusError ? (
          <p role="alert">{statusError}</p>
        ) : (
          <p role="status">운영 상태를 확인하고 있습니다.</p>
        )}
      </main>
    );
  }

  const agentReady = status.home.state === "ready";
  const entranceOnline =
    status.dashboard?.devices.some(
      ({ device_id, status: deviceStatus }) =>
        device_id === "entrance-01" && deviceStatus === "online",
    ) ?? false;
  const petzoneOnline =
    status.dashboard?.devices.some(
      ({ device_id, status: deviceStatus }) =>
        device_id === "petzone-01" && deviceStatus === "online",
    ) ?? false;
  const cameraOnline = status.camera?.state === "online";

  return (
    <div className="remote-page">
      {statusError && <p role="alert">{statusError}</p>}
      <section className="connection-card" aria-labelledby="connection-title">
        <header className="connection-heading">
          <div>
            <p className="eyebrow">기기 설정</p>
            <h1 id="connection-title">우리 집 연결</h1>
          </div>
          {agentReady && entranceOnline && petzoneOnline && (
            <strong className="connection-complete" role="status">
              필수 연결 완료
            </strong>
          )}
        </header>
        <ol
          className="connection-checklist"
          aria-labelledby="connection-title"
        >
          <li data-state={agentReady ? "complete" : "active"}>
            <div className="connection-step-heading">
              <h2>홈 에이전트</h2>
              <span>{agentReady ? "연결됨" : "연결 필요"}</span>
            </div>
            {agentReady ? (
              <>
                <p>홈 에이전트가 등록되었습니다. Pico 두 대를 Wi-Fi에 연결하세요.</p>
                <a href={LOCAL_SETUP_URL}>Pico Wi-Fi 설정 열기</a>
              </>
            ) : (
              <>
                <p>먼저 이 집에서 사용할 홈 에이전트를 등록하세요.</p>
                <button
                  type="button"
                  disabled={enrolling}
                  aria-busy={enrolling}
                  onClick={() => void issueEnrollment()}
                >
                  10분 코드 만들기
                </button>
                {enrollmentError && <p role="alert">{enrollmentError}</p>}
                {enrollment && (
                  <p aria-live="polite">
                    <strong>{enrollment.code}</strong>{" "}
                    <time dateTime={enrollment.expiresAt}>
                      {enrollment.expiresAt}
                    </time>
                  </p>
                )}
              </>
            )}
          </li>
          <li data-state={entranceOnline ? "complete" : "pending"}>
            <div className="connection-step-heading">
              <h2>현관 Pico</h2>
              <span>{entranceOnline ? "연결됨" : "연결 필요"}</span>
            </div>
            <p>entrance-01</p>
          </li>
          <li data-state={petzoneOnline ? "complete" : "pending"}>
            <div className="connection-step-heading">
              <h2>생활공간 Pico</h2>
              <span>{petzoneOnline ? "연결됨" : "연결 필요"}</span>
            </div>
            <p>petzone-01</p>
          </li>
          <li data-state={cameraOnline ? "complete" : "optional"}>
            <div className="connection-step-heading">
              <h2>Jetson 카메라</h2>
              <span>선택</span>
              <strong>{cameraOnline ? "연결됨" : "연결 안 됨"}</strong>
            </div>
            <p>Jetson 카메라는 선택 사항입니다.</p>
          </li>
        </ol>
      </section>
      {status.dashboard && status.agent && (
        <>
          <p className="remote-online" role="status">
            에이전트 온라인 · {cameraOnline ? "카메라 온라인" : "카메라 선택 안 함"} · 마지막 확인:{" "}
            <time dateTime={status.agent.last_seen_at}>
              {status.agent.last_seen_at}
            </time>
          </p>
          <div className="remote-operational">
            <Dashboard
              data={operationalData(status.dashboard)}
              mode="connected"
              camera={
                status.camera
                  ? {
                      src: media.videoFeedUrl(status.camera.id),
                      alt: "실시간 반려동물 카메라",
                    }
                  : undefined
              }
            />
          </div>
          <EventClips client={client} media={media} />
        </>
      )}
      <AccountDeletion client={accountClient} />
    </div>
  );
}
