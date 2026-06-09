"""In-memory metrics collection and aggregation.

Everything runs in a single asyncio event loop, so plain attributes are safe
without locking. The orchestrator records each LLM call and tool call here; the
API reads snapshots and the per-second timeline for the dashboard.
"""

from __future__ import annotations

import time
from collections import defaultdict, deque
from dataclasses import dataclass, field
from typing import Any


@dataclass
class CallResult:
    """Outcome of a single LLM chat-completion request."""

    success: bool
    ttft_s: float | None = None
    latency_s: float = 0.0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    error: str | None = None


def _percentile(samples: list[float], pct: float) -> float:
    if not samples:
        return 0.0
    ordered = sorted(samples)
    k = (len(ordered) - 1) * (pct / 100.0)
    lo = int(k)
    hi = min(lo + 1, len(ordered) - 1)
    if lo == hi:
        return ordered[lo]
    return ordered[lo] + (ordered[hi] - ordered[lo]) * (k - lo)


@dataclass
class ScenarioStats:
    started: int = 0
    completed: int = 0
    failed: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    tool_calls: int = 0


class Metrics:
    """Aggregates all metrics for one load-test run."""

    def __init__(self) -> None:
        self.start_time: float = time.monotonic()
        self.start_wall: float = time.time()

        # Cumulative counters.
        self.requests_ok: int = 0
        self.requests_failed: int = 0
        self.prompt_tokens: int = 0
        self.completion_tokens: int = 0
        self.tool_calls: int = 0

        # Live gauges.
        self.active_users: int = 0

        # Bounded sample buffers for percentile/latency stats.
        self.ttft_samples: deque[float] = deque(maxlen=10000)
        self.latency_samples: deque[float] = deque(maxlen=10000)

        # Breakdowns.
        self.scenarios: dict[str, ScenarioStats] = defaultdict(ScenarioStats)
        self.tool_call_counts: dict[str, int] = defaultdict(int)
        self.errors: dict[str, int] = defaultdict(int)

        # Per-second timeline for charting.
        self.timeline: list[dict[str, Any]] = []
        self._last_sample_tokens: int = 0
        self._last_sample_requests: int = 0
        self._last_sample_t: float = self.start_time

    @property
    def total_tokens(self) -> int:
        return self.prompt_tokens + self.completion_tokens

    @property
    def elapsed_s(self) -> float:
        return time.monotonic() - self.start_time

    # ----- recording -------------------------------------------------------

    def record_call(self, scenario: str, result: CallResult) -> None:
        stats = self.scenarios[scenario]
        if result.success:
            self.requests_ok += 1
            self.prompt_tokens += result.prompt_tokens
            self.completion_tokens += result.completion_tokens
            stats.prompt_tokens += result.prompt_tokens
            stats.completion_tokens += result.completion_tokens
            if result.ttft_s is not None:
                self.ttft_samples.append(result.ttft_s)
            self.latency_samples.append(result.latency_s)
        else:
            self.requests_failed += 1
            stats.failed += 1
            if result.error:
                self.errors[result.error[:120]] += 1

    def record_tool_call(self, scenario: str, tool: str) -> None:
        self.tool_calls += 1
        self.tool_call_counts[tool] += 1
        self.scenarios[scenario].tool_calls += 1

    def scenario_started(self, scenario: str) -> None:
        self.scenarios[scenario].started += 1

    def scenario_completed(self, scenario: str) -> None:
        self.scenarios[scenario].completed += 1

    # ----- timeline --------------------------------------------------------

    def sample_timeline(self) -> dict[str, Any]:
        """Append and return a per-second timeline point with derived rates."""

        now = time.monotonic()
        dt = max(now - self._last_sample_t, 1e-6)
        total_tokens = self.total_tokens
        total_requests = self.requests_ok + self.requests_failed

        point = {
            "t": round(now - self.start_time, 2),
            "total_tokens": total_tokens,
            "prompt_tokens": self.prompt_tokens,
            "completion_tokens": self.completion_tokens,
            "tokens_per_sec": round((total_tokens - self._last_sample_tokens) / dt, 1),
            "requests_per_sec": round((total_requests - self._last_sample_requests) / dt, 2),
            "active_users": self.active_users,
            "ttft_p50": round(_percentile(list(self.ttft_samples), 50), 3),
            "ttft_p95": round(_percentile(list(self.ttft_samples), 95), 3),
        }
        self._last_sample_tokens = total_tokens
        self._last_sample_requests = total_requests
        self._last_sample_t = now
        self.timeline.append(point)
        return point

    # ----- snapshot --------------------------------------------------------

    def snapshot(self) -> dict[str, Any]:
        total_requests = self.requests_ok + self.requests_failed
        ttft = list(self.ttft_samples)
        lat = list(self.latency_samples)
        return {
            "elapsed_s": round(self.elapsed_s, 1),
            "active_users": self.active_users,
            "requests_ok": self.requests_ok,
            "requests_failed": self.requests_failed,
            "error_rate": round(self.requests_failed / total_requests, 4) if total_requests else 0.0,
            "prompt_tokens": self.prompt_tokens,
            "completion_tokens": self.completion_tokens,
            "total_tokens": self.total_tokens,
            "avg_tokens_per_sec": round(self.total_tokens / self.elapsed_s, 1)
            if self.elapsed_s > 0
            else 0.0,
            "tool_calls": self.tool_calls,
            "ttft": {
                "p50": round(_percentile(ttft, 50), 3),
                "p95": round(_percentile(ttft, 95), 3),
                "p99": round(_percentile(ttft, 99), 3),
                "count": len(ttft),
            },
            "latency": {
                "p50": round(_percentile(lat, 50), 3),
                "p95": round(_percentile(lat, 95), 3),
                "p99": round(_percentile(lat, 99), 3),
            },
            "scenarios": {
                name: {
                    "started": s.started,
                    "completed": s.completed,
                    "failed": s.failed,
                    "tool_calls": s.tool_calls,
                    "total_tokens": s.prompt_tokens + s.completion_tokens,
                }
                for name, s in self.scenarios.items()
            },
            "tool_call_counts": dict(self.tool_call_counts),
            "errors": dict(self.errors),
        }
