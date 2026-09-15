"""Phase 11: omnichannel shared inbox — threads, AI text replies, handoff, webhooks, web chat."""

from __future__ import annotations

import hashlib
import hmac
import json
from datetime import timedelta
from typing import Any

import httpx
import pytest
from fastapi import FastAPI
from httpx import AsyncClient

from parlio_api.auth import DEV_TENANT
from parlio_api.inbox import (
    Author,
    Channel,
    Direction,
    InboundText,
    InboxMessage,
    InboxService,
    OpenAITextAgent,
    RuleTextAgent,
    Thread,
    ThreadStatus,
    parse_meta_whatsapp,
    parse_telnyx_sms,
    verify_meta_signature,
)
from parlio_api.postcall import PostCallProcessor
from parlio_api.settings import get_settings
from parlio_voice.models import AssistantConfig, CallEventType, Destination, Faq

from .test_api import HEADERS, ev

DEMO_NUMBER = "+440000000000"


def _svc(app: FastAPI) -> InboxService:
    svc: InboxService = app.state.inbox
    return svc


def telnyx_sms(text: str, frm: str = "+447700900123", to: str = DEMO_NUMBER) -> dict[str, Any]:
    return {
        "data": {
            "event_type": "message.received",
            "id": "evt-1",
            "payload": {
                "id": "msg-1",
                "from": {"phone_number": frm},
                "to": [{"phone_number": to}],
                "text": text,
            },
        }
    }


def meta_wa(text: str, phone_id: str = "1234567890", frm: str = "447700900555") -> dict[str, Any]:
    return {
        "object": "whatsapp_business_account",
        "entry": [
            {
                "changes": [
                    {
                        "value": {
                            "metadata": {"phone_number_id": phone_id},
                            "contacts": [{"profile": {"name": "Sam"}, "wa_id": frm}],
                            "messages": [
                                {
                                    "from": frm,
                                    "id": "wamid.1",
                                    "type": "text",
                                    "text": {"body": text},
                                }
                            ],
                        }
                    }
                ]
            }
        ],
    }


async def _set_faq(client: AsyncClient) -> None:
    cfg = AssistantConfig.model_validate((await client.get("/v1/assistants")).json()[0])
    cfg.faqs.append(Faq(question="Do you offer free parking?", answer="Yes, free parking on site."))
    r = await client.put("/v1/assistants/demo", json={"config": cfg.model_dump(mode="json")})
    assert r.status_code == 200, r.text


def _hist(text: str, thread: Thread) -> list[InboxMessage]:
    return [
        InboxMessage(
            tenant_id=thread.tenant_id,
            thread_id=thread.id,
            channel=thread.channel,
            direction=Direction.IN,
            author=Author.CONTACT,
            text=text,
        )
    ]


async def _drain(client: AsyncClient, app: FastAPI, cid: str, events: list[Any]) -> None:
    for t, p in events:
        r = await client.post("/v1/worker/events", json=ev(t, cid, p), headers=HEADERS)
        assert r.status_code == 202, r.text
    proc: PostCallProcessor = app.state.postcall
    await proc.drain()


# -- parsers / signatures --------------------------------------------------------------------------


def test_parse_telnyx_sms() -> None:
    inb = parse_telnyx_sms(telnyx_sms("hello"))
    assert inb is not None
    assert inb.channel == Channel.SMS
    assert inb.identity == "+447700900123"
    assert inb.to == DEMO_NUMBER
    assert inb.text == "hello"
    assert inb.provider_ref == "msg-1"
    assert parse_telnyx_sms({"data": {"event_type": "message.sent"}}) is None


def test_parse_meta_whatsapp() -> None:
    msgs = parse_meta_whatsapp(meta_wa("hi there"))
    assert len(msgs) == 1
    assert msgs[0].channel == Channel.WHATSAPP
    assert msgs[0].identity == "+447700900555"
    assert msgs[0].to == "1234567890"
    assert msgs[0].name == "Sam"
    assert parse_meta_whatsapp({"entry": []}) == []


def test_meta_signature() -> None:
    body = b'{"a":1}'
    sig = "sha256=" + hmac.new(b"secret", body, hashlib.sha256).hexdigest()
    assert verify_meta_signature("secret", body, sig)
    assert not verify_meta_signature("secret", body, "sha256=deadbeef")
    assert not verify_meta_signature("secret", body, None)
    assert verify_meta_signature(None, body, None)


# -- rules agent -----------------------------------------------------------------------------------


