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

import { createSceneDirector } from "../../components/landing/scene-director";

beforeEach(() => {
  vi.clearAllMocks();
  mocks.timeline.mockReturnValue({ to: mocks.to });
  mocks.to.mockReturnThis();
});

it("creates one scoped timeline and reverts it exactly once", () => {
  const root = document.createElement("main");
  const lookAt = vi.fn();
  const invalidate = vi.fn();
  const target = { x: 0, y: 0.7, z: 0 };
  const cleanup = createSceneDirector({
    root,
    camera: { position: { x: 12, y: 9, z: 16 }, lookAt },
    target,
    bowlLight: { intensity: 0.35 },
    bedLight: { intensity: 0.25 },
    eventScreen: { scale: { x: 1, y: 1, z: 1 } },
    invalidate,
  });

  expect(mocks.context).toHaveBeenCalledOnce();
  expect(mocks.timeline).toHaveBeenCalledWith({
    onUpdate: expect.any(Function),
    scrollTrigger: expect.objectContaining({
      trigger: root,
      start: "top top",
      end: "bottom bottom",
      scrub: 1,
    }),
  });

  const [{ onUpdate }] = mocks.timeline.mock.calls[0];
  onUpdate();
  expect(lookAt).toHaveBeenLastCalledWith(target.x, target.y, target.z);
  expect(invalidate).toHaveBeenCalledOnce();

  cleanup();
  expect(mocks.revert).toHaveBeenCalledOnce();
});
