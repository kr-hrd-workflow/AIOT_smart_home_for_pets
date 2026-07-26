import "@testing-library/jest-dom/vitest";

import { cleanup, render, screen } from "@testing-library/react";
import { renderToString } from "react-dom/server";
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
      name: "평범한 하루는 그대로. 달라진 순간만 알려드려요.",
    }),
  ).toBeInTheDocument();
  expect(screen.getAllByRole("link", { name: "로그인" })[0]).toHaveAttribute(
    "href",
    "/login",
  );
  expect(screen.getAllByRole("link", { name: "회원가입" })[0]).toHaveAttribute(
    "href",
    "/signup",
  );
  expect(screen.getAllByRole("link", { name: "먼저 둘러보기" })[0]).toHaveAttribute(
    "href",
    "/demo",
  );
  expect(screen.getAllByRole("link", { name: "PetCare 시작하기" })[0]).toHaveAttribute(
    "href",
    "/signup",
  );
  expect(screen.getByRole("heading", { name: "식사 패턴의 변화를 알아채요" })).toBeInTheDocument();
  expect(screen.getByRole("heading", { name: "쉬는 시간도 함께 살펴요" })).toBeInTheDocument();
  expect(screen.getByRole("heading", { name: "필요한 장면만 남겨요" })).toBeInTheDocument();
  expect(
    screen.getByRole("heading", { name: "집의 기기를 한곳에 연결하세요" }),
  ).toBeInTheDocument();
  expect(
    screen.getByRole("link", { name: "로그인하고 연결하기" }),
  ).toHaveAttribute("href", "/login");
  expect(screen.getByRole("link", { name: "계정 만들기" })).toHaveAttribute(
    "href",
    "/signup",
  );
  expect(screen.getByRole("link", { name: "Home Agent 설치하기" })).toHaveAttribute(
    "href",
    "/dashboard",
  );
  expect(
    screen.getAllByText(/디지털 서명이 없어 Windows SmartScreen/),
  ).toHaveLength(1);
  expect(screen.getByText(/Pico 센서와 Jetson 카메라를 Home Agent에 등록/)).toBeInTheDocument();
  expect(screen.getByText(/이벤트 영상은 7일 뒤 자동으로 삭제/)).toBeInTheDocument();
  expect(screen.getByTestId("landing-fallback")).toHaveAttribute("aria-hidden", "true");
});

it("renders the public landing unless the proxy supplied a verified marker", async () => {
  requestHeaders.mockResolvedValue(new Headers({ "x-petcare-authenticated": "0" }));
  const html = renderToString(await Home());
  expect(html).toContain("평범한 하루는 그대로. 달라진 순간만 알려드려요.");
  expect(html).not.toContain("remote-dashboard");
});

it("keeps the public landing first for a verified session", async () => {
  requestHeaders.mockResolvedValue(new Headers({ "x-petcare-authenticated": "1" }));
  const html = renderToString(await Home());
  expect(html).toContain('id="petcare-story"');
  expect(html).not.toContain("remote-dashboard");
});
