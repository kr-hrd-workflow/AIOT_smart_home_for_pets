import { gsap } from "gsap";
import { ScrollTrigger } from "gsap/ScrollTrigger";

if (typeof window !== "undefined" && typeof window.matchMedia === "function") {
  gsap.registerPlugin(ScrollTrigger);
}

type Position = { x: number; y: number; z: number };

export type SceneDirectorNodes = {
  root: HTMLElement;
  camera: { position: Position };
  bowlLight: { intensity: number };
  bedLight: { intensity: number };
  eventScreen: { scale: Position };
};

export function createSceneDirector(nodes: SceneDirectorNodes): () => void {
  const context = gsap.context(() => {
    const timeline = gsap.timeline({
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
      .to(nodes.bowlLight, { intensity: 1.15, ease: "none" }, "<")
      .to(nodes.camera.position, { x: -1.8, y: 4.4, z: 10.2, ease: "none" })
      .to(nodes.bedLight, { intensity: 0.82, ease: "none" }, "<")
      .to(nodes.camera.position, { x: 6.5, y: 5.6, z: 12.4, ease: "none" })
      .to(nodes.eventScreen.scale, { x: 1.08, y: 1.08, z: 1.08, ease: "none" }, "<");
  }, nodes.root);

  return () => context.revert();
}
