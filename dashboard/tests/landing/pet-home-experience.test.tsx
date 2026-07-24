import "@testing-library/jest-dom/vitest";

import { render, screen, waitFor } from "@testing-library/react";
import { beforeEach, expect, it, vi } from "vitest";

const mocks = vi.hoisted(() => ({
  cleanup: vi.fn(),
  mountScrollWorld: vi.fn(),
  readCompactScene: vi.fn(() => false),
  readSceneMode: vi.fn(),
}));

vi.mock("../../components/landing/scene-quality", async (importOriginal) => ({
  ...(await importOriginal<typeof import("../../components/landing/scene-quality")>()),
  readCompactScene: mocks.readCompactScene,
  readSceneMode: mocks.readSceneMode,
}));
vi.mock("../../components/landing/scene-director", () => ({
  mountScrollWorld: mocks.mountScrollWorld,
}));

import { PetHomeExperience } from "../../components/landing/pet-home-experience";

beforeEach(() => {
  vi.clearAllMocks();
  mocks.mountScrollWorld.mockReturnValue(mocks.cleanup);
});

it("keeps the local still when capability detection selects fallback", async () => {
  mocks.readSceneMode.mockReturnValue("fallback");
  render(<PetHomeExperience />);

  expect(screen.getByTestId("landing-fallback")).toBeInTheDocument();
  expect(mocks.readSceneMode).toHaveBeenCalled();
  expect(mocks.mountScrollWorld).not.toHaveBeenCalled();
});

it("mounts a static scroll world for reduced motion", async () => {
  mocks.readSceneMode.mockReturnValue("reduced");
  const { unmount } = render(
    <main id="petcare-story">
      <PetHomeExperience />
    </main>,
  );

  await waitFor(
    () => expect(mocks.mountScrollWorld).toHaveBeenCalledOnce(),
    { timeout: 5_000 },
  );
  expect(mocks.mountScrollWorld).toHaveBeenCalledWith(
    expect.any(HTMLElement),
    expect.objectContaining({ reducedMotion: true, mobile: false }),
  );
  unmount();
  expect(mocks.cleanup).toHaveBeenCalledOnce();
});

it("mounts the animated scroll world when motion is available", async () => {
  mocks.readSceneMode.mockReturnValue("animated");
  render(
    <main id="petcare-story">
      <PetHomeExperience />
    </main>,
  );

  await waitFor(
    () => expect(mocks.mountScrollWorld).toHaveBeenCalledOnce(),
    { timeout: 5_000 },
  );
  expect(mocks.mountScrollWorld).toHaveBeenCalledWith(
    expect.any(HTMLElement),
    expect.objectContaining({ reducedMotion: false, mobile: false }),
  );
});
