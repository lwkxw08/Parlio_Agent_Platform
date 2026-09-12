"""Idempotently create the LiveKit inbound SIP trunk + dispatch rule for the test number.
Run on the server: docker compose exec -e DID=+44... voice-worker python /dev/stdin < lk_setup.py
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
    print("trunks", names)
    print(
        "rules",
        [
            (r.name, list(r.trunk_ids))
            for r in (await lk.sip.list_sip_dispatch_rule(sipp.ListSIPDispatchRuleRequest())).items
        ],
    )
    await lk.aclose()


asyncio.run(main())
