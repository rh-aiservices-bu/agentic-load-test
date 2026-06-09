"""Scenario definitions loaded from YAML.

A scenario describes one kind of agentic task: who the user is (persona), what
they want (goal), which tools are available, how long the agent may run, and any
follow-up turns that make the interaction multi-turn.
"""

from __future__ import annotations

from pathlib import Path

import yaml
from pydantic import BaseModel, Field


class Scenario(BaseModel):
    name: str
    description: str = ""
    weight: float = Field(default=1.0, ge=0)
    persona: str = "You are a capable AI assistant."
    goal: str
    tools: list[str] = Field(default_factory=list)
    max_turns: int = Field(default=10, ge=1)
    follow_ups: list[str] = Field(default_factory=list)


def load_scenarios(scenarios_dir: Path) -> dict[str, Scenario]:
    """Load every ``*.yaml`` scenario in a directory, keyed by name."""

    scenarios: dict[str, Scenario] = {}
    if not scenarios_dir.exists():
        return scenarios
    for fp in sorted(scenarios_dir.glob("*.y*ml")):
        data = yaml.safe_load(fp.read_text())
        if not data:
            continue
        scenario = Scenario.model_validate(data)
        scenarios[scenario.name] = scenario
    return scenarios