def _cfg(**kw: Any) -> AssistantConfig:
    return AssistantConfig(
        assistant_id="a1",
        tenant_id="t1",
        company_id="t1-main",
        business_name="Acme Dental",
        greeting="Hi",
        **kw,
    )


def _thread(channel: Channel = Channel.SMS) -> Thread:
    return Thread(tenant_id="t1", company_id="t1-main", channel=channel, identity="+447700900123")


async def test_rules_agent_faq_and_handoff() -> None:
    agent = RuleTextAgent()
    cfg = _cfg(faqs=[Faq(question="Do you offer free parking?", answer="Yes, on site.")])
    th = _thread()
    r = await agent.respond(cfg, th, _hist("is there free parking at your place?", th))
    assert r.handoff is False
    assert "on site" in r.reply
    r = await agent.respond(cfg, th, _hist("I want to speak to a human please", th))
    assert r.handoff is True  # single department: connect straight away
    assert r.ticket is None  # callback ticket comes from the SLA sweep, not up front
    assert "couple of minutes" in r.reply
    r = await agent.respond(cfg, th, _hist("what is the meaning of life", th))
    assert r.ticket is not None  # unknown -> take a message and hand to the team
    assert r.handoff is True


async def test_openai_agent_falls_back_on_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, json={"error": "boom"})

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="https://x")
    agent = OpenAITextAgent("k", client=client, fallback=RuleTextAgent())
    cfg = _cfg(faqs=[Faq(question="parking?", answer="Free parking on site.")])
    th = _thread()
    r = await agent.respond(cfg, th, _hist("do you have parking on site", th))
    assert "parking" in r.reply.lower()


async def test_openai_agent_parses_json() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        content = json.dumps({"reply": "Sure, Monday 9am works.", "handoff": False, "ticket": None})
        return httpx.Response(200, json={"choices": [{"message": {"content": content}}]})

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="https://x")
    agent = OpenAITextAgent("k", client=client)
    th = _thread()
    r = await agent.respond(_cfg(), th, _hist("can I book Monday 9am?", th))
    assert r.reply == "Sure, Monday 9am works."
    assert r.handoff is False


async def test_openai_agent_handoff_is_sticky_and_department_aware() -> None:
    seen: list[dict[str, Any]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        seen.append(body)
        # model wrongly tries to drop the handoff and raise a ticket on the 2nd turn
        content = json.dumps(
            {
                "reply": "I can raise a ticket for you.",
                "handoff": False,
                "department": None,
                "ticket": {"reason": "invoice query", "category": None, "priority": "normal"},
            }
        )
        return httpx.Response(200, json={"choices": [{"message": {"content": content}}]})

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="https://x")
    agent = OpenAITextAgent("k", client=client)
    th = _thread()
    hist = _hist("I need to speak to a real person", th)
    hist.append(
        InboxMessage(
            tenant_id=th.tenant_id,
            thread_id=th.id,
            channel=th.channel,
            direction=Direction.OUT,
            author=Author.AI,
            text="Connecting you to the team now.",
            handoff=True,
        )
    )
    hist.extend(_hist("It's about an invoice", th))
    r = await agent.respond(_cfg(), th, hist)
    assert r.handoff is True
    assert r.ticket is None
    system = seen[0]["messages"][0]["content"]
    assert "Handoff policy" in system
    assert "already in progress" in system


async def test_openai_agent_prompt_lists_department_descriptions() -> None:
    seen: list[dict[str, Any]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(json.loads(request.content))
        content = json.dumps({"reply": "Connecting you to accounts.", "handoff": True})
        return httpx.Response(200, json={"choices": [{"message": {"content": content}}]})

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="https://x")
    agent = OpenAITextAgent("k", client=client)
    cfg = _cfg()
    cfg.transfer.destinations.append(
        Destination(id="d1", name="Jo", department="accounts", address="+447700900001")
    )
    cfg.transfer.department_notes = {"accounts": "invoices, payments, refunds"}
    th = _thread()
    await agent.respond(cfg, th, _hist("It's about an invoice", th))
    assert "- accounts: invoices, payments, refunds;" in seen[0]["messages"][0]["content"]


def _multi_dept_cfg() -> AssistantConfig:
    cfg = _cfg()
    cfg.transfer.destinations.extend(
        [
            Destination(id="d1", name="Jo", department="accounts", address="+447700900001"),
            Destination(id="d2", name="Sam", department="bookings", address="+447700900002"),
        ]
    )
    cfg.transfer.department_notes = {
        "accounts": "invoices, payments, refunds",
        "bookings": "appointments",
    }
    return cfg


