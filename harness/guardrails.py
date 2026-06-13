"""Pillar B — Guardrails.

Declared, not implicit. Every guardrail lives in the single ``GUARDRAILS``
registry so they can be listed, audited, and toggled without reading the
worker. Each has an id, a phase, a check function, and an on-trip decision.

Three phases — input / action / output — plus the hard limits that bound a
run. A tripped guardrail is logged, may raise an alarm, and **changes worker
behavior** (deflect / reject / block / re-ask / escalate) rather than
silently passing.

Guardrails are *boundary constraints* ("don't let this in / out / happen").
Checkpoints (Pillar C) are *evaluations of work product*. Deliberately
separate.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable

from harness.config import HARD_LIMITS


# --------------------------------------------------------------------------
# Decisions taken on a trip
# --------------------------------------------------------------------------
class Decision(str, Enum):
    DEFLECT = "deflect"      # input: refuse politely, do not run worker
    REJECT = "reject"        # input: hard reject (too big, etc.)
    BLOCK = "block"          # action: do not run this tool call
    ASK_AGAIN = "ask_again"  # action: bounce back for corrected args
    ESCALATE = "escalate"    # action: route to a human
    FALLBACK = "fallback"    # output: replace with safe fallback


@dataclass
class GuardrailResult:
    tripped: bool
    reason: str = ""
    decision: Decision | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class Guardrail:
    id: str
    phase: str  # "input" | "action" | "output"
    criteria: str
    fn: Callable[[dict[str, Any]], GuardrailResult]
    on_trip: Decision

    def check(self, ctx: dict[str, Any]) -> GuardrailResult:
        res = self.fn(ctx)
        if res.tripped and res.decision is None:
            res.decision = self.on_trip
        return res


def _ok() -> GuardrailResult:
    return GuardrailResult(tripped=False)


def _trip(reason: str, **meta: Any) -> GuardrailResult:
    return GuardrailResult(tripped=True, reason=reason, metadata=meta)


# --------------------------------------------------------------------------
# Input checks
# --------------------------------------------------------------------------
_INJECTION_PATTERNS = [
    r"ignore (all|any|the)? ?(previous|prior|above) instructions",
    r"disregard (the )?(system|previous) prompt",
    r"reveal (your )?(system )?prompt",
    r"you are now",
    r"developer mode",
    r"print your (instructions|system prompt|prompt)",
    r"repeat the (text|words) above",
    r"act as (?:if|though) you (?:have|had) no (?:rules|restrictions)",
]


def detect_injection(ctx: dict[str, Any]) -> GuardrailResult:
    text = (ctx.get("text") or "").lower()
    for pat in _INJECTION_PATTERNS:
        if re.search(pat, text):
            return _trip(f"prompt-injection pattern matched: /{pat}/", pattern=pat)
    return _ok()


def max_input_len(ctx: dict[str, Any]) -> GuardrailResult:
    text = ctx.get("text") or ""
    if len(text) > HARD_LIMITS.max_input_chars:
        return _trip(
            f"input length {len(text)} exceeds cap {HARD_LIMITS.max_input_chars}",
            length=len(text),
        )
    return _ok()


# --------------------------------------------------------------------------
# Action checks
# --------------------------------------------------------------------------
ALLOWED_TOOLS = {"record_unknown_question", "record_user_details"}
RISKY_TOOLS = {"record_user_details"}  # side-effecting → require approval
_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def tool_allowlisted(ctx: dict[str, Any]) -> GuardrailResult:
    name = ctx.get("name")
    if name not in ALLOWED_TOOLS:
        return _trip(f"tool '{name}' is not on the allow-list", tool=name)
    return _ok()


def valid_email(ctx: dict[str, Any]) -> GuardrailResult:
    name = ctx.get("name")
    args = ctx.get("args") or {}
    if name == "record_user_details":
        email = (args.get("email") or "").strip()
        if email and not _EMAIL_RE.match(email):
            return _trip(f"invalid email format: {email!r}", email=email)
    return _ok()


def not_duplicate(ctx: dict[str, Any]) -> GuardrailResult:
    """Dedup/rate-limit side effects using a per-run seen-set."""
    name = ctx.get("name")
    args = ctx.get("args") or {}
    seen: set = ctx.get("_seen_tool_calls")  # injected by engine
    key = (name, repr(sorted(args.items())))
    if seen is not None and key in seen:
        return _trip(f"duplicate tool call suppressed: {name}", tool=name)
    if seen is not None:
        seen.add(key)
    return _ok()


def needs_human(ctx: dict[str, Any]) -> GuardrailResult:
    """Require human approval for risky (side-effecting) calls."""
    name = ctx.get("name")
    if name in RISKY_TOOLS:
        return _trip(
            f"tool '{name}' is risky and requires human approval/notification",
            tool=name,
        )
    return _ok()


# --------------------------------------------------------------------------
# Output checks
# --------------------------------------------------------------------------
_LEAK_MARKERS = [
    "=== curated summary ===",
    "=== linkedin export",
    "system prompt",
    "you are role-playing",
    "my instructions are",
]


def no_prompt_leak(ctx: dict[str, Any]) -> GuardrailResult:
    text = (ctx.get("text") or "").lower()
    for marker in _LEAK_MARKERS:
        if marker in text:
            return _trip(f"output appears to leak harness internals: {marker!r}", marker=marker)
    return _ok()


def matches_schema(ctx: dict[str, Any]) -> GuardrailResult:
    text = ctx.get("text") or ""
    if not text.strip():
        return _trip("output is empty")
    return _ok()


# --------------------------------------------------------------------------
# The registry — the single declared source of truth
# --------------------------------------------------------------------------
GUARDRAILS: list[Guardrail] = [
    Guardrail("input.injection", "input", "No prompt-injection patterns in the request.",
              detect_injection, Decision.DEFLECT),
    Guardrail("input.size", "input", "Request length within the input cap.",
              max_input_len, Decision.REJECT),
    Guardrail("action.allowlist", "action", "Tool is on the allow-list.",
              tool_allowlisted, Decision.BLOCK),
    Guardrail("action.email_fmt", "action", "Email argument is well-formed.",
              valid_email, Decision.ASK_AGAIN),
    Guardrail("action.rate_limit", "action", "Side-effecting tool call is not a duplicate.",
              not_duplicate, Decision.BLOCK),
    Guardrail("action.approval", "action", "Risky tool calls require human approval.",
              needs_human, Decision.ESCALATE),
    Guardrail("output.no_leak", "output", "Response does not leak harness internals/source dump.",
              no_prompt_leak, Decision.FALLBACK),
    Guardrail("output.schema", "output", "Response is non-empty and well-formed.",
              matches_schema, Decision.FALLBACK),
]


def by_phase(phase: str) -> list[Guardrail]:
    return [g for g in GUARDRAILS if g.phase == phase]


def list_guardrails() -> list[dict[str, str]]:
    """Auditable listing — every constraint without reading the worker."""
    return [
        {"id": g.id, "phase": g.phase, "criteria": g.criteria, "on_trip": g.on_trip.value}
        for g in GUARDRAILS
    ]


def run_phase(phase: str, ctx: dict[str, Any]) -> list[tuple[Guardrail, GuardrailResult]]:
    """Run all guardrails for a phase; return every tripped (guardrail, result)."""
    trips: list[tuple[Guardrail, GuardrailResult]] = []
    for g in by_phase(phase):
        res = g.check(ctx)
        if res.tripped:
            trips.append((g, res))
    return trips
