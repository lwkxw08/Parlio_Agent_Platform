"""Browser voice (Phase 11b): job metadata the Core API attaches when a widget visitor clicks
"talk". Mirrors ``parlio_api.browser_voice.WebVoiceJob``. The visitor joins the same room as a
normal WebRTC participant; the caller identity is ``web:<visitor>`` and the dialed number ``web``.
"""

from __future__ import annotations

import json
import logging

from pydantic import BaseModel, ValidationError

log = logging.getLogger("parlio.web")

WEB_DIALED = "web"
WEB_CALLER_PREFIX = "web:"


class WebJob(BaseModel):
    call_id: str
    tenant_id: str
    assistant_id: str
    visitor: str
    name: str | None = None
    page_url: str | None = None

    @property
    def caller(self) -> str:
        return f"{WEB_CALLER_PREFIX}{self.visitor}"

    def instructions(self) -> str:
        who = f"The visitor gave their name as {self.name}. " if self.name else ""
        where = f"They are on the page {self.page_url}. " if self.page_url else ""
        return (
            "This conversation is a voice call from the website chat widget, not a phone call. "
            f"{who}{where}There is no caller ID: if you need to text a link or confirmation, ask "
            "for a mobile number first. Everything else works as on the phone."
        )


def parse_web(metadata: str | None) -> WebJob | None:
    if not metadata:
        return None
    try:
        raw = json.loads(metadata)
    except ValueError:
        return None
    if not isinstance(raw, dict) or "web" not in raw:
        return None
    try:
        return WebJob.model_validate(raw["web"])
    except ValidationError as e:
        log.warning("web metadata rejected: %s", [err["loc"] for err in e.errors()])
        return None
