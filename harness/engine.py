"""The bounded run loop — ties the four pillars together.

This is the deck's "~15 line loop," hardened. The worker is reached only
through the ``Worker`` Protocol; guardrails, checkpoints, material handling
and alarms hang off this skeleton. Stop conditions (turn cap, token budget,
wall-clock, spend ceiling) are enforced here so a confused worker cannot spin
forever.
"""

from __future__ import annotations

import time
import uuid
from datetime import datetime, timezone
from typing import Any

from harness import config, guardrails as G, store
from harness.alarms import AlarmBus, AlarmType, Severity
from harness.checkpoints import CheckpointContext, run_checkpoints
from harness.escalation import escalate
from harness.material import (
    OutboundMaterial,
    material_in,
    run_tool,
)
from harness.observability import (
    RunMetrics,
    estimate_cost,
    init_tracing,
    span,
)
from harness.tools import TOOLS
from harness.worker import (
    Worker,
    WorkerContext,
    WorkerReply,
    grounding_context,
)


DEFLECTION = (
    "I can only chat about this person's professional background and career. "
    "Could you rephrase your question around that?"
)
SAFE_FALLBACK = (
    "Sorry — I couldn't produce a reliable answer to that. I've flagged it so "
    "the owner can follow up with you directly."
)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _journal(run_id: str, stage: str, payload: dict[str, Any]) -> None:
    try:
        store.append(run_id, stage, payload, _now())
    except Exception:  # noqa: BLE001 — persistence must not crash a run
        pass


