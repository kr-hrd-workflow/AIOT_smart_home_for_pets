import { VIDEO_FEED_URL } from "../lib/api-client";
import type { CameraStatus, ZoneOut } from "../lib/types";

export function LiveCamera({
  status,
  zones,
}: {
  status: CameraStatus;
  zones: [ZoneOut, ZoneOut];
}) {
  return (
    <>
      <div className="camera-frame">
        {status.state === "online" ? (
          <img src={VIDEO_FEED_URL} width="640" height="480" alt="실시간 반려동물 카메라" />
        ) : (
          <p className="camera-unavailable">카메라 연결 끊김</p>
        )}
        {zones.map((zone) => (
          <div
            className={`zone zone-${zone.zone_name}`}
            key={zone.zone_name}
            aria-label={`${zone.zone_name} 감시 영역`}
            style={{
              left: `${(zone.x1 / 640) * 100}%`,
              top: `${(zone.y1 / 480) * 100}%`,
              width: `${((zone.x2 - zone.x1) / 640) * 100}%`,
              height: `${((zone.y2 - zone.y1) / 480) * 100}%`,
            }}
          >
            <span>{zone.zone_name === "food_bowl" ? "급식" : "침대"}</span>
          </div>
        ))}
      </div>
      <dl className="camera-meta">
        <div><dt>상태</dt><dd>{status.state === "online" ? "온라인" : "오프라인"}</dd></div>
        <div><dt>FPS</dt><dd>{status.state === "online" ? status.fps.toFixed(1) : "사용 불가"}</dd></div>
        <div><dt>추론</dt><dd>{status.state === "online" ? `${status.inference_ms.toFixed(1)} ms` : "사용 불가"}</dd></div>
      </dl>
    </>
  );
}
