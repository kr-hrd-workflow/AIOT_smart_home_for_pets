"use client";

import { Canvas } from "@react-three/fiber";
import { Component, type ReactNode, useEffect, useState } from "react";
import { LandingFallback } from "./landing-fallback";
import { PetHomeScene } from "./pet-home-scene";
import { readSceneMode, type SceneMode } from "./scene-quality";

class SceneBoundary extends Component<
  { children: ReactNode },
  { failed: boolean }
> {
  state = { failed: false };

  static getDerivedStateFromError() {
    return { failed: true };
  }

  render() {
    return this.state.failed ? <LandingFallback /> : this.props.children;
  }
}

export function PetHomeExperience() {
  const [mode, setMode] = useState<SceneMode | null>(null);

  useEffect(() => {
    setMode(readSceneMode());
  }, []);

  if (mode === null || mode === "fallback") return <LandingFallback />;

  const animated = mode === "animated";
  return (
    <SceneBoundary>
      <div className="pet-home-experience">
        <Canvas
          camera={{ position: [12, 9, 16], fov: 42, near: 0.1, far: 80 }}
          dpr={[1, animated ? 1.5 : 1.2]}
          frameloop={animated ? "always" : "demand"}
          gl={{ antialias: animated, powerPreference: "high-performance" }}
          shadows={animated}
        >
          <PetHomeScene animated={animated} />
        </Canvas>
      </div>
    </SceneBoundary>
  );
}
