import { fetchMarkets, fetchMe, fetchStaff, fetchStaffSettings, fetchVoiceSettings } from "@/lib/api";
import Staff from "./staff";
import VoiceEngine from "./voice-engine";

export const dynamic = "force-dynamic";

export default async function Page() {
  const [me, staff, settings, voice, markets] = await Promise.all([
    fetchMe(), fetchStaff(), fetchStaffSettings(), fetchVoiceSettings(), fetchMarkets(),
  ]);
  if (!staff || !settings) return <p className="muted">API unreachable</p>;
  const meData = me.ok ? me.data : null;
  const isOwner = meData?.staff_role === "owner";
  return (
    <>
      <Staff staff={staff} settings={settings} isOwner={isOwner} selfId={meData?.user_id ?? null} />
      {voice && markets && <VoiceEngine settings={voice} markets={markets} isOwner={isOwner} />}
    </>
  );
}
