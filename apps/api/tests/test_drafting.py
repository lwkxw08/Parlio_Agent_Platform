"""Ask AI to draft (Studio fields) + mobile forwarding guide."""

from __future__ import annotations

import json

import httpx
import pytest
from fastapi import FastAPI
from httpx import AsyncClient

from parlio_api.drafting import Drafter, DraftRequest, find_url, template_draft
from parlio_api.onboarding import analyse_html
from parlio_api.sip import PROVIDER_GUIDES
from parlio_voice.config_client import DEMO_CONFIG

SITE = """<html><head><title>Parlio Demo Plumbing</title>
<meta name="description"
 content="Family-run plumbers covering Manchester. 24/7 emergency call-outs."></head>
<body><h1>Parlio Demo Plumbing</h1><h2>Our services</h2>
<h3>Boiler repairs</h3><h3>Leak detection</h3>
<p>Call 0161 123 4567. Open Mon-Fri 8am-6pm.</p></body></html>"""


def test_find_url() -> None:
    assert (
        find_url("write a description, use www.parliodemo.co.uk please") == "www.parliodemo.co.uk"
    )
    assert find_url("see https://example.com/about.") == "https://example.com/about"
    assert find_url("email me at bob@example.com") is None
    assert find_url("no links here e.g. this") is None


def test_template_fallback_uses_site_facts() -> None:
    site = analyse_html("https://parliodemo.co.uk", SITE)
    req = DraftRequest(field="description", brief="describe Parlio Demo Plumbing")
    text = template_draft(req, DEMO_CONFIG, site)
    assert "Parlio Demo Plumbing" in text and "24/7" in text
    generic = template_draft(
        DraftRequest(field="description", brief="help me write a description"), DEMO_CONFIG, None
    )
    assert "help me write" not in generic and DEMO_CONFIG.business_name in generic
    services = template_draft(
        DraftRequest(field="services", brief="list services"), DEMO_CONFIG, site
    )
    assert "Boiler repairs" in services.splitlines()
    sms = template_draft(
        DraftRequest(field="sms_template", brief="thank them for calling"), DEMO_CONFIG, None
    )
    assert "{name}" in sms and len(sms) <= 320


async def test_drafter_without_key_is_template() -> None:
    d = Drafter(None, fetch_sites=False)
    out = await d.draft(DraftRequest(field="greeting", brief="warm greeting"), DEMO_CONFIG)
    assert out.source == "template" and out.text and out.website_used is None


async def test_drafter_llm_and_failure_fallback() -> None:
    seen: list[dict] = []  # type: ignore[type-arg]

    def ok(request: httpx.Request) -> httpx.Response:
        seen.append(json.loads(request.content))
        return httpx.Response(
            200, json={"choices": [{"message": {"content": '"We are a family-run plumber."'}}]}
        )

    d = Drafter(
        "sk-test",
        client=httpx.AsyncClient(transport=httpx.MockTransport(ok), base_url="https://llm"),
        fetch_sites=False,
    )
    out = await d.draft(
        DraftRequest(field="description", brief="describe us", current="old text", context=None),
        DEMO_CONFIG,
    )
    assert out.source == "llm" and out.text == "We are a family-run plumber."
    user_msg = seen[0]["messages"][1]["content"]
    assert "old text" in user_msg and "describe us" in user_msg

    def boom(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text="nope")

    d2 = Drafter(
        "sk-test",
        client=httpx.AsyncClient(transport=httpx.MockTransport(boom), base_url="https://llm"),
        fetch_sites=False,
    )
    out2 = await d2.draft(DraftRequest(field="rule", brief="no same-day after 3pm"), DEMO_CONFIG)
    assert out2.source == "template" and "3pm" in out2.text


@pytest.mark.parametrize("backend", ["memory"], indirect=True)
async def test_draft_endpoint(client: AsyncClient, app: FastAPI) -> None:
    r = await client.post(
        "/v1/assistants/demo/draft",
        json={
            "field": "faq_answer",
            "brief": "we cover Greater Manchester",
            "context": "Where do you cover?",
        },
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["source"] == "template" and "Greater Manchester" in body["text"]

    r = await client.post(
        "/v1/assistants/nope/draft", json={"field": "description", "brief": "abc"}
    )
    assert r.status_code == 404
    r = await client.post("/v1/assistants/demo/draft", json={"field": "bogus", "brief": "abc"})
    assert r.status_code == 422
    r = await client.post("/v1/assistants/demo/draft", json={"field": "description", "brief": "a"})
    assert r.status_code == 422


def test_mobile_forwarding_guide() -> None:
    g = next(g for g in PROVIDER_GUIDES if g.id == "mobile")
    steps = " ".join(g.steps)
    assert "**61*" in steps and "**21*" in steps and "##002#" in steps
    assert any("tariff" in q.lower() or "charge" in q.lower() for q in g.quirks)
