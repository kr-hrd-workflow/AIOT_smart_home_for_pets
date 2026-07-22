import { ThreeCanvas } from "@remotion/three";
import {
  AbsoluteFill,
  Easing,
  Sequence,
  interpolate,
  useCurrentFrame,
  useVideoConfig,
} from "remotion";

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

type Position = [number, number, number];

function Block({
  position,
  size,
  color,
}: {
  position: Position;
  size: Position;
  color: string;
}) {
  return (
    <mesh position={position} castShadow receiveShadow>
      <boxGeometry args={size} />
      <meshStandardMaterial color={color} roughness={0.82} metalness={0.04} />
    </mesh>
  );
}

function KoreanApartment() {
  const frame = useCurrentFrame();
  const scene = sceneAtFrame(frame);
  const rotation = interpolate(frame, [0, 449], [-0.28, 0.18], {
    extrapolateLeft: "clamp",
    extrapolateRight: "clamp",
    easing: Easing.bezier(0.16, 1, 0.3, 1),
  });
  const feedingLift = scene === "feeding" ? 0.55 : 0.12;
  const restLift = scene === "rest" ? 0.5 : 0.12;
  const screenLift = scene === "events" ? 0.65 : 0.16;

  return (
    <group position={[3, -0.7, 0]} rotation={[0, rotation, 0]} scale={0.72}>
      <Block position={[0, -0.25, 0]} size={[18, 0.5, 11]} color="#65594b" />
      <Block position={[0, 2.5, -5.5]} size={[18, 5.5, 0.3]} color="#171d21" />
      <Block position={[-9, 2.5, 0]} size={[0.3, 5.5, 11]} color="#171d21" />
      <Block position={[9, 2.5, 0]} size={[0.3, 5.5, 11]} color="#171d21" />

      <mesh position={[-1.5, 2.75, -5.3]}>
        <boxGeometry args={[6.5, 3.7, 0.08]} />
          <meshStandardMaterial color="#20404b" emissive="#173842" emissiveIntensity={0.62} />
      </mesh>
      {[-4.7, -3.1, -1.5, 0.1, 1.7].map((x) => (
        <Block key={x} position={[x, 2.75, -5.2]} size={[0.08, 3.8, 0.12]} color="#303a3f" />
      ))}

      <Block position={[4.6, 1.05, -5.1]} size={[6.6, 2.1, 0.7]} color="#252b2f" />
      <Block position={[4.5, 0.82, -2.65]} size={[4.1, 1.65, 1.65]} color="#30363a" />
      <Block position={[4.5, 1.7, -2.65]} size={[4.3, 0.12, 1.8]} color="#77736b" />

      <Block position={[-1.75, 0.78, -3.35]} size={[3.8, 0.18, 1.55]} color="#6b5543" />
      {[-3.15, -0.35].flatMap((x) =>
        [-2.35, -4.35].map((z) => (
          <Block key={`${x}-${z}`} position={[x, 0.48, z]} size={[0.72, 0.95, 0.72]} color="#34393b" />
        )),
      )}

      <group position={[1.1, 0, 0.45]}>
        <Block position={[0, 0.46, 0]} size={[4.8, 0.55, 1.5]} color="#55524d" />
        <Block position={[0, 1.02, -0.58]} size={[4.8, 1.0, 0.3]} color="#474642" />
      </group>
      <Block position={[0.5, 0.28, 2.55]} size={[2.5, 0.22, 1.2]} color="#353533" />

      <Block position={[7.6, 1.35, 3.4]} size={[1.5, 2.7, 3.6]} color="#252b2f" />
      <Block position={[6.15, 1.2, 5.25]} size={[4.4, 2.4, 0.4]} color="#303539" />
      <Block position={[6.0, 1.5, 3.55]} size={[0.18, 3.0, 2.35]} color="#3a4347" />

      <group position={[-5.15, 0, 2.2]}>
        <mesh position={[0, 0.24, 0]}>
          <cylinderGeometry args={[1.5, 1.65, 0.45, 32]} />
          <meshStandardMaterial color="#6d655c" roughness={0.96} />
        </mesh>
        <mesh position={[0, 0.56, 0]} scale={[1.18, 0.5, 0.72]}>
          <sphereGeometry args={[0.72, 24, 18]} />
          <meshStandardMaterial color="#b89268" roughness={0.95} />
        </mesh>
        <mesh position={[-0.72, 0.82, 0.08]}>
          <sphereGeometry args={[0.43, 24, 18]} />
          <meshStandardMaterial color="#bb946a" roughness={0.95} />
        </mesh>
      </group>

      <group position={[-6.55, feedingLift, 4.35]}>
        <mesh>
          <cylinderGeometry args={[0.72, 0.55, 0.36, 32]} />
          <meshStandardMaterial color="#b8b1a5" roughness={0.68} />
        </mesh>
        <mesh position={[1.1, 0.24, 0]}>
          <cylinderGeometry args={[0.22, 0.25, 0.84, 24]} />
          <meshStandardMaterial color="#e1ded4" roughness={0.55} />
        </mesh>
      </group>

      <pointLight position={[-6.2, 2.0, 4.2]} intensity={feedingLift} color="#d2a75d" distance={5} />
      <pointLight position={[-4.8, 2.8, 2.0]} intensity={restLift} color="#78bac7" distance={6} />

      <group position={[-8.78, 2.45, -0.1]} rotation={[0, Math.PI / 2, 0]} scale={1 + screenLift * 0.06}>
        <Block position={[0, 0, 0]} size={[0.16, 2.1, 3.35]} color="#0e1418" />
        <mesh position={[0.1, 0, 0]} rotation={[0, Math.PI / 2, 0]}>
          <planeGeometry args={[3, 1.75]} />
          <meshStandardMaterial color="#24343a" emissive="#17272e" emissiveIntensity={screenLift} />
        </mesh>
      </group>
    </group>
  );
}

