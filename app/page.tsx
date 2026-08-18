import { LiveMonitor } from "@/components/LiveMonitor";
import { MercuryDashboard } from "@/components/MercuryDashboard";
import { getDashboard } from "@/lib/dashboard";

export const dynamic = "force-dynamic";

export default async function Home() {
  const data = await getDashboard();
  return (
    <>
      <MercuryDashboard initialData={data} />
      <LiveMonitor />
    </>
  );
}
