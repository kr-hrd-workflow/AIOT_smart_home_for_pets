import { afterEach, beforeEach, expect, it, vi } from "vitest";

const mocks = vi.hoisted(() => ({
  requireHome: vi.fn(),
  replaceEnrollmentToken: vi.fn(),
}));

vi.mock("../../db", () => ({ getDb: vi.fn(() => ({ binding: "test" })) }));
vi.mock("../../lib/tenancy/repository", () => ({
  TenantRepository: class {
    requireHome = mocks.requireHome;
    replaceEnrollmentToken = mocks.replaceEnrollmentToken;
  },
}));

import { hashEnrollmentCode, issueEnrollment } from "../../lib/tenancy/enrollment";

beforeEach(() => {
  vi.useFakeTimers();
  vi.setSystemTime(new Date("2026-07-20T03:00:00.000Z"));
  mocks.requireHome.mockResolvedValue({ id: "home-a" });
  mocks.replaceEnrollmentToken.mockResolvedValue(undefined);
});

afterEach(() => {
  vi.useRealTimers();
  vi.restoreAllMocks();
  vi.clearAllMocks();
});

it("stores only the hash and expires exactly ten minutes after issue", async () => {
  vi.spyOn(crypto, "getRandomValues").mockImplementation((array) => {
    (array as Uint8Array).fill(1);
    return array;
  });

  const issued = await issueEnrollment("owner-a");
  expect(issued).toEqual({
    code: "AQEBAQEBAQEBAQEBAQEBAQ",
    expiresAt: "2026-07-20T03:10:00.000Z",
  });
  expect(issued.code).toHaveLength(22);
  expect(issued.code).toMatch(/^[A-Za-z0-9_-]{22}$/);
  expect(mocks.requireHome).toHaveBeenCalledWith("owner-a");
  expect(mocks.requireHome).toHaveBeenCalledTimes(1);
  expect(mocks.replaceEnrollmentToken).toHaveBeenCalledWith(
    "home-a",
    await hashEnrollmentCode("AQEBAQEBAQEBAQEBAQEBAQ"),
    "2026-07-20T03:10:00.000Z",
  );
  expect(mocks.replaceEnrollmentToken).not.toHaveBeenCalledWith(
    "home-a",
    "AQEBAQEBAQEBAQEBAQEBAQ",
    expect.any(String),
  );
});

it("retries a unique-hash collision and returns only the successful code", async () => {
  mocks.replaceEnrollmentToken
    .mockRejectedValueOnce(
      Object.assign(new Error("UNIQUE"), {
        code: "SQLITE_CONSTRAINT_UNIQUE",
      }),
    )
    .mockResolvedValueOnce(undefined);
  vi.spyOn(crypto, "getRandomValues")
    .mockImplementationOnce((array) => {
      (array as Uint8Array).fill(0);
      return array;
    })
    .mockImplementationOnce((array) => {
      (array as Uint8Array).fill(1);
      return array;
    });

  await expect(issueEnrollment("owner-a")).resolves.toEqual({
    code: "AQEBAQEBAQEBAQEBAQEBAQ",
    expiresAt: "2026-07-20T03:10:00.000Z",
  });
  expect(crypto.getRandomValues).toHaveBeenCalledTimes(2);
});
