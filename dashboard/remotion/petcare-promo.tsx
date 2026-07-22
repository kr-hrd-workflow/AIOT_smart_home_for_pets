import { loadFont } from "@remotion/fonts";
import { ThreeCanvas } from "@remotion/three";
import {
  AbsoluteFill,
  Easing,
  Img,
  Sequence,
  interpolate,
  staticFile,
  useCurrentFrame,
  useVideoConfig,
} from "remotion";

const FONT_FAMILY = "PetCare Pretendard";

if (typeof FontFace !== "undefined") {
  void loadFont({
    family: FONT_FAMILY,
    url: staticFile("fonts/Pretendard-Bold.woff2"),
    format: "woff2",
    weight: "700",
    display: "block",
  });
}

export const PETCARE_PROMO = {
  id: "PetCarePromo",
  width: 1920,
  height: 1080,
  fps: 30,
  durationInFrames: 450,
} as const;

export type PromoScene = "home" | "feeding" | "rest" | "events";

export function sceneAtFrame(frame: number): PromoScene {
  if (frame < 120) return "home";
  if (frame < 240) return "feeding";
  if (frame < 360) return "rest";
  return "events";
}

const SCENE_START: Record<PromoScene, number> = {
  home: 0,
  feeding: 120,
  rest: 240,
  events: 360,
};

const SCENE_DURATION: Record<PromoScene, number> = {
  home: 120,
  feeding: 120,
  rest: 120,
  events: 90,
};

type Position = [number, number, number];

const ACCENTS: ReadonlyArray<{
  scene: Exclude<PromoScene, "home">;
  position: Position;
  color: string;
}> = [
  { scene: "feeding", position: [-4.7, -2.45, 0], color: "#e3b36a" },
  { scene: "rest", position: [-3.35, -1.55, 0], color: "#8bc7cd" },
  { scene: "events", position: [-5.05, 1.55, 0], color: "#c8dcda" },
];

function ApartmentBackdrop() {
  const frame = useCurrentFrame();
  const scale = interpolate(frame, [0, PETCARE_PROMO.durationInFrames - 1], [1.02, 1.075], {
    extrapolateLeft: "clamp",
    extrapolateRight: "clamp",
    easing: Easing.bezier(0.45, 0, 0.55, 1),
  });
  const translateX = interpolate(frame, [0, PETCARE_PROMO.durationInFrames - 1], [12, -20], {
    extrapolateLeft: "clamp",
    extrapolateRight: "clamp",
    easing: Easing.bezier(0.45, 0, 0.55, 1),
  });

  return (
    <AbsoluteFill style={{ overflow: "hidden", backgroundColor: "#0b0f13" }}>
      <Img
        src={staticFile("og.png")}
        style={{
          width: "100%",
          height: "100%",
          objectFit: "cover",
          objectPosition: "center",
          scale,
          translate: `${translateX}px 0`,
        }}
      />
      <AbsoluteFill
        style={{
          background:
            "linear-gradient(90deg, rgba(5, 8, 11, 0.08) 0%, rgba(5, 8, 11, 0.2) 42%, rgba(5, 8, 11, 0.9) 76%, rgba(5, 8, 11, 0.97) 100%)",
        }}
      />
      <AbsoluteFill
        style={{
          background:
            "linear-gradient(180deg, rgba(5, 8, 11, 0.34) 0%, transparent 32%, transparent 72%, rgba(5, 8, 11, 0.42) 100%)",
        }}
      />
    </AbsoluteFill>
  );
}

function SensorAccents() {
  const frame = useCurrentFrame();
  const scene = sceneAtFrame(frame);
  const localFrame = frame - SCENE_START[scene];
  const duration = SCENE_DURATION[scene];
  const pulse = interpolate(localFrame, [0, 16, duration - 18, duration - 1], [0.35, 1, 1, 0.35], {
    extrapolateLeft: "clamp",
    extrapolateRight: "clamp",
    easing: Easing.bezier(0.16, 1, 0.3, 1),
  });
  const rotation = interpolate(frame, [0, PETCARE_PROMO.durationInFrames - 1], [-0.16, 0.18], {
    extrapolateLeft: "clamp",
    extrapolateRight: "clamp",
    easing: Easing.bezier(0.45, 0, 0.55, 1),
  });

  return (
    <group rotation={[0, 0, rotation]}>
      {ACCENTS.map((accent) => {
        const strength = scene === "home" ? 0.44 : scene === accent.scene ? pulse : 0.12;
        return (
          <group
            key={accent.scene}
            position={accent.position}
            scale={0.82 + strength * 0.24}
          >
            <mesh>
              <torusGeometry args={[0.46, 0.035, 16, 64]} />
              <meshStandardMaterial
                color={accent.color}
                emissive={accent.color}
                emissiveIntensity={1.8 * strength}
                metalness={0.18}
                opacity={0.22 + strength * 0.64}
                roughness={0.42}
                transparent
              />
            </mesh>
            <mesh scale={0.72 + strength * 0.4}>
              <sphereGeometry args={[0.075, 24, 24]} />
              <meshStandardMaterial
                color={accent.color}
                emissive={accent.color}
                emissiveIntensity={2.4 * strength}
                opacity={0.4 + strength * 0.6}
                transparent
              />
            </mesh>
          </group>
        );
      })}
    </group>
  );
}

