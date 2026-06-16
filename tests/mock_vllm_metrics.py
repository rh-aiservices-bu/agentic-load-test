"""Mock vLLM /metrics endpoint that simulates a warming prefix cache."""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.responses import PlainTextResponse

app = FastAPI()
_state = {"queries": 1000.0, "hits": 100.0}


@app.get("/metrics")
async def metrics() -> PlainTextResponse:
    # Each scrape advances the counters; hits grow faster than queries so the
    # interval hit rate climbs toward ~95% (cache warming).
    _state["queries"] += 1000.0
    _state["hits"] += 950.0
    body = (
        "# HELP vllm:prefix_cache_queries_total Prefix cache queries\n"
        "# TYPE vllm:prefix_cache_queries_total counter\n"
        f'vllm:prefix_cache_queries_total{{engine="0",model_name="m"}} {_state["queries"]}\n'
        "# HELP vllm:prefix_cache_hits_total Prefix cache hits\n"
        "# TYPE vllm:prefix_cache_hits_total counter\n"
        f'vllm:prefix_cache_hits_total{{engine="0",model_name="m"}} {_state["hits"]}\n'
    )
    return PlainTextResponse(body)
