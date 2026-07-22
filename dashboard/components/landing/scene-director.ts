import { gsap } from "gsap";

type Position = { x: number; y: number; z: number };

export type SceneDirectorNodes = {
  root: HTMLElement;
  camera: { position: Position; lookAt: (x: number, y: number, z: number) => void };
  target: Position;
  bowlLight: { intensity: number };
  bedLight: { intensity: number };
  eventScreen: { scale: Position };
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
      .to(nodes.camera.position, { x: -6.2, y: 5.1, z: 10.8, ease: "none" })
      .to(nodes.target, { x: -6.2, y: 0.45, z: 4.4, ease: "none" }, "<")
      .to(nodes.bowlLight, { intensity: 1.15, ease: "none" }, "<")
      .to(nodes.camera.position, { x: -1.8, y: 4.4, z: 10.2, ease: "none" })
      .to(nodes.target, { x: -5.1, y: 0.55, z: 2.3, ease: "none" }, "<")
      .to(nodes.bedLight, { intensity: 0.82, ease: "none" }, "<")
      .to(nodes.camera.position, { x: 6.5, y: 5.6, z: 12.4, ease: "none" })
      .to(nodes.target, { x: -8.7, y: 2.45, z: -0.25, ease: "none" }, "<")
      .to(nodes.eventScreen.scale, { x: 1.08, y: 1.08, z: 1.08, ease: "none" }, "<");
  }, nodes.root);

  return () => context.revert();
}
