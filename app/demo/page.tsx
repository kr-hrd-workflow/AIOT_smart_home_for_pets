import { Dashboard } from "../../components/dashboard";
import { demoDashboardData } from "../../lib/demo-data";

export default function DemoPage() {
  return <Dashboard data={demoDashboardData} />;
}
