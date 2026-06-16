# Agentic Load Test

A load-test tool that simulates many concurrent users running **real, multi-turn,
tool-calling agentic applications** against an OpenAI-compatible LLM endpoint.

The reasoning and tool selection are driven by **real LLM calls** — so the load
profile matches production agentic traffic — while the tools themselves (Slack,
Google Docs, email, calendar, a code assistant) are **simulated**. You configure
the model connection and the number of users, pick a mix of scenarios, and watch
token usage, TTFT, latency, and throughput grow live in a dashboard.

![Dashboard](assets/dashboard.png)

## What it simulates

Each simulated user repeatedly picks a weighted scenario and runs an agent loop:

```
system(persona) + user(goal)
  → assistant (decides to call tools)        ← real LLM call (streamed, measured)
     → simulated tool results                ← fixtures, or LLM-fabricated fallback
  → assistant reasons over results, repeats
  → next follow-up user turn (multi-turn)
```

Shipped scenarios (`config/scenarios/*.yaml`):

| Scenario | What it does |
|---|---|
| `personal_assistant` | OpenClaw-style daily catch-up across email/Slack/calendar |
| `project_research` | Collate Project Atlas info from Slack → create a summary Google Doc → email it |
| `code_assistant` | Investigate a bug, write a fix, run tests |
| `triage_support` | Short, high-frequency triage of an incoming request |

Add a scenario by dropping a YAML file in `config/scenarios/` (no code needed).
Add a tool in `src/agentic_loadtest/tools/registry.py` and a fixture in
`fixtures/*.json`. Add a large system-prompt harness by dropping a `.md`/`.txt`
file in `config/prompts/` — it appears in the UI's preset picker automatically.

## KV-cache / llm-d prefix-routing demo

To demonstrate the benefit of **llm-d's prefix-cache-aware (intelligent)
scheduling**, the tool can drive a **pool of distinct large prompts** shared
across users. Set `prompt_pool.num_unique_prompts` (in the UI: *Prompt pool*) to
N and the tool builds N distinct, deterministic, large system prompts and assigns
one to each user round-robin — so many users send the **same large prefix**.

Why this shows the benefit:

- Each prompt begins with a unique header, so the N prefixes are genuinely
  distinct cache entries; each is large (`prompt_tokens_target`, ~1500 tok) so the
  prefix is worth caching; and generation is deterministic so prefixes are stable.
- With N distinct large prefixes across many replicas, **prefix-aware routing**
  co-locates each prefix on the replica that already holds its KV cache (hit →
  fast prefill → low TTFT). **Round-robin** routing scatters each prefix across
  replicas, thrashing the cache (miss → full prefill → high TTFT).

The tool reads vLLM/llm-d's `usage.prompt_tokens_details.cached_tokens` and shows
a live **KV cache hit rate** (cumulative + interval) and **cached tokens**. Run
the same config against a round-robin gateway vs an llm-d prefix-aware gateway and
compare the hit rate and TTFT — that delta is the win.

### When the model returns `prompt_tokens_details: null`

Some vLLM builds (e.g. 0.18.x) do prefix caching but **don't** report per-request
`cached_tokens` in the OpenAI response — so the cache-hit card stays at 0% even
though caching is working (you'll see `Prefix cache hit rate: NN%` in the vLLM
pod logs). The authoritative numbers are the Prometheus counters
`vllm:prefix_cache_hits_total` / `vllm:prefix_cache_queries_total` on each pod's
`/metrics`.

Enable **`vllm_metrics`** (UI: *vLLM metrics scrape*) to poll those counters and
show the real fleet-wide hit rate. Point `endpoints` at a **headless** service
host and keep `expand_dns: true` — the tool resolves it to every pod IP and sums
the counters across the fleet (reporting the delta over the run, plus a per-second
interval rate). Example for a KServe/llm-d inference pool:

```yaml
vllm_metrics:
  enabled: true
  endpoints:
    - https://qwen-tools-inference-pool-ip-XXXXXXXX.demo-llm.svc.cluster.local:8000/metrics
  expand_dns: true       # resolve headless svc -> all pod IPs, scrape + sum each
  poll_interval_s: 2.0
```

When scraping is active, the dashboard's cache hit rate and chart use the
server-reported value and the "cached tokens" card switches to **hits / queries
(N pods)**.

> Tip: use more unique prompts than a single replica's cache comfortably holds
> (e.g. 16–64) with enough users that each prompt is shared by several users.

## Metrics

Live, per-second and cumulative:

