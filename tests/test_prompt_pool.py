"""Verify the prompt pool: distinct large prefixes, sharing, and cache metrics."""

from __future__ import annotations

import asyncio
from pathlib import Path

from agentic_loadtest.config import LLMConfig, PromptPoolConfig, RunConfig, ToolSimConfig
from agentic_loadtest.orchestrator import Orchestrator
from agentic_loadtest.prompt_pool import build_prompt_pool


def test_pool_is_distinct_and_large() -> None:
    pool = build_prompt_pool(6, target_tokens=1200)
    assert len(pool) == 6
    # All distinct.
    assert len(set(pool)) == 6, "prompts must be unique"
    # Distinct *prefixes* (first 64 chars differ) — required for separate cache entries.
    assert len({p[:64] for p in pool}) == 6, "prompt prefixes must diverge early"
    # All large and near the target.
    for p in pool:
        assert len(p) // 4 >= 1100, "each prompt should be ~target tokens"
    # Deterministic: same inputs -> identical text (stable prefixes across requests).
    assert build_prompt_pool(6, 1200) == pool
    print(f"pool: 6 prompts, ~{len(pool[0])//4} tok each, all prefixes distinct, deterministic")


def cfg(n: int) -> RunConfig:
    return RunConfig(
        llm=LLMConfig(base_url="http://127.0.0.1:8099/v1", api_key="t", model="mock"),
        tool_sim=ToolSimConfig(use_llm_fallback=False, min_latency_ms=0, max_latency_ms=2),
        prompt_pool=PromptPoolConfig(num_unique_prompts=n, prompt_tokens_target=1200),
        num_users=8, ramp_up_s=0.3, duration_s=4.0, max_concurrent_requests=16,
        think_time_min_ms=0, think_time_max_ms=20,
    )


async def main() -> None:
    test_pool_is_distinct_and_large()
    orch = Orchestrator(Path("config/scenarios"), Path("fixtures"), Path("config/prompts"))
    await orch.start(cfg(4))
    while orch.is_running:
        await asyncio.sleep(0.2)

    snap = orch.metrics.snapshot()
    print("pool_info:", orch.pool_info)
    print("cached_tokens:", snap["cached_tokens"], "cache_hit_rate:", snap["cache_hit_rate"])
    print("avg_prompt_tokens:", snap["avg_prompt_tokens"], "requests_ok:", snap["requests_ok"])

    assert orch.pool_info["count"] == 4
    assert orch.pool_info["users_per_prompt"] == 2.0, "8 users / 4 prompts = 2 each"
    assert snap["cached_tokens"] > 0, "prefix cache hits should be recorded"
    assert snap["cache_hit_rate"] > 0.3, "shared prefixes should give a high hit rate"
    print("\nPROMPT_POOL_OK")


if __name__ == "__main__":
    asyncio.run(main())
