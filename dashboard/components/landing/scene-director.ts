import { gsap } from "gsap";
import { ScrollTrigger } from "gsap/ScrollTrigger.js";

gsap.registerPlugin(ScrollTrigger);

type Position = { x: number; y: number; z: number };

export type SceneDirectorNodes = {
  root: HTMLElement;
  camera: { position: Position; lookAt: (x: number, y: number, z: number) => void };
  target: Position;
  bowlSignal: { scale: Position };
  bedSignal: { scale: Position };
  cameraSignal: { scale: Position };
  invalidate: () => void;
};

export type StageDirectorNodes = {
  root: HTMLElement;
  stage: HTMLElement;
};

const scrollTriggerFor = (root: HTMLElement) => ({
  trigger: root,
  start: "top top",
  end: "bottom bottom",
  scrub: 1,
  invalidateOnRefresh: true,
});

export function createStageDirector(nodes: StageDirectorNodes): () => void {
  const context = gsap.context(() => {
    const timeline = gsap.timeline({
      scrollTrigger: scrollTriggerFor(nodes.root),
    });

    timeline
      .to(nodes.stage, { scale: 1.025, xPercent: -0.25, yPercent: -0.1, ease: "none" })
      .to(nodes.stage, { scale: 1.045, xPercent: -0.1, yPercent: -0.2, ease: "none" })
      .to(nodes.stage, { scale: 1.03, xPercent: 0.2, yPercent: 0.08, ease: "none" });
  }, nodes.root);

  return () => context.revert();
}

export function createSceneDirector(nodes: SceneDirectorNodes): () => void {
  const context = gsap.context(() => {
    const timeline = gsap.timeline({
      onUpdate: () => {
        nodes.camera.lookAt(nodes.target.x, nodes.target.y, nodes.target.z);
        nodes.invalidate();
      },
      scrollTrigger: scrollTriggerFor(nodes.root),
    });

    timeline
      .to(nodes.bowlSignal.scale, { x: 1.34, y: 1.34, z: 1.34, ease: "none" }, "<")
      .to(nodes.bedSignal.scale, { x: 1.38, y: 1.38, z: 1.38, ease: "none" }, "<")
      .to(nodes.cameraSignal.scale, { x: 1.3, y: 1.3, z: 1.3, ease: "none" }, "<");
  }, nodes.root);

  return () => context.revert();
}
