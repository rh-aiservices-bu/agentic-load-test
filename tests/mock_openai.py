"""Minimal mock OpenAI-compatible streaming server for end-to-end testing.

Implements just enough of /v1/chat/completions (streaming + usage + tool calls)
to exercise the agent loop without a real model. If the conversation already
contains a tool result, it returns a final text answer; otherwise, if tools are
offered, it emits one tool call; otherwise plain text.
"""

from __future__ import annotations

import json

from fastapi import FastAPI, Request
from fastapi.responses import StreamingResponse

app = FastAPI()

# Simulate a prefix cache: remember system-prompt prefixes we've "seen" and
# report a high cached_tokens hit on subsequent requests carrying the same one.
_SEEN_PREFIXES: set[str] = set()


def chunk(delta=None, finish=None, usage=None):
    payload = {
        "id": "chatcmpl-mock",
        "object": "chat.completion.chunk",
        "created": 0,
        "model": "mock",
        "choices": [] if usage is not None else [{"index": 0, "delta": delta or {}, "finish_reason": finish}],
    }
    if usage is not None:
        payload["usage"] = usage
    return f"data: {json.dumps(payload)}\n\n"


@app.post("/v1/chat/completions")
async def chat(request: Request):
    body = await request.json()
    messages = body.get("messages", [])
    tools = body.get("tools")
    has_tool_result = any(m.get("role") == "tool" for m in messages)
    # Estimate prompt tokens from the actual request so a large system prompt
    # visibly inflates the reported usage (~4 chars/token).
    prompt_chars = sum(len(str(m.get("content", "") or "")) for m in messages)
    prompt_tokens = max(1, prompt_chars // 4)

    # Prefix-cache simulation: key on the system prompt (the large shared prefix).
    # First time we see it -> miss (0 cached); afterwards -> ~90% of it is cached.
    system = next((m.get("content", "") for m in messages if m.get("role") == "system"), "")
    sys_tokens = max(1, len(str(system)) // 4)
    key = str(system)[:512]
    cached_tokens = 0
    if key in _SEEN_PREFIXES:
        cached_tokens = int(sys_tokens * 0.9)
    else:
        _SEEN_PREFIXES.add(key)

    async def gen():
        if tools and not has_tool_result:
            name = tools[0]["function"]["name"]
            yield chunk({"role": "assistant"})
            yield chunk({"tool_calls": [{"index": 0, "id": "call_1", "type": "function",
                                          "function": {"name": name, "arguments": ""}}]})
            yield chunk({"tool_calls": [{"index": 0, "function": {"arguments": "{\"query\": \"Project Atlas\"}"}}]})
            yield chunk(finish="tool_calls")
        else:
            for word in ["Here ", "is ", "the ", "summary ", "you ", "asked ", "for."]:
                yield chunk({"content": word})
            yield chunk(finish="stop")
        yield chunk(usage={"prompt_tokens": prompt_tokens, "completion_tokens": 40,
                            "total_tokens": prompt_tokens + 40,
                            "prompt_tokens_details": {"cached_tokens": cached_tokens}})
        yield "data: [DONE]\n\n"

    return StreamingResponse(gen(), media_type="text/event-stream")
