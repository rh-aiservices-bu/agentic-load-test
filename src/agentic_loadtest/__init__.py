"""Agentic load-test tool.

Simulates many concurrent users running multi-turn, tool-calling agentic
applications against an OpenAI-compatible LLM endpoint. Tools (Slack, Google
Docs, email, etc.) are simulated; the reasoning and tool-selection are driven
by real LLM calls so the load profile matches real agentic traffic.
"""

__version__ = "0.1.0"
