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
        camera={{ position: [0, 0, 8], fov: 42, near: 0.1, far: 20 }}
        dpr={[1, compact ? 1.15 : animated ? 1.5 : 1.2]}
        frameloop="demand"
        gl={{
          antialias: animated && !compact,
          alpha: false,
          powerPreference: compact ? "low-power" : "high-performance",
        }}
      >
        <PetHomeScene animated={animated && !compact} compact={compact} />
      </Canvas>
    </div>
  );
}
