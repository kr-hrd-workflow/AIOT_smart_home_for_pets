import "@testing-library/jest-dom/vitest";

import { render, screen, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { AnomalyList } from "../components/anomaly-list";
import type { AnomalyEventOut } from "../lib/types";

const anomalies: AnomalyEventOut[] = [
  {
    id: 3,
    subject_id: null,
    anomaly_type: "bed_sensor_mismatch",
    severity: "warning",
    mismatch_kind: "unconfirmed_pressure",
    message: "침대 압력은 감지됐지만 카메라 확인이 없습니다.",
    occurred_at: "2026-07-15T01:40:00Z",
  },
  {
    id: 2,
    subject_id: "cat_001",
    anomaly_type: "bed_sensor_mismatch",
    severity: "warning",
    mismatch_kind: "sensor_check",
    message: "침대 센서 확인 필요",
    occurred_at: "2026-07-15T01:20:00Z",
  },
  {
    id: 1,
    subject_id: "dog_001",
    anomaly_type: "no_meal_12h",
    severity: "warning",
    mismatch_kind: null,
    message: "12시간 식사 기록 없음",
    occurred_at: "2026-07-15T00:10:00Z",
  },
];

describe("AnomalyList", () => {
  it("renders newest-first warnings and preserves nullable mismatch identity", () => {
    render(<AnomalyList anomalies={anomalies} />);

    const items = screen.getAllByRole("listitem");
    expect(items).toHaveLength(3);
    expect(within(items[0]).getByText("카메라 확인 대기")).toBeInTheDocument();
    expect(items[0]).toHaveTextContent("침대 영역");
    expect(within(items[1]).getAllByText("침대 센서 확인 필요")).toHaveLength(2);
    expect(items[1]).toHaveTextContent("cat_001");
    expect(within(items[2]).getAllByText("12시간 식사 기록 없음")).toHaveLength(2);
    expect(items[2]).toHaveTextContent("dog_001");
  });

  it("renders an explicit empty state", () => {
    render(<AnomalyList anomalies={[]} />);
    expect(screen.getByText("현재 확인할 경고가 없습니다.")).toBeInTheDocument();
  });
});