const messages: Record<PromoScene, { title: string; body: string }> = {
  home: {
    title: "집을 비운 시간도 안심하세요",
    body: "넓은 한국 가정의 식사와 휴식 공간을 하나의 PetCare 홈으로 연결합니다.",
  },
  feeding: {
    title: "식사 순간을 함께 확인합니다",
    body: "Pico 2 W 센서와 카메라가 같은 변화를 감지하면 필요한 장면을 준비합니다.",
  },
  rest: {
    title: "달라진 휴식을 발견합니다",
    body: "침대 센서와 영상이 어긋나면 보호자가 확인할 수 있는 경고를 남깁니다.",
  },
  events: {
    title: "필요한 순간만 남깁니다",
    body: "계정별로 분리된 짧은 클립을 안전하게 확인하고 7일 뒤 자동 삭제합니다.",
  },
};

function Message({ scene, durationInFrames }: { scene: PromoScene; durationInFrames: number }) {
  const frame = useCurrentFrame();
  const opacity = interpolate(
    frame,
    [0, 14, durationInFrames - 22, durationInFrames - 1],
    [0, 1, 1, 0],
    {
      extrapolateLeft: "clamp",
      extrapolateRight: "clamp",
      easing: Easing.bezier(0.16, 1, 0.3, 1),
    },
  );
  const translateY = interpolate(frame, [0, 20], [34, 0], {
    extrapolateLeft: "clamp",
    extrapolateRight: "clamp",
    easing: Easing.bezier(0.16, 1, 0.3, 1),
  });
  const message = messages[scene];

  return (
    <div
      style={{
        position: "absolute",
        top: 100,
        right: 104,
        bottom: 100,
        width: 760,
        display: "flex",
        flexDirection: "column",
        justifyContent: "center",
        color: "#f3f5f4",
        opacity,
        translate: `0 ${translateY}px`,
        fontFamily: FONT_FAMILY,
        textShadow: "0 8px 30px rgba(0, 0, 0, 0.5)",
      }}
    >
      <div
        style={{
          color: "#e3b36a",
          fontSize: 32,
          fontWeight: 700,
          letterSpacing: "0.06em",
          marginBottom: 22,
        }}
      >
        PetCare
      </div>
      <div
        style={{
          fontSize: 92,
          fontWeight: 700,
          lineHeight: 1.12,
          letterSpacing: "-0.045em",
          wordBreak: "keep-all",
        }}
      >
        {message.title}
      </div>
      <div
        style={{
          color: "#d7dfdd",
          fontSize: 44,
          fontWeight: 700,
          lineHeight: 1.48,
          marginTop: 28,
          maxWidth: 720,
          wordBreak: "keep-all",
        }}
      >
        {message.body}
      </div>
    </div>
  );
}

export function PetCarePromo() {
  const { width, height } = useVideoConfig();

  return (
    <AbsoluteFill style={{ backgroundColor: "#0b0f13" }}>
      <ApartmentBackdrop />
      <ThreeCanvas
        width={width}
        height={height}
        camera={{ position: [0, 0, 12], fov: 34 }}
        gl={{ alpha: true }}
      >
        <ambientLight intensity={0.7} color="#c8dcda" />
        <pointLight position={[-4, 1, 5]} intensity={2.4} color="#e3b36a" />
        <SensorAccents />
      </ThreeCanvas>
      <Sequence
        from={0}
        durationInFrames={120}
        premountFor={PETCARE_PROMO.fps}
        layout="none"
      >
        <Message scene="home" durationInFrames={120} />
      </Sequence>
      <Sequence
        from={120}
        durationInFrames={120}
        premountFor={PETCARE_PROMO.fps}
        layout="none"
      >
        <Message scene="feeding" durationInFrames={120} />
      </Sequence>
      <Sequence
        from={240}
        durationInFrames={120}
        premountFor={PETCARE_PROMO.fps}
        layout="none"
      >
        <Message scene="rest" durationInFrames={120} />
      </Sequence>
      <Sequence
        from={360}
        durationInFrames={90}
        premountFor={PETCARE_PROMO.fps}
        layout="none"
      >
        <Message scene="events" durationInFrames={90} />
      </Sequence>
    </AbsoluteFill>
  );
}
