"""OpenAI function/tool schemas for the simulated tool surface.

Add a tool here and it becomes available to any scenario that lists its name in
``tools:``. The matching fixtures live in ``fixtures/*.json`` keyed by tool name.
"""

from __future__ import annotations

from typing import Any


def _tool(name: str, description: str, params: dict[str, Any], required: list[str]) -> dict[str, Any]:
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": description,
            "parameters": {
                "type": "object",
                "properties": params,
                "required": required,
            },
        },
    }


_STR = {"type": "string"}


TOOL_SCHEMAS: dict[str, dict[str, Any]] = {
    # ---- Slack -----------------------------------------------------------
    "slack_search": _tool(
        "slack_search",
        "Search Slack messages across channels matching a query.",
        {"query": {**_STR, "description": "Search terms, e.g. a project name."},
         "channel": {**_STR, "description": "Optional channel to restrict to."}},
        ["query"],
    ),
    "slack_read_thread": _tool(
        "slack_read_thread",
        "Read all messages in a Slack thread.",
        {"channel": _STR, "thread_ts": {**_STR, "description": "Thread timestamp id."}},
        ["channel", "thread_ts"],
    ),
    "slack_post_message": _tool(
        "slack_post_message",
        "Post a message to a Slack channel.",
        {"channel": _STR, "text": _STR},
        ["channel", "text"],
    ),
    # ---- Google Docs -----------------------------------------------------
    "gdocs_search": _tool(
        "gdocs_search",
        "Search the user's Google Docs by title or content.",
        {"query": _STR},
        ["query"],
    ),
    "gdocs_create": _tool(
        "gdocs_create",
        "Create a new Google Doc and return its id and shareable URL.",
        {"title": _STR, "content": {**_STR, "description": "Initial document body (markdown ok)."}},
        ["title", "content"],
    ),
    "gdocs_append": _tool(
        "gdocs_append",
        "Append content to an existing Google Doc.",
        {"doc_id": _STR, "content": _STR},
        ["doc_id", "content"],
    ),
    # ---- Email -----------------------------------------------------------
    "email_search": _tool(
        "email_search",
        "Search the user's mailbox for messages matching a query.",
        {"query": _STR},
        ["query"],
    ),
    "email_send": _tool(
        "email_send",
        "Send an email.",
        {"to": _STR, "subject": _STR, "body": _STR},
        ["to", "subject", "body"],
    ),
    # ---- Calendar --------------------------------------------------------
    "calendar_list_events": _tool(
        "calendar_list_events",
        "List the user's calendar events within a date range.",
        {"start_date": _STR, "end_date": _STR},
        ["start_date", "end_date"],
    ),
    "calendar_create_event": _tool(
        "calendar_create_event",
        "Create a calendar event.",
        {"title": _STR, "start": _STR, "end": _STR, "attendees": _STR},
        ["title", "start", "end"],
    ),
    # ---- Code assistant --------------------------------------------------
    "search_code": _tool(
        "search_code",
        "Search the codebase for a symbol or text and return matching files/lines.",
        {"query": _STR, "path": {**_STR, "description": "Optional path to scope the search."}},
        ["query"],
    ),
    "read_file": _tool(
        "read_file",
        "Read the contents of a file from the repository.",
        {"path": _STR},
        ["path"],
    ),
    "write_file": _tool(
        "write_file",
        "Write or overwrite a file in the repository.",
        {"path": _STR, "content": _STR},
        ["path", "content"],
    ),
    "run_tests": _tool(
        "run_tests",
        "Run the test suite (optionally a subset) and return the result summary.",
        {"target": {**_STR, "description": "Optional test target/path."}},
        [],
    ),
}


def schemas_for(names: list[str]) -> list[dict[str, Any]]:
    """Return the OpenAI tool schemas for the given tool names (unknown names skipped)."""

    return [TOOL_SCHEMAS[n] for n in names if n in TOOL_SCHEMAS]
