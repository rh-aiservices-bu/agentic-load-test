"""Hybrid tool-result simulator.

Strategy per tool call:

1. If a fixture exists for the tool, return it (a random variant when the
   fixture is a list), lightly templated with the call arguments so results
   look call-specific.
2. Otherwise, if LLM fallback is enabled, ask a (cheap) model to fabricate a
   plausible JSON result. This token cost is recorded as part of the run.
3. Otherwise return a generic acknowledgement.

A configurable artificial latency is applied to every call to mimic real MCP
round-trips.
"""

from __future__ import annotations

import asyncio
import json
import random
from pathlib import Path
from typing import Any

from ..config import ToolSimConfig
from ..llm import LLMClient
from ..metrics import Metrics


class ToolSimulator:
    def __init__(
        self,
        fixtures_dir: Path,
        cfg: ToolSimConfig,
        llm: LLMClient,
        metrics: Metrics,
        model: str,
    ) -> None:
        self.cfg = cfg
        self.llm = llm
        self.metrics = metrics
        self.model = model
        self.fixtures = self._load_fixtures(fixtures_dir)

    @staticmethod
    def _load_fixtures(fixtures_dir: Path) -> dict[str, Any]:
        merged: dict[str, Any] = {}
        if fixtures_dir.exists():
            for fp in sorted(fixtures_dir.glob("*.json")):
                try:
                    data = json.loads(fp.read_text())
                    if isinstance(data, dict):
                        merged.update(data)
                except (json.JSONDecodeError, OSError):
                    continue
        return merged

    async def call(self, scenario: str, name: str, arguments: str) -> str:
        """Simulate one tool call, returning a JSON string result for the model."""

        self.metrics.record_tool_call(scenario, name)
        try:
            args = json.loads(arguments) if arguments.strip() else {}
        except json.JSONDecodeError:
            args = {}

        await self._simulate_latency()

        if name in self.fixtures:
            return self._render_fixture(self.fixtures[name], args)

        if self.cfg.use_llm_fallback:
            fabricated = await self._fabricate(scenario, name, args)
            if fabricated is not None:
                return fabricated

        return json.dumps({"status": "ok", "tool": name, "args": args})

    async def _simulate_latency(self) -> None:
        lo, hi = self.cfg.min_latency_ms, max(self.cfg.min_latency_ms, self.cfg.max_latency_ms)
        if hi > 0:
            await asyncio.sleep(random.randint(lo, hi) / 1000.0)

    def _render_fixture(self, fixture: Any, args: dict[str, Any]) -> str:
        # A list fixture is a set of variants; pick one at random for variety.
        if isinstance(fixture, list) and fixture:
            fixture = random.choice(fixture)
        try:
            text = json.dumps(fixture)
            # Best-effort templating: {query}, {channel}, etc. -> arg values.
            for key, value in args.items():
                text = text.replace("{" + key + "}", str(value))
            return text
        except (TypeError, ValueError):
            return json.dumps({"status": "ok"})

    async def _fabricate(self, scenario: str, name: str, args: dict[str, Any]) -> str | None:
        prompt = (
            "You are simulating the result of a tool/API call for a test "
            "environment. Return ONLY a compact JSON object that is a realistic "
            "result for this call. Do not include explanations.\n\n"
            f"Tool: {name}\nArguments: {json.dumps(args)}\n"
        )
        resp = await self.llm.chat(
            messages=[{"role": "user", "content": prompt}],
            model=self.model,
            max_tokens=self.cfg.fallback_max_tokens,
            temperature=0.8,
        )
        # Fabrication tokens still cost the model under test, so record them.
        self.metrics.record_call(scenario, resp.result)
        if not resp.result.success:
            return None
        return resp.content or json.dumps({"status": "ok"})
