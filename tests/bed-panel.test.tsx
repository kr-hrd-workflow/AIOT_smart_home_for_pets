import "@testing-library/jest-dom/vitest";

import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { BedPanel } from "../components/bed-panel";
import { demoDashboardData } from "../lib/demo-data";

describe("BedPanel", () => {
  it("exposes current bed ownership state and triggers calibration once", async () => {
    const user = userEvent.setup();
    const onCalibrate = vi.fn().mockResolvedValue(undefined);
    render(
      <BedPanel
        bed={demoDashboardData.bed}
        calibration={{ phase: "idle", code: null, channels: [], message: "보정 준비" }}
        onCalibrate={onCalibrate}
      />,
    );

    expect(screen.getByText("휴식 추정")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "침대 영점 재설정" }));
    expect(onCalibrate).toHaveBeenCalledTimes(1);
  });

  it("keeps ordered backend calibration errors explicit", () => {
    render(
      <BedPanel
        bed={demoDashboardData.bed}
        calibration={{
          phase: "error",
          code: "sensor_unavailable",
          channels: ["left", "center"],
          message: "센서 입력을 확인하세요.",
        }}
        onCalibrate={vi.fn()}
      />,
    );

    expect(screen.getByRole("status")).toHaveTextContent("센서 입력을 확인하세요.");
    expect(screen.getByText("왼쪽, 가운데")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "침대 영점 재설정" })).toBeEnabled();
  });

  it("disables duplicate submission while calibration is running", () => {
    render(
      <BedPanel
        bed={demoDashboardData.bed}
        calibration={{ phase: "submitting", code: null, channels: [], message: "보정 중" }}
        onCalibrate={vi.fn()}
      />,
    );

    expect(screen.getByRole("button", { name: "침대 영점 재설정" })).toBeDisabled();
  });
});