def _ai(th: Thread, text: str, **kw: Any) -> InboxMessage:
    return InboxMessage(
        tenant_id=th.tenant_id,
        thread_id=th.id,
        channel=th.channel,
        direction=Direction.OUT,
        author=Author.AI,
        text=text,
        **kw,
    )


async def test_rules_agent_asks_topic_then_routes_handoff() -> None:
    agent = RuleTextAgent()
    cfg = _multi_dept_cfg()
    th = _thread(Channel.WEBCHAT)
    r = await agent.respond(cfg, th, _hist("transfer me to a real person", th))
    assert r.clarifying is True and r.handoff is False and r.ticket is None
    hist = _hist("transfer me to a real person", th)
    hist.append(_ai(th, r.reply, clarifying=True))
    hist.extend(_hist("it's about an invoice", th))
    r = await agent.respond(cfg, th, hist)
    assert r.handoff is True and r.department == "accounts" and r.ticket is None
    assert "couple of minutes" in r.reply


async def test_openai_agent_question_plus_handoff_becomes_clarifying() -> None:
    seen: list[dict[str, Any]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(json.loads(request.content))
        # model asks the topic but pauses itself in the same turn (the bug the user saw)
        content = json.dumps(
            {"reply": "What do you need help with?", "handoff": True, "department": None}
        )
        return httpx.Response(200, json={"choices": [{"message": {"content": content}}]})

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="https://x")
    agent = OpenAITextAgent("k", client=client)
    cfg = _multi_dept_cfg()
    th = _thread(Channel.WEBCHAT)
    r = await agent.respond(cfg, th, _hist("transfer to human", th))
    assert r.handoff is False and r.clarifying is True
    assert "ask ONE short question" in seen[0]["messages"][0]["content"]

    # next turn: even if the model wants to ask again, it must connect now
    hist = _hist("transfer to human", th)
    hist.append(_ai(th, r.reply, clarifying=True))
    hist.extend(_hist("an invoice", th))
    r = await agent.respond(cfg, th, hist)
    assert r.handoff is True and r.clarifying is False
    assert "Do not ask again" in seen[1]["messages"][0]["content"]


async def test_rules_agent_closing_remark_does_not_hand_off() -> None:
    agent = RuleTextAgent()
    th = _thread()
    hist = _hist("what time do you close today?", th)
    hist.append(
        InboxMessage(
            tenant_id=th.tenant_id,
            thread_id=th.id,
            channel=th.channel,
            direction=Direction.OUT,
            author=Author.AI,
            text="We close at 6pm.",
        )
    )
    hist.extend(_hist("Great, thanks.", th))
    r = await agent.respond(_cfg(), th, hist)
    assert r.handoff is False and r.ticket is None


# -- end-to-end via routes -------------------------------------------------------------------------


async def test_sms_inbound_creates_thread_and_ai_reply(client: AsyncClient, app: FastAPI) -> None:
    await _set_faq(client)
    r = await client.post("/v1/inbound/sms", json=telnyx_sms("do you have free parking?"))
    assert r.status_code == 202, r.text
    assert r.json()["replied"] is True

    r = await client.get("/v1/inbox/threads", params={"tenant_id": DEV_TENANT})
    threads = r.json()
    assert len(threads) == 1
    t = threads[0]
    assert t["channel"] == "sms"
    assert t["identity"] == "+447700900123"
    assert t["unread"] == 1
    assert t["contact_id"]

    r = await client.get(f"/v1/inbox/threads/{t['id']}", params={"tenant_id": DEV_TENANT})
    msgs = r.json()["messages"]
    assert [m["author"] for m in msgs] == ["contact", "ai"]
    assert "parking" in msgs[1]["text"].lower()

    # same sender -> same thread
    r = await client.post("/v1/inbound/sms", json=telnyx_sms("thanks"))
    assert r.status_code == 202
    r = await client.get("/v1/inbox/threads", params={"tenant_id": DEV_TENANT})
    assert len(r.json()) == 1
    assert r.json()[0]["message_count"] >= 3

    r = await client.get("/v1/inbox/stats", params={"tenant_id": DEV_TENANT})
    st = r.json()
    assert st["open"] + st["waiting"] == 1
    assert st["unread"] >= 1
    assert st["by_channel"] == {"sms": 1}


async def test_sms_unknown_number_ignored(client: AsyncClient) -> None:
    r = await client.post("/v1/inbound/sms", json=telnyx_sms("hi", to="+449999999999"))
    assert r.status_code == 202
    assert r.json()["ignored"] is True


