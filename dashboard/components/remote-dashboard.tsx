"use client";

import { useEffect, useRef, useState, type FormEvent } from "react";

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
  PicoProduct,
} from "../lib/petcare-remote";
import type { DashboardData, DashboardSummary } from "../lib/types";

const LOCAL_SETUP_URL = "http://127.0.0.1:8000/setup";
const HOME_AGENT_INSTALLER_URL = "/downloads/PetCare-Home-Agent-Setup.exe";

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
  const [wifiSsid, setWifiSsid] = useState("");
  const [wifiPassword, setWifiPassword] = useState("");
  const [provisioningProduct, setProvisioningProduct] =
    useState<PicoProduct | null>(null);
  const [picoMessage, setPicoMessage] = useState<string | null>(null);
  const [picoError, setPicoError] = useState<string | null>(null);
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

  const configurePico = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (provisioningProduct) return;
    const submitter = (event.nativeEvent as SubmitEvent)
      .submitter as HTMLButtonElement | null;
    const product = submitter?.value as PicoProduct | undefined;
    if (product !== "entrance-01" && product !== "petzone-01") return;

    setProvisioningProduct(product);
    setPicoMessage(null);
    setPicoError(null);
    try {
      await client.provisionPico(product, {
        ssid: wifiSsid,
        password: wifiPassword,
      });
      setPicoMessage(
        `${product === "entrance-01" ? "현관" : "생활공간"} Pico 설정을 전달했습니다. 온라인 상태를 확인합니다.`,
      );
    } catch {
      setPicoError(
        "Pico 설정에 실패했습니다. USB 연결과 Home Agent 상태를 확인한 뒤 다시 시도하세요.",
      );
    } finally {
      setWifiPassword("");
      setProvisioningProduct(null);
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
        <p>
          <a href={LOCAL_SETUP_URL}>오프라인 복구 설정 열기</a>
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
  const cameraOnline = status.dashboard?.camera.state === "online";

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
                <p>
                  Windows Home Agent를 설치한 다음, 10분 코드를 설치 창에
                  입력하세요.
                </p>
                <div className="connection-enrollment-actions">
                  <a href={HOME_AGENT_INSTALLER_URL} download>
                    Windows Home Agent 베타 설치
                  </a>
                  <button
                    type="button"
                    disabled={enrolling}
                    aria-busy={enrolling}
                    onClick={() => void issueEnrollment()}
                  >
                    10분 코드 만들기
                  </button>
                </div>
                <small>
                  코드서명 준비 전 베타 파일은 Windows SmartScreen 확인이 필요할 수
                  있습니다.
                </small>
                {enrollmentError && <p role="alert">{enrollmentError}</p>}
                {enrollment && (
                  <p aria-live="polite">
                    <strong>{enrollment.code}</strong>{" "}
                    <time dateTime={enrollment.expiresAt}>
                      {enrollment.expiresAt}
                    </time>{" "}
                    · 설치 프로그램 창에 입력하세요.
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
            {agentReady && (
              <a href={LOCAL_SETUP_URL}>Jetson 연결 설정</a>
            )}
          </li>
        </ol>
        {agentReady && (
          <section
            className="pico-cloud-setup"
            aria-labelledby="pico-cloud-setup-title"
          >
            <div>
              <p className="eyebrow">Pico를 처음 연결할 때</p>
              <h2 id="pico-cloud-setup-title">Pico Wi-Fi 설정</h2>
              <p>
                Pico를 Home Agent PC에 USB로 연결한 뒤, 이 화면에서 집 Wi-Fi를
                입력하세요. MQTT 비밀번호는 Home Agent가 기기에 직접 결합하며
                웹에 표시하거나 저장하지 않습니다.
              </p>
            </div>
            <form onSubmit={(event) => void configurePico(event)}>
              <label>
                Wi-Fi 이름 (SSID)
                <input
                  type="text"
                  autoComplete="off"
                  minLength={1}
                  maxLength={32}
                  required
                  disabled={provisioningProduct !== null}
                  value={wifiSsid}
                  onChange={(event) => setWifiSsid(event.target.value)}
                />
              </label>
              <label>
                Wi-Fi 비밀번호
                <input
                  type="password"
                  autoComplete="new-password"
                  minLength={8}
                  maxLength={63}
                  required
                  disabled={provisioningProduct !== null}
                  value={wifiPassword}
                  onChange={(event) => setWifiPassword(event.target.value)}
                />
              </label>
              <div className="pico-cloud-actions">
                <button
                  type="submit"
                  value="entrance-01"
                  disabled={provisioningProduct !== null}
                  aria-busy={provisioningProduct === "entrance-01"}
                >
                  {entranceOnline ? "현관 Pico 다시 설정" : "현관 Pico 설정"}
                </button>
                <button
                  type="submit"
                  value="petzone-01"
                  disabled={provisioningProduct !== null}
                  aria-busy={provisioningProduct === "petzone-01"}
                >
                  {petzoneOnline
                    ? "생활공간 Pico 다시 설정"
                    : "생활공간 Pico 설정"}
                </button>
                <a href={LOCAL_SETUP_URL}>오프라인 복구 설정 열기</a>
              </div>
              {picoMessage && <p role="status">{picoMessage}</p>}
              {picoError && <p role="alert">{picoError}</p>}
            </form>
          </section>
        )}
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
