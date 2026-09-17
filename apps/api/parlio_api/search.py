"""Phase 20i: transcript & recording search.

The store finds candidate calls (in-memory substring match locally, Postgres FTS in production;
ClickHouse when Part G triggers). This module turns them into hits: which lines matched, a
snippet around each match and the offset into the recording so the dashboard can jump
straight to that moment.
"""

from __future__ import annotations

import re
from datetime import datetime

from pydantic import BaseModel, Field

from parlio_api.sites import Site, site_of
from parlio_api.store import CallRecord, search_terms

_SNIPPET = 90


class Moment(BaseModel):
    seq: int
    role: str
    snippet: str
    at: datetime | None = None
    offset_s: float | None = None  # seconds into the recording (None when not timestamped)
    recording_index: int | None = None  # index into CallRecord.recordings to seek in


class SearchHit(BaseModel):
    call_id: str
    started_at: datetime
    party: str | None
    direction: str
    kind: str
    duration_s: float | None
    summary: str | None
    summary_matched: bool
    site_id: str | None = None
    site_name: str | None = None
    recordings: int = 0
    moments: list[Moment] = Field(default_factory=list)
    total_matches: int = 0


class SearchResponse(BaseModel):
    query: str
    total: int
    hits: list[SearchHit]
    engine: str = "postgres_fts"  # or "memory"


def _parse_at(raw: object) -> datetime | None:
    if isinstance(raw, datetime):
        return raw
    if isinstance(raw, str) and raw:
        try:
            return datetime.fromisoformat(raw)
        except ValueError:
            return None
    return None


def _snippet(text: str, term_re: re.Pattern[str]) -> str:
    m = term_re.search(text)
    if m is None or len(text) <= 2 * _SNIPPET:
        return text
    start = max(0, m.start() - _SNIPPET)
    end = min(len(text), m.end() + _SNIPPET)
    return ("…" if start else "") + text[start:end].strip() + ("…" if end < len(text) else "")


def _term_re(query: str) -> re.Pattern[str] | None:
    terms = [re.escape(t) for t in search_terms(query)]
    return re.compile("|".join(terms), re.IGNORECASE) if terms else None


def hit_for(call: CallRecord, query: str, sites: list[Site], *, max_moments: int = 5) -> SearchHit:
    term_re = _term_re(query)
    # the recording clock starts when the call is answered (greeting), else at call start
    t0 = call.answered_at or call.started_at
    rec_index = 0 if call.recordings else None
    moments: list[Moment] = []
    total = 0
    for seq, item in enumerate(call.transcript):
        text = str(item.get("text") or "")
        if term_re is None or not term_re.search(text):
            continue
        total += 1
        if len(moments) >= max_moments:
            continue
        at = _parse_at(item.get("at"))
        offset = None
        if at is not None and t0 is not None and at.tzinfo is not None and t0.tzinfo is not None:
            offset = max(0.0, round((at - t0).total_seconds(), 1))
        moments.append(
            Moment(
                seq=seq,
                role=str(item.get("role") or "unknown"),
                snippet=_snippet(text, term_re),
                at=at,
                offset_s=offset,
                recording_index=rec_index if offset is not None else None,
            )
        )
    summary_matched = bool(term_re and call.summary and term_re.search(call.summary))
    site = site_of(call, sites)
    return SearchHit(
        call_id=call.call_id,
        started_at=call.started_at,
        party=call.party,
        direction=call.direction,
        kind=call.kind,
        duration_s=call.duration_s,
        summary=call.summary,
        summary_matched=summary_matched,
        site_id=site.id if site else None,
        site_name=site.name if site else None,
        recordings=len(call.recordings),
        moments=moments,
        total_matches=total + (1 if summary_matched else 0),
    )


def build_response(
    calls: list[CallRecord], query: str, sites: list[Site], *, engine: str
) -> SearchResponse:
    hits = [hit_for(c, query, sites) for c in calls]
    # FTS may match on stems the simple snippet matcher misses; keep those hits, unranked
    hits.sort(key=lambda h: (h.total_matches == 0, h.started_at.timestamp() * -1))
    return SearchResponse(query=query, total=len(hits), hits=hits, engine=engine)
