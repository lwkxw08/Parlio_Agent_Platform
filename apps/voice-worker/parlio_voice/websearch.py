"""Live look-ups on the tenant's own website during a call (Studio -> Business -> Website search).

Domain-locked: only pages on the business website's host (plus explicitly listed extra URLs on
that host) are ever fetched. Pages are fetched once per process and cached, so the mid-call tool
call is a ranking over already-downloaded text and stays well inside a conversational turn.
"""

from __future__ import annotations

import asyncio
import logging
import re
import time
from html.parser import HTMLParser
from typing import Any
from urllib.parse import urldefrag, urljoin, urlparse

import httpx

log = logging.getLogger("parlio.websearch")

_CACHE_TTL_S = 15 * 60
_MAX_PAGE_BYTES = 400_000
_SNIPPET_CHARS = 320
_SKIP_EXT = (".pdf", ".jpg", ".jpeg", ".png", ".gif", ".svg", ".zip", ".mp3", ".mp4", ".webp")
_WORD = re.compile(r"[a-z0-9£$€]+")
_STOP_WORDS = (
    "a an the and or of to in on for with at by from is are be do does can you your we our us "
    "it this that how what when where which who much many any some i my me please"
)
_STOP = frozenset(_STOP_WORDS.split())


class _Page(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.title = ""
        self.blocks: list[str] = []
        self.links: list[str] = []
        self._skip = 0
        self._in_title = False
        self._buf: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in ("script", "style", "noscript", "svg", "nav", "footer", "header"):
            self._skip += 1
        if tag == "title":
            self._in_title = True
        if tag == "a":
            href = dict(attrs).get("href")
            if href:
                self.links.append(href)
        if tag in ("p", "li", "h1", "h2", "h3", "h4", "td", "div", "section", "article", "br"):
            self._flush()

    def handle_endtag(self, tag: str) -> None:
        if tag in ("script", "style", "noscript", "svg", "nav", "footer", "header") and self._skip:
            self._skip -= 1
        if tag == "title":
            self._in_title = False
        if tag in ("p", "li", "h1", "h2", "h3", "h4", "td", "div", "section", "article"):
            self._flush()

    def handle_data(self, data: str) -> None:
        if self._in_title:
            self.title = (self.title + " " + data).strip()
            return
        if self._skip:
            return
        self._buf.append(data)

    def _flush(self) -> None:
        text = " ".join(" ".join(self._buf).split())
        self._buf = []
        if len(text) >= 30:
            self.blocks.append(text)

    def close(self) -> None:
        super().close()
        self._flush()


class Snippet(dict[str, Any]):
    """{"url", "title", "text", "score"} - a dict so it serialises straight into the tool result."""


class SiteIndex:
    def __init__(self, root: str, extra_urls: list[str], max_pages: int) -> None:
        self.root = root if root.startswith(("http://", "https://")) else "https://" + root
        self.host = urlparse(self.root).netloc.lower().removeprefix("www.")
        self.extra_urls = extra_urls
        self.max_pages = max(1, min(max_pages, 40))
        self.pages: list[tuple[str, str, list[str]]] = []  # url, title, blocks
        self.built_at = 0.0
        self._lock = asyncio.Lock()

    def _same_site(self, url: str) -> bool:
        host = urlparse(url).netloc.lower().removeprefix("www.")
        return host == self.host

    async def build(self, client: httpx.AsyncClient | None = None) -> None:
        async with self._lock:
            if self.pages and time.monotonic() - self.built_at < _CACHE_TTL_S:
                return
            own = client is None
            client = client or httpx.AsyncClient(timeout=6.0, follow_redirects=True)
            queue = [self.root, *[u for u in self.extra_urls if self._same_site(u)]]
            seen: set[str] = set()
            pages: list[tuple[str, str, list[str]]] = []
            try:
                while queue and len(pages) < self.max_pages:
                    url = urldefrag(queue.pop(0)).url.rstrip("/") or self.root
                    if url in seen or not self._same_site(url):
                        continue
                    if url.lower().endswith(_SKIP_EXT):
                        continue
                    seen.add(url)
                    try:
                        r = await client.get(url, headers={"user-agent": "ParlioTec-Assistant/1.0"})
                        if r.status_code >= 400 or "html" not in r.headers.get("content-type", ""):
                            continue
                        if not self._same_site(str(r.url)):  # redirected off-site
                            continue
                        p = _Page()
                        p.feed(r.text[:_MAX_PAGE_BYTES])
                        p.close()
                    except Exception as e:  # unreachable page: skip, never fail the call
                        log.info("website search skipped %s: %s", url, e)
                        continue
                    pages.append((str(r.url), p.title, p.blocks))
                    for href in p.links:
                        nxt = urljoin(str(r.url), href)
                        if nxt.startswith(("http://", "https://")) and self._same_site(nxt):
                            queue.append(nxt)
            finally:
                if own:
                    await client.aclose()
            self.pages = pages
            self.built_at = time.monotonic()

    def search(self, query: str, limit: int = 3) -> list[Snippet]:
        terms = [w for w in _WORD.findall(query.lower()) if w not in _STOP]
        if not terms:
            return []
        scored: list[Snippet] = []
        for url, title, blocks in self.pages:
            for block in blocks:
                low = block.lower()
                hits = sum(low.count(t) for t in terms)
                distinct = sum(1 for t in terms if t in low)
                if not distinct:
                    continue
                score = distinct * 2 + hits + (1 if any(t in title.lower() for t in terms) else 0)
                text = block if len(block) <= _SNIPPET_CHARS else block[:_SNIPPET_CHARS] + "..."
                scored.append(Snippet(url=url, title=title, text=text, score=score))
        scored.sort(key=lambda s: -s["score"])
        out: list[Snippet] = []
        for s in scored:
            if any(o["text"] == s["text"] for o in out):
                continue
            out.append(s)
            if len(out) >= limit:
                break
        return out


_INDEXES: dict[str, SiteIndex] = {}


def site_index(root: str, extra_urls: list[str], max_pages: int) -> SiteIndex:
    key = f"{root}|{','.join(extra_urls)}|{max_pages}"
    idx = _INDEXES.get(key)
    if idx is None:
        idx = _INDEXES[key] = SiteIndex(root, extra_urls, max_pages)
    return idx
