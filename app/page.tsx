"use client";

import { useEffect, useState } from "react";

import { Dashboard, selectDashboardMode } from "../components/dashboard";
import { demoDashboardData } from "../lib/demo-data";
import type { DashboardMode } from "../lib/types";

export default function Home() {
  const [mode, setMode] = useState<DashboardMode>("demo");

  useEffect(() => {
    setMode(
      selectDashboardMode(
        window.location.pathname,
        window.location.hostname,
      ),
    );
  }, []);

  return <Dashboard data={demoDashboardData} mode={mode} />;
}
