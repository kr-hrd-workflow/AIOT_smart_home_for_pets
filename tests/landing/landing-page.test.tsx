import "@testing-library/jest-dom/vitest";

import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";

const requestHeaders = vi.hoisted(() => vi.fn());

vi.mock("next/headers", () => ({ headers: requestHeaders }));
vi.mock("../../components/remote-dashboard", () => ({
  RemoteDashboard: () => <div data-testid="remote-dashboard" />,
}));

import Home from "../../app/page";
import { LandingPage } from "../../components/landing/landing-page";

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

it("keeps the product story and primary actions available without WebGL", () => {
  render(<LandingPage />);

  expect(
    screen.getByRole("heading", {
      level: 1,
      name: "반려동물의 하루를 필요한 순간만 기록합니다",
    }),
  ).toBeInTheDocument();
  expect(screen.getAllByRole("link", { name: "로그인" })[0]).toHaveAttribute(
    "href",
    "/login",
  );
  expect(screen.getAllByRole("link", { name: "데모 보기" })[0]).toHaveAttribute(
    "href",
    "/demo",
  );
  expect(screen.getByRole("heading", { name: "식사 순간을 알아봅니다" })).toBeInTheDocument();
  expect(screen.getByRole("heading", { name: "휴식 변화를 놓치지 않습니다" })).toBeInTheDocument();
  expect(screen.getByRole("heading", { name: "이벤트만 안전하게 보관합니다" })).toBeInTheDocument();
  expect(screen.getByText(/이벤트 클립은 7일 후 자동 삭제/)).toBeInTheDocument();
  expect(screen.getByTestId("landing-fallback")).toHaveAttribute("aria-hidden", "true");
});

it("renders the public landing unless the proxy supplied a verified marker", async () => {
  requestHeaders.mockResolvedValue(new Headers({ "x-petcare-authenticated": "0" }));
  render(await Home());
  expect(screen.getByRole("heading", { level: 1 })).toHaveTextContent("반려동물의 하루");
  expect(screen.queryByTestId("remote-dashboard")).not.toBeInTheDocument();
});

it("keeps the operational dashboard for a verified session", async () => {
  requestHeaders.mockResolvedValue(new Headers({ "x-petcare-authenticated": "1" }));
  render(await Home());
  expect(screen.getByTestId("remote-dashboard")).toBeInTheDocument();
  expect(screen.queryByRole("heading", { level: 1 })).not.toBeInTheDocument();
});
