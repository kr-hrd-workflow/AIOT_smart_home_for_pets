import "@testing-library/jest-dom/vitest";

import { render, screen, waitFor } from "@testing-library/react";
import { beforeEach, expect, it, vi } from "vitest";

const readSceneMode = vi.hoisted(() => vi.fn());

vi.mock("../../components/landing/scene-quality", async (importOriginal) => ({
  ...(await importOriginal<typeof import("../../components/landing/scene-quality")>()),
  readSceneMode,
}));
vi.mock("@react-three/fiber", () => ({
  Canvas: ({ children }: { children: React.ReactNode }) => (
    <div data-testid="three-canvas">{children}</div>
  ),
}));
vi.mock("../../components/landing/pet-home-scene", () => ({
  PetHomeScene: ({ animated }: { animated: boolean }) => (
    <div data-testid="pet-home-scene" data-animated={String(animated)} />
  ),
}));

import { PetHomeExperience } from "../../components/landing/pet-home-experience";

beforeEach(() => {
  vi.clearAllMocks();
});

it("keeps the local still when capability detection selects fallback", async () => {
  readSceneMode.mockReturnValue("fallback");
  render(<PetHomeExperience />);

  expect(screen.getByTestId("landing-fallback")).toBeInTheDocument();
  expect(readSceneMode).toHaveBeenCalled();
  expect(screen.queryByTestId("three-canvas")).not.toBeInTheDocument();
});

it("renders a static scene without the animated director for reduced motion", async () => {
  readSceneMode.mockReturnValue("reduced");
  render(<PetHomeExperience />);

  await waitFor(
    () => expect(screen.getByTestId("pet-home-scene")).toHaveAttribute(
      "data-animated",
      "false",
    ),
    { timeout: 5_000 },
  );
});

it("enables the scene director only in animated mode", async () => {
  readSceneMode.mockReturnValue("animated");
  render(<PetHomeExperience />);

  await waitFor(
    () => expect(screen.getByTestId("pet-home-scene")).toHaveAttribute(
      "data-animated",
      "true",
    ),
    { timeout: 5_000 },
  );
});
