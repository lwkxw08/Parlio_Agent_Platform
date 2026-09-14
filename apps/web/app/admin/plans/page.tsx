import { fetchAdminCoupons, fetchAdminPlans, fetchMe } from "@/lib/api";
import Plans from "./plans";

export const dynamic = "force-dynamic";

export default async function Page() {
  const [me, plans, coupons] = await Promise.all([fetchMe(), fetchAdminPlans(), fetchAdminCoupons()]);
  if (!plans || !coupons) return <p className="muted">API unreachable</p>;
  const role = me.ok ? me.data.staff_role : null;
  return <Plans plans={plans} coupons={coupons} canEdit={role === "owner" || role === "finance"} />;
}
