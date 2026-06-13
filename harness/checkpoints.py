"""Pillar C — Checkpoints.

Named evaluation gates with **explicit pass/fail** criteria, run on the
worker's candidate output. Each yields a structured ``CheckpointResult``.

Checkpoints evaluate *work product* (is this answer grounded? on-persona?),
distinct from guardrails which constrain boundaries. A failed ``grounding``
checkpoint causes the engine to re-prompt the worker with the failure as
feedback (bounded retries), then escalate — the concrete "behavior changes on
feedback" demonstration.

The grounding check uses a cheap LLM judge when an OpenAI key is available,
and falls back to a deterministic lexical-overlap heuristic otherwise, so the
demo still runs offline.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field, asdict
from typing import Any

from harness import config


@dataclass
class CheckpointResult:
    id: str
    passed: bool
    criteria: str
    evidence: dict[str, Any] = field(default_factory=dict)
    score: float | None = None
    recommended_action: str = "accept"  # "accept" | "retry" | "escalate"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class CheckpointContext:
    """What every checkpoint sees about the worker's candidate output."""

    answer: str
    question: str
    source: str
    tool_events: list[dict[str, Any]] = field(default_factory=list)


@dataclass
class Checkpoint:
    id: str
    criteria: str
    fn: Any  # Callable[[CheckpointContext], CheckpointResult]
    retriable: bool = False  # whether a failure should trigger retry-with-feedback

    def evaluate(self, ctx: CheckpointContext) -> CheckpointResult:
        return self.fn(ctx)


# --------------------------------------------------------------------------
# Grounding — the marquee checkpoint
# --------------------------------------------------------------------------
_REFUSAL_MARKERS = [
    "i don't have", "i do not have", "i'm not sure", "i am not sure",
    "i don't know", "i can't find", "cannot find", "not in my background",
    "no information", "i'd be happy to connect", "reach out",
]

_GROUNDING_JUDGE_PROMPT = (
    "You are a strict grounding evaluator. Given SOURCE material and an ANSWER, "
    "decide if every factual claim in the ANSWER is supported by the SOURCE. "
    "Polite refusals or offers to follow up count as grounded (they invent nothing). "
    "Respond ONLY with compact JSON: "
    '{"grounded": true|false, "reason": "<short>"}.'
)


def _looks_like_refusal(answer: str) -> bool:
    a = answer.lower()
    return any(m in a for m in _REFUSAL_MARKERS)


def _lexical_grounding(ctx: CheckpointContext) -> tuple[bool, float, str]:
    """Deterministic fallback: fraction of answer content words present in source."""
    src = ctx.source.lower()
    words = re.findall(r"[a-zA-Z][a-zA-Z0-9+#.\-]{3,}", ctx.answer.lower())
    if not words:
        return True, 1.0, "no factual content words to check"
    stop = {"this", "that", "with", "from", "have", "your", "about", "their",
            "would", "could", "there", "which", "what", "when", "they", "them",
            "been", "were", "will", "more", "most", "some", "such", "than",
            "then", "into", "over", "also", "very", "just", "like", "well"}
    content = [w for w in words if w not in stop]
    if not content:
        return True, 1.0, "only common words"
    present = sum(1 for w in content if w in src)
    score = present / len(content)
    return score >= 0.45, score, f"lexical overlap {present}/{len(content)}={score:.2f}"


def _llm_grounding(ctx: CheckpointContext) -> tuple[bool, float, str] | None:
    if not config.OPENAI_API_KEY:
        return None
    try:
        from openai import OpenAI

        client = OpenAI(api_key=config.OPENAI_API_KEY)
        resp = client.chat.completions.create(
            model=config.MODEL,
            temperature=0,
            messages=[
                {"role": "system", "content": _GROUNDING_JUDGE_PROMPT},
                {
                    "role": "user",
                    "content": (
                        f"SOURCE:\n{ctx.source[:6000]}\n\n"
                        f"ANSWER:\n{ctx.answer[:2000]}"
                    ),
                },
            ],
        )
        content = (resp.choices[0].message.content or "").strip()
        content = re.sub(r"^```(json)?|```$", "", content, flags=re.MULTILINE).strip()
        data = json.loads(content)
        grounded = bool(data.get("grounded"))
        return grounded, 1.0 if grounded else 0.0, str(data.get("reason", ""))[:200]
    except Exception:  # noqa: BLE001 — judge failure falls back to lexical
        return None


