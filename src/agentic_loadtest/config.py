"""Configuration models.

There are two layers of configuration:

* ``ServerSettings`` — process-level settings read from environment variables
  (host/port and where to find the default config + scenario/fixture files).
* ``RunConfig`` — the parameters of a single load-test run. This is what the UI
  posts to ``/api/start`` and what gets persisted as the editable default in
  ``config.example.yaml``. Everything here is tunable at runtime.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class LLMConfig(BaseModel):
    """Connection details for the OpenAI-compatible model under test."""

    base_url: str = Field(
        default="https://api.openai.com/v1",
        description="OpenAI-compatible base URL (e.g. vLLM, TGI, Ollama, OpenAI).",
    )
    api_key: str = Field(default="sk-no-key-required", description="API key/token.")
    model: str = Field(default="gpt-4o-mini", description="Model name to request.")
    temperature: float = Field(default=0.7, ge=0.0, le=2.0)
    max_tokens: int = Field(default=1024, ge=1)
    request_timeout_s: float = Field(default=120.0, gt=0)
    extra_headers: dict[str, str] = Field(default_factory=dict)


class ToolSimConfig(BaseModel):
    """How simulated MCP-style tools produce their results."""

    use_llm_fallback: bool = Field(
        default=True,
        description="If no fixture matches, fabricate a plausible result with an LLM call.",
    )
    fallback_model: str | None = Field(
        default=None,
        description="Model for fabricated tool results. Defaults to the main model.",
    )
    fallback_max_tokens: int = Field(default=400, ge=1)
    min_latency_ms: int = Field(default=50, ge=0, description="Simulated tool latency floor.")
    max_latency_ms: int = Field(default=400, ge=0, description="Simulated tool latency ceiling.")


class SystemPromptConfig(BaseModel):
    """A large, shared "agent harness" system prompt layered over each scenario.

    This mimics how Claude Code / Hermes prepend a big standing system prompt to
    every request, so prompt-token counts start high from the very first call and
    grow as the conversation accumulates tool output.
    """

    preamble: str = Field(
        default="",
        description="Inline harness prompt prepended to each scenario's persona.",
    )
    preamble_file: str | None = Field(
        default=None,
        description="Path to a prompt file. Used when 'preamble' is empty. "
        "Resolved relative to the prompts dir, then the working dir.",
    )
    position: str = Field(
        default="prepend",
        description="'prepend' = harness then scenario persona; 'replace' = harness only.",
    )


class PromptPoolConfig(BaseModel):
    """A pool of distinct large prompts shared across users, for KV-cache demos.

    When ``num_unique_prompts > 0``, the orchestrator builds that many distinct
    large prompts and assigns one to each user (round-robin), so many users share
    each large prefix. This is what makes llm-d's prefix-cache-aware scheduling
    pay off: same prefix → same replica → KV cache hit → lower TTFT. Overrides the
    single ``system_prompt`` preamble when enabled.
    """

    num_unique_prompts: int = Field(
        default=0,
        ge=0,
        description="Number of distinct large prompts. 0 = disabled (use system_prompt).",
    )
    prompt_tokens_target: int = Field(
        default=1500,
        ge=100,
        description="Approximate size of each large prompt (the shared cacheable prefix).",
    )
    assignment: str = Field(
        default="round_robin",
        description="How users map to prompts. 'round_robin' = even, deterministic sharing.",
    )


class VLLMMetricsConfig(BaseModel):
    """Scrape vLLM Prometheus /metrics for the true prefix-cache hit rate.

    Needed because some vLLM builds don't report per-request ``cached_tokens``
    in the OpenAI usage object, even while prefix caching is active. Point
    ``endpoints`` at a headless service host (with ``expand_dns``) to aggregate
    the counters across every replica.
    """

    enabled: bool = Field(default=False, description="Poll vLLM /metrics for prefix-cache stats.")
    endpoints: list[str] = Field(
        default_factory=list,
        description="vLLM /metrics URLs, e.g. https://<headless-svc>:8000/metrics",
    )
    expand_dns: bool = Field(
        default=True,
        description="Resolve each endpoint host to all pod IPs (headless svc) and scrape each.",
    )
    poll_interval_s: float = Field(default=2.0, gt=0)


class RunConfig(BaseModel):
    """Parameters of a single load-test run."""

    llm: LLMConfig = Field(default_factory=LLMConfig)
    tool_sim: ToolSimConfig = Field(default_factory=ToolSimConfig)
    system_prompt: SystemPromptConfig = Field(default_factory=SystemPromptConfig)
    prompt_pool: PromptPoolConfig = Field(default_factory=PromptPoolConfig)
    vllm_metrics: VLLMMetricsConfig = Field(default_factory=VLLMMetricsConfig)

    num_users: int = Field(default=10, ge=1, le=5000, description="Concurrent simulated users.")
    ramp_up_s: float = Field(default=5.0, ge=0, description="Spread user starts over this window.")
    duration_s: float = Field(
        default=300.0, ge=0, description="Run length in seconds. 0 = run until stopped."
    )
    iterations_per_user: int = Field(
        default=0,
        ge=0,
        description="Scenarios each user runs then exits. 0 = loop until duration/stop.",
    )
    max_concurrent_requests: int = Field(
        default=100, ge=1, description="Global cap on in-flight LLM HTTP requests."
    )
    think_time_min_ms: int = Field(default=200, ge=0, description="Pause between user turns (floor).")
    think_time_max_ms: int = Field(default=1500, ge=0, description="Pause between user turns (ceiling).")

    scenario_weights: dict[str, float] = Field(
        default_factory=dict,
        description="Override scenario selection weights by name. Empty = use weights from files.",
    )

    def model_for_tool_sim(self) -> str:
        return self.tool_sim.fallback_model or self.llm.model


class ServerSettings(BaseSettings):
    """Process-level settings, sourced from ``ALT_*`` environment variables."""

    model_config = SettingsConfigDict(env_prefix="ALT_", extra="ignore")

    host: str = "0.0.0.0"
    port: int = 8080
    config: Path = Path("config/config.example.yaml")
    scenarios_dir: Path = Path("config/scenarios")
    fixtures_dir: Path = Path("fixtures")
    prompts_dir: Path = Path("config/prompts")


def load_run_config(path: Path) -> RunConfig:
    """Load a :class:`RunConfig` from a YAML file, falling back to defaults."""

    if path.exists():
        data: dict[str, Any] = yaml.safe_load(path.read_text()) or {}
        cfg = RunConfig.model_validate(data)
    else:
        cfg = RunConfig()
    # Allow the LLM connection to be supplied via env (e.g. an OpenShift Secret)
    # without baking credentials into the config file or image.
    if v := os.getenv("ALT_LLM_API_KEY"):
        cfg.llm.api_key = v
    if v := os.getenv("ALT_LLM_BASE_URL"):
        cfg.llm.base_url = v
    if v := os.getenv("ALT_LLM_MODEL"):
        cfg.llm.model = v
    return cfg


def resolve_preamble(cfg: SystemPromptConfig, prompts_dir: Path) -> str:
    """Return the effective harness preamble text.

    Inline ``preamble`` wins; otherwise ``preamble_file`` is read (looked up in
    ``prompts_dir`` first, then as a plain path). Returns "" if neither yields text.
    """

    if cfg.preamble.strip():
        return cfg.preamble
    if cfg.preamble_file:
        for candidate in (prompts_dir / cfg.preamble_file, Path(cfg.preamble_file)):
            if candidate.is_file():
                return candidate.read_text()
    return ""


def dump_run_config(cfg: RunConfig, path: Path) -> None:
    """Persist a :class:`RunConfig` to a YAML file."""

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(cfg.model_dump(mode="json"), sort_keys=False))
