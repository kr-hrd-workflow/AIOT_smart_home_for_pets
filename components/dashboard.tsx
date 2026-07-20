import { AnomalyList } from "./anomaly-list";
import type {
  DashboardData,
  DashboardMode,
  SensorReadingOut,
} from "../lib/types";

export const API_BASE_URL = "http://127.0.0.1:8000";
export const WEBSOCKET_BASE_URL = "ws://127.0.0.1:8000";

export function selectDashboardMode(
  pathname: string,
  hostname?: string,
): DashboardMode {
  if (pathname === "/demo") return "demo";
  if (pathname !== "/") return "not_found";
  return hostname === "localhost" || hostname === "127.0.0.1"
    ? "connected"
    : "demo";
}

const sensorLabels = {
  temperature: "온도",
  humidity: "습도",
  presence_moving: "움직임",
  presence_stationary: "정지 감지",
  food_weight: "사료",
  water_weight: "물",
  bed_pressure_left: "왼쪽",
  bed_pressure_center: "가운데",
  bed_pressure_right: "오른쪽",
} as const;

const channelLabels = { left: "왼쪽", center: "가운데", right: "오른쪽" } as const;

function seconds(value: number) {
  const hours = Math.floor(value / 3600);
  const minutes = Math.floor((value % 3600) / 60);
  return hours ? `${hours}시간 ${minutes}분` : `${minutes}분`;
}

function sensorValue(sensor?: SensorReadingOut) {
  if (!sensor) return "사용 불가";
  if (sensor.unit === "bool") return sensor.value ? "감지" : "없음";
  return `${sensor.value} ${sensor.unit === "adc" ? "ADC" : sensor.unit}`;
}

function sevenDayCopy(data: DashboardData) {
  const comparison = data.bed.seven_day;
  if (comparison.status === "insufficient_data") {
    return `기준 데이터 수집 중 · ${comparison.complete_days}/7일`;
  }
  if (comparison.status === "zero_baseline") return "7일 기준 0분 · 오늘 기록 시작";
  const direction = (comparison.difference_seconds ?? 0) >= 0 ? "증가" : "감소";
  return `7일 평균 대비 ${Math.abs(comparison.percent_change ?? 0).toFixed(1)}% ${direction}`;
}

type DashboardCamera = { src: string; alt: string };

const demoCamera: DashboardCamera = {
  src: "/demo-camera.webp",
  alt: "반려동물 침대와 급식 구역 데모 카메라",
};

