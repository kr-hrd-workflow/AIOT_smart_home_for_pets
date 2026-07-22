import { beforeEach, expect, it, vi } from "vitest";

const mocks = vi.hoisted(() => {
  const revert = vi.fn();
  const to = vi.fn();
  const timeline = vi.fn(() => ({ to }));
  const context = vi.fn((callback: () => void) => {
    callback();
    return { revert };
  });
  return { context, registerPlugin: vi.fn(), revert, timeline, to };
});

vi.mock("gsap", () => ({
  gsap: {
    context: mocks.context,
    registerPlugin: mocks.registerPlugin,
    timeline: mocks.timeline,
  },
}));
vi.mock("gsap/ScrollTrigger", () => ({ ScrollTrigger: {} }));

import { createSceneDirector } from "../../components/landing/scene-director";

beforeEach(() => {
  vi.clearAllMocks();
  mocks.timeline.mockReturnValue({ to: mocks.to });
  mocks.to.mockReturnThis();
});

it("creates one scoped timeline and reverts it exactly once", () => {
  const root = document.createElement("main");
  const cleanup = createSceneDirector({
    root,
    camera: { position: { x: 12, y: 9, z: 16 } },
    bowlLight: { intensity: 0.35 },
    bedLight: { intensity: 0.25 },
    eventScreen: { scale: { x: 1, y: 1, z: 1 } },
  });

  expect(mocks.context).toHaveBeenCalledOnce();
  expect(mocks.timeline).toHaveBeenCalledWith({
    scrollTrigger: expect.objectContaining({
      trigger: root,
      start: "top top",
      end: "bottom bottom",
      scrub: 1,
    }),
  });
  expect(mocks.to).toHaveBeenCalledTimes(6);

  cleanup();
  expect(mocks.revert).toHaveBeenCalledOnce();
});
