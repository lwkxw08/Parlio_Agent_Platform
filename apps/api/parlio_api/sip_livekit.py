"""LiveKit SIP implementation of the trunk provisioning seam.

Inbound trunk = customer's DDIs + digest auth (pbx mode) or the registered identity (byo_register,
where the registering SIP edge forwards to LiveKit). Outbound trunk = address of the customer's
PBX/provider so extension transfers and callbacks leave over the customer's trunk.
"""

from __future__ import annotations

import asyncio
import logging
import uuid

from livekit import api
from livekit.protocol import sip as sipp

from parlio_api.sip import Codec, SipTrunk, TestCallResult, Transport, TrunkMode
from parlio_api.telephony.base import InboundEdge

log = logging.getLogger("parlio.api.sip.livekit")

_TRANSPORT = {
    Transport.UDP: sipp.SIPTransport.SIP_TRANSPORT_UDP,
    Transport.TCP: sipp.SIPTransport.SIP_TRANSPORT_TCP,
    Transport.TLS: sipp.SIPTransport.SIP_TRANSPORT_TLS,
}


class LiveKitProvisioner:
    def __init__(self, lk: api.LiveKitAPI | None = None, *, agent_name: str = "parlio") -> None:
        self._lk = lk or api.LiveKitAPI()
        self._agent = agent_name

    async def ensure_inbound(self, trunk: SipTrunk, password: str | None) -> str:
        info = sipp.SIPInboundTrunkInfo(
            name=f"{trunk.tenant_id}/{trunk.name}",
            metadata=_meta(trunk),
            numbers=[d.e164 for d in trunk.ddis],
            allowed_addresses=list(trunk.allowed_ips),
            media_encryption=(
                sipp.SIPMediaEncryption.SIP_MEDIA_ENCRYPT_REQUIRE
                if trunk.srtp
                else sipp.SIPMediaEncryption.SIP_MEDIA_ENCRYPT_ALLOW
            ),
        )
        if trunk.mode == TrunkMode.PBX and trunk.sip_username and password:
            info.auth_username = trunk.sip_username
            info.auth_password = password
        if trunk.lk_inbound_trunk_id:
            await self._lk.sip.delete_sip_trunk(
                sipp.DeleteSIPTrunkRequest(sip_trunk_id=trunk.lk_inbound_trunk_id)
            )
        created = await self._lk.sip.create_sip_inbound_trunk(
            sipp.CreateSIPInboundTrunkRequest(trunk=info)
        )
        return str(created.sip_trunk_id)

    async def ensure_outbound(self, trunk: SipTrunk, password: str | None) -> str | None:
        address = trunk.pbx_address if trunk.mode == TrunkMode.PBX else trunk.registrar
        if not address:
            return None
        info = sipp.SIPOutboundTrunkInfo(
            name=f"{trunk.tenant_id}/{trunk.name} (out)",
            metadata=_meta(trunk),
            address=trunk.outbound_proxy or address,
            transport=_TRANSPORT[trunk.transport],
            numbers=[d.e164 for d in trunk.ddis],
            media_encryption=(
                sipp.SIPMediaEncryption.SIP_MEDIA_ENCRYPT_REQUIRE
                if trunk.srtp
                else sipp.SIPMediaEncryption.SIP_MEDIA_ENCRYPT_ALLOW
            ),
        )
        if trunk.mode == TrunkMode.BYO_REGISTER and password:
            info.auth_username = trunk.auth_username or trunk.username or ""
            info.auth_password = password
        if trunk.lk_outbound_trunk_id:
            await self._lk.sip.delete_sip_trunk(
                sipp.DeleteSIPTrunkRequest(sip_trunk_id=trunk.lk_outbound_trunk_id)
            )
        created = await self._lk.sip.create_sip_outbound_trunk(
            sipp.CreateSIPOutboundTrunkRequest(trunk=info)
        )
        return str(created.sip_trunk_id)

    async def remove(self, trunk: SipTrunk) -> None:
        for tid in (trunk.lk_inbound_trunk_id, trunk.lk_outbound_trunk_id):
            if tid:
                await self._lk.sip.delete_sip_trunk(sipp.DeleteSIPTrunkRequest(sip_trunk_id=tid))

    async def test_call(self, trunk: SipTrunk, to: str) -> TestCallResult:
        """Dial `to` over the tenant's outbound trunk into a scratch room and report the outcome."""
        if not trunk.lk_outbound_trunk_id:
            return TestCallResult(
                ok=False,
                outcome="no_outbound_trunk",
                detail="forward-only trunks have nothing to dial; call the number instead",
            )
        room = f"trunk-test-{trunk.id}-{uuid.uuid4().hex[:6]}"
        try:
            await asyncio.wait_for(
                self._lk.sip.create_sip_participant(
                    sipp.CreateSIPParticipantRequest(
                        sip_trunk_id=trunk.lk_outbound_trunk_id,
                        sip_call_to=to,
                        room_name=room,
                        participant_identity="trunk-test",
                        wait_until_answered=True,
                        play_dialtone=False,
                    )
                ),
                timeout=30,
            )
        except TimeoutError:
            return TestCallResult(ok=False, outcome="no_answer", detail="30s ring timeout")
        except api.TwirpError as e:
            return TestCallResult(ok=False, outcome="sip_error", detail=str(e.message)[:300])
        finally:
            try:
                await self._lk.room.delete_room(api.DeleteRoomRequest(room=room))
            except Exception:
                log.debug("test room cleanup failed", exc_info=True)
        return TestCallResult(ok=True, outcome="answered", detail=f"{to} answered via {trunk.mode}")


class LiveKitInboundEdge(InboundEdge):
    """Platform-owned inbound trunk (carrier → LiveKit SIP): keeps its DID list in step with
    the numbers tenants buy, so the existing dispatch rule routes them to the worker."""

    def __init__(self, lk: api.LiveKitAPI, trunk_id: str) -> None:
        self._lk = lk
        self._trunk_id = trunk_id

    async def _numbers(self) -> list[str]:
        res = await self._lk.sip.list_inbound_trunk(
            sipp.ListSIPInboundTrunkRequest(trunk_ids=[self._trunk_id])
        )
        for t in res.items:
            if t.sip_trunk_id == self._trunk_id:
                return list(t.numbers)
        raise RuntimeError(f"LiveKit inbound trunk {self._trunk_id} not found")

    async def add_number(self, e164: str) -> None:
        nums = await self._numbers()
        if e164 in nums:
            return
        await self._lk.sip.update_inbound_trunk_fields(self._trunk_id, numbers=[*nums, e164])
        log.info("added %s to inbound trunk %s", e164, self._trunk_id)

    async def remove_number(self, e164: str) -> None:
        nums = await self._numbers()
        if e164 not in nums:
            return
        await self._lk.sip.update_inbound_trunk_fields(
            self._trunk_id, numbers=[n for n in nums if n != e164]
        )
        log.info("removed %s from inbound trunk %s", e164, self._trunk_id)


def _meta(trunk: SipTrunk) -> str:
    codecs = ",".join(c.value for c in trunk.codecs) or Codec.PCMA.value
    return (
        f'{{"tenant_id":"{trunk.tenant_id}","trunk_id":"{trunk.id}",'
        f'"codecs":"{codecs}","dtmf":"{trunk.dtmf}"}}'
    )
