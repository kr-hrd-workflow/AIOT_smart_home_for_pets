"use client";

import {
  Component,
  lazy,
  type ReactNode,
  Suspense,
  useSyncExternalStore,
} from "react";
import { LandingFallback } from "./landing-fallback";
import {
  readCompactScene,
  readSceneMode,
  type SceneMode,
} from "./scene-quality";

const LazyPetHomeCanvas = lazy(() => import("./pet-home-canvas").then((module) => ({
  default: module.PetHomeCanvas,
})));

function subscribeSceneQuality() {
  return () => undefined;
}

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
  const mode = useSyncExternalStore<SceneMode>(
    subscribeSceneQuality,
    readSceneMode,
    () => "fallback",
  );
  const compact = useSyncExternalStore(
    subscribeSceneQuality,
    readCompactScene,
    () => true,
  );

  if (mode === "fallback") return <LandingFallback />;

  const animated = mode === "animated";
  return (
    <SceneBoundary>
      <Suspense fallback={<LandingFallback />}>
        <LazyPetHomeCanvas animated={animated} compact={compact} />
      </Suspense>
    </SceneBoundary>
  );
}
