import {
  AbsoluteFill,
  Img,
  staticFile,
  useCurrentFrame,
  useVideoConfig,
} from "remotion";

export const PETCARE_HERO_LOOPS = {
  desktop: {
    id: "PetCareHeroLoopDesktop",
    width: 1600,
    height: 900,
    fps: 24,
    durationInFrames: 288,
  },
  mobile: {
    id: "PetCareHeroLoopMobile",
    width: 720,
    height: 1280,
    fps: 24,
    durationInFrames: 288,
  },
} as const;

export type HeroLoopFormat = keyof typeof PETCARE_HERO_LOOPS;

export function heroLoopState(frame: number, durationInFrames: number) {
  const phase = (Math.PI * 2 * frame) / durationInFrames;
  const blueOpacity = (1 - Math.cos(phase)) / 2;
  const horizontalWave = Math.sin(phase);

  return {
    blueOpacity,
    scale: 1.035 + blueOpacity * 0.024,
    translateX: Math.abs(horizontalWave) < 1e-10 ? 0 : horizontalWave * 10,
    translateY: -blueOpacity * 5,
  };
}

const SOURCES: Record<HeroLoopFormat, { base: string; blue: string }> = {
  desktop: {
    base: staticFile("landing-apartment-photoreal-v3.webp"),
    blue: staticFile("landing-apartment-photoreal-v3-blue.webp"),
  },
  mobile: {
    base: staticFile("landing-apartment-photoreal-mobile-v2.webp"),
    blue: staticFile("landing-apartment-photoreal-mobile-v2-blue.webp"),
  },
};

export function PetCareHeroLoop({ format }: { format: HeroLoopFormat }) {
  const frame = useCurrentFrame();
  const { durationInFrames } = useVideoConfig();
  const state = heroLoopState(frame, durationInFrames);
  const source = SOURCES[format];
  const transform = `translate3d(${state.translateX}px, ${state.translateY}px, 0) scale(${state.scale})`;

  return (
    <AbsoluteFill style={{ overflow: "hidden", backgroundColor: "#080a0c" }}>
      <Img
        src={source.base}
        style={{
          width: "100%",
          height: "100%",
          objectFit: "cover",
          transform,
          transformOrigin: "center",
        }}
      />
      <Img
        src={source.blue}
        style={{
          position: "absolute",
          inset: 0,
          width: "100%",
          height: "100%",
          objectFit: "cover",
          opacity: state.blueOpacity,
          transform,
          transformOrigin: "center",
        }}
      />
    </AbsoluteFill>
  );
}
