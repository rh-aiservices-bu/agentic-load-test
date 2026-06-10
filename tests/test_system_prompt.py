"""Verify the configurable harness preamble plumbs through and inflates prompt tokens."""

from __future__ import annotations

import asyncio
from pathlib import Path

from agentic_loadtest.config import (
    LLMConfig,
    RunConfig,
    SystemPromptConfig,
    ToolSimConfig,
    resolve_preamble,
)
from agentic_loadtest.orchestrator import Orchestrator


def base_cfg(**sp) -> RunConfig:
    return RunConfig(
        llm=LLMConfig(base_url="http://127.0.0.1:8099/v1", api_key="t", model="mock"),
        tool_sim=ToolSimConfig(use_llm_fallback=False, min_latency_ms=0, max_latency_ms=2),
        system_prompt=SystemPromptConfig(**sp),
        num_users=6, ramp_up_s=0.3, duration_s=3.0, max_concurrent_requests=16,
        think_time_min_ms=0, think_time_max_ms=20,
    )


def test_resolve_and_compose() -> None:
    prompts = Path("config/prompts")
    text = resolve_preamble(SystemPromptConfig(preamble_file="hermes_agent.md"), prompts)
    assert len(text) > 2000, "hermes preset should be large"
    assert resolve_preamble(SystemPromptConfig(preamble="inline wins"), prompts) == "inline wins"
    assert resolve_preamble(SystemPromptConfig(), prompts) == ""
    print(f"hermes preset: {len(text)} chars (~{len(text)//4} tok)")


async def run_avg_prompt_tokens(cfg: RunConfig) -> int:
    orch = Orchestrator(Path("config/scenarios"), Path("fixtures"), Path("config/prompts"))
    await orch.start(cfg)
    while orch.is_running:
        await asyncio.sleep(0.2)
    return orch.metrics.snapshot()["avg_prompt_tokens"]


async def main() -> None:
    test_resolve_and_compose()
    plain = await run_avg_prompt_tokens(base_cfg())
    hermes = await run_avg_prompt_tokens(base_cfg(preamble_file="hermes_agent.md", position="prepend"))
    print(f"avg prompt tokens — no preamble: {plain}, hermes preamble: {hermes}")
    assert hermes > plain + 500, "harness preamble should add hundreds of prompt tokens/request"
    print("\nSYSTEM_PROMPT_OK")


if __name__ == "__main__":
    asyncio.run(main())
