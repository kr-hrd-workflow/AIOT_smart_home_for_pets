import { ClientDashboardEntry } from "../components/dashboard";
import { LandingPage } from "../components/landing/landing-page";

export const dynamic = "force-dynamic";

export default function Home() {
  return <ClientDashboardEntry fallback={<LandingPage />} />;
}