const messages: Record<PromoScene, { title: string; body: string }> = {
  home: {
    title: "집을 비운 시간도 안심하세요",
    body: "50평 한국 가정의 식사와 휴식 공간을 하나의 PetCare 홈으로 연결합니다.",
  },
  feeding: {
    title: "식사 순간을 함께 확인합니다",
    body: "Pico 2 W 센서와 카메라가 같은 변화를 감지할 때 필요한 장면을 준비합니다.",
  },
  rest: {
    title: "달라진 휴식을 발견합니다",
    body: "침대 센서와 영상이 어긋나면 확인할 수 있는 경고를 남깁니다.",
  },
  events: {
    title: "이벤트만 기록하고 7일 후 삭제합니다",
    body: "계정별로 분리된 짧은 클립을 어디서든 안전하게 확인하세요.",
  },
};

function Message({ scene }: { scene: PromoScene }) {
  const frame = useCurrentFrame();
  const opacity = interpolate(frame, [0, 16, 94, 116], [0, 1, 1, 0], {
    extrapolateLeft: "clamp",
    extrapolateRight: "clamp",
    easing: Easing.bezier(0.16, 1, 0.3, 1),
  });
  const translate = interpolate(frame, [0, 20], [36, 0], {
    extrapolateLeft: "clamp",
    extrapolateRight: "clamp",
    easing: Easing.bezier(0.16, 1, 0.3, 1),
  });
  const message = messages[scene];

  return (
    <div
      style={{
        position: "absolute",
        left: 110,
        bottom: 100,
        width: 760,
        padding: "42px 48px",
        border: "1px solid rgba(198, 220, 219, 0.28)",
        borderRadius: 22,
        backgroundColor: "rgba(15, 20, 25, 0.92)",
        color: "#edf3f2",
        opacity,
        translate: `0 ${translate}px`,
        fontFamily: "Arial, sans-serif",
      }}
    >
      <div
        style={{
          color: "#d2a75d",
          fontSize: 32,
          fontWeight: 700,
          marginBottom: 18,
        }}
      >
        PetCare
      </div>
      <div
        style={{
          fontSize: 86,
          fontWeight: 760,
          lineHeight: 1.04,
          letterSpacing: "-0.055em",
        }}
      >
        {message.title}
      </div>
      <div
        style={{
          color: "#aebdbc",
          fontSize: 44,
          lineHeight: 1.45,
          marginTop: 24,
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
      <ThreeCanvas
        width={width}
        height={height}
        camera={{ position: [0, 7, 17], rotation: [-0.35, 0, 0], fov: 42 }}
        shadows
      >
        <ambientLight intensity={1.15} color="#c7e1e4" />
        <directionalLight position={[8, 13, 9]} intensity={2.2} color="#e0eceb" castShadow />
        <KoreanApartment />
      </ThreeCanvas>
      <Sequence from={0} durationInFrames={120}><Message scene="home" /></Sequence>
      <Sequence from={120} durationInFrames={120}><Message scene="feeding" /></Sequence>
      <Sequence from={240} durationInFrames={120}><Message scene="rest" /></Sequence>
      <Sequence from={360} durationInFrames={90}><Message scene="events" /></Sequence>
    </AbsoluteFill>
  );
}
