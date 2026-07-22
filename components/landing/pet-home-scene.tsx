"use client";

import { useEffect, useRef } from "react";
import { useThree } from "@react-three/fiber";
import { gsap } from "gsap";
import { ScrollTrigger } from "gsap/ScrollTrigger.js";
import type { Group, PointLight } from "three";
import { createSceneDirector } from "./scene-director";

gsap.registerPlugin(ScrollTrigger);

type Position = [number, number, number];

function Block({
  position,
  size,
  color,
  rotation = [0, 0, 0],
  castShadow = false,
}: {
  position: Position;
  size: Position;
  color: string;
  rotation?: Position;
  castShadow?: boolean;
}) {
  return (
    <mesh
      position={position}
      rotation={rotation}
      castShadow={castShadow}
      receiveShadow
    >
      <boxGeometry args={size} />
      <meshStandardMaterial color={color} roughness={0.82} metalness={0.05} />
    </mesh>
  );
}

function Chair({ position, rotation = [0, 0, 0] }: { position: Position; rotation?: Position }) {
  return (
    <group position={position} rotation={rotation}>
      <Block position={[0, 0.42, 0]} size={[0.75, 0.16, 0.72]} color="#33383a" />
      <Block position={[0, 0.9, 0.3]} size={[0.75, 0.92, 0.14]} color="#292e31" />
      {[-0.27, 0.27].flatMap((x) =>
        [-0.25, 0.25].map((z) => (
          <Block key={`${x}-${z}`} position={[x, 0.18, z]} size={[0.09, 0.44, 0.09]} color="#202528" />
        )),
      )}
    </group>
  );
}

function Sofa() {
  return (
    <group position={[1.1, 0, 0.7]} rotation={[0, -0.1, 0]}>
      <Block position={[0, 0.48, 0]} size={[4.8, 0.55, 1.55]} color="#625c55" castShadow />
      <Block position={[0, 1.05, -0.58]} size={[4.8, 1.05, 0.34]} color="#514d48" castShadow />
      <Block position={[-2.25, 0.9, 0]} size={[0.3, 0.9, 1.5]} color="#4d4945" />
      <Block position={[2.25, 0.9, 0]} size={[0.3, 0.9, 1.5]} color="#4d4945" />
      <Block position={[-1.2, 0.88, 0.25]} size={[1.85, 0.28, 1.05]} color="#716a62" />
      <Block position={[1.0, 0.88, 0.25]} size={[1.85, 0.28, 1.05]} color="#716a62" />
      <Block position={[1.65, 0.46, 1.2]} size={[1.45, 0.5, 2.5]} color="#625c55" castShadow />
    </group>
  );
}

const CITY_LIGHTS: ReadonlyArray<[number, number, string]> = [
  [-5.6, 1.8, "#d8a85d"],
  [-4.9, 3.4, "#6e9ca4"],
  [-3.9, 2.5, "#d8a85d"],
  [-2.8, 1.5, "#789ba2"],
  [-1.8, 3.7, "#d8a85d"],
  [-0.7, 2.1, "#82aeb5"],
  [0.3, 3.2, "#d8a85d"],
] as const;

function CityWindowWall() {
  return (
    <group>
      <mesh position={[-2.7, 2.8, -5.78]}>
        <boxGeometry args={[8.4, 3.8, 0.08]} />
        <meshStandardMaterial
          color="#111c21"
          emissive="#0c1920"
          emissiveIntensity={0.52}
          metalness={0.18}
          roughness={0.28}
        />
      </mesh>
      {[-6.1, -4.7, -3.3, -1.9, -0.5, 0.9].map((x) => (
        <Block key={x} position={[x, 2.8, -5.68]} size={[0.07, 3.8, 0.12]} color="#30373a" />
      ))}
      {CITY_LIGHTS.map(([x, y, color]) => (
        <mesh key={`${x}-${y}`} position={[x, y, -5.62]}>
          <boxGeometry args={[0.18, 0.1, 0.03]} />
          <meshStandardMaterial color={color} emissive={color} emissiveIntensity={1.1} />
        </mesh>
      ))}
    </group>
  );
}

