import { headers } from "next/headers";
import { LandingPage } from "../components/landing/landing-page";
import { RemoteDashboard } from "../components/remote-dashboard";

export const dynamic = "force-dynamic";
export default async function Home() {
  const requestHeaders = await headers();
  return requestHeaders.get("x-petcare-authenticated") === "1" ? (
    <RemoteDashboard />
  ) : (
    <LandingPage />
  );
}
