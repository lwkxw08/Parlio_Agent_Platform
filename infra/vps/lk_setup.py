"""Idempotently create the LiveKit inbound SIP trunk + dispatch rule for the test number, and an
outbound trunk (used for warm transfers / outbound calls) via the carrier's SIP host.
Run on the server: docker compose exec -e DID=+44... voice-worker python /dev/stdin < lk_setup.py
Then put the printed outbound trunk id in .env as OUTBOUND_SIP_TRUNK_ID and redeploy the worker.
"""

import asyncio
import os

from livekit import api
from livekit.protocol import sip as sipp
from livekit.protocol.agent_dispatch import RoomAgentDispatch
from livekit.protocol.room import RoomConfiguration


async def main():
    lk = api.LiveKitAPI()
    trunks = await lk.sip.list_sip_inbound_trunk(sipp.ListSIPInboundTrunkRequest())
    names = {t.name: t.sip_trunk_id for t in trunks.items}
    if "telnyx-uk-inbound" not in names:
        t = await lk.sip.create_sip_inbound_trunk(
            sipp.CreateSIPInboundTrunkRequest(
                trunk=sipp.SIPInboundTrunkInfo(
                    name="telnyx-uk-inbound", numbers=[os.environ["DID"]], krisp_enabled=True
                )
            )
        )
        names[t.name] = t.sip_trunk_id
    rules = await lk.sip.list_sip_dispatch_rule(sipp.ListSIPDispatchRuleRequest())
    if not any(r.name == "parlio-inbound" for r in rules.items):
        await lk.sip.create_sip_dispatch_rule(
            sipp.CreateSIPDispatchRuleRequest(
                name="parlio-inbound",
                trunk_ids=[names["telnyx-uk-inbound"]],
                rule=sipp.SIPDispatchRule(
                    dispatch_rule_individual=sipp.SIPDispatchRuleIndividual(room_prefix="call-")
                ),
                room_config=RoomConfiguration(
                    agents=[RoomAgentDispatch(agent_name="parlio-voice")]
                ),
            )
        )
    out = await lk.sip.list_sip_outbound_trunk(sipp.ListSIPOutboundTrunkRequest())
    out_names = {t.name: t.sip_trunk_id for t in out.items}
    if "telnyx-uk-outbound" not in out_names:
        t = await lk.sip.create_sip_outbound_trunk(
            sipp.CreateSIPOutboundTrunkRequest(
                trunk=sipp.SIPOutboundTrunkInfo(
                    name="telnyx-uk-outbound",
                    address=os.environ.get("SIP_HOST", "sip.telnyx.eu"),
                    numbers=[os.environ["DID"]],
                    transport=sipp.SIPTransport.SIP_TRANSPORT_UDP,
                )
            )
        )
        out_names[t.name] = t.sip_trunk_id
    print("trunks", names)
    print("outbound trunks", out_names)
    print(
        "rules",
        [
            (r.name, list(r.trunk_ids))
            for r in (await lk.sip.list_sip_dispatch_rule(sipp.ListSIPDispatchRuleRequest())).items
        ],
    )
    await lk.aclose()


asyncio.run(main())
