import { gsap } from "gsap";

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

export function createSceneDirector(nodes: SceneDirectorNodes): () => void {
  const context = gsap.context(() => {
    const timeline = gsap.timeline({
      onUpdate: () => {
        nodes.camera.lookAt(nodes.target.x, nodes.target.y, nodes.target.z);
        nodes.invalidate();
      },
      scrollTrigger: {
        trigger: nodes.root,
        start: "top top",
        end: "bottom bottom",
        scrub: 1,
        invalidateOnRefresh: true,
      },
    });

    timeline
      .to(nodes.camera.position, { x: -0.42, y: -0.18, z: 7.45, ease: "none" })
      .to(nodes.target, { x: -0.12, y: -0.06, z: 0, ease: "none" }, "<")
      .to(nodes.bowlSignal.scale, { x: 1.34, y: 1.34, z: 1.34, ease: "none" }, "<")
      .to(nodes.camera.position, { x: -0.18, y: -0.05, z: 7.1, ease: "none" })
      .to(nodes.target, { x: -0.2, y: -0.08, z: 0, ease: "none" }, "<")
      .to(nodes.bedSignal.scale, { x: 1.38, y: 1.38, z: 1.38, ease: "none" }, "<")
      .to(nodes.camera.position, { x: 0.24, y: 0.08, z: 7.55, ease: "none" })
      .to(nodes.target, { x: 0.08, y: 0.02, z: 0, ease: "none" }, "<")
      .to(nodes.cameraSignal.scale, { x: 1.3, y: 1.3, z: 1.3, ease: "none" }, "<");
  }, nodes.root);

  return () => context.revert();
}
