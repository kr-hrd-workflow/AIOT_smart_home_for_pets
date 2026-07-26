import { Composition, registerRoot } from "remotion";
import {
  PETCARE_HERO_LOOPS,
  PetCareHeroLoop,
} from "./petcare-hero-loop";
import { PETCARE_PROMO, PetCarePromo } from "./petcare-promo";

export function RemotionRoot() {
  return (
    <>
      <Composition
        id={PETCARE_PROMO.id}
        component={PetCarePromo}
        durationInFrames={PETCARE_PROMO.durationInFrames}
        fps={PETCARE_PROMO.fps}
        width={PETCARE_PROMO.width}
        height={PETCARE_PROMO.height}
      />
      <Composition
        id={PETCARE_HERO_LOOPS.desktop.id}
        component={PetCareHeroLoop}
        defaultProps={{ format: "desktop" }}
        durationInFrames={PETCARE_HERO_LOOPS.desktop.durationInFrames}
        fps={PETCARE_HERO_LOOPS.desktop.fps}
        width={PETCARE_HERO_LOOPS.desktop.width}
        height={PETCARE_HERO_LOOPS.desktop.height}
      />
      <Composition
        id={PETCARE_HERO_LOOPS.mobile.id}
        component={PetCareHeroLoop}
        defaultProps={{ format: "mobile" }}
        durationInFrames={PETCARE_HERO_LOOPS.mobile.durationInFrames}
        fps={PETCARE_HERO_LOOPS.mobile.fps}
        width={PETCARE_HERO_LOOPS.mobile.width}
        height={PETCARE_HERO_LOOPS.mobile.height}
      />
    </>
  );
}

registerRoot(RemotionRoot);
