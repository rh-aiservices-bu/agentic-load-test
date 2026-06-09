"""Drive a short load-test run against the mock server and assert metrics move."""

from __future__ import annotations

import asyncio
from pathlib import Path

from agentic_loadtest.config import LLMConfig, RunConfig, ToolSimConfig
from agentic_loadtest.orchestrator import Orchestrator


async def main() -> None:
    cfg = RunConfig(
        llm=LLMConfig(base_url="http://127.0.0.1:8099/v1", api_key="test", model="mock"),
        tool_sim=ToolSimConfig(use_llm_fallback=False, min_latency_ms=0, max_latency_ms=5),
        num_users=8,
        ramp_up_s=0.5,
        duration_s=4.0,
        iterations_per_user=0,
        max_concurrent_requests=16,
        think_time_min_ms=0,
        think_time_max_ms=50,
    )
    orch = Orchestrator(Path("config/scenarios"), Path("fixtures"))
    assert orch.available_scenarios, "no scenarios loaded"
    await orch.start(cfg)

    # Wait for the supervisor to finish (duration + drain).
    while orch.is_running:
        await asyncio.sleep(0.2)

    m = orch.metrics
    snap = m.snapshot()
    print("state:", orch.state.value)
    print("requests_ok:", snap["requests_ok"], "failed:", snap["requests_failed"])
    print("total_tokens:", snap["total_tokens"])
    print("tool_calls:", snap["tool_calls"])
    print("ttft p50/p95:", snap["ttft"]["p50"], snap["ttft"]["p95"])
    print("scenarios:", {k: v["started"] for k, v in snap["scenarios"].items()})
    print("tool_call_counts:", snap["tool_call_counts"])
    print("timeline points:", len(m.timeline))

    assert snap["requests_ok"] > 0, "no successful requests"
    assert snap["total_tokens"] > 0, "no tokens recorded"
    assert snap["tool_calls"] > 0, "no tool calls recorded"
    assert snap["ttft"]["count"] > 0, "no TTFT samples"
    assert len(m.timeline) > 0, "no timeline points"
    print("\nE2E_OK")


if __name__ == "__main__":
    asyncio.run(main())