async def test_sms_webhook_secret(client: AsyncClient, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(get_settings(), "inbound_webhook_secret", "s3cret")
    r = await client.post("/v1/inbound/sms", json=telnyx_sms("hi"))
    assert r.status_code == 401
    r = await client.post("/v1/inbound/sms", params={"secret": "s3cret"}, json=telnyx_sms("hi"))
    assert r.status_code == 202


async def test_handoff_pauses_ai_without_upfront_ticket(client: AsyncClient) -> None:
    r = await client.post("/v1/inbound/sms", json=telnyx_sms("I need to speak to a human"))
    assert r.status_code == 202
    r = await client.get("/v1/inbox/threads", params={"tenant_id": DEV_TENANT})
    t = r.json()[0]
    r = await client.get(f"/v1/inbox/threads/{t['id']}", params={"tenant_id": DEV_TENANT})
    ai = [m for m in r.json()["messages"] if m["author"] == "ai"]
    assert ai
    if ai[0]["clarifying"]:  # several departments configured: answer the topic question
        r = await client.post("/v1/inbound/sms", json=telnyx_sms("about an invoice"))
        assert r.json()["replied"] is True
        r = await client.get("/v1/inbox/threads", params={"tenant_id": DEV_TENANT})
        t = r.json()[0]
    assert t["ai_enabled"] is False
    assert t["status"] == "waiting"
    assert t["callback_ticket_id"] is None

    # AI paused: next inbound gets no auto-reply
    r = await client.post("/v1/inbound/sms", json=telnyx_sms("hello?"))
    assert r.json()["replied"] is False


async def test_human_reply_notes_assign_close(client: AsyncClient, app: FastAPI) -> None:
    r = await client.post("/v1/inbound/sms", json=telnyx_sms("hello there"))
    tid = r.json()["thread_id"]
    q = {"tenant_id": DEV_TENANT}

    r = await client.post(f"/v1/inbox/threads/{tid}/reply", params=q, json={"text": "Hi Sam!"})
    assert r.status_code == 200, r.text
    assert r.json()["author"] == "agent"
    assert r.json()["status"] in ("sent", "skipped")  # no SMS provider configured in tests

    r = await client.post(f"/v1/inbox/threads/{tid}/note", params=q, json={"text": "VIP"})
    assert r.status_code == 200
    assert r.json()["direction"] == "note"

    r = await client.patch(
        f"/v1/inbox/threads/{tid}",
        params=q,
        json={"assigned_to": "owner@demo.parlio.local", "ai_enabled": False, "read": True},
    )
    assert r.status_code == 200
    t = r.json()
    assert t["assigned_to"] == "owner@demo.parlio.local"
    assert t["ai_enabled"] is False
    assert t["unread"] == 0

    r = await client.get(
        "/v1/inbox/threads", params={**q, "assigned_to": "owner@demo.parlio.local"}
    )
    assert [x["id"] for x in r.json()] == [tid]
    r = await client.get("/v1/inbox/threads", params={**q, "unassigned": "true"})
    assert r.json() == []

    r = await client.patch(f"/v1/inbox/threads/{tid}", params=q, json={"status": "closed"})
    assert r.json()["status"] == "closed"
    r = await client.get("/v1/inbox/threads", params={**q, "status": "open"})
    assert r.json() == []

    # a new SMS from the same contact after closing opens a fresh thread
    r = await client.post("/v1/inbound/sms", json=telnyx_sms("one more thing"))
    assert r.json()["thread_id"] != tid

    r = await client.get(f"/v1/inbox/threads/{tid}", params=q)
    kinds = [m["direction"] for m in r.json()["messages"]]
    assert kinds == ["in", "out", "out", "note"]


async def test_canned_replies_crud(client: AsyncClient) -> None:
    q = {"tenant_id": DEV_TENANT}
    r = await client.post(
        "/v1/inbox/canned",
        params=q,
        json={"title": "Hours", "body": "We're open 9-5 Mon-Fri.", "shortcut": "hours"},
    )
    assert r.status_code == 201, r.text
    cid = r.json()["id"]
    r = await client.put(
        f"/v1/inbox/canned/{cid}", params=q, json={"title": "Hours", "body": "Open 9-5."}
    )
    assert r.json()["body"] == "Open 9-5."
    r = await client.get("/v1/inbox/canned", params=q)
    assert len(r.json()) == 1
    r = await client.delete(f"/v1/inbox/canned/{cid}", params=q)
    assert r.status_code == 204
    r = await client.get("/v1/inbox/canned", params=q)
    assert r.json() == []


async def test_tenant_isolation(client: AsyncClient) -> None:
    await client.post("/v1/inbound/sms", json=telnyx_sms("hello"))
    r = await client.get("/v1/inbox/threads", params={"tenant_id": "other"})
    assert r.status_code == 403
    r = await client.get("/v1/inbox/threads", params={"tenant_id": DEV_TENANT})
    tid = r.json()[0]["id"]
    r = await client.get(f"/v1/inbox/threads/{tid}", params={"tenant_id": "other"})
    assert r.status_code == 403


async def test_sla_sweep_flags_breach(client: AsyncClient, app: FastAPI) -> None:
    svc = _svc(app)
    svc.sla = timedelta(0)
    await client.post("/v1/inbound/sms", json=telnyx_sms("I need to speak to a human"))
    r = await client.get("/v1/inbox/threads", params={"tenant_id": DEV_TENANT})
    if r.json()[0]["status"] != "waiting":
        await client.post("/v1/inbound/sms", json=telnyx_sms("about an invoice"))
    assert len(await svc.sweep_sla()) == 1
    r = await client.get("/v1/inbox/threads", params={"tenant_id": DEV_TENANT})
    assert r.json()[0]["sla_breached"] is True
    assert r.json()[0]["callback_ticket_id"]
    assert await svc.sweep_sla() == []


async def _breached_thread(client: AsyncClient, app: FastAPI) -> tuple[str, str]:
    """A waiting SMS thread whose SLA has lapsed, plus the callback ticket it raised."""
    svc = _svc(app)
    svc.sla = timedelta(0)
    await client.post("/v1/inbound/sms", json=telnyx_sms("I need to speak to a human"))
    r = await client.get("/v1/inbox/threads", params={"tenant_id": DEV_TENANT})
    if r.json()[0]["status"] != "waiting":
        await client.post("/v1/inbound/sms", json=telnyx_sms("about an invoice"))
    await svc.sweep_sla()
    r = await client.get("/v1/inbox/threads", params={"tenant_id": DEV_TENANT})
    t = r.json()[0]
    assert t["callback_ticket_id"] and t["ticket_ids"] == [t["callback_ticket_id"]]
    return t["id"], t["callback_ticket_id"]


async def test_callback_ticket_links_back_to_thread(client: AsyncClient, app: FastAPI) -> None:
    tid, tk = await _breached_thread(client, app)
    r = await client.get(f"/v1/tickets/{tk}")
    assert r.json()["ticket"]["thread_id"] == tid
    assert r.json()["ticket"]["status"] == "open"


async def test_inbox_progress_updates_ticket(client: AsyncClient, app: FastAPI) -> None:
    tid, tk = await _breached_thread(client, app)
    q = {"tenant_id": DEV_TENANT}

    # picking the thread up (human reply) claims the ticket for that person
    r = await client.post(f"/v1/inbox/threads/{tid}/reply", params=q, json={"text": "Hi, Jo here"})
    assert r.status_code == 200, r.text
    t = (await client.get(f"/v1/tickets/{tk}")).json()["ticket"]
    assert t["status"] == "claimed"
    assert (
        t["assigned_to"]
        == (await client.get(f"/v1/inbox/threads/{tid}", params=q)).json()["thread"]["assigned_to"]
    )

    # closing the thread resolves the ticket
    r = await client.patch(f"/v1/inbox/threads/{tid}", params=q, json={"status": "closed"})
    assert r.json()["status"] == "closed"
    t = (await client.get(f"/v1/tickets/{tk}")).json()["ticket"]
    assert t["status"] == "resolved"
    assert t["resolved_at"]

    # reopening the thread reopens the ticket
    await client.patch(f"/v1/inbox/threads/{tid}", params=q, json={"status": "open"})
    t = (await client.get(f"/v1/tickets/{tk}")).json()["ticket"]
    assert t["status"] == "open"
    assert t["resolved_at"] is None


async def test_inbox_assign_claims_ticket(client: AsyncClient, app: FastAPI) -> None:
    tid, tk = await _breached_thread(client, app)
    q = {"tenant_id": DEV_TENANT}
    r = await client.patch(
        f"/v1/inbox/threads/{tid}", params=q, json={"assigned_to": "owner@demo.parlio.local"}
    )
    assert r.status_code == 200
    t = (await client.get(f"/v1/tickets/{tk}")).json()["ticket"]
    assert t["status"] == "claimed"
    assert t["assigned_to"] == "owner@demo.parlio.local"
    events = (await client.get(f"/v1/tickets/{tk}")).json()["events"]
    assert any(e["type"] == "claimed" and e["actor"] for e in events)


async def test_ticket_progress_updates_inbox(client: AsyncClient, app: FastAPI) -> None:
    tid, tk = await _breached_thread(client, app)
    q = {"tenant_id": DEV_TENANT}

    r = await client.post(f"/v1/tickets/{tk}/claim", json={"actor": "jo@demo.parlio.local"})
    assert r.status_code == 200, r.text
    t = (await client.get(f"/v1/inbox/threads/{tid}", params=q)).json()["thread"]
    assert t["assigned_to"] == "jo@demo.parlio.local"
    assert t["status"] == "waiting"  # still awaiting the actual reply to the customer

    r = await client.post(f"/v1/tickets/{tk}/resolve", json={"actor": "jo@demo.parlio.local"})
    assert r.json()["status"] == "resolved"
    d = (await client.get(f"/v1/inbox/threads/{tid}", params=q)).json()
    assert d["thread"]["status"] == "closed"
    assert d["thread"]["sla_due_at"] is None
    assert d["thread"]["unread"] == 0

    # ticket already resolved -> closing the thread again is a no-op, no loop
    r = await client.patch(f"/v1/inbox/threads/{tid}", params=q, json={"status": "closed"})
    assert r.status_code == 200
    assert (await client.get(f"/v1/tickets/{tk}")).json()["ticket"]["status"] == "resolved"


async def test_unlinked_ticket_and_thread_untouched(client: AsyncClient, app: FastAPI) -> None:
    q = {"tenant_id": DEV_TENANT}
    r = await client.post("/v1/inbound/sms", json=telnyx_sms("hello there"))
    tid = r.json()["thread_id"]
    r = await client.post(
        "/v1/tickets",
        json={"tenant_id": DEV_TENANT, "company_id": DEV_TENANT, "intake": {"reason": "manual"}},
    )
    assert r.status_code in (200, 201), r.text
    tk = r.json()["id"]
    assert r.json()["thread_id"] is None

    await client.patch(f"/v1/inbox/threads/{tid}", params=q, json={"status": "closed"})
    assert (await client.get(f"/v1/tickets/{tk}")).json()["ticket"]["status"] == "open"
    await client.post(f"/v1/tickets/{tk}/resolve", json={"actor": "x"})
    t = (await client.get(f"/v1/inbox/threads/{tid}", params=q)).json()["thread"]
    assert t["status"] == "closed"  # closed by us above, not reopened/touched by the ticket


async def test_webchat_status_lines_and_state(client: AsyncClient, app: FastAPI) -> None:
    svc = _svc(app)
    svc.sla = timedelta(0)
    q = {"tenant_id": DEV_TENANT}
    token = (await client.get("/v1/inbox/widget", params=q)).json()["token"]
    visitor = "visitor-status-000001"
    url = f"/v1/public/chat/{token}"
    r = await client.get(f"{url}/state", params={"visitor": visitor})
    assert r.json()["status"] == "none"

    await client.post(f"{url}/messages", json={"visitor": visitor, "text": "transfer to human"})
    st = (await client.get(f"{url}/state", params={"visitor": visitor})).json()
    if st["status"] == "ai":  # asked what it's about first
        await client.post(f"{url}/messages", json={"visitor": visitor, "text": "an invoice"})
        st = (await client.get(f"{url}/state", params={"visitor": visitor})).json()
    assert st["status"] == "waiting"
    system = [m["text"] for m in st["messages"] if m["author"] == "system"]
    assert any("Connecting you to" in s for s in system)

    # nobody picks up -> callback ticket + visitor told
    await svc.sweep_sla()
    st = (await client.get(f"{url}/state", params={"visitor": visitor})).json()
    assert any("logged your request" in m["text"] for m in st["messages"])

    # a human replies -> joined line, no email leaked, state=human
    tid = (await client.get("/v1/inbox/threads", params={**q, "channel": "webchat"})).json()[0][
        "id"
    ]
    await client.post(f"/v1/inbox/threads/{tid}/reply", params=q, json={"text": "Hi, Jo here"})
    st = (await client.get(f"{url}/state", params={"visitor": visitor})).json()
    assert st["status"] == "human"
    assert st["agent_name"] and "@" not in st["agent_name"]
    assert any("has joined the chat" in m["text"] for m in st["messages"])
    assert all("@" not in (m["author_name"] or "") for m in st["messages"])

    # close -> closed line + state
    await client.patch(f"/v1/inbox/threads/{tid}", params=q, json={"status": "closed"})
    st = (await client.get(f"{url}/state", params={"visitor": visitor})).json()
    assert st["status"] == "closed"
    assert any("has been closed" in m["text"] for m in st["messages"])


async def test_call_ended_appends_to_call_thread(client: AsyncClient, app: FastAPI) -> None:
    cid = "c-inbox-1"
    await _drain(
        client,
        app,
        cid,
        [
            (CallEventType.CALL_STARTED, {"caller": "+447700900123"}),
            (CallEventType.CALL_ANSWERED, {}),
            (CallEventType.CALL_ENDED, {"reason": "hangup", "transcript": []}),
        ],
    )
    r = await client.get("/v1/inbox/threads", params={"tenant_id": DEV_TENANT, "channel": "call"})
    threads = r.json()
    assert len(threads) == 1
    assert threads[0]["identity"] == "+447700900123"
    r = await client.get(f"/v1/inbox/threads/{threads[0]['id']}", params={"tenant_id": DEV_TENANT})
    m = r.json()["messages"][-1]
    assert m["call_id"] == cid
    assert m["author"] == "system"


async def test_missed_call_lands_in_voicemail_channel(client: AsyncClient, app: FastAPI) -> None:
    cid = "c-inbox-2"
    await _drain(
        client,
        app,
        cid,
        [
            (CallEventType.CALL_STARTED, {"caller": "+447700900124"}),
            (CallEventType.CALL_ENDED, {"reason": "no_answer", "transcript": []}),
        ],
    )
    r = await client.get(
        "/v1/inbox/threads", params={"tenant_id": DEV_TENANT, "channel": "voicemail"}
    )
    assert len(r.json()) == 1
    assert r.json()[0]["unread"] >= 1


# -- WhatsApp -------------------------------------------------------------------------------------


async def test_whatsapp_config_and_webhook(client: AsyncClient, app: FastAPI) -> None:
    q = {"tenant_id": DEV_TENANT}
    r = await client.put(
        "/v1/inbox/whatsapp",
        params=q,
        json={"phone_number_id": "1234567890", "display_number": "+442046206823"},
    )
    assert r.status_code == 200, r.text
    acct = r.json()
    assert "access_token" not in acct
    verify = acct["verify_token"]

    r = await client.get(
        "/v1/inbound/whatsapp",
        params={"hub.mode": "subscribe", "hub.verify_token": verify, "hub.challenge": "42"},
    )
    assert r.status_code == 200 and r.text == "42"
    r = await client.get(
        "/v1/inbound/whatsapp",
        params={"hub.mode": "subscribe", "hub.verify_token": "nope", "hub.challenge": "42"},
    )
    assert r.status_code == 403

    r = await client.post("/v1/inbound/whatsapp", json=meta_wa("hello, are you open?"))
    assert r.status_code == 200
    assert r.json()["handled"] == 1
    r = await client.get("/v1/inbox/threads", params={**q, "channel": "whatsapp"})
    t = r.json()[0]
    assert t["contact_name"] == "Sam"
    r = await client.get(f"/v1/inbox/threads/{t['id']}", params=q)
    assert [m["author"] for m in r.json()["messages"]] == ["contact", "ai"]

    # unknown phone_number_id ignored
    r = await client.post("/v1/inbound/whatsapp", json=meta_wa("x", phone_id="000"))
    assert r.json()["handled"] == 0


async def test_whatsapp_signature_enforced(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(get_settings(), "whatsapp_app_secret", "app-secret")
    body = json.dumps(meta_wa("hi")).encode()
    r = await client.post(
        "/v1/inbound/whatsapp", content=body, headers={"content-type": "application/json"}
    )
    assert r.status_code == 401
    sig = "sha256=" + hmac.new(b"app-secret", body, hashlib.sha256).hexdigest()
    r = await client.post(
        "/v1/inbound/whatsapp",
        content=body,
        headers={"content-type": "application/json", "x-hub-signature-256": sig},
    )
    assert r.status_code == 200


# -- web chat widget -------------------------------------------------------------------------------


async def test_webchat_widget_flow(client: AsyncClient) -> None:
    q = {"tenant_id": DEV_TENANT}
    await _set_faq(client)
    r = await client.get("/v1/inbox/widget", params=q)
    assert r.status_code == 200, r.text
    token = r.json()["token"]
    assert r.json()["enabled"] is True

    r = await client.get(f"/v1/public/chat/{token}")
    assert r.status_code == 200
    assert "token" not in r.json()
    assert r.json()["greeting"]

    visitor = "visitor-abcdef123456"
    r = await client.post(
        f"/v1/public/chat/{token}/messages",
        json={"visitor": visitor, "text": "is parking free?", "name": "Jo"},
    )
    assert r.status_code == 200, r.text
    msgs = r.json()
    assert [m["author"] for m in msgs] == ["contact", "ai"]
    assert "parking" in msgs[1]["text"].lower()

    r = await client.get(f"/v1/public/chat/{token}/messages", params={"visitor": visitor})
    assert len(r.json()) == 2
    r = await client.get(
        f"/v1/public/chat/{token}/messages", params={"visitor": "visitor-nobody-000000"}
    )
    assert r.json() == []

    # internal notes never leak to the visitor
    r = await client.get("/v1/inbox/threads", params={**q, "channel": "webchat"})
    tid = r.json()[0]["id"]
    await client.post(f"/v1/inbox/threads/{tid}/note", params=q, json={"text": "secret"})
    r = await client.get(f"/v1/public/chat/{token}/messages", params={"visitor": visitor})
    assert all(m["text"] != "secret" for m in r.json())

    # disable + rotate
    r = await client.patch("/v1/inbox/widget", params=q, json={"enabled": False})
    assert r.json()["enabled"] is False
    r = await client.get(f"/v1/public/chat/{token}")
    assert r.status_code == 404
    r = await client.patch(
        "/v1/inbox/widget", params=q, json={"enabled": True, "rotate_token": True}
    )
    assert r.json()["token"] != token
    r = await client.get(f"/v1/public/chat/{token}")
    assert r.status_code == 404

    r = await client.post(
        f"/v1/public/chat/{r.json().get('token', 'x')}/messages",
        json={"visitor": "short", "text": "hi"},
    )
    assert r.status_code in (404, 422)


async def test_widget_requires_admin(client: AsyncClient, app: FastAPI) -> None:
    r = await client.post(
        f"/v1/organisations/{DEV_TENANT}/members",
        json={"email": "agent@demo.parlio.local", "role": "member"},
    )
    assert r.status_code in (200, 201), r.text
    hdr = {"X-Parlio-User": "agent@demo.parlio.local"}
    r = await client.patch(
        "/v1/inbox/widget", params={"tenant_id": DEV_TENANT}, json={"enabled": False}, headers=hdr
    )
    assert r.status_code == 403
    r = await client.get("/v1/inbox/threads", params={"tenant_id": DEV_TENANT}, headers=hdr)
    assert r.status_code == 200


async def test_service_direct_isolation(client: AsyncClient, app: FastAPI) -> None:
    svc = _svc(app)
    await svc.inbound("demo", InboundText(channel=Channel.SMS, identity="+4471", text="a"))
    assert await svc.threads("other") == []
    t = await svc.find_existing("demo", Channel.SMS, "+4471")
    assert t is not None and t.status in (ThreadStatus.OPEN, ThreadStatus.WAITING)
    assert await svc.find_existing("other", Channel.SMS, "+4471") is None


async def test_nav_badges_and_handoff_alert(client: AsyncClient, app: FastAPI) -> None:
    r = await client.get("/v1/nav/badges", params={"tenant_id": DEV_TENANT})
    assert r.status_code == 200
    assert r.json()["inbox"] == 0

    live = app.state.live
    q = live.subscribe(DEV_TENANT)
    try:
        r = await client.post("/v1/inbound/sms", json=telnyx_sms("I need to speak to a human"))
        assert r.status_code == 202
        r = await client.post("/v1/inbound/sms", json=telnyx_sms("about an invoice"))
        assert r.status_code == 202
        types = []
        while not q.empty():
            types.append(q.get_nowait().type)
        assert "inbox.handoff" in types
    finally:
        live.unsubscribe(DEV_TENANT, q)

    r = await client.get("/v1/nav/badges", params={"tenant_id": DEV_TENANT})
    body = r.json()
    assert body["inbox"] >= 1
    assert body["total"] >= body["inbox"]

    r = await client.get("/v1/nav/badges", params={"tenant_id": "other"})
    assert r.status_code == 403


async def test_nav_snapshot(client: AsyncClient) -> None:
    r = await client.get("/v1/nav/snapshot", params={"tenant_id": DEV_TENANT})
    assert r.status_code == 200
    body = r.json()
    assert body["active_calls"] == 0
    assert body["waiting_chats"] == 0
    assert body["avg_answer_s"] is None

    r = await client.post("/v1/inbound/sms", json=telnyx_sms("I need to speak to a human"))
    assert r.status_code == 202
    r = await client.post("/v1/inbound/sms", json=telnyx_sms("about an invoice"))
    assert r.status_code == 202
    r = await client.get("/v1/nav/snapshot", params={"tenant_id": DEV_TENANT})
    assert r.json()["waiting_chats"] >= 1

    r = await client.get("/v1/nav/snapshot", params={"tenant_id": "other"})
    assert r.status_code == 403
