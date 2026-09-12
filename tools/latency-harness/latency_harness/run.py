"""Simulated-call runner.

Text-modality turns through a real AgentSession (agent instructions, turn handling, metrics) with
either the scripted offline LLM (CI) or the tenant's configured LLM chain (needs vendor keys).
Audio-in/audio-out end-to-end runs (STT + TTS on the wire) are exercised against staging numbers;
see infra/README.

    uv run parlio-latency --turns 5                # offline, scripted LLM
    uv run parlio-latency --real --turns 5         # real LLM via PARLIO_* env / demo assistant
"""

from __future__ import annotations

import argparse
import asyncio
import json
import time
from dataclasses import dataclass, field

from livekit.agents import Agent, AgentSession
from livekit.agents.metrics import LLMMetrics
from livekit.agents.voice.run_result import RunResult

from latency_harness.fakes import ScriptedLLM
from parlio_voice.config_client import DEMO_CONFIG
from parlio_voice.models import AssistantConfig

CALLER_SCRIPT = [
    "Hi, my kitchen tap has been dripping for two days.",
    "It's Sarah Thompson.",
    "07700 900123.",
    "Tomorrow morning would be great, thanks.",
    "No that's everything, bye.",
]

AGENT_SCRIPT = [
    "Sorry to hear that. I can get someone booked in - could I take your name first?",
    "Thanks Sarah. And the best phone number to reach you on?",
    "Got it. When would suit you for a visit?",
    "Perfect, I've noted tomorrow morning and a plumber will confirm shortly. Anything else?",
    "Thanks for calling, goodbye.",
]


@dataclass
class TurnResult:
    user: str
    agent: str
    llm_ttft_s: float | None
    wall_s: float


@dataclass
class RunReport:
    turns: list[TurnResult] = field(default_factory=list)

    def summary(self) -> dict[str, float | int]:
        ttfts = sorted(t.llm_ttft_s for t in self.turns if t.llm_ttft_s is not None)
        walls = sorted(t.wall_s for t in self.turns)
        if not walls:
            return {"turns": 0}

        def pct(v: list[float], p: float) -> float:
            return round(v[min(len(v) - 1, round((len(v) - 1) * p))], 3)

        out: dict[str, float | int] = {
            "turns": len(walls),
            "wall_p50_s": pct(walls, 0.5),
            "wall_p95_s": pct(walls, 0.95),
        }
        if ttfts:
            out["llm_ttft_p50_s"] = pct(ttfts, 0.5)
            out["llm_ttft_p95_s"] = pct(ttfts, 0.95)
        return out


async def simulate_call(
    cfg: AssistantConfig,
    caller_lines: list[str],
    *,
    llm_override: object | None = None,
) -> RunReport:
    if llm_override is None:
        from parlio_voice import providers
        from parlio_voice.settings import Settings

        llm_v = providers.build_llm(cfg, Settings())
    else:
        llm_v = llm_override  # type: ignore[assignment]

    session: AgentSession[None] = AgentSession(llm=llm_v)
    report = RunReport()
    ttft: dict[str, float] = {}

    @session.on("metrics_collected")
    def _m(ev: object) -> None:
        m = ev.metrics  # type: ignore[attr-defined]
        if isinstance(m, LLMMetrics) and m.speech_id:
            ttft[m.speech_id] = m.ttft

    await session.start(Agent(instructions=cfg.rendered_instructions()))
    try:
        for line in caller_lines:
            t0 = time.perf_counter()
            result: RunResult[None] = await session.run(user_input=line)
            wall = time.perf_counter() - t0
            agent_text = ""
            for ev in result.events:
                if ev.type == "message" and ev.item.role == "assistant":
                    agent_text = ev.item.text_content or ""
            report.turns.append(
                TurnResult(
                    user=line,
                    agent=agent_text,
                    llm_ttft_s=next(iter(ttft.values()), None) if ttft else None,
                    wall_s=wall,
                )
            )
            ttft.clear()
    finally:
        await session.aclose()
    return report


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--turns", type=int, default=len(CALLER_SCRIPT))
    ap.add_argument("--real", action="store_true", help="use the configured LLM chain")
    ap.add_argument("--budget-ms", type=int, default=500, help="fail if p50 wall time exceeds this")
    args = ap.parse_args()

    lines = CALLER_SCRIPT[: args.turns]
    llm_override = None if args.real else ScriptedLLM(AGENT_SCRIPT)
    report = asyncio.run(simulate_call(DEMO_CONFIG, lines, llm_override=llm_override))

    for t in report.turns:
        print(f"[{t.wall_s * 1000:6.0f} ms] caller: {t.user}\n{'':12}agent : {t.agent}")
    summary = report.summary()
    print(json.dumps(summary, indent=2))
    p50 = summary.get("wall_p50_s", 0)
    if isinstance(p50, float) and p50 * 1000 > args.budget_ms:
        raise SystemExit(f"p50 {p50 * 1000:.0f} ms exceeds budget {args.budget_ms} ms")


if __name__ == "__main__":
    main()
