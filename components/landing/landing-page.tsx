import type { ReactNode } from "react";
import { LandingOverlay } from "./landing-overlay";
import { PetHomeExperience } from "./pet-home-experience";

export function LandingPage({ experience }: { experience?: ReactNode }) {
  return (
    <main className="landing-page" id="petcare-story">
      <div className="landing-experience" aria-hidden="true">
        {experience ?? <PetHomeExperience />}
      </div>
      <LandingOverlay />
    </main>
  );
}
