"""Scrape vLLM Prometheus ``/metrics`` to get the real prefix-cache hit rate.

Some vLLM builds (e.g. 0.18.x) do prefix caching but return
``usage.prompt_tokens_details = null`` in the OpenAI response, so the per-request
``cached_tokens`` the dashboard would normally use is empty. The authoritative
numbers live in the Prometheus counters instead:

    vllm:prefix_cache_queries_total   - prompt tokens looked up in the cache
    vllm:prefix_cache_hits_total      - of those, how many were cache hits

Each replica exposes its own counters, so to get a fleet-wide hit rate we sum
across all pods. A headless Kubernetes service resolves (via DNS) to every pod
IP, so pointing one endpoint at a headless service host and enabling
``expand_dns`` scrapes the whole fleet without needing Kubernetes API access.
"""

from __future__ import annotations

import asyncio
import socket
from urllib.parse import urlparse

import httpx

HITS_METRIC = "vllm:prefix_cache_hits_total"
QUERIES_METRIC = "vllm:prefix_cache_queries_total"


def _sum_counter(text: str, name: str) -> float:
    """Sum all Prometheus samples for ``name`` (across label sets), ignoring comments."""

    total = 0.0
    for line in text.splitlines():
        if not line or line[0] == "#":
            continue
        # match "name " or "name{labels} " at the start of the line
        if line.startswith(name) and (len(line) == len(name) or line[len(name)] in " {"):
            try:
                total += float(line.rsplit(" ", 1)[1])
            except (ValueError, IndexError):
                continue
    return total


def _resolve(host: str, expand_dns: bool) -> list[str]:
    """Return the IPs to scrape for a host (all A records when expanding)."""

    if not expand_dns or not host:
        return [host]
    try:
        infos = socket.getaddrinfo(host, None, proto=socket.IPPROTO_TCP)
        ips = sorted({info[4][0] for info in infos})
        return ips or [host]
    except socket.gaierror:
        return [host]


async def scrape_prefix_cache(
    endpoints: list[str], expand_dns: bool = True, timeout: float = 5.0
) -> dict:
    """Scrape and aggregate prefix-cache counters across all endpoints/pods.

    Returns ``{"hits", "queries", "targets", "errors"}`` where ``targets`` is the
    number of pods successfully scraped this round.
    """

    hits = queries = 0.0
    targets = 0
    errors = 0
    # TLS on the serving port is typically a self-signed/internal cert; the load
    # test only reads a metrics counter, so certificate verification is disabled.
    async with httpx.AsyncClient(verify=False, timeout=timeout) as client:
        for url in endpoints:
            u = urlparse(url if "://" in url else f"https://{url}")
            host = u.hostname or ""
            scheme = u.scheme or "https"
            path = u.path or "/metrics"
            portpart = f":{u.port}" if u.port else ""
            ips = await asyncio.to_thread(_resolve, host, expand_dns)
            for ip in ips:
                target = f"{scheme}://{ip}{portpart}{path}"
                try:
                    # Preserve the original Host header so vhost-routed servers work.
                    resp = await client.get(target, headers={"Host": host} if host else None)
                    resp.raise_for_status()
                    hits += _sum_counter(resp.text, HITS_METRIC)
                    queries += _sum_counter(resp.text, QUERIES_METRIC)
                    targets += 1
                except Exception:
                    errors += 1
    return {"hits": hits, "queries": queries, "targets": targets, "errors": errors}
