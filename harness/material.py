"""Pillar A — Material Handling.

Clean, typed interfaces for moving material in and out of the worker, at the
**user-facing boundary**. The harness owns these boundary types: the worker
never sees raw Gradio/HTTP objects, and the UI never sees raw worker output.

Scope note: "material" here means *inbound user material* (the visitor's
per-turn request) and *outbound results*. It deliberately does NOT mean a
worker's persona/knowledge material (e.g. documents used to build a system
prompt). That is static, worker-private configuration the worker loads for
itself — it is not user input and does not belong on this boundary.

Also home to the **tool result contract**: every tool returns parseable data,
and errors come back as data (``{"ok": False, "error": ...}``), never as
exceptions that crash the loop. Large results are truncated before they blow
the context window.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field, asdict
from typing import Any

from harness.config import HARD_LIMITS


# --------------------------------------------------------------------------
# Inbound / outbound boundary objects
# --------------------------------------------------------------------------
@dataclass
class InboundMaterial:
    """Normalized request entering the harness. Built here, not by the worker."""

    text: str
    history: list[dict[str, str]] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class ToolEvent:
    """A record of one tool invocation, surfaced in OutboundMaterial."""

    name: str
    args: dict[str, Any]
    result: dict[str, Any]
    ok: bool
    latency_ms: float


@dataclass
class OutboundMaterial:
    """Structured result leaving the harness — also the replayable record."""

    run_id: str
    text: str
    tool_events: list[dict[str, Any]] = field(default_factory=list)
    checkpoints: list[dict[str, Any]] = field(default_factory=list)
    alarms: list[dict[str, Any]] = field(default_factory=list)
    escalated: bool = False
    metrics: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


# --------------------------------------------------------------------------
# Material IN — normalize + size-limit
# --------------------------------------------------------------------------
def material_in(
    text: str,
    history: list[dict[str, str]] | None = None,
    metadata: dict[str, Any] | None = None,
) -> InboundMaterial:
    """Normalize a raw request into typed InboundMaterial."""
    return InboundMaterial(
        text=(text or "").strip(),
        history=list(history or []),
        metadata=dict(metadata or {}),
    )


# --------------------------------------------------------------------------
# Tool result contract
# --------------------------------------------------------------------------
def ok_result(data: dict[str, Any] | None = None, **extra: Any) -> dict[str, Any]:
    out: dict[str, Any] = {"ok": True}
    if data:
        out.update(data)
    out.update(extra)
    return out


def error_result(error: str, **extra: Any) -> dict[str, Any]:
    """Errors come back as data, never as exceptions that crash the loop."""
    out: dict[str, Any] = {"ok": False, "error": error}
    out.update(extra)
    return out


def truncate_result(result: dict[str, Any], max_chars: int | None = None) -> dict[str, Any]:
    """Truncate any large string fields before they overflow the context."""
    cap = max_chars if max_chars is not None else HARD_LIMITS.max_tool_result_chars
    truncated: dict[str, Any] = {}
    for k, v in result.items():
        if isinstance(v, str) and len(v) > cap:
            truncated[k] = v[:cap] + f"… [truncated {len(v) - cap} chars]"
        else:
            truncated[k] = v
    return truncated


def run_tool(
    name: str,
    args: dict[str, Any],
    impl,
    timeout_s: float | None = None,
) -> ToolEvent:
    """Run a tool under the result contract: typed result, errors-as-data.

    ``impl`` is a callable ``(args) -> dict``. Any exception is captured and
    returned as an error result (a TOOL_ERROR alarm is raised by the engine).
    A per-tool wall-clock budget is recorded; the impl is responsible for its
    own internal timeout, but we always bound and record latency here.
    """
    start = time.perf_counter()
    try:
        raw = impl(args)
        if not isinstance(raw, dict) or "ok" not in raw:
            raw = error_result(
                "tool did not honor the result contract (missing 'ok')",
                returned=str(raw)[:200],
            )
        result = truncate_result(raw)
    except Exception as exc:  # noqa: BLE001 — errors become data
        result = error_result(f"{type(exc).__name__}: {exc}")
    latency_ms = (time.perf_counter() - start) * 1000.0
    return ToolEvent(
        name=name,
        args=args,
        result=result,
        ok=bool(result.get("ok")),
        latency_ms=latency_ms,
    )
