"use client";

import { Canvas } from "@react-three/fiber";
import { PetHomeScene } from "./pet-home-scene";

export function PetHomeCanvas({
  animated,
  compact,
}: {
  animated: boolean;
  compact: boolean;
}) {
  return (
    <div className="pet-home-experience">
      <Canvas
        camera={{ position: [12.8, 9.4, 17.2], fov: 39, near: 0.1, far: 80 }}
        dpr={[1, compact ? 1.15 : animated ? 1.5 : 1.2]}
        frameloop="demand"
        gl={{
          antialias: animated && !compact,
          powerPreference: compact ? "low-power" : "high-performance",
        }}
        shadows={animated && !compact}
      >
        <PetHomeScene animated={animated} />
      </Canvas>
    </div>
  );
}
