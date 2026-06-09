"""Simulated MCP-style tools (Slack, Google Docs, email, code assistant).

The tool *schemas* are real OpenAI function definitions handed to the model, so
the model performs genuine tool selection and argument synthesis. The tool
*results* are simulated (fixtures first, optional LLM fabrication as fallback).
"""

from .registry import TOOL_SCHEMAS, schemas_for
from .simulator import ToolSimulator

__all__ = ["TOOL_SCHEMAS", "schemas_for", "ToolSimulator"]
