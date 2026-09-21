import { fetchAdminCoupons, fetchAdminPlanDefaults, fetchAdminPlans, fetchBillingSettings, fetchEntitlementCatalogue, fetchMe, fetchNumberPool, fetchNumberRegions } from "@/lib/api";
import NumberPoolCard from "./number-pool";
import Plans from "./plans";
import StripeModeCard from "./stripe-mode";

export const dynamic = "force-dynamic";

export default async function Page() {
  const [me, plans, coupons, catalogue, defaults, billing, pool, regions] = await Promise.all([fetchMe(), fetchAdminPlans(), fetchAdminCoupons(), fetchEntitlementCatalogue(), fetchAdminPlanDefaults(), fetchBillingSettings(), fetchNumberPool(), fetchNumberRegions()]);
  if (!plans || !coupons) return <p className="muted">Couldn&apos;t load this page — the API rejected the request (e.g. your IP isn&apos;t on the staff allow-list) or didn&apos;t answer.</p>;
  const role = me.ok ? me.data.staff_role : null;
  return (
    <>
      {billing && <StripeModeCard settings={billing} isOwner={role === "owner"} />}
      {pool && <NumberPoolCard pool={pool} regions={regions ?? []} canBuy={role === "owner" || role === "finance"} />}
      <Plans plans={plans} coupons={coupons} catalogue={catalogue ?? {}} canEdit={role === "owner" || role === "finance"} defaultTrialDays={defaults?.trial_days ?? 14} />
    </>
  );
}
