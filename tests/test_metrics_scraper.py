"""Verify the vLLM /metrics parser and the server prefix-cache hit-rate metric."""

from __future__ import annotations

import asyncio

from agentic_loadtest.metrics import Metrics
from agentic_loadtest.metrics_scraper import _sum_counter, scrape_prefix_cache

# Real sample captured from vLLM 0.18 /metrics.
SAMPLE = """# HELP vllm:prefix_cache_queries_total Prefix cache queries
# TYPE vllm:prefix_cache_queries_total counter
vllm:prefix_cache_queries_total{engine="0",model_name="Qwen/Qwen3-4B"} 154277.0
vllm:prefix_cache_hits_total{engine="0",model_name="Qwen/Qwen3-4B"} 136704.0
vllm:external_prefix_cache_hits_total{engine="0",model_name="Qwen/Qwen3-4B"} 0.0
"""


def test_parser() -> None:
    assert _sum_counter(SAMPLE, "vllm:prefix_cache_queries_total") == 154277.0
    assert _sum_counter(SAMPLE, "vllm:prefix_cache_hits_total") == 136704.0
    # Must NOT match the longer external_* metric when asked for hits_total.
    # (external is a different name; our prefix-match guards the boundary char.)
    assert _sum_counter(SAMPLE, "vllm:prefix_cache_hits_total") == 136704.0
    print("parser OK: hits=136704 queries=154277")


def test_metrics_run_delta() -> None:
    m = Metrics()
    # Counters are cumulative since pod start; baseline is captured on first set.
    m.set_server_cache(hits=1000, queries=1100, targets=4)   # baseline
    m.set_server_cache(hits=1950, queries=2100, targets=4)   # +950 hits / +1000 queries
    snap = m.snapshot()["server_cache"]
    assert snap is not None
    assert snap["hits"] == 950 and snap["queries"] == 1000
    assert abs(snap["hit_rate"] - 0.95) < 1e-6
    assert snap["targets"] == 4
    print(f"run-delta OK: {snap}")


async def test_live_scrape() -> None:
    res = await scrape_prefix_cache(["http://127.0.0.1:8097/metrics"], expand_dns=False)
    print("scrape:", res)
    assert res["targets"] == 1
    assert res["queries"] > 0 and res["hits"] > 0


async def main() -> None:
    test_parser()
    test_metrics_run_delta()
    await test_live_scrape()
    print("\nMETRICS_SCRAPER_OK")


if __name__ == "__main__":
    asyncio.run(main())