export function Dashboard({
  data,
  mode = "demo",
  camera = demoCamera,
}: {
  data: DashboardData;
  mode?: DashboardMode;
  camera?: DashboardCamera;
}) {
  const byType = new Map(data.latest_sensors.map((sensor) => [sensor.sensor_type, sensor]));
  const currentRest = data.behaviors.find(
    (behavior) => behavior.behavior_type === "resting" && behavior.ended_at === null,
  );

  return (
    <div className="app-shell" data-dashboard-mode={mode}>
      <header className="topbar">
        <a className="brand" href="#main-content" aria-label="PetCare 운영 현황으로 이동">
          <span aria-hidden="true">PC</span>
          <strong>PetCare</strong>
        </a>
        <div className="topbar-state" aria-live="polite">
          <span className={`state-marker ${data.health.status}`} />
          {data.health.status === "healthy" ? "로컬 시스템 정상" : "일부 시스템 확인 필요"}
          <time dateTime={data.generated_at}>{formatTime(data.generated_at)}</time>
        </div>
      </header>

      <div className="workspace">
        <nav className="rail" aria-label="주요 화면">
          <a aria-current="page" href="#summary">현황</a>
          <a href="#camera">카메라</a>
          <a href="#rest">휴식</a>
          <a href="#timeline">기록</a>
          <a href="#device-health">설정</a>
        </nav>

        <main id="main-content" className="content">
          <div className="page-heading">
            <div>
              <p className="eyebrow">HOME OPERATIONS</p>
              <h1>PetCare 운영 현황</h1>
            </div>
            <p>카메라와 센서가 함께 확인한 상태만 표시합니다.</p>
          </div>

          <div className="dashboard-grid">
            <section id="summary" className="summary-strip" data-dashboard-section="summary" aria-label="핵심 요약">
              <SummaryCell label="현재 휴식" value={seconds(data.bed.current_rest_seconds)} detail={currentRest?.subject_id ?? "확인 없음"} />
              <SummaryCell label="오늘 휴식 추정" value={seconds(data.bed.today_rest_seconds)} detail={sevenDayCopy(data)} />
              <SummaryCell label="야간 침대 이탈" value={`${data.bed.nighttime_exit_count}회`} detail="22:00–06:00" />
              <SummaryCell label="사료" value={sensorValue(byType.get("food_weight"))} detail="최근 측정" />
              <SummaryCell label="물" value={sensorValue(byType.get("water_weight"))} detail="최근 측정" />
            </section>

            <section id="camera" className="camera-section" data-dashboard-section="camera">
              <SectionHeading title="카메라 확인" meta="640 × 480" />
              <div className="camera-frame">
                <img src={camera.src} width="640" height="480" alt={camera.alt} />
                {data.zones.map((zone) => (
                  <div
                    className={`zone zone-${zone.zone_name}`}
                    key={zone.zone_name}
                    aria-label={`${zone.zone_name} 감시 영역`}
                  >
                    <span>{zone.zone_name === "food_bowl" ? "급식" : "침대"}</span>
                  </div>
                ))}
                {data.camera.state === "offline" && <p className="camera-unavailable">카메라 연결 끊김</p>}
              </div>
              <dl className="camera-meta">
                <div><dt>상태</dt><dd>{data.camera.state === "online" ? "온라인" : "오프라인"}</dd></div>
                <div><dt>FPS</dt><dd>{data.camera.state === "online" ? data.camera.fps.toFixed(1) : "사용 불가"}</dd></div>
                <div><dt>추론</dt><dd>{data.camera.state === "online" ? `${data.camera.inference_ms.toFixed(1)} ms` : "사용 불가"}</dd></div>
              </dl>
            </section>

            <section id="rest" className="rest-panel panel" data-dashboard-section="confirmed-rest">
              <SectionHeading title="확인된 휴식" meta={data.bed.camera_confirmed ? "카메라 확인" : "확인 대기"} />
              <div className="rest-subject">
                <strong>{currentRest?.subject_id ?? "현재 휴식 없음"}</strong>
                <span>{data.bed.fusion_state === "confirmed_rest" ? "휴식 추정" : fusionCopy(data.bed.fusion_state)}</span>
              </div>
              <p className="rest-duration">{seconds(data.bed.current_rest_seconds)}</p>
              <p className="secondary-copy">{sevenDayCopy(data)}</p>
              <div className="channel-table" role="table" aria-label="침대 센서 세 채널">
                {data.bed.channels.map((channel) => (
                  <div role="row" key={channel.channel}>
                    <strong role="rowheader">{channelLabels[channel.channel]}</strong>
                    <span role="cell">{channel.available && channel.raw !== null ? `${channel.raw} ADC` : "센서 사용 불가"}</span>
                    <span role="cell">기준 {channel.baseline ?? "—"}</span>
                    <span role="cell">변화 {channel.delta ?? "—"}</span>
                  </div>
                ))}
              </div>
            </section>

            <section className="warnings-panel panel" data-dashboard-section="warnings" aria-live="polite">
              <SectionHeading title="최근 경고" meta={`${data.anomalies.length}건`} />
              <AnomalyList anomalies={data.anomalies} />
            </section>

            <section className="environment-section" data-dashboard-section="environment-food">
              <SectionHeading title="환경 · 급식" meta="최근 측정" />
              <div className="reading-table">
                {(["temperature", "humidity", "presence_moving", "presence_stationary", "food_weight", "water_weight"] as const).map((type) => (
                  <div key={type}>
                    <span>{sensorLabels[type]}</span>
                    <strong>{sensorValue(byType.get(type))}</strong>
                  </div>
                ))}
              </div>
            </section>

            <section id="timeline" className="timeline-section" data-dashboard-section="timeline">
              <SectionHeading title="최근 행동 기록" meta="최신순" />
              <ol className="timeline-list">
                {data.behaviors.map((behavior) => (
                  <li key={behavior.id}>
                    <time dateTime={behavior.started_at}>{formatTime(behavior.started_at)}</time>
                    <strong>{behavior.subject_id}</strong>
                    <span>{behavior.behavior_type === "resting" ? "휴식 추정" : "식사"}</span>
                    <span>{behavior.duration_seconds === null ? "진행 중" : seconds(behavior.duration_seconds)}</span>
                  </li>
                ))}
              </ol>
            </section>

            <section className="roi-section" data-dashboard-section="roi">
              <SectionHeading title="감시 영역" meta="읽기 전용 데모" />
              <div className="zone-table">
                {data.zones.map((zone) => (
                  <div key={zone.zone_name}>
                    <strong>{zone.zone_name === "food_bowl" ? "급식 구역" : "침대 구역"}</strong>
                    <span className="numeric">({zone.x1}, {zone.y1}) – ({zone.x2}, {zone.y2})</span>
                    <span>{zone.enabled ? "사용 중" : "사용 안 함"}</span>
                  </div>
                ))}
              </div>
              <div className="calibration-control" data-calibration-phase={data.calibration.phase}>
                <button type="button" disabled aria-busy={data.calibration.phase === "submitting"}>침대 영점 재설정</button>
                <p role="status" aria-live="polite">{data.calibration.message}</p>
              </div>
            </section>

            <section id="device-health" className="health-section" data-dashboard-section="device-health">
              <SectionHeading title="장치 상태" meta={data.health.status === "healthy" ? "정상" : "확인 필요"} />
              <div className="health-list">
                {data.devices.map((device) => (
                  <div key={device.device_id}>
                    <strong>{device.device_id}</strong>
                    <span>{device.status === "online" ? "온라인" : device.status === "offline" ? "오프라인" : "상태 확인 중"}</span>
                  </div>
                ))}
                <div><strong>데이터베이스</strong><span>{data.health.database === "up" ? "연결됨" : "연결 끊김"}</span></div>
                <div><strong>MQTT</strong><span>{data.health.mqtt === "up" ? "연결됨" : data.health.mqtt === "disabled" ? "사용 안 함" : "연결 끊김"}</span></div>
                <div><strong>카메라 처리</strong><span>{data.health.camera === "online" ? "온라인" : "사용 불가"}</span></div>
                <div><strong>이벤트 큐</strong><span>{data.health.queue === "ok" ? "정상" : "처리 지연"}</span></div>
                <div><strong>백그라운드 워커</strong><span>{data.health.worker === "running" ? "실행 중" : "중지됨"}</span></div>
              </div>
            </section>
          </div>
        </main>
      </div>
    </div>
  );
}

function SummaryCell({ label, value, detail }: { label: string; value: string; detail: string }) {
  return <div><span>{label}</span><strong>{value}</strong><small>{detail}</small></div>;
}

function SectionHeading({ title, meta }: { title: string; meta: string }) {
  return <header className="section-heading"><h2>{title}</h2><span>{meta}</span></header>;
}

function fusionCopy(state: DashboardData["bed"]["fusion_state"]) {
  const labels = {
    unavailable: "센서 사용 불가",
    empty: "침대 비어 있음",
    confirmed_rest: "휴식 추정",
    unconfirmed_pressure: "카메라 확인 대기",
    sensor_check: "침대 센서 확인 필요",
  } as const;
  return labels[state];
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
