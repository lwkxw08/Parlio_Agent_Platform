import Link from "next/link";
import { fetchAdminCoupons, fetchAdminPlans, fetchAdminTenant, fetchFeatureFlagCatalogue, fetchMe } from "@/lib/api";
import Tenant from "./tenant";

export const dynamic = "force-dynamic";

export default async function Page({ params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  const [me, detail, plans, coupons, catalogue] = await Promise.all([
    fetchMe(), fetchAdminTenant(id), fetchAdminPlans(), fetchAdminCoupons(), fetchFeatureFlagCatalogue(),
  ]);
  if (!detail.ok) return <p className="muted">{detail.status === 404 ? "Tenant not found." : detail.error} <Link href="/admin/tenants">Back to tenants</Link></p>;
  const role = me.ok ? me.data.staff_role : null;
  return (
    <>
      <p className="small"><Link href="/admin/tenants">← All tenants</Link></p>
      <Tenant detail={detail.data} plans={plans ?? []} coupons={coupons ?? []} catalogue={catalogue ?? {}} role={role} />
    </>
  );
}
