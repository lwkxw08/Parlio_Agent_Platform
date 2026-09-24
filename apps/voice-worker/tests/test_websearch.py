"""Domain-locked live website search (Fonio #5)."""

from __future__ import annotations

import httpx
import pytest

from parlio_voice.models import AssistantConfig, BusinessInfo, WebsiteSearchConfig
from parlio_voice.tools import ReceptionistTools
from parlio_voice.transfer import SimulatedBridge, TransferEngine
from parlio_voice.websearch import SiteIndex, site_index

HTML = "text/html; charset=utf-8"

PAGES = {
    "https://acme.co.uk": (
        "<html><head><title>Acme Heating</title></head><body><nav><a href='/prices'>x</a></nav>"
        "<p>Acme Heating is a family-run boiler installation and servicing company in Manchester "
        "covering Stockport and Salford.</p>"
        "<a href='/prices'>Prices</a> <a href='https://www.acme.co.uk/about'>About</a> "
        "<a href='https://evil.example.com/steal'>Partner</a> <a href='/brochure.pdf'>PDF</a>"
        "</body></html>"
    ),
    "https://acme.co.uk/prices": (
        "<html><head><title>Prices</title></head><body>"
        "<p>A standard boiler service costs £89 including VAT and takes about an hour.</p>"
        "<p>Landlord gas safety certificates are £65 when booked with a service.</p>"
        "</body></html>"
    ),
    "https://acme.co.uk/about": (
        "<html><head><title>About</title></head><body>"
        "<p>We have been installing boilers across Greater Manchester since 1998 and are Gas Safe "
        "registered.</p></body></html>"
    ),
    "https://acme.co.uk/redirect": None,  # 302 off-site
}

fetched: list[str] = []


def _handler(request: httpx.Request) -> httpx.Response:
    url = str(request.url).rstrip("/").replace("://www.", "://")
    fetched.append(url)
    if url.startswith("https://evil.example.com"):
        return httpx.Response(
            200, headers={"content-type": HTML}, text="<p>SECRET off-site page</p>"
        )
    if url == "https://acme.co.uk/redirect":
        return httpx.Response(302, headers={"location": "https://evil.example.com/landing"})
    if url == "https://evil.example.com/landing":
        return httpx.Response(200, headers={"content-type": HTML}, text="<p>LANDING off-site</p>")
    body = PAGES.get(url)
    if body is None:
        return httpx.Response(404)
    return httpx.Response(200, headers={"content-type": HTML}, text=body)


def _client() -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(_handler), follow_redirects=True)


async def test_index_stays_on_the_tenant_host_and_ranks_snippets() -> None:
    fetched.clear()
    idx = SiteIndex("www.acme.co.uk", ["https://acme.co.uk/redirect"], max_pages=10)
    async with _client() as c:
        await idx.build(c)
    urls = {u.replace("://www.", "://") for u, _, _ in idx.pages}
    assert urls == {"https://acme.co.uk", "https://acme.co.uk/prices", "https://acme.co.uk/about"}
    # the off-site link was never fetched; the PDF was skipped; redirect target not indexed
    assert not any("evil.example.com/steal" in u for u in fetched)
    assert not any(u.endswith(".pdf") for u in fetched)
    assert all("off-site" not in " ".join(b) for _, _, b in idx.pages)

    hits = idx.search("how much is a boiler service?")
    assert hits and hits[0]["url"].endswith("acme.co.uk/prices")
    assert "£89" in hits[0]["text"]
    assert idx.search("the and of") == []  # stop words only
    assert idx.search("swimming pool") == []  # nothing on the site


async def test_page_cap_and_extra_urls_off_host_are_ignored() -> None:
    idx = SiteIndex("https://acme.co.uk", ["https://evil.example.com/steal"], max_pages=1)
    async with _client() as c:
        await idx.build(c)
    assert [u for u, _, _ in idx.pages] == ["https://acme.co.uk"]


async def test_build_survives_unreachable_site() -> None:
    def boom(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("no route")

    idx = SiteIndex("https://down.example", [], 5)
    async with httpx.AsyncClient(transport=httpx.MockTransport(boom)) as c:
        await idx.build(c)
    assert idx.pages == [] and idx.search("anything") == []


def test_site_index_cache_is_keyed_per_tenant_website() -> None:
    a = site_index("https://acme.co.uk", [], 12)
    b = site_index("https://other.co.uk", [], 12)
    assert a is not b and a is site_index("https://acme.co.uk", [], 12)
    assert site_index("https://acme.co.uk", ["https://acme.co.uk/x"], 12) is not a


@pytest.mark.parametrize(
    ("enabled", "website", "expect"),
    [(True, "https://acme.co.uk", True), (False, "https://acme.co.uk", False), (True, None, False)],
)
def test_tool_only_exposed_when_enabled_and_website_set(
    enabled: bool, website: str | None, expect: bool
) -> None:
    cfg = AssistantConfig(
        tenant_id="t1",
        company_id="c1",
        assistant_id="a1",
        business=BusinessInfo(website=website),
        website_search=WebsiteSearchConfig(enabled=enabled),
    )

    async def say(_: str) -> None:
        return None

    engine = TransferEngine(cfg.transfer, SimulatedBridge())
    tools = ReceptionistTools(cfg, "call-1", "+447700900001", engine, None, lambda *_: None, say)
    assert (tools.site is not None) is expect
