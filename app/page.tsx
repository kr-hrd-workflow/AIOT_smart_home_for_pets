import { headers } from "next/headers";
import { ClientDashboardEntry } from "../components/dashboard";
import { LandingPage } from "../components/landing/landing-page";
import { RemoteDashboard } from "../components/remote-dashboard";

export const dynamic = "force-dynamic";

export default async function Home() {
  const requestHeaders = await headers();
  if (requestHeaders.get("x-petcare-authenticated") === "1") return <RemoteDashboard />;
  return <ClientDashboardEntry fallback={<LandingPage />} />;
}
