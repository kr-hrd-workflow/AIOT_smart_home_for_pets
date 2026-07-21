import "@testing-library/jest-dom/vitest";

import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, expect, it, vi } from "vitest";

import { AccountDeletion } from "../components/account-deletion";

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

function stubNavigation() {
  const assign = vi.fn();
  const testWindow = Object.create(window) as Window;
  Object.defineProperty(testWindow, "location", {
    configurable: true,
    value: { assign },
  });
  vi.stubGlobal("window", testWindow);
  return assign;
}

it.each(["cleanup_pending", "complete"] as const)(
  "requires double confirmation, handles %s, then logs out",
  async (status) => {
    const user = userEvent.setup();
    const remove = vi.fn().mockResolvedValue({ status });
    const logout = vi.fn().mockResolvedValue(new Response(null, { status: 200 }));
    vi.stubGlobal("fetch", logout);
    const assign = stubNavigation();
    render(<AccountDeletion client={{ deleteAccount: remove }} />);

    const submit = screen.getByRole("button", {
      name: "PetCare 데이터 삭제",
    });
    expect(submit).toBeDisabled();
    await user.type(screen.getByLabelText("현재 비밀번호"), "current-password");
    await user.click(screen.getByLabelText("PetCare 데이터 삭제를 이해합니다"));
    await user.type(screen.getByLabelText("삭제 확인 문구"), "DELETE");
    await user.click(submit);

    expect(remove).toHaveBeenCalledWith("current-password");
    expect(await screen.findByRole("status")).toHaveTextContent(status);
    expect(logout).toHaveBeenCalledWith("/auth/logout", {
      method: "POST",
      credentials: "same-origin",
    });
    expect(assign).toHaveBeenCalledWith("/login");
  },
);

it("keeps password local and allows retry after deletion failure", async () => {
  const user = userEvent.setup();
  const remove = vi
    .fn()
    .mockRejectedValueOnce(new Error("failed"))
    .mockResolvedValueOnce({ status: "complete" });
  vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(null)));
  stubNavigation();
  render(<AccountDeletion client={{ deleteAccount: remove }} />);

  await user.type(screen.getByLabelText("현재 비밀번호"), "current-password");
  await user.click(screen.getByLabelText("PetCare 데이터 삭제를 이해합니다"));
  await user.type(screen.getByLabelText("삭제 확인 문구"), "DELETE");
  const submit = screen.getByRole("button", { name: "PetCare 데이터 삭제" });
  await user.click(submit);
  expect(await screen.findByRole("alert")).toHaveTextContent(
    "PetCare 데이터를 삭제하지 못했습니다",
  );
  expect(submit).toBeEnabled();
  await user.click(submit);
  expect(remove).toHaveBeenCalledTimes(2);
});

it("does not permit another deletion after logout failure", async () => {
  const user = userEvent.setup();
  const remove = vi.fn().mockResolvedValue({ status: "complete" });
  vi.stubGlobal(
    "fetch",
    vi
      .fn()
      .mockResolvedValueOnce(new Response(null, { status: 503 }))
      .mockResolvedValueOnce(new Response(null, { status: 200 })),
  );
  const assign = stubNavigation();
  render(<AccountDeletion client={{ deleteAccount: remove }} />);

  await user.type(screen.getByLabelText("현재 비밀번호"), "current-password");
  await user.click(screen.getByLabelText("PetCare 데이터 삭제를 이해합니다"));
  await user.type(screen.getByLabelText("삭제 확인 문구"), "DELETE");
  const submit = screen.getByRole("button", { name: "PetCare 데이터 삭제" });
  await user.click(submit);

  expect(await screen.findByRole("status")).toHaveTextContent("complete");
  expect(await screen.findByRole("alert")).toHaveTextContent(
    "로그아웃하지 못했습니다",
  );
  expect(submit).toBeDisabled();
  await user.click(submit);
  expect(remove).toHaveBeenCalledTimes(1);
  expect(assign).not.toHaveBeenCalled();
  await user.click(
    screen.getByRole("button", { name: "로그아웃 다시 시도" }),
  );
  expect(fetch).toHaveBeenCalledTimes(2);
  expect(remove).toHaveBeenCalledTimes(1);
  expect(assign).toHaveBeenCalledWith("/login");
});
