// @vitest-environment node

import { beforeEach, expect, it, vi } from "vitest";

const requestHeaders = vi.hoisted(() => vi.fn());

vi.mock("next/headers", () => ({ headers: requestHeaders }));

import { generateMetadata } from "../../app/layout";

beforeEach(() => {
  requestHeaders.mockResolvedValue(
    new Headers({
      "x-forwarded-host": "app.test",
      "x-forwarded-proto": "https",
    }),
  );
});

it("publishes the local 1200x630 PetCare social card", async () => {
  const metadata = await generateMetadata();

  expect(metadata.openGraph?.images).toEqual([
    expect.objectContaining({
      url: "https://app.test/og.png",
      width: 1200,
      height: 630,
    }),
  ]);
  expect(metadata.twitter?.images).toEqual(["https://app.test/og.png"]);
});
