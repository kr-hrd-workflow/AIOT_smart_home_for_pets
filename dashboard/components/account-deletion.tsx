"use client";

import { useRef, useState } from "react";
import type { FormEvent } from "react";

import type { PetCareAccountClient } from "../lib/petcare-remote";

type Phase = "idle" | "pending" | "cleanup_pending" | "complete" | "error";

export function AccountDeletion({
  client,
}: {
  client: PetCareAccountClient;
}) {
  const [currentPassword, setCurrentPassword] = useState("");
  const [acknowledged, setAcknowledged] = useState(false);
  const [phrase, setPhrase] = useState("");
  const [phase, setPhase] = useState<Phase>("idle");
  const [logoutError, setLogoutError] = useState(false);
  const [loggingOut, setLoggingOut] = useState(false);
  const loggingOutRef = useRef(false);
  const pending = phase === "pending" || phase === "cleanup_pending" || phase === "complete";
  const ready = acknowledged && phrase === "DELETE" && Boolean(currentPassword);

  const finishLogout = async () => {
    if (loggingOutRef.current) return;
    loggingOutRef.current = true;
    setLoggingOut(true);
    setLogoutError(false);
    try {
      const response = await fetch("/auth/logout", {
        method: "POST",
        credentials: "same-origin",
      });
      if (!response.ok) throw new Error("logout_failed");
      window.location.assign("/login");
    } catch {
      setLogoutError(true);
    } finally {
      loggingOutRef.current = false;
      setLoggingOut(false);
    }
  };

  const submit = async (event: FormEvent) => {
    event.preventDefault();
    if (pending || !ready) return;
    setPhase("pending");
    let accepted: Awaited<ReturnType<PetCareAccountClient["deleteAccount"]>>;
    try {
      accepted = await client.deleteAccount(currentPassword);
    } catch {
      setPhase("error");
      return;
    }
    setCurrentPassword("");
    setPhase(accepted.status);
    await finishLogout();
  };

  return (
    <section className="account-deletion" aria-labelledby="account-delete-title">
      <h2 id="account-delete-title">PetCare 데이터 삭제</h2>
      <p>
        PetCare 홈, 카메라, 이벤트 데이터를 삭제합니다. 로그인 계정은 유지되어
        나중에 홈 연결을 다시 시작할 수 있습니다.
      </p>
      <form onSubmit={submit}>
        <fieldset disabled={pending} aria-busy={pending}>
          <label>
            현재 비밀번호
            <input
              aria-label="현재 비밀번호"
              type="password"
              value={currentPassword}
              onChange={(event) => setCurrentPassword(event.target.value)}
              autoComplete="current-password"
              required
            />
          </label>
          <label className="deletion-acknowledgement">
            <input
              aria-label="PetCare 데이터 삭제를 이해합니다"
              type="checkbox"
              checked={acknowledged}
              onChange={(event) => setAcknowledged(event.target.checked)}
            />
            PetCare 데이터 삭제를 이해합니다
          </label>
          <label>
            삭제 확인 문구
            <input
              aria-label="삭제 확인 문구"
              value={phrase}
              onChange={(event) => setPhrase(event.target.value)}
              required
            />
          </label>
          <button
            className="destructive-delete"
            type="submit"
            disabled={pending || !ready}
            aria-busy={pending}
          >
            PetCare 데이터 삭제
          </button>
        </fieldset>
      </form>
      {(phase === "cleanup_pending" || phase === "complete") && (
        <p role="status">{phase}</p>
      )}
      {phase === "error" && (
        <p role="alert">PetCare 데이터를 삭제하지 못했습니다. 다시 시도하세요.</p>
      )}
      {logoutError && (
        <div className="logout-retry">
          <p role="alert">삭제는 접수됐지만 로그아웃하지 못했습니다.</p>
          <button
            type="button"
            disabled={loggingOut}
            aria-busy={loggingOut}
            onClick={() => void finishLogout()}
          >
            로그아웃 다시 시도
          </button>
        </div>
      )}
    </section>
  );
}
