import type { BedStatus, CalibrationUiState } from "../lib/types";

const fusionLabels: Record<BedStatus["fusion_state"], string> = {
  unavailable: "센서 사용 불가",
  empty: "침대 비어 있음",
  confirmed_rest: "휴식 추정",
  unconfirmed_pressure: "카메라 확인 대기",
  sensor_check: "침대 센서 확인 필요",
};
const channelLabels = { left: "왼쪽", center: "가운데", right: "오른쪽" } as const;

export function BedPanel({
  bed,
  calibration,
  onCalibrate,
}: {
  bed: BedStatus;
  calibration: CalibrationUiState;
  onCalibrate: () => Promise<void>;
}) {
  return (
    <div className="calibration-control" data-calibration-phase={calibration.phase}>
      <p>
        침대 상태 <strong>{fusionLabels[bed.fusion_state]}</strong>
      </p>
      <button
        type="button"
        disabled={calibration.phase === "submitting"}
        aria-busy={calibration.phase === "submitting"}
        onClick={() => void onCalibrate()}
      >
        침대 영점 재설정
      </button>
      <p role="status" aria-live="polite">{calibration.message}</p>
      {calibration.channels.length > 0 && (
        <p>{calibration.channels.map((channel) => channelLabels[channel]).join(", ")}</p>
      )}
    </div>
  );
}
