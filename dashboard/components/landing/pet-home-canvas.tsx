"use client";

import { useEffect, useRef, useState } from "react";
import { Canvas } from "@react-three/fiber";
import { PetHomeScene } from "./pet-home-scene";
import { createStageDirector } from "./scene-director";

const DESKTOP_FILM = "/landing-apartment-cinematic-loop.mp4";
const MOBILE_FILM = "/landing-apartment-cinematic-loop-mobile.mp4";
const DESKTOP_POSTER = "/landing-apartment-photoreal-v3.webp";
const MOBILE_POSTER = "/landing-apartment-photoreal-mobile-v2.webp";

export function PetHomeCanvas({
  animated,
  compact,
}: {
  animated: boolean;
  compact: boolean;
}) {
  const stageRef = useRef<HTMLDivElement>(null);
  const [filmReady, setFilmReady] = useState(false);
  const film = compact ? MOBILE_FILM : DESKTOP_FILM;
  const poster = compact ? MOBILE_POSTER : DESKTOP_POSTER;

  useEffect(() => {
    if (!animated || compact || !stageRef.current) return;
    const root = document.getElementById("petcare-story");
    if (!root) return;
    return createStageDirector({ root, stage: stageRef.current });
  }, [animated, compact]);

  return (
    <div className="pet-home-experience" data-compact={compact ? "true" : "false"}>
      <div className="pet-home-stage" ref={stageRef}>
        <picture className="pet-home-poster">
          <source media="(max-width: 767px)" srcSet={MOBILE_POSTER} />
          <img src={DESKTOP_POSTER} alt="" width="1679" height="945" />
        </picture>
        {animated ? (
          <video
            aria-hidden="true"
            autoPlay
            className="pet-home-film"
            data-ready={filmReady ? "true" : "false"}
            disablePictureInPicture
            loop
            muted
            onCanPlay={() => setFilmReady(true)}
            onLoadedData={() => setFilmReady(true)}
            onPlaying={() => setFilmReady(true)}
            playsInline
            poster={poster}
            preload="metadata"
            src={film}
            tabIndex={-1}
          />
        ) : null}
        <Canvas
          className="pet-home-signal-canvas"
          camera={{ position: [0, 0, 8], fov: 42, near: 0.1, far: 20 }}
          dpr={[1, compact ? 1.15 : animated ? 1.5 : 1.2]}
          frameloop="demand"
          gl={{
            antialias: animated && !compact,
            alpha: animated,
            powerPreference: compact ? "low-power" : "high-performance",
          }}
        >
          <PetHomeScene
            animated={animated && !compact}
            compact={compact}
            showPhoto={!animated}
            stageRef={stageRef}
          />
        </Canvas>
      </div>
    </div>
  );
}
