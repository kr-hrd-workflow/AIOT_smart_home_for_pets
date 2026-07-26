"use client";

import { useEffect, useRef } from "react";
import { mountScrollWorld } from "./scene-director";
import { SCROLL_WORLD_CONFIG } from "./scroll-world-config";

export function PetHomeCanvas({
  animated,
  compact,
}: {
  animated: boolean;
  compact: boolean;
}) {
  const stageRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const stage = stageRef.current;
    const root = document.getElementById("petcare-story");
    if (!stage || !root) return;

    return mountScrollWorld(stage, {
      config: SCROLL_WORLD_CONFIG,
      root,
      reducedMotion: !animated,
      mobile: compact,
    });
  }, [animated, compact]);

  return (
    <div className="pet-home-experience" data-compact={compact ? "true" : "false"}>
      <div className="pet-home-stage" ref={stageRef} />
    </div>
  );
}
