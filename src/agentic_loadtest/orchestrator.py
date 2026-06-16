"""Load-test orchestration.

Owns the lifecycle of a single run: builds the LLM client / tool simulator /
metrics, ramps up N simulated users, has each user repeatedly pick a weighted
scenario and run the agent loop, and tears everything down on stop or when the
configured duration elapses. A background sampler appends a per-second timeline
point used by the dashboard charts.
"""

from __future__ import annotations

import asyncio
import random
import time
from enum import Enum
from pathlib import Path

from .agent import AgentRunner
from .config import RunConfig, resolve_preamble
from .llm import LLMClient
from .metrics import Metrics
from .prompt_pool import build_prompt_pool, pool_summary
from .scenarios import Scenario, load_scenarios
from .tools import ToolSimulator


class RunState(str, Enum):
    IDLE = "idle"
    RUNNING = "running"
    STOPPING = "stopping"
    FINISHED = "finished"


class Orchestrator:
    """Single-run controller. One instance per process; reset between runs."""

    def __init__(self, scenarios_dir, fixtures_dir, prompts_dir=None) -> None:
        self._scenarios_dir = scenarios_dir
        self._fixtures_dir = fixtures_dir
        # Default the prompts dir next to the scenarios dir (config/prompts).
        self._prompts_dir = prompts_dir or Path(scenarios_dir).parent / "prompts"
        self.available_scenarios: dict[str, Scenario] = load_scenarios(scenarios_dir)

        self.state: RunState = RunState.IDLE
        self.metrics: Metrics | None = None
        self.config: RunConfig | None = None
        self.prompt_pool: list[str] = []
        self.pool_info: dict = {}

        self._llm: LLMClient | None = None
        self._stop = asyncio.Event()
        self._tasks: list[asyncio.Task] = []
        self._supervisor: asyncio.Task | None = None
        self._sampler: asyncio.Task | None = None

    # ----- public API ------------------------------------------------------

    def reload_scenarios(self) -> None:
        self.available_scenarios = load_scenarios(self._scenarios_dir)

    @property
    def is_running(self) -> bool:
        return self.state in (RunState.RUNNING, RunState.STOPPING)

    async def start(self, cfg: RunConfig) -> None:
        if self.is_running:
            raise RuntimeError("a run is already in progress")
        if not self.available_scenarios:
            raise RuntimeError("no scenarios are defined")

        self.config = cfg
        self.metrics = Metrics()
        self._stop = asyncio.Event()
        self._llm = LLMClient(cfg.llm, max_concurrency=cfg.max_concurrent_requests)
        self.state = RunState.RUNNING

        self._sampler = asyncio.create_task(self._sample_loop())
        self._supervisor = asyncio.create_task(self._supervise(cfg))

    async def stop(self) -> None:
        if not self.is_running:
            return
        self.state = RunState.STOPPING
        self._stop.set()

    # ----- selection -------------------------------------------------------

    def _weighted_scenarios(self, cfg: RunConfig) -> tuple[list[Scenario], list[float]]:
        scenarios = list(self.available_scenarios.values())
        weights = [
            cfg.scenario_weights.get(s.name, s.weight) for s in scenarios
        ]
        # Drop zero-weight scenarios entirely.
        pairs = [(s, w) for s, w in zip(scenarios, weights) if w > 0]
        if not pairs:
            pairs = [(s, 1.0) for s in scenarios]
        return [p[0] for p in pairs], [p[1] for p in pairs]

    # ----- run loop --------------------------------------------------------

    async def _supervise(self, cfg: RunConfig) -> None:
        assert self.metrics is not None and self._llm is not None
        simulator = ToolSimulator(
            self._fixtures_dir, cfg.tool_sim, self._llm, self.metrics, cfg.model_for_tool_sim()
        )
        preamble = resolve_preamble(cfg.system_prompt, self._prompts_dir)
        runner = AgentRunner(cfg, self._llm, simulator, self.metrics, preamble=preamble)
        scenarios, weights = self._weighted_scenarios(cfg)

        # Build the pool of distinct large prompts (for KV-cache demos). Empty
        # when num_unique_prompts == 0, in which case the single preamble is used.
        self.prompt_pool = build_prompt_pool(
            cfg.prompt_pool.num_unique_prompts, cfg.prompt_pool.prompt_tokens_target
        )
        self.pool_info = pool_summary(self.prompt_pool)
        if self.prompt_pool:
            log_n = len(self.prompt_pool)
            shared = cfg.num_users / log_n if log_n else 0
            self.pool_info["users_per_prompt"] = round(shared, 1)

        # Stagger user starts across the ramp-up window.
        per_user_delay = (cfg.ramp_up_s / cfg.num_users) if cfg.num_users else 0.0
        self._tasks = [
            asyncio.create_task(self._user_loop(i, i * per_user_delay, cfg, runner, scenarios, weights))
            for i in range(cfg.num_users)
        ]

        # Enforce the duration limit (0 = unbounded / until stopped).
        if cfg.duration_s > 0:
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=cfg.duration_s)
            except asyncio.TimeoutError:
                self._stop.set()
        else:
            await self._stop.wait()

        # Either stopped or timed out: wait for users to finish their current turn.
        self.state = RunState.STOPPING
        await asyncio.gather(*self._tasks, return_exceptions=True)
        await self._teardown()

    async def _user_loop(
        self,
        user_id: int,
        start_delay: float,
        cfg: RunConfig,
        runner: AgentRunner,
        scenarios: list[Scenario],
        weights: list[float],
    ) -> None:
        assert self.metrics is not None
        if start_delay:
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=start_delay)
                return  # stopped before this user even started
            except asyncio.TimeoutError:
                pass

        # Assign this user a fixed large prompt from the pool (round-robin), so
        # many users share each prefix and a given user always sends the same one.
        preamble = self.prompt_pool[user_id % len(self.prompt_pool)] if self.prompt_pool else None

        self.metrics.active_users += 1
        try:
            iterations = 0
            while not self._stop.is_set():
                scenario = random.choices(scenarios, weights=weights, k=1)[0]
                try:
                    await runner.run(scenario, preamble=preamble)
                except Exception:
                    # A crashed scenario must not kill the user loop.
                    pass
                iterations += 1
                if cfg.iterations_per_user and iterations >= cfg.iterations_per_user:
                    break
                await self._user_think(cfg)
        finally:
            self.metrics.active_users -= 1

    async def _user_think(self, cfg: RunConfig) -> None:
        lo, hi = cfg.think_time_min_ms, max(cfg.think_time_min_ms, cfg.think_time_max_ms)
        if hi <= 0:
            return
        try:
            await asyncio.wait_for(self._stop.wait(), timeout=random.randint(lo, hi) / 1000.0)
        except asyncio.TimeoutError:
            pass

    async def _sample_loop(self) -> None:
        assert self.metrics is not None
        while not self._stop.is_set():
            self.metrics.sample_timeline()
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=1.0)
            except asyncio.TimeoutError:
                continue
        self.metrics.sample_timeline()  # final point

    async def _teardown(self) -> None:
        if self._sampler:
            self._sampler.cancel()
        if self._llm:
            await self._llm.close()
        self.state = RunState.FINISHED
