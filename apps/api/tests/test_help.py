"""In-app user guide + Ask Parlio."""

from __future__ import annotations

import httpx
import pytest
from httpx import AsyncClient

from parlio_api.help import Guide, HelpAssistant, HelpQuestion

OWNER = {"X-Parlio-User": "owner@demo.parlio.local"}


def test_guide_loads_every_page_with_route_and_sections() -> None:
    g = Guide()
    assert g.loaded and g.directory is not None
    slugs = {p.slug for p in g.pages}
    for expected in ("assistant-studio", "integrations", "telephony", "calls", "contacts", "faq"):
        assert expected in slugs
    for p in g.pages:
        assert p.route.startswith("/"), p.slug
        assert p.summary, p.slug
        assert p.sections, p.slug
        assert len({s.anchor for s in p.sections}) == len(p.sections), p.slug


def test_route_lookup_picks_the_page_for_the_screen() -> None:
    g = Guide()
    assert g.page_for_route("/assistant").slug == "assistant-studio"  # type: ignore[union-attr]
    assert g.page_for_route("/calls/abc123?x=1").slug == "calls"  # type: ignore[union-attr]
    assert g.page_for_route("/handoff").slug == "transfers"  # type: ignore[union-attr]
    assert g.page_for_route("/").slug == "getting-started"  # type: ignore[union-attr]
    assert g.page_for_route("/admin/tenants") is None


@pytest.mark.parametrize(
    ("question", "page", "anchor_contains"),
    [
        ("can I block anonymous callers?", "assistant-studio", "call-screening"),
        ("how do I stop withheld numbers ringing us", "assistant-studio", "call-screening"),
        ("text me a summary after every call", "integrations", "text-me-after-every-call"),
        ("connect my google calendar", "integrations", "connect"),
        ("how do I invite a colleague", "team", "invite"),
        ("delete a customer's data under GDPR", "compliance", "erasure"),
        ("why did a test call happen that I didn't make", "health", "synthetic"),
    ],
)
def test_search_finds_the_setting(question: str, page: str, anchor_contains: str) -> None:
    hits = Guide().search(question, limit=4)
    assert hits
    paths = [(h.section.page, h.section.anchor) for h in hits]
    assert any(p == page and anchor_contains in a for p, a in paths), paths


async def test_ask_without_llm_returns_guide_text_and_citations() -> None:
    h = HelpAssistant(Guide(), api_key=None)
    ans = await h.ask(HelpQuestion(question="can I block anonymous callers?"))
    assert ans.source == "guide"
    assert "Reject withheld" in ans.answer
    assert ans.confident
    assert any(c.page == "assistant-studio" and c.anchor == "call-screening" for c in ans.citations)


async def test_ask_unknown_topic_is_honest() -> None:
    h = HelpAssistant(Guide(), api_key=None)
    ans = await h.ask(HelpQuestion(question="zzqx plorf"))
    assert ans.source == "none" and not ans.citations and "support" in ans.answer.lower()


async def test_ask_with_llm_is_grounded_and_maps_citations() -> None:
    seen: dict[str, object] = {}

    def handler(req: httpx.Request) -> httpx.Response:
        import json

        body = json.loads(req.content)
        seen["body"] = body
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {
                            "content": "Yes. Go to **Assistant Studio → Call screening** and tick "
                            "Reject withheld / anonymous caller IDs, then Publish. [2]"
                        }
                    }
                ]
            },
        )

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="https://llm")
    h = HelpAssistant(Guide(), api_key="k", client=client)
    ans = await h.ask(HelpQuestion(question="can I block anonymous callers?", route="/assistant"))
    assert ans.source == "llm"
    assert ans.citations and ans.citations[0].anchor == "call-screening"
    body = seen["body"]
    assert isinstance(body, dict)
    user_msg = body["messages"][-1]["content"]
    assert "Guide excerpts" in user_msg and "Reject withheld" in user_msg


async def test_ask_llm_failure_falls_back_to_guide() -> None:
    def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(500)

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="https://llm")
    h = HelpAssistant(Guide(), api_key="k", client=client)
    ans = await h.ask(HelpQuestion(question="stop recording calls"))
    assert ans.source == "guide" and ans.citations


async def test_help_routes(client: AsyncClient) -> None:
    r = await client.get("/v1/help/pages", headers=OWNER)
    assert r.status_code == 200
    pages = r.json()
    assert any(p["slug"] == "assistant-studio" for p in pages)

    r = await client.get("/v1/help/pages/assistant-studio", headers=OWNER)
    assert r.status_code == 200
    assert any(s["heading"] == "Call screening" for s in r.json()["sections"])

    r = await client.get("/v1/help/pages/nope", headers=OWNER)
    assert r.status_code == 404

    r = await client.get("/v1/help/for-route", params={"route": "/integrations"}, headers=OWNER)
    assert r.status_code == 200 and r.json()["slug"] == "integrations"

    r = await client.post(
        "/v1/help/ask", json={"question": "can I block anonymous callers?"}, headers=OWNER
    )
    assert r.status_code == 200
    data = r.json()
    assert data["source"] in ("guide", "llm")
    assert any(c["anchor"] == "call-screening" for c in data["citations"])

    r = await client.post("/v1/help/ask", json={"question": "?"})
    assert r.status_code in (401, 422)
