"""Thin async wrapper around the OpenAI-compatible chat-completions API.

Streams responses so we can measure time-to-first-token (TTFT) accurately, and
accumulates streamed tool-call deltas into complete tool calls. Usage (token
counts) is requested via ``stream_options.include_usage`` which most
OpenAI-compatible servers (OpenAI, vLLM, TGI) support; when a server omits it
we fall back to a rough token estimate so metrics still move.
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from typing import Any

from openai import AsyncOpenAI

from .config import LLMConfig
from .metrics import CallResult


@dataclass
class ToolCall:
    id: str
    name: str
    arguments: str  # raw JSON string as emitted by the model


@dataclass
class ChatResponse:
    """A completed assistant turn plus its measured cost."""

    content: str
    tool_calls: list[ToolCall]
    result: CallResult
    raw_message: dict[str, Any] = field(default_factory=dict)


def _estimate_tokens(text: str) -> int:
    # ~4 chars/token heuristic, used only when the server omits usage.
    return max(1, len(text) // 4)


class _null_async_cm:
    """No-op async context manager used when concurrency is unbounded."""

    async def __aenter__(self) -> None:
        return None

    async def __aexit__(self, *exc: object) -> bool:
        return False


class LLMClient:
    """Reusable async client bound to one :class:`LLMConfig`."""

    def __init__(self, cfg: LLMConfig, max_concurrency: int | None = None) -> None:
        self.cfg = cfg
        # Bound in-flight HTTP requests so a large user count doesn't open an
        # unbounded number of sockets. None = unlimited.
        self._sem = asyncio.Semaphore(max_concurrency) if max_concurrency else None
        self._client = AsyncOpenAI(
            base_url=cfg.base_url,
            api_key=cfg.api_key,
            timeout=cfg.request_timeout_s,
            max_retries=0,  # the load test should observe failures, not hide them
            default_headers=cfg.extra_headers or None,
        )

    async def close(self) -> None:
        await self._client.close()

    async def chat(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        *,
        model: str | None = None,
        max_tokens: int | None = None,
        temperature: float | None = None,
    ) -> ChatResponse:
        """Run one streaming chat completion and measure it."""

        start = time.monotonic()
        ttft: float | None = None
        content_parts: list[str] = []
        # tool calls accumulate across chunks, keyed by their stream index
        tool_acc: dict[int, dict[str, str]] = {}
        usage: Any = None

        kwargs: dict[str, Any] = {
            "model": model or self.cfg.model,
            "messages": messages,
            "temperature": self.cfg.temperature if temperature is None else temperature,
            "max_tokens": max_tokens or self.cfg.max_tokens,
            "stream": True,
            "stream_options": {"include_usage": True},
        }
        if tools:
            kwargs["tools"] = tools
            kwargs["tool_choice"] = "auto"

        gate = self._sem if self._sem is not None else _null_async_cm()
        try:
            async with gate:
                stream = await self._client.chat.completions.create(**kwargs)
                async for chunk in stream:
                    if chunk.usage is not None:
                        usage = chunk.usage
                    if not chunk.choices:
                        continue
                    delta = chunk.choices[0].delta
                    if delta is None:
                        continue
                    if ttft is None and (delta.content or delta.tool_calls):
                        ttft = time.monotonic() - start
                    if delta.content:
                        content_parts.append(delta.content)
                    for tc in delta.tool_calls or []:
                        slot = tool_acc.setdefault(tc.index, {"id": "", "name": "", "arguments": ""})
                        if tc.id:
                            slot["id"] = tc.id
                        if tc.function and tc.function.name:
                            slot["name"] = tc.function.name
                        if tc.function and tc.function.arguments:
                            slot["arguments"] += tc.function.arguments
        except Exception as exc:  # network, auth, server errors — all observable
            latency = time.monotonic() - start
            return ChatResponse(
                content="",
                tool_calls=[],
                result=CallResult(
                    success=False,
                    latency_s=latency,
                    error=f"{type(exc).__name__}: {exc}",
                ),
            )

        latency = time.monotonic() - start
        content = "".join(content_parts)
        tool_calls = [
            ToolCall(id=v["id"] or f"call_{i}", name=v["name"], arguments=v["arguments"] or "{}")
            for i, (_, v) in enumerate(sorted(tool_acc.items()))
            if v["name"]
        ]

        if usage is not None:
            prompt_tokens = int(getattr(usage, "prompt_tokens", 0) or 0)
            completion_tokens = int(getattr(usage, "completion_tokens", 0) or 0)
        else:
            prompt_tokens = sum(_estimate_tokens(str(m.get("content", ""))) for m in messages)
            completion_tokens = _estimate_tokens(content) + sum(
                _estimate_tokens(t.arguments) for t in tool_calls
            )

        result = CallResult(
            success=True,
            ttft_s=ttft,
            latency_s=latency,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            total_tokens=prompt_tokens + completion_tokens,
        )

        raw_message: dict[str, Any] = {"role": "assistant", "content": content or None}
        if tool_calls:
            raw_message["tool_calls"] = [
                {
                    "id": t.id,
                    "type": "function",
                    "function": {"name": t.name, "arguments": t.arguments},
                }
                for t in tool_calls
            ]
        return ChatResponse(content=content, tool_calls=tool_calls, result=result, raw_message=raw_message)
