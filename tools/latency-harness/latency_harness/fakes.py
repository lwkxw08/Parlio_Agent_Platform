"""Deterministic offline LLM so simulated calls run in CI without vendor keys."""

from __future__ import annotations

import asyncio
from collections.abc import Sequence

from livekit.agents import DEFAULT_API_CONNECT_OPTIONS, APIConnectOptions, llm
from livekit.agents.llm import ChatChunk, ChatContext, ChoiceDelta, LLMStream
from livekit.agents.llm.tool_context import Tool
from livekit.agents.types import NOT_GIVEN, NotGivenOr


class ScriptedLLM(llm.LLM):
    """Replies with canned answers in order, streaming a few tokens with a configurable delay."""

    def __init__(self, replies: Sequence[str], ttft_s: float = 0.05, token_delay_s: float = 0.005):
        super().__init__()
        self._replies = list(replies)
        self._i = 0
        self._ttft_s = ttft_s
        self._token_delay_s = token_delay_s

    @property
    def model(self) -> str:
        return "scripted"

    @property
    def provider(self) -> str:
        return "parlio-fake"

    def chat(
        self,
        *,
        chat_ctx: ChatContext,
        tools: list[Tool] | None = None,
        conn_options: APIConnectOptions = DEFAULT_API_CONNECT_OPTIONS,
        parallel_tool_calls: NotGivenOr[bool] = NOT_GIVEN,
        tool_choice: NotGivenOr[llm.ToolChoice] = NOT_GIVEN,
        extra_kwargs: NotGivenOr[dict[str, object]] = NOT_GIVEN,
    ) -> LLMStream:
        reply = self._replies[min(self._i, len(self._replies) - 1)] if self._replies else "..."
        self._i += 1
        return _ScriptedStream(
            self, chat_ctx=chat_ctx, tools=tools or [], conn_options=conn_options, reply=reply
        )


class _ScriptedStream(LLMStream):
    def __init__(
        self,
        parent: ScriptedLLM,
        *,
        chat_ctx: ChatContext,
        tools: list[Tool],
        conn_options: APIConnectOptions,
        reply: str,
    ) -> None:
        super().__init__(parent, chat_ctx=chat_ctx, tools=tools, conn_options=conn_options)
        self._parent = parent
        self._reply = reply

    async def _run(self) -> None:
        await asyncio.sleep(self._parent._ttft_s)
        words = self._reply.split(" ")
        for i, w in enumerate(words):
            tok = w if i == len(words) - 1 else w + " "
            self._event_ch.send_nowait(
                ChatChunk(id="scripted", delta=ChoiceDelta(role="assistant", content=tok))
            )
            await asyncio.sleep(self._parent._token_delay_s)