def run(
    text: str,
    worker: Worker,
    history: list[dict[str, str]] | None = None,
    metadata: dict[str, Any] | None = None,
) -> OutboundMaterial:
    """Execute one governed turn through the harness.

    The harness supplies no persona/knowledge material — each worker owns its
    own. For the grounding checkpoint, the harness asks the worker (via the
    optional ``grounding_context()`` capability) to publish a corpus to check
    against; workers that publish none get an auto-pass on grounding.
    """
    init_tracing()
    store.init_store()

    run_id = uuid.uuid4().hex[:12]
    bus = AlarmBus()
    metrics = RunMetrics()
    limits = config.HARD_LIMITS
    started = time.perf_counter()
    seen_tool_calls: set = set()

    # Pillar C corpus: ask the worker what (if anything) to ground against.
    grounding_corpus = grounding_context(worker)

    inbound = material_in(text, history, metadata)
    _journal(run_id, "material.in", {"text": inbound.text, "history_len": len(inbound.history)})

    with span("harness.run", **{"run.id": run_id, "worker.name": getattr(worker, "name", "?")}):

        # --- Pillar B: input guardrails -------------------------------------
        in_trips = G.run_phase("input", {"text": inbound.text})
        for g, res in in_trips:
            bus.raise_alarm(
                AlarmType.GUARDRAIL_TRIP, Severity.WARNING,
                {"run_id": run_id, "guardrail": g.id, "reason": res.reason, "phase": "input"},
                res.decision.value if res.decision else "deflect",
            )
        _journal(run_id, "guardrails.in", {"trips": [g.id for g, _ in in_trips]})

        if in_trips:
            # Behavior change: deflect/reject instead of running the worker.
            msg = DEFLECTION
            out = OutboundMaterial(
                run_id=run_id, text=msg,
                alarms=bus.flush(),
                checkpoints=[],
                metrics=metrics.snapshot(),
            )
            _journal(run_id, "material.out", out.to_dict())
            return out

        # --- Bounded worker loop -------------------------------------------
        reply: WorkerReply | None = None
        tool_events: list[dict[str, Any]] = []
        verdicts: list = []
        feedback: str | None = None
        escalated = False

        for turn in range(limits.max_turns):
            metrics.turns = turn + 1

            # Hard limits: wall-clock + budget
            if (time.perf_counter() - started) > limits.wall_clock_seconds:
                bus.raise_alarm(
                    AlarmType.TURN_LIMIT, Severity.ERROR,
                    {"run_id": run_id, "limit": "wall_clock", "seconds": limits.wall_clock_seconds},
                    "stop and return current best answer",
                )
                break
            if metrics.cost_usd > limits.spend_ceiling_usd:
                bus.raise_alarm(
                    AlarmType.BUDGET_EXCEEDED, Severity.ERROR,
                    {"run_id": run_id, "spend_usd": round(metrics.cost_usd, 4)},
                    "stop and return current best answer",
                )
                break

            ctx = WorkerContext(
                text=inbound.text,
                history=inbound.history,
                feedback=feedback,
                metadata=inbound.metadata,
            )

            # --- Worker turn (wrapped in OTel span) ------------------------
            t0 = time.perf_counter()
            with span("worker.turn", **{"turn": turn, "worker.name": getattr(worker, "name", "?")}) as sp:
                try:
                    reply = worker.act(ctx)
                except Exception as exc:  # noqa: BLE001 — worker error → alarm + fallback
                    bus.raise_alarm(
                        AlarmType.WORKER_ERROR, Severity.ERROR,
                        {"run_id": run_id, "turn": turn, "error": f"{type(exc).__name__}: {exc}"},
                        "return safe fallback",
                    )
                    reply = WorkerReply(text=SAFE_FALLBACK, model=config.MODEL)
                latency_ms = (time.perf_counter() - t0) * 1000.0
                model = reply.model or config.MODEL
                cost = estimate_cost(model, reply.tokens_in, reply.tokens_out)
                metrics.record_llm(reply.tokens_in, reply.tokens_out, cost, latency_ms)
                try:
                    sp.set_attribute("llm.model", model)
                    sp.set_attribute("tokens_in", reply.tokens_in)
                    sp.set_attribute("tokens_out", reply.tokens_out)
                    sp.set_attribute("cost_usd", cost)
                    sp.set_attribute("latency_ms", latency_ms)
                except Exception:  # noqa: BLE001
                    pass

            _journal(run_id, f"worker.turn.{turn}", {
                "text": reply.text, "tool_calls": [tc.name for tc in reply.tool_calls],
                "tokens_in": reply.tokens_in, "tokens_out": reply.tokens_out, "model": model,
            })

            # --- Pillar B (action) + Pillar A (run tool) -------------------
            for call in reply.tool_calls:
                action_ctx = {"name": call.name, "args": call.args, "_seen_tool_calls": seen_tool_calls}
                trips = G.run_phase("action", action_ctx)
                blocked = False
                for g, res in trips:
                    decision = res.decision
                    bus.raise_alarm(
                        AlarmType.GUARDRAIL_TRIP, Severity.WARNING,
                        {"run_id": run_id, "guardrail": g.id, "tool": call.name, "reason": res.reason},
                        decision.value if decision else "block",
                    )
                    if decision == G.Decision.ESCALATE:
                        # Risky tool → human approval. Fire escalation, run the
                        # tool (notify owner), but mark the run as escalated.
                        escalate(
                            run_id=run_id,
                            reason=f"risky tool '{call.name}' requires approval",
                            failing_gate=g.id,
                            offending_input=inbound.text,
                            bus=bus,
                            extra={"tool_args": call.args},
                        )
                        escalated = True
                    elif decision in (G.Decision.BLOCK, G.Decision.ASK_AGAIN):
                        blocked = True
                if blocked:
                    tool_events.append({
                        "name": call.name, "args": call.args, "ok": False,
                        "result": {"ok": False, "error": "blocked by action guardrail"},
                        "latency_ms": 0.0,
                    })
                    continue

                impl = TOOLS.get(call.name)
                if impl is None:
                    bus.raise_alarm(
                        AlarmType.TOOL_ERROR, Severity.ERROR,
                        {"run_id": run_id, "tool": call.name, "error": "no implementation"},
                        "ignore tool call",
                    )
                    continue
                with span("tool.call", **{"tool.name": call.name}):
                    ev = run_tool(call.name, call.args, impl)
                metrics.record_tool(ev.ok, ev.latency_ms)
                if not ev.ok:
                    bus.raise_alarm(
                        AlarmType.TOOL_ERROR, Severity.WARNING,
                        {"run_id": run_id, "tool": call.name, "result": ev.result},
                        "return error to worker as data",
                    )
                tool_events.append({
                    "name": ev.name, "args": ev.args, "ok": ev.ok,
                    "result": ev.result, "latency_ms": ev.latency_ms,
                })

            # --- Pillar C: checkpoints -------------------------------------
            cp_ctx = CheckpointContext(
                answer=reply.text, question=inbound.text,
                source=grounding_corpus or "", tool_events=tool_events,
            )
            with span("checkpoints"):
                verdicts = run_checkpoints(cp_ctx)
            passed = sum(1 for v in verdicts if v.passed)
            metrics.record_checkpoints(len(verdicts), passed)
            _journal(run_id, "checkpoints", [v.to_dict() for v in verdicts])

            failed = [v for v in verdicts if not v.passed]
            for v in failed:
                bus.raise_alarm(
                    AlarmType.CHECKPOINT_FAIL, Severity.WARNING,
                    {"run_id": run_id, "checkpoint": v.id, "evidence": v.evidence,
                     "recommended_action": v.recommended_action},
                    v.recommended_action,
                )

            if not failed:
                break  # all passed — ship it

            # Behavior change on feedback: retry with the failure as context.
            retriable = [v for v in failed if v.recommended_action == "retry"]
            if retriable and turn < limits.max_turns - 1:
                feedback = (
                    "Your previous answer failed these checks: "
                    + "; ".join(f"{v.id} — {v.evidence}" for v in retriable)
                    + ". Only state facts supported by the provided source material; "
                    "if the source does not support a claim, say you don't have that "
                    "information and offer to connect them with the owner."
                )
                continue
            else:
                # Repeated failure or non-retriable → escalate, do not ship a guess.
                escalate(
                    run_id=run_id,
                    reason="checkpoint(s) failed and could not be remediated",
                    failing_gate=",".join(v.id for v in failed),
                    offending_input=inbound.text,
                    bus=bus,
                    extra={"final_answer": reply.text[:500]},
                )
                escalated = True
                reply = WorkerReply(text=SAFE_FALLBACK, model=reply.model)
                break

        else:
            # Loop exhausted without break → turn limit.
            bus.raise_alarm(
                AlarmType.TURN_LIMIT, Severity.ERROR,
                {"run_id": run_id, "max_turns": limits.max_turns},
                "escalate to owner",
            )

        # --- Pillar B: output guardrails -----------------------------------
        final_text = reply.text if reply else SAFE_FALLBACK
        out_trips = G.run_phase("output", {"text": final_text})
        for g, res in out_trips:
            bus.raise_alarm(
                AlarmType.GUARDRAIL_TRIP, Severity.WARNING,
                {"run_id": run_id, "guardrail": g.id, "reason": res.reason, "phase": "output"},
                res.decision.value if res.decision else "fallback",
            )
            final_text = SAFE_FALLBACK
        _journal(run_id, "guardrails.out", {"trips": [g.id for g, _ in out_trips]})

        out = OutboundMaterial(
            run_id=run_id,
            text=final_text,
            tool_events=tool_events,
            checkpoints=[v.to_dict() for v in verdicts],
            alarms=bus.flush(),
            escalated=escalated,
            metrics=metrics.snapshot(),
        )
        _journal(run_id, "material.out", out.to_dict())
        return out
