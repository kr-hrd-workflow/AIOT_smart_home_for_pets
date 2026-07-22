import type { ReactNode } from "react";
import { LandingFallback } from "./landing-fallback";
import { LandingOverlay } from "./landing-overlay";

export function LandingPage({ experience }: { experience?: ReactNode }) {
  return (
    <main className="landing-page" id="petcare-story">
      <div className="landing-experience" aria-hidden="true">
        {experience ?? <LandingFallback />}
      </div>
      <LandingOverlay />
    </main>
  );
}
