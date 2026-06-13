"""Harness-side tool implementations.

The worker only *requests* tool calls; the harness executes them under the
Pillar A result contract (errors-as-data) and the Pillar B action guardrails
(allow-list, approval, dedup). Keeping the implementations here — not in the
worker — is part of "constraint-handling is invisible to the worker."

These two tools mirror the classic Alter Ego behavior: capturing an unknown
question and capturing a visitor's contact details. Both are side-effecting
(they notify the owner), which is why ``record_user_details`` is on the risky
list requiring approval/escalation.
"""

from __future__ import annotations

from typing import Any, Callable

from harness.material import ok_result, error_result


def record_unknown_question(args: dict[str, Any]) -> dict[str, Any]:
    question = (args.get("question") or "").strip()
    if not question:
        return error_result("missing 'question'")
    # Side effect is the escalation email, fired by the engine on the
    # action.approval / answerable path. Here we just acknowledge capture.
    return ok_result({"recorded": True, "question": question})


def record_user_details(args: dict[str, Any]) -> dict[str, Any]:
    email = (args.get("email") or "").strip()
    name = (args.get("name") or "Anonymous").strip()
    notes = (args.get("notes") or "").strip()
    if not email:
        return error_result("missing 'email'")
    return ok_result({"recorded": True, "email": email, "name": name, "notes": notes})


TOOLS: dict[str, Callable[[dict[str, Any]], dict[str, Any]]] = {
    "record_unknown_question": record_unknown_question,
    "record_user_details": record_user_details,
}


# Tool schemas, exposed to workers that need them (e.g. the Agents SDK worker).
TOOL_SCHEMAS = [
    {
        "name": "record_unknown_question",
        "description": "Record a question that could not be answered from the source material.",
        "parameters": {
            "type": "object",
            "properties": {
                "question": {"type": "string", "description": "The unanswered question."}
            },
            "required": ["question"],
        },
    },
    {
        "name": "record_user_details",
        "description": "Record a visitor's contact details so the owner can follow up.",
        "parameters": {
            "type": "object",
            "properties": {
                "email": {"type": "string", "description": "Visitor email address."},
                "name": {"type": "string", "description": "Visitor name, if given."},
                "notes": {"type": "string", "description": "Any context about the visitor."},
            },
            "required": ["email"],
        },
    },
]
