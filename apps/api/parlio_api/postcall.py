"""Post-call pipeline: runs after `call.ended`, off the live-call path.

summary + structured extraction (assistant's required fields) -> new/returning classification
-> missed-information flags -> stored on the call. The analyser is pluggable: `HeuristicAnalyser`
needs no vendor (used offline/CI); `OpenAIAnalyser` uses a JSON-mode chat completion.
Dispatch is an in-process queue for now; swap for a Redis Stream/NATS consumer at scale
without touching the analysers or the store.
"""

from __future__ import annotations

import asyncio
import logging
import re
from collections.abc import Awaitable, Callable
from typing import Any, Protocol

import httpx
from pydantic import BaseModel, Field

from parlio_api.store import CallRecord, CallStore, ContactUpdate, PostCallResult, RequiredField

log = logging.getLogger("parlio.api.postcall")

# Always extracted (on top of the assistant's own required fields) so the caller's contact card
# fills itself in from the conversation even when no fields are configured.
BASELINE_FIELDS = [
    RequiredField(name="name", description="caller's full name", required=False),
    RequiredField(name="email", description="caller's email address", required=False),
]


def _with_baseline(fields: list[RequiredField]) -> list[RequiredField]:
    have = {f.name for f in fields}
    return fields + [f for f in BASELINE_FIELDS if f.name not in have]


_EMAIL = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")
_PHONE = re.compile(r"(?:\+44|0)\s?\d(?:[\s-]?\d){8,9}")
_NAME = re.compile(r"\b(?:my name is|this is|i'm|i am)\s+([A-Z][a-z]+(?:\s[A-Z][a-z]+)?)", re.I)


class Analysis(BaseModel):
    summary: str
    extracted: dict[str, Any] = Field(default_factory=dict)


class Analyser(Protocol):
    async def analyse(self, call: CallRecord, fields: list[RequiredField]) -> Analysis: ...


def _user_text(call: CallRecord) -> list[str]:
    return [str(t.get("text") or "") for t in call.transcript if t.get("role") == "user"]


class HeuristicAnalyser:
    """Deterministic, offline. Regex extraction for the common fields; summary from the caller's
    first utterance. Good enough for tests and as a fallback when the LLM vendor is down."""

    async def analyse(self, call: CallRecord, fields: list[RequiredField]) -> Analysis:
        user = _user_text(call)
        joined = " ".join(user)
        extracted: dict[str, Any] = {}
        if m := _EMAIL.search(joined):
            extracted["email"] = m.group(0)
        if m := _PHONE.search(joined):
            extracted["phone"] = re.sub(r"[\s-]", "", m.group(0))
        if m := _NAME.search(joined):
            extracted["name"] = m.group(1)
        wanted = {f.name for f in fields}
        extracted = {k: v for k, v in extracted.items() if not wanted or k in wanted}
        first = user[0].strip() if user else ""
        summary = (
            f'{len(user)} caller turn(s). Caller opened with: "{first[:160]}"'
            if first
            else "No caller speech captured."
        )
        return Analysis(summary=summary, extracted=extracted)


class OpenAIAnalyser:
    def __init__(
        self,
        api_key: str,
        model: str = "gpt-4o-mini",
        base_url: str = "https://api.openai.com/v1",
        client: httpx.AsyncClient | None = None,
        fallback: Analyser | None = None,
    ) -> None:
        self._client = client or httpx.AsyncClient(
            base_url=base_url, headers={"Authorization": f"Bearer {api_key}"}, timeout=30
        )
        self._model = model
        self._fallback = fallback or HeuristicAnalyser()

    async def analyse(self, call: CallRecord, fields: list[RequiredField]) -> Analysis:
        transcript = "\n".join(f"{t.get('role')}: {t.get('text')}" for t in call.transcript)
        schema = ", ".join(f'"{f.name}" ({f.description or f.name})' for f in fields) or "none"
        prompt = (
            "You summarise phone calls handled by an AI receptionist for a UK business.\n"
            'Return JSON: {"summary": <2 sentences, plain English>, "extracted": {<field>: '
            "<value or null>}}. Fields to extract: " + schema + ". Never invent values.\n\n"
            "Transcript:\n" + transcript
        )
        try:
            r = await self._client.post(
                "/chat/completions",
                json={
                    "model": self._model,
                    "temperature": 0,
                    "response_format": {"type": "json_object"},
                    "messages": [{"role": "user", "content": prompt}],
                },
            )
            r.raise_for_status()
            content = r.json()["choices"][0]["message"]["content"]
            data = Analysis.model_validate_json(content)
            data.extracted = {k: v for k, v in data.extracted.items() if v not in (None, "")}
            return data
        except Exception:
            log.warning("LLM analysis failed for %s; using heuristic", call.call_id, exc_info=True)
            return await self._fallback.analyse(call, fields)


async def process_call(store: CallStore, analyser: Analyser, call_id: str) -> PostCallResult | None:
    call = await store.get_call(call_id)
    if call is None:
        return None
    fields = await store.required_fields(call.assistant_id)
    analysis = await analyser.analyse(call, _with_baseline(fields))

    caller_type, contact_id = "unknown", None
    if call.caller and call.caller != "unknown" and not call.caller.startswith("web:"):
        contact_id, returning = await store.touch_contact(
            call.tenant_id, call.company_id, call.caller
        )
        caller_type = "returning" if returning else "new"
        await _fill_contact(store, contact_id, analysis.extracted)

    missed = [f.name for f in fields if f.required and not analysis.extracted.get(f.name)]
    result = PostCallResult(
        summary=analysis.summary,
        extracted=analysis.extracted,
        missed_fields=missed,
        caller_type=caller_type,
        contact_id=contact_id,
    )
    await store.record_postcall(call_id, result)
    return result


async def _fill_contact(store: CallStore, contact_id: str, extracted: dict[str, Any]) -> None:
    """Copy name/email the caller gave onto their contact card, never overwriting what's there."""
    contact = await store.get_contact(contact_id)
    if contact is None:
        return
    upd = ContactUpdate()
    name, email = extracted.get("name"), extracted.get("email")
    if not contact.name and isinstance(name, str) and name.strip():
        upd.name = name.strip()[:120]
    if not contact.email and isinstance(email, str) and _EMAIL.fullmatch(email.strip()):
        upd.email = email.strip()
    if upd.model_dump(exclude_none=True):
        await store.update_contact(contact_id, upd)


class PostCallProcessor:
    """Bounded in-process worker pool feeding `process_call`."""

    def __init__(
        self,
        store: CallStore,
        analyser: Analyser,
        concurrency: int = 4,
        on_done: Callable[[CallRecord], Awaitable[None]] | None = None,
    ) -> None:
        self._store = store
        self._analyser = analyser
        self.on_done = on_done
        self._queue: asyncio.Queue[str] = asyncio.Queue()
        self._workers = [asyncio.create_task(self._run()) for _ in range(concurrency)]

    def enqueue(self, call_id: str) -> None:
        self._queue.put_nowait(call_id)

    async def drain(self) -> None:
        await self._queue.join()

    async def _run(self) -> None:
        while True:
            call_id = await self._queue.get()
            try:
                result = await process_call(self._store, self._analyser, call_id)
                if result is not None and self.on_done is not None:
                    call = await self._store.get_call(call_id)
                    if call is not None:
                        await self.on_done(call)
            except Exception:
                log.exception("post-call processing failed for %s", call_id)
            finally:
                self._queue.task_done()

    async def close(self) -> None:
        for w in self._workers:
            w.cancel()
        await asyncio.gather(*self._workers, return_exceptions=True)
