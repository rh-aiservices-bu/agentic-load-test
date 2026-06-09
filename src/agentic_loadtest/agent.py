"""The agentic loop for a single scenario run.

Runs a standard multi-turn tool-calling loop against the model under test:

    system(persona) + user(goal)
      -> assistant (maybe tool_calls)
         -> simulate each tool, append tool results
      -> repeat until the assistant stops calling tools or max_turns is hit
    -> inject the next follow-up user turn (if any) and continue

Every model call is recorded in metrics; every tool call is simulated and
recorded too. The loop is resilient: a failed model call ends the run cleanly
so one user's failure never takes down the orchestrator.
"""

from __future__ import annotations

import asyncio
import random
from typing import Any

from .config import RunConfig
from .llm import LLMClient
from .metrics import Metrics
from .scenarios import Scenario
from .tools import ToolSimulator, schemas_for


class AgentRunner:
    def __init__(
        self,
        cfg: RunConfig,
        llm: LLMClient,
        simulator: ToolSimulator,
        metrics: Metrics,
    ) -> None:
        self.cfg = cfg
        self.llm = llm
        self.simulator = simulator
        self.metrics = metrics

    async def run(self, scenario: Scenario) -> bool:
        """Run one full scenario (including follow-ups). Returns True on success."""

        self.metrics.scenario_started(scenario.name)
        tools = schemas_for(scenario.tools)
        messages: list[dict[str, Any]] = [
            {"role": "system", "content": scenario.persona},
            {"role": "user", "content": scenario.goal},
        ]

        turns_remaining = scenario.max_turns
        follow_ups = list(scenario.follow_ups)
        ok = True

        while turns_remaining > 0:
            resp = await self.llm.chat(messages, tools=tools or None)
            self.metrics.record_call(scenario.name, resp.result)
            turns_remaining -= 1

            if not resp.result.success:
                ok = False
                break

            messages.append(resp.raw_message)

            if resp.tool_calls:
                # Resolve every tool call the model requested this turn.
                results = await asyncio.gather(
                    *(
                        self.simulator.call(scenario.name, tc.name, tc.arguments)
                        for tc in resp.tool_calls
                    )
                )
                for tc, result in zip(resp.tool_calls, results):
                    messages.append(
                        {"role": "tool", "tool_call_id": tc.id, "name": tc.name, "content": result}
                    )
                continue  # let the model react to the tool results

            # No tool call this turn: the agent produced a final answer for the
            # current user request. Move on to the next follow-up, if any.
            if follow_ups:
                await self._think()
                messages.append({"role": "user", "content": follow_ups.pop(0)})
                continue

            break  # conversation complete

        if ok:
            self.metrics.scenario_completed(scenario.name)
        return ok

    async def _think(self) -> None:
        lo = self.cfg.think_time_min_ms
        hi = max(lo, self.cfg.think_time_max_ms)
        if hi > 0:
            await asyncio.sleep(random.randint(lo, hi) / 1000.0)
