import "@testing-library/jest-dom/vitest";

import { act, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { RoiEditor } from "../components/roi-editor";
import { LiveCamera } from "../components/live-camera";
import { demoDashboardData } from "../lib/demo-data";

describe("RoiEditor", () => {
  it("maps saved 640x480 coordinates onto the live camera frame", () => {
    const { container } = render(
      <LiveCamera status={demoDashboardData.camera} zones={demoDashboardData.zones} />,
    );

    expect(container.querySelector(".zone-food_bowl")).toHaveStyle({
      left: "6.25%",
      top: "54.166666666666664%",
      width: "34.375%",
      height: "43.75%",
    });
  });

  it("submits exact integer 640x480 geometry through the PUT callback", async () => {
    const user = userEvent.setup();
    const onSave = vi.fn().mockResolvedValue(undefined);
    render(<RoiEditor zones={demoDashboardData.zones} onSave={onSave} />);

    const x1 = screen.getByRole("spinbutton", { name: "급식 구역 x1" });
    await user.clear(x1);
    await user.type(x1, "20");
    await user.click(screen.getByRole("button", { name: "급식 구역 저장" }));

    expect(onSave).toHaveBeenCalledWith("food_bowl", {
      x1: 20,
      y1: 260,
      x2: 260,
      y2: 470,
      enabled: true,
    });
  });

  it("rejects reversed, fractional, and out-of-frame geometry before saving", async () => {
    const user = userEvent.setup();
    const onSave = vi.fn();
    render(<RoiEditor zones={demoDashboardData.zones} onSave={onSave} />);

    const x2 = screen.getByRole("spinbutton", { name: "급식 구역 x2" });
    await user.clear(x2);
    await user.type(x2, "10.5");
    await user.click(screen.getByRole("button", { name: "급식 구역 저장" }));

    expect(onSave).not.toHaveBeenCalled();
    expect(screen.getByRole("alert")).toHaveTextContent("0–640 × 0–480 정수 범위");
  });

  it("preserves the other unsaved ROI draft when one zone finishes saving", async () => {
    const user = userEvent.setup();
    const onSave = vi.fn().mockResolvedValue(undefined);
    const { rerender } = render(<RoiEditor zones={demoDashboardData.zones} onSave={onSave} />);

    const foodX1 = screen.getByRole("spinbutton", { name: "급식 구역 x1" });
    const bedX1 = screen.getByRole("spinbutton", { name: "침대 구역 x1" });
    await user.clear(foodX1);
    await user.type(foodX1, "20");
    await user.clear(bedX1);
    await user.type(bedX1, "300");
    await user.click(screen.getByRole("button", { name: "급식 구역 저장" }));

    const savedZones = structuredClone(demoDashboardData.zones);
    savedZones[0].x1 = 20;
    rerender(<RoiEditor zones={savedZones} onSave={onSave} />);

    expect(screen.getByRole("spinbutton", { name: "침대 구역 x1" })).toHaveValue(300);
  });

  it("prevents draft edits while an ROI save is in flight", async () => {
    const user = userEvent.setup();
    let finish!: () => void;
    const onSave = vi.fn(() => new Promise<void>((resolve) => { finish = resolve; }));
    render(<RoiEditor zones={demoDashboardData.zones} onSave={onSave} />);

    await user.click(screen.getByRole("button", { name: "급식 구역 저장" }));
    expect(screen.getByRole("spinbutton", { name: "급식 구역 x1" })).toBeDisabled();
    expect(screen.getByRole("spinbutton", { name: "침대 구역 x1" })).toBeDisabled();

    await act(async () => finish());
    await waitFor(() =>
      expect(screen.getByRole("spinbutton", { name: "급식 구역 x1" })).toBeEnabled(),
    );
  });
});
