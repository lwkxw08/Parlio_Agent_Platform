import { fetchAdminCoupons, fetchAdminPlanDefaults, fetchAdminPlans, fetchEntitlementCatalogue, fetchMe } from "@/lib/api";
import Plans from "./plans";

export const dynamic = "force-dynamic";

export default async function Page() {
  const [me, plans, coupons, catalogue, defaults] = await Promise.all([fetchMe(), fetchAdminPlans(), fetchAdminCoupons(), fetchEntitlementCatalogue(), fetchAdminPlanDefaults()]);
  if (!plans || !coupons) return <p className="muted">Couldn&apos;t load this page — the API rejected the request (e.g. your IP isn&apos;t on the staff allow-list) or didn&apos;t answer.</p>;
  const role = me.ok ? me.data.staff_role : null;
  return <Plans plans={plans} coupons={coupons} catalogue={catalogue ?? {}} canEdit={role === "owner" || role === "finance"} defaultTrialDays={defaults?.trial_days ?? 14} />;
}