def check_grounding(ctx: CheckpointContext) -> CheckpointResult:
    # No grounding corpus published by the worker → nothing to check against;
    # the harness stays domain-agnostic and auto-passes grounding.
    if not ctx.source.strip():
        return CheckpointResult(
            id="grounding",
            passed=True,
            criteria="Answer is supported by the worker's grounding corpus; no invented facts.",
            evidence={"mode": "no_corpus", "note": "worker published no grounding corpus"},
            score=1.0,
            recommended_action="accept",
        )

    if _looks_like_refusal(ctx.answer):
        return CheckpointResult(
            id="grounding",
            passed=True,
            criteria="Answer is supported by the worker's grounding corpus; no invented facts.",
            evidence={"mode": "refusal", "note": "polite deflection invents nothing"},
            score=1.0,
            recommended_action="accept",
        )

    judged = _llm_grounding(ctx)
    if judged is not None:
        grounded, score, reason = judged
        mode = "llm_judge"
    else:
        grounded, score, reason = _lexical_grounding(ctx)
        mode = "lexical_fallback"

    return CheckpointResult(
        id="grounding",
        passed=grounded,
        criteria="Answer is supported by the worker's grounding corpus; no invented facts.",
        evidence={"mode": mode, "reason": reason},
        score=score,
        recommended_action="accept" if grounded else "retry",
    )


# --------------------------------------------------------------------------
# On-persona
# --------------------------------------------------------------------------
_OFF_PERSONA = [
    "as an ai language model", "i am an ai", "i'm an ai", "as a chatbot",
    "i cannot have personal", "i don't have personal experiences",
]


def check_on_persona(ctx: CheckpointContext) -> CheckpointResult:
    a = ctx.answer.lower()
    broke = [m for m in _OFF_PERSONA if m in a]
    passed = not broke
    return CheckpointResult(
        id="on_persona",
        passed=passed,
        criteria="Response stays in persona and does not break the fourth wall.",
        evidence={"broke_persona_markers": broke},
        score=1.0 if passed else 0.0,
        recommended_action="accept" if passed else "retry",
    )


# --------------------------------------------------------------------------
# Lead capture
# --------------------------------------------------------------------------
_EMAIL_RE = re.compile(r"[^@\s]+@[^@\s]+\.[^@\s]+")


def check_lead_capture(ctx: CheckpointContext) -> CheckpointResult:
    gave_contact = bool(_EMAIL_RE.search(ctx.question))
    recorded = any(
        ev.get("name") == "record_user_details" and ev.get("ok")
        for ev in ctx.tool_events
    )
    # Passes if no contact was offered, OR contact was offered and recorded.
    passed = (not gave_contact) or recorded
    return CheckpointResult(
        id="lead_capture",
        passed=passed,
        criteria="If visitor gave contact info, it was recorded/escalated.",
        evidence={"gave_contact": gave_contact, "recorded": recorded},
        score=1.0 if passed else 0.0,
        recommended_action="accept" if passed else "escalate",
    )


# --------------------------------------------------------------------------
# Answerable
# --------------------------------------------------------------------------
def check_answerable(ctx: CheckpointContext) -> CheckpointResult:
    refused = _looks_like_refusal(ctx.answer)
    recorded_unknown = any(
        ev.get("name") == "record_unknown_question" and ev.get("ok")
        for ev in ctx.tool_events
    )
    # This checkpoint always passes structurally; it records whether the
    # unknown-question path fired when the worker deflected.
    passed = True
    return CheckpointResult(
        id="answerable",
        passed=passed,
        criteria="If unanswerable from docs, the unknown-question path fired.",
        evidence={"refused": refused, "recorded_unknown": recorded_unknown},
        score=1.0,
        recommended_action="accept",
    )


# --------------------------------------------------------------------------
# The registry
# --------------------------------------------------------------------------
CHECKPOINTS: list[Checkpoint] = [
    Checkpoint("grounding",
               "Answer is supported by the worker's grounding corpus; no invented facts.",
               check_grounding, retriable=True),
    Checkpoint("on_persona",
               "Response stays in persona and on the career domain.",
               check_on_persona, retriable=True),
    Checkpoint("lead_capture",
               "If visitor gave contact info, it was recorded/escalated.",
               check_lead_capture, retriable=False),
    Checkpoint("answerable",
               "If unanswerable from docs, the unknown-question path fired.",
               check_answerable, retriable=False),
]


def list_checkpoints() -> list[dict[str, str]]:
    return [{"id": c.id, "criteria": c.criteria, "retriable": c.retriable} for c in CHECKPOINTS]


def run_checkpoints(ctx: CheckpointContext) -> list[CheckpointResult]:
    return [c.evaluate(ctx) for c in CHECKPOINTS]
