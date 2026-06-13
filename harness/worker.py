"""The swappable Worker interface.

The harness depends on this Protocol, never on a concrete model SDK. The
four pillar modules wrap this interface; a worker imports nothing from them.
A worker just thinks (produces text) and optionally asks to call tools.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable


@dataclass
class ToolCall:
    """A worker's request to invoke a named tool with arguments."""

    name: str
    args: dict[str, Any] = field(default_factory=dict)
    id: str | None = None


@dataclass
class WorkerContext:
    """The per-turn, user-facing material the worker needs to reply.

    This carries only *inbound* material — the visitor's message, the
    conversation history, optional harness feedback, and metadata. It does
    NOT carry the worker's persona/knowledge material: that is static
    configuration the worker loads for itself at construction (e.g. a system
    prompt built from its own documents), not per-turn user input.

    The worker never sees raw Gradio/HTTP objects, and it never touches
    guardrails/checkpoints/alarms.
    """

    text: str
    history: list[dict[str, str]] = field(default_factory=list)
    feedback: str | None = None  # checkpoint failure fed back for retry
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class WorkerReply:
    """What a worker hands back: text plus optional tool calls and usage."""

    text: str
    tool_calls: list[ToolCall] = field(default_factory=list)
    tokens_in: int = 0
    tokens_out: int = 0
    model: str = ""
    raw: Any = None


@runtime_checkable
class Worker(Protocol):
    name: str

    def act(self, context: WorkerContext) -> WorkerReply:
        """Produce a reply for the given context."""
        ...


def grounding_context(worker: Worker) -> str | None:
    """Return the corpus a worker wants its answers grounded against, if any.

    The grounding checkpoint (Pillar C) evaluates whether an answer invented
    facts. To stay domain-agnostic, the harness does not know what a worker's
    knowledge is — it simply *asks* the worker to publish a grounding corpus.

    A worker opts in by implementing ``grounding_context(self) -> str | None``.
    Workers with no corpus (or that decline) return ``None``, and the grounding
    checkpoint auto-passes for them.
    """
    fn = getattr(worker, "grounding_context", None)
    if callable(fn):
        try:
            return fn()
        except Exception:  # noqa: BLE001 — never let a worker break the harness
            return None
    return None
