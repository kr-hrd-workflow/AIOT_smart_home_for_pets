import type { AnomalyEventOut } from "../lib/types";

const labels = {
  no_meal_12h: "12시간 식사 기록 없음",
  unconfirmed_pressure: "카메라 확인 대기",
  sensor_check: "침대 센서 확인 필요",
} as const;

function warningLabel(anomaly: AnomalyEventOut) {
  if (anomaly.mismatch_kind) return labels[anomaly.mismatch_kind];
  return anomaly.anomaly_type === "no_meal_12h"
    ? labels.no_meal_12h
    : labels.sensor_check;
}

export function AnomalyList({ anomalies }: { anomalies: AnomalyEventOut[] }) {
  if (anomalies.length === 0) {
    return <p className="empty-state">현재 확인할 경고가 없습니다.</p>;
  }

  return (
    <ol className="warning-list" aria-label="최신 경고">
      {anomalies.map((anomaly) => (
        <li key={anomaly.id}>
          <div>
            <strong>{warningLabel(anomaly)}</strong>
            <span>{anomaly.subject_id ?? "침대 영역"}</span>
          </div>
          <p>{anomaly.message}</p>
          <time dateTime={anomaly.occurred_at}>{formatTime(anomaly.occurred_at)}</time>
        </li>
      ))}
    </ol>
  );
}

function formatTime(value: string) {
  return new Intl.DateTimeFormat("ko-KR", {
    timeZone: "Asia/Seoul",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    hour12: false,
  }).format(new Date(value));
}
