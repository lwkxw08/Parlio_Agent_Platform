"""First simulated-call CI test: full AgentSession turn loop with an offline scripted LLM."""

from latency_harness.fakes import ScriptedLLM
from latency_harness.run import AGENT_SCRIPT, CALLER_SCRIPT, simulate_call
from parlio_voice.config_client import DEMO_CONFIG


async def test_simulated_call_completes_all_turns_within_budget() -> None:
    report = await simulate_call(DEMO_CONFIG, CALLER_SCRIPT, llm_override=ScriptedLLM(AGENT_SCRIPT))
    assert len(report.turns) == len(CALLER_SCRIPT)
    assert [t.agent for t in report.turns] == AGENT_SCRIPT
    s = report.summary()
    assert s["turns"] == len(CALLER_SCRIPT)
    # fake LLM has 50 ms TTFT; the session overhead itself must stay well inside the 500 ms budget
    assert s["wall_p50_s"] < 0.5, s
