"use client";

import { useEffect, useMemo, useRef } from "react";
import { useTexture } from "@react-three/drei";
import { useThree } from "@react-three/fiber";
import {
  AdditiveBlending,
  LinearFilter,
  SRGBColorSpace,
  type Group,
} from "three";
import { createSceneDirector } from "./scene-director";

const DESKTOP_PHOTO_URL = "/landing-apartment-photoreal-v3.webp";
const MOBILE_PHOTO_URL = "/landing-apartment-photoreal-mobile-v2.webp";
const DESKTOP_PHOTO_ASPECT = 1679 / 945;
const MOBILE_PHOTO_ASPECT = 945 / 1679;

type Position = [number, number, number];

function SignalHalo({
  color,
  groupRef,
  position,
  size,
}: {
  color: string;
  groupRef: React.RefObject<Group | null>;
  position: Position;
  size: number;
}) {
  return (
    <group ref={groupRef} position={position}>
      <mesh>
        <ringGeometry args={[size * 0.72, size, 96]} />
        <meshBasicMaterial
          blending={AdditiveBlending}
          color={color}
          depthWrite={false}
          opacity={0.58}
          toneMapped={false}
          transparent
        />
      </mesh>
      <mesh>
        <ringGeometry args={[size * 1.18, size * 1.26, 96]} />
        <meshBasicMaterial
          blending={AdditiveBlending}
          color={color}
          depthWrite={false}
          opacity={0.18}
          toneMapped={false}
          transparent
        />
      </mesh>
    </group>
  );
}

function mapPhotoPoint(
  u: number,
  v: number,
  width: number,
  height: number,
): Position {
  return [(u - 0.5) * width, (0.5 - v) * height, 0.08];
}

export function PetHomeScene({
  animated,
  compact,
  showPhoto,
  stageRef,
}: {
  animated: boolean;
  compact: boolean;
  showPhoto: boolean;
  stageRef: React.RefObject<HTMLDivElement | null>;
}) {
  const camera = useThree((state) => state.camera);
  const invalidate = useThree((state) => state.invalidate);
  const viewport = useThree((state) => state.viewport);
  const photoUrl = compact ? MOBILE_PHOTO_URL : DESKTOP_PHOTO_URL;
  const photoAspect = compact ? MOBILE_PHOTO_ASPECT : DESKTOP_PHOTO_ASPECT;
  const texture = useTexture(photoUrl);
  const photoTexture = useMemo(() => {
    const nextTexture = texture.clone();
    nextTexture.colorSpace = SRGBColorSpace;
    nextTexture.minFilter = LinearFilter;
    nextTexture.magFilter = LinearFilter;
    nextTexture.needsUpdate = true;
    return nextTexture;
  }, [texture]);
  const cameraTarget = useRef({ x: 0, y: 0, z: 0 });
  const bowlSignal = useRef<Group>(null);
  const bedSignal = useRef<Group>(null);
  const cameraSignal = useRef<Group>(null);

  const [photoWidth, photoHeight] = useMemo(() => {
    const viewportAspect = viewport.width / viewport.height;
    const overscan = compact ? 1.12 : 1.08;
    if (viewportAspect > photoAspect) {
      const width = viewport.width * overscan;
      return [width, width / photoAspect];
    }
    const height = viewport.height * overscan;
    return [height * photoAspect, height];
  }, [compact, photoAspect, viewport.height, viewport.width]);

  const photoShiftY = compact ? photoHeight * 0.28 : 0;
  const bowlPoint = compact ? [0.22, 0.75] : [0.165, 0.885];
  const bedPoint = compact ? [0.21, 0.61] : [0.19, 0.735];
  const cameraPoint = compact ? [0.025, 0.13] : [0.018, 0.17];

  useEffect(() => {
    camera.lookAt(0, 0, 0);
    invalidate();
    return () => photoTexture.dispose();
  }, [camera, invalidate, photoTexture]);

  useEffect(() => {
    if (
      !animated ||
      !stageRef.current ||
      !bowlSignal.current ||
      !bedSignal.current ||
      !cameraSignal.current
    ) {
      return;
    }
    const root = document.getElementById("petcare-story");
    if (!root) return;
    return createSceneDirector({
      root,
      camera,
      target: cameraTarget.current,
      bowlSignal: bowlSignal.current,
      bedSignal: bedSignal.current,
      cameraSignal: cameraSignal.current,
      invalidate,
    });
  }, [animated, camera, invalidate, stageRef]);

  return (
    <>
      {showPhoto ? <color attach="background" args={["#08090a"]} /> : null}
      <group position={[0, photoShiftY, 0]}>
        {showPhoto ? (
          <mesh position={[0, 0, -0.02]} scale={[photoWidth, photoHeight, 1]}>
            <planeGeometry args={[1, 1]} />
            <meshBasicMaterial map={photoTexture} toneMapped={false} />
          </mesh>
        ) : null}
        <SignalHalo
          color="#e7b86e"
          groupRef={bowlSignal}
          position={mapPhotoPoint(bowlPoint[0], bowlPoint[1], photoWidth, photoHeight)}
          size={photoHeight * 0.018}
        />
        <SignalHalo
          color="#9edfe2"
          groupRef={bedSignal}
          position={mapPhotoPoint(bedPoint[0], bedPoint[1], photoWidth, photoHeight)}
          size={photoHeight * 0.02}
        />
        <SignalHalo
          color="#d7ecee"
          groupRef={cameraSignal}
          position={mapPhotoPoint(cameraPoint[0], cameraPoint[1], photoWidth, photoHeight)}
          size={photoHeight * 0.014}
        />
      </group>
    </>
  );
}