function KitchenAndDining() {
  return (
    <group>
      <Block position={[4.5, 1.1, -5.45]} size={[7.2, 2.2, 0.75]} color="#242a2e" />
      <Block position={[4.5, 2.7, -5.68]} size={[7.2, 0.85, 0.3]} color="#1b2024" />
      <Block position={[4.5, 0.85, -2.8]} size={[4.3, 1.7, 1.7]} color="#30363a" castShadow />
      <Block position={[4.5, 1.75, -2.8]} size={[4.5, 0.12, 1.85]} color="#77736b" />
      {[-1.25, 0, 1.25].map((offset) => (
        <Chair key={offset} position={[4.5 + offset, 0, -1.55]} rotation={[0, Math.PI, 0]} />
      ))}
      <Block position={[-1.7, 0.82, -3.55]} size={[3.8, 0.18, 1.65]} color="#6c5541" castShadow />
      <Block position={[-1.7, 0.42, -3.55]} size={[0.22, 0.84, 0.22]} color="#292d30" />
      {[-3.2, -0.2].map((x) => (
        <Chair key={`${x}-front`} position={[x, 0, -2.45]} rotation={[0, Math.PI, 0]} />
      ))}
      {[-3.2, -0.2].map((x) => (
        <Chair key={`${x}-back`} position={[x, 0, -4.65]} />
      ))}
    </group>
  );
}

function EntryAndStorage() {
  return (
    <group>
      <Block position={[7.75, 1.35, 3.55]} size={[1.45, 2.7, 3.9]} color="#252b2f" />
      <Block position={[6.2, 1.2, 5.55]} size={[4.5, 2.4, 0.45]} color="#292b2c" />
      <Block position={[6.0, 1.55, 3.9]} size={[0.14, 3.1, 2.5]} color="#22282c" />
      <Block position={[6.0, 1.55, 3.9]} size={[0.2, 2.65, 1.9]} color="#45423e" />
      <Block position={[6.0, 1.55, 3.9]} size={[0.22, 2.35, 0.1]} color="#151a1e" />
    </group>
  );
}

function PetAndRestZone() {
  return (
    <group position={[-5.2, 0, 2.4]}>
      <mesh position={[0, 0.25, 0]} receiveShadow>
        <cylinderGeometry args={[1.55, 1.7, 0.45, 32]} />
        <meshStandardMaterial color="#6d655c" roughness={0.95} />
      </mesh>
      <mesh position={[0, 0.56, 0]} scale={[1.18, 0.5, 0.72]} castShadow>
        <sphereGeometry args={[0.72, 24, 18]} />
        <meshStandardMaterial color="#b89268" roughness={0.94} />
      </mesh>
      <mesh position={[-0.7, 0.82, 0.1]} castShadow>
        <sphereGeometry args={[0.43, 24, 18]} />
        <meshStandardMaterial color="#bb946a" roughness={0.94} />
      </mesh>
      <mesh position={[-0.98, 0.82, 0.12]} scale={[0.5, 0.28, 0.34]}>
        <sphereGeometry args={[0.42, 20, 14]} />
        <meshStandardMaterial color="#8e6d4e" roughness={0.95} />
      </mesh>
      <mesh position={[-0.56, 1.16, -0.12]} rotation={[0.35, 0, 0.4]}>
        <coneGeometry args={[0.2, 0.45, 12]} />
        <meshStandardMaterial color="#8c684a" roughness={0.95} />
      </mesh>
      <mesh position={[-0.56, 1.15, 0.32]} rotation={[-0.35, 0, 0.4]}>
        <coneGeometry args={[0.2, 0.45, 12]} />
        <meshStandardMaterial color="#8c684a" roughness={0.95} />
      </mesh>
    </group>
  );
}