- **Token usage growth** (prompt / completion / total) across all users
- **KV cache hit rate** + cached tokens (prefix-cache reuse, from `cached_tokens`)
- **TTFT** p50 / p95 / p99 (measured from streamed first token)
- **Request latency** p50 / p95 / p99
- **Throughput**: tokens/sec and requests/sec
- **Active users**, total/failed requests, error breakdown
- **Per-scenario** started/completed/failed/tokens, and **per-tool** call counts

## Configuration

Everything is tunable from the UI (or `config/config.example.yaml`):

- **LLM connection**: base URL, API key, model, temperature, max tokens, timeout
- **System prompt (agent harness)**: a large, shared preamble prepended to every
  scenario — mimicking the big standing system prompts of Claude Code / Hermes so
  prompt-token counts start high and grow fast. Pick a shipped preset
  (`config/prompts/*.md`), paste your own, or point `preamble_file` at a file.
  `position: prepend` layers it over the scenario persona; `replace` uses it alone.
- **Load profile**: users, ramp-up, duration (or iterations/user), max concurrent
  in-flight requests, think-time between turns
- **Tool simulation**: fixtures-first with optional LLM fallback; simulated tool
  latency range
- **Scenario mix**: per-scenario weights

The LLM connection can also come from the environment (handy with an OpenShift
Secret): `ALT_LLM_API_KEY`, `ALT_LLM_BASE_URL`, `ALT_LLM_MODEL`.

## Run locally

```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
PYTHONPATH=src .venv/bin/python -m agentic_loadtest.main
# open http://localhost:8080
```

Server env vars: `ALT_HOST` (default `0.0.0.0`), `ALT_PORT` (`8080`),
`ALT_CONFIG`, `ALT_SCENARIOS_DIR`, `ALT_FIXTURES_DIR`.

## Test without a real model

A mock OpenAI-compatible streaming server is included:

```bash
PYTHONPATH=src .venv/bin/python -m uvicorn tests.mock_openai:app --port 8099 &
PYTHONPATH=src .venv/bin/python tests/e2e_run.py   # drives a short run, asserts metrics
```

## Container

```bash
# Build for the cluster's architecture. OpenShift nodes are usually amd64, so on
# Apple Silicon add --platform=linux/amd64 (otherwise the image won't run there).
podman build --platform=linux/amd64 -t agentic-loadtest -f Containerfile .
podman run -p 8080:8080 -e ALT_LLM_API_KEY=sk-... agentic-loadtest
```

The image is based on UBI9 Python and runs as an **arbitrary non-root UID in
group 0** (OpenShift `restricted-v2` SCC compatible). Permission setup runs as
root at build time, then the runtime drops to a non-root user. Verified by
running locally as a random UID:

```bash
podman run --user 100777:0 -p 8081:8080 agentic-loadtest   # mimics OpenShift
```

## Deploy to OpenShift

```bash
oc new-project agentic-loadtest
oc create secret generic agentic-loadtest-llm --from-literal=api_key=sk-...

# Build in-cluster from Git…
oc apply -f openshift/buildconfig.yaml
oc start-build agentic-loadtest --follow
# …or push a locally built image to the internal registry.

oc apply -f openshift/deployment.yaml -f openshift/service.yaml -f openshift/route.yaml
oc get route agentic-loadtest -o jsonpath='{.spec.host}{"\n"}'
```

## Architecture

| Module | Responsibility |
|---|---|
| `config.py` | `RunConfig` (per-run, UI-editable) + `ServerSettings` (env) |
| `llm.py` | Async OpenAI-compatible client; streams to measure TTFT, accumulates tool-call deltas, captures token usage + `cached_tokens`; global concurrency gate |
| `tools/` | Tool schemas (`registry.py`) + hybrid fixture/LLM simulator (`simulator.py`) |
| `prompt_pool.py` | Builds N distinct, deterministic large prompts for the KV-cache demo |
| `scenarios.py` | YAML scenario loading |
| `agent.py` | Multi-turn tool-calling agent loop for one scenario |
| `orchestrator.py` | Ramps up N users, weighted scenario selection, duration/stop, per-second sampler |
| `metrics.py` | In-memory aggregation, percentiles, timeline |
| `api.py` / `main.py` | FastAPI REST + WebSocket + static dashboard |
| `static/` | Single-page dashboard (vanilla JS + Chart.js) |

### Scaling note

A single asyncio process comfortably drives hundreds of users because the work
is IO-bound (waiting on the model). `max_concurrent_requests` caps in-flight HTTP
requests so user count and socket count are decoupled. For thousands of users,
run multiple pod replicas and aggregate metrics externally (e.g. Prometheus) —
the current metrics store is per-process and in-memory.
