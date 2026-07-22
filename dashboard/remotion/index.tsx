import { Composition, registerRoot } from "remotion";
import { PETCARE_PROMO, PetCarePromo } from "./petcare-promo";

export function RemotionRoot() {
  return (
    <Composition
      id={PETCARE_PROMO.id}
      component={PetCarePromo}
      durationInFrames={PETCARE_PROMO.durationInFrames}
      fps={PETCARE_PROMO.fps}
      width={PETCARE_PROMO.width}
      height={PETCARE_PROMO.height}
    />
  );
}

registerRoot(RemotionRoot);