function FeedingZone() {
  return (
    <group position={[-6.65, 0, 4.75]}>
      <mesh position={[0, 0.18, 0]} receiveShadow>
        <cylinderGeometry args={[0.72, 0.55, 0.36, 32]} />
        <meshStandardMaterial color="#b8b1a5" roughness={0.68} />
      </mesh>
      <mesh position={[1.15, 0.42, 0]} castShadow>
        <cylinderGeometry args={[0.22, 0.25, 0.84, 24]} />
        <meshStandardMaterial color="#e1ded4" roughness={0.55} />
      </mesh>
      <mesh position={[1.15, 0.45, 0.22]}>
        <sphereGeometry args={[0.045, 12, 8]} />
        <meshStandardMaterial color="#d2a75d" emissive="#d2a75d" emissiveIntensity={1.2} />
      </mesh>
    </group>
  );
}

export function PetHomeScene({ animated }: { animated: boolean }) {
  const camera = useThree((state) => state.camera);
  const invalidate = useThree((state) => state.invalidate);
  const cameraTarget = useRef({ x: 0, y: 0.7, z: 0 });
  const bowlLight = useRef<PointLight>(null);
  const bedLight = useRef<PointLight>(null);
  const eventScreen = useRef<Group>(null);

  useEffect(() => {
    camera.lookAt(cameraTarget.current.x, cameraTarget.current.y, cameraTarget.current.z);
    invalidate();
    if (!animated || !bowlLight.current || !bedLight.current || !eventScreen.current) {
      return;
    }
    const root = document.getElementById("petcare-story");
    if (!root) return;
    return createSceneDirector({
      root,
      camera,
      target: cameraTarget.current,
      bowlLight: bowlLight.current,
      bedLight: bedLight.current,
      eventScreen: eventScreen.current,
      invalidate,
    });
  }, [animated, camera, invalidate]);

  return (
    <>
      <color attach="background" args={["#090b0d"]} />
      <fog attach="fog" args={["#090b0d", 23, 38]} />
      <ambientLight intensity={0.58} color="#b9ced0" />
      <directionalLight
        position={[7, 13, 8]}
        intensity={1.35}
        color="#d8d8d2"
        castShadow
        shadow-mapSize-width={1024}
        shadow-mapSize-height={1024}
      />
      <pointLight ref={bowlLight} position={[-6.3, 2.1, 4.5]} intensity={0.35} color="#d2a75d" distance={5} />
      <pointLight ref={bedLight} position={[-4.5, 3.0, 1.8]} intensity={0.25} color="#78bac7" distance={6} />

      <group>
        <Block position={[0, -0.3, 0]} size={[18, 0.6, 12]} color="#594638" />
        <Block position={[0, 2.6, -6]} size={[18, 5.8, 0.35]} color="#17191b" />
        <Block position={[-9, 2.6, 0]} size={[0.35, 5.8, 12]} color="#17191b" />
        <Block position={[9, 2.6, 0]} size={[0.35, 5.8, 12]} color="#17191b" />

        <CityWindowWall />

        <KitchenAndDining />
        <Sofa />
        <Block position={[-0.15, -0.01, 1.25]} size={[7.2, 0.08, 4.5]} color="#3f3a35" />
        <Block position={[0.8, 0.35, 2.65]} size={[2.5, 0.22, 1.3]} color="#383735" castShadow />
        <EntryAndStorage />
        <PetAndRestZone />
        <FeedingZone />

        <group ref={eventScreen} position={[-8.76, 2.5, -0.25]} rotation={[0, Math.PI / 2, 0]}>
          <Block position={[0, 0, 0]} size={[0.16, 2.15, 3.4]} color="#0e1418" />
          <mesh position={[0.1, 0, 0]} rotation={[0, Math.PI / 2, 0]}>
            <planeGeometry args={[3.05, 1.8]} />
            <meshStandardMaterial color="#24343a" emissive="#17272e" emissiveIntensity={0.42} />
          </mesh>
        </group>

        <group position={[-8.55, 3.75, 3.6]} rotation={[0, Math.PI / 2, 0]}>
          <mesh castShadow>
            <cylinderGeometry args={[0.2, 0.2, 0.7, 24]} />
            <meshStandardMaterial color="#727a7d" metalness={0.55} roughness={0.28} />
          </mesh>
          <mesh position={[0, -0.35, 0]}>
            <boxGeometry args={[0.2, 0.55, 0.3]} />
            <meshStandardMaterial color="#252b2f" />
          </mesh>
        </group>
      </group>
    </>
  );
}
