# HARNESS.md — Alter Ego Harness

A domain-agnostic **AI harness**: the runtime scaffolding that turns a raw chat model into
a reliable, observable, governed agent. The harness provides four things **for free** to any
worker dropped into it — **guardrails, checkpoints, material handling, and alarms** — each
implemented as a **distinct component that is demonstrably separate from the worker**.

> Agents focus on tasks. Harnesses focus on constraints. This harness makes
> constraint-handling **invisible to the worker**: the worker just thinks and calls tools;
> the harness validates, evaluates, bounds, traces, and escalates around it.

The demo worker is a career-conversation chatbot ("Alter Ego") that answers questions about
a real person using their LinkedIn export + summary. It is **just one worker** — the harness
knows nothing about careers, and a second worker can be swapped in with no harness changes.

---

## 1. Design Principles

1. **Harness ≠ worker.** The worker is reached only through a narrow `Worker` Protocol. The
   four pillar modules wrap that interface; the worker imports nothing from them.
2. **Declared, not implicit.** Guardrails live in a registry; checkpoints have explicit
   pass/fail criteria; alarms are typed. You can list every constraint without reading the
   worker.
3. **Failures become data, then alarms.** Tool/model errors return as structured data the
   worker can react to; the harness simultaneously raises a structured alarm.
4. **Behavior changes on feedback.** A failed checkpoint feeds back into the loop (retry
   with the failure as context) or escalates — the worker's behavior measurably changes.
5. **Bounded by construction.** Turn cap, token budget, wall-clock timeout, spend ceiling.
6. **No backend lock-in.** Observability is OpenTelemetry; any OTel sink can ingest it.
7. **Stop and ask when unsure.** Human-in-the-loop escalation is a first-class path.

---

## 2. Architecture Overview

```
Visitor ─▶ [Material IN] ─▶ [Guardrails IN] ─▶ ┌─ RUN ENGINE (bounded loop) ─┐ ─▶ [Checkpoints]
                                               │   call Worker (swappable)    │        │ pass? ─▶ [Guardrails OUT] ─▶ [Material OUT] ─▶ Visitor
                                               │   run tools (Action guards)  │        │ fail? ─▶ retry w/ feedback  OR  escalate (human-in-the-loop)
                                               └──────────────────────────────┘
        └────────────────── ALARMS + OBSERVABILITY (OTel spans, run journal) wrap everything ──────────────────┘
```

### Module map (the four pillars are physically distinct)

| Path | Pillar / role |
|---|---|
| `harness/material.py` | **Material handling** — typed in/out boundary objects |
| `harness/guardrails.py` | **Guardrails** — declared input/action/output checks |
| `harness/checkpoints.py` | **Checkpoints** — named pass/fail evaluation gates |
| `harness/alarms.py` | **Alarms** — `Alarm` schema, `AlarmType`, `Severity`, sinks |
| `harness/observability.py` | OTel spans + run journal (substrate alarms ride on) |
| `harness/escalation.py` | Human-in-the-loop policy + routing |
| `harness/engine.py` | Bounded run loop tying the pillars together |
| `harness/worker.py` | `Worker` Protocol — the swappable interface |
| `harness/store.py` | Checkpoint/run persistence for replay |
| `workers/alter_ego.py` | Demo worker #1 (OpenAI Agents SDK) |
| `workers/echo_worker.py` | Demo worker #2 (portability proof) |
| `app.py` | Gradio UI — thin client of `harness.engine.run()` |

---

## 3. The Four Pillars

### Pillar A — Material Handling
Clean, typed interfaces for moving material in and out of the worker.

- `InboundMaterial` — normalized request (`text`, `history`, `metadata`).
- `SourceMaterial` — the engineer's **real input** (`linkedin.pdf` via `pypdf` + `summary.txt`),
  loaded, validated, and size-capped by the harness.
- `OutboundMaterial` — structured result (`text`, `tool_events`, `checkpoints`, `alarms`,
  `escalated`) — also the replayable record.
- Tool **result contract**: parseable output; errors returned as `{"ok": false, "error": …}`.
- Engineering: per-tool timeouts, idempotency keys, large-result truncation before the
  context window overflows.

### Pillar B — Guardrails
Declared boundary constraints in a single registry, in three phases plus hard limits.

| Phase | Examples | On trip |
|---|---|---|
| Input | prompt-injection detection, size limit | deflect / reject |
| Action | tool allow-list, arg/email validation, dedup/rate-limit, **approval for risky calls** | block / re-ask / **escalate** |
| Output | no prompt-leak, schema/fact gate | safe fallback |
| Hard limits | turn cap, token budget, timeout, spend ceiling | alarm + stop |

A trip is logged, may emit an alarm, and changes worker behavior.

### Pillar C — Checkpoints
Named evaluation gates with **explicit pass/fail** criteria, run on the worker's candidate
output. Each yields a `CheckpointResult { id, passed, criteria, score?, evidence,
recommended_action }`.

Demo checkpoints: `grounding` (supported by source, no invented facts), `on_persona`,
`lead_capture`, `answerable`. A failed `grounding` checkpoint **re-prompts the worker with
the failure as feedback** (bounded retries), then **escalates** — the concrete "behavior
changes on feedback" demonstration. Results are **persisted** for replay.

### Pillar D — Alarms (+ Observability)
Structured alarms: `Alarm { type, severity, context, recommended_action, ts }` with
`AlarmType` ∈ {guardrail_trip, checkpoint_fail, turn_limit_exceeded, tool_error,
budget_exceeded, worker_error, human_escalation_required} and `Severity` ∈ {info, warning,
error, critical}. Sinks: stdout (HF logs), OTel span events, Mailtrap (actionable ones).

Observability: every model/tool call is an OTel span with `llm.model`, `tokens_in`,
`tokens_out`, `cost_usd`, latency. Tracked signals: **p95 latency, $/run, err%, eval
pass-rate** (checkpoint pass-rate vs. a small test set).

---

## 4. Swappable Worker Interface

```python
class Worker(Protocol):
    name: str
    def act(self, context: WorkerContext) -> WorkerReply: ...  # text + optional tool_calls
```

- **Worker #1** `workers/alter_ego.py` — OpenAI Agents SDK (`Agent` + `Runner`, gpt-4o-mini).
- **Worker #2** `workers/echo_worker.py` — a different impl/model, dropped in via
  `ACTIVE_WORKER` with **zero harness changes** (portability bonus).

Because the pillars wrap this Protocol, constraint-handling is invisible to the worker.

---

## 5. Human-in-the-Loop Escalation

The harness stops and asks rather than guesses when: an `action.approval` guardrail trips on
a risky tool; a checkpoint fails repeatedly; a question is unanswerable from source; or a
lead is captured. It emits an `ESCALATION` alarm and routes full context (run_id, failing
gate, offending input) to the human via Mailtrap, deflecting instead of shipping a guess.

---

## 6. Checkpoint Persistence & Replay

Each run has a `run_id`; every stage writes a journal entry to `store.py`. Checkpoint
results and worker I/O are persisted, so a run can be **replayed from any checkpoint
forward** without re-running prior stages (or re-calling the model). Because HF Spaces disk
is ephemeral, the journal targets an **off-box** sink.

---

## 7. Control Flow

```
run(inbound):
  material = material_in(inbound)        # Pillar A
  guard_in(material)                     # Pillar B (input)
  for turn in range(MAX_TURNS):          # hard limit
    reply = worker.act(context)          # Worker (swappable), wrapped in OTel span
    for call in reply.tool_calls:
      guard_action(call)                 # Pillar B (action) — allow-list / approval
      result = run_tool(call)            # Pillar A — typed result, errors-as-data
    verdicts = run_checkpoints(reply)    # Pillar C — pass/fail
    if all_passed(verdicts): break
    elif retriable(verdicts): context += feedback(verdicts)   # behavior change
    else: escalate(verdicts); break      # human-in-the-loop
  guard_out(reply)                       # Pillar B (output)
  alarms.flush(); journal.persist(run_id)# Pillar D + persistence
  return material_out(reply)             # Pillar A
```

---

## 8. Configuration

| Variable | Purpose |
|---|---|
| `OPENAI_API_KEY` | OpenAI Agents SDK worker + traces |
| `MAILTRAP_API_KEY` | Escalation/alarm email |
| `EMAIL_FROM` | Verified sender |
| `OTEL_EXPORTER_OTLP_ENDPOINT` | (optional) OTel sink |
| `ACTIVE_WORKER` | Selects worker impl (portability) |

Dev: `.env`. Deployed: HF Spaces Secrets.

---

## 9. Running

```bash
uv sync
cp .env.example .env   # fill in secrets
python app.py          # Gradio UI; thin client of harness.engine.run()
```

Deployed on Hugging Face Spaces (Gradio 6.18.0, Python 3.12, free CPU).

---

## 10. Demo Script (5 min)

1. On-topic question → grounded answer; `grounding` checkpoint passes (show trace span).
2. Hallucination bait → `grounding` **fails** → worker retries with feedback → escalates.
   *(Must: behavior changes on checkpoint feedback.)*
3. Prompt-injection → input guardrail trips; deflection; `GUARDRAIL_TRIP` alarm.
4. Unanswerable question / lead → human-in-the-loop escalation email.
5. Show structured alarms (type/severity/context/action) and the reliability signals
   (p95, $/run, err%, eval pass-rate).
6. **Swap `ACTIVE_WORKER` to worker #2** live → same harness, no changes. *(Bonus.)*
7. **Replay** a stored `run_id` from the `grounding` checkpoint without re-calling the model.
   *(Should.)*

---

## 11. Requirements Coverage

| Requirement | Level | Met by |
|---|---|---|
| Four pillars, separate from worker | Must | `harness/` modules vs. `workers/` |
| Behavior changes on guardrail/checkpoint feedback | Must | retry-with-feedback / escalate |
| Guardrails declared, not implicit | Must | `GUARDRAILS` registry |
| Checkpoints with explicit pass/fail | Must | `CheckpointResult.passed` + criteria |
| Alarms structured (type/severity/context/action) | Must | `Alarm` schema |
| Runs on real input from own work | Must | `linkedin.pdf` + `summary.txt` |
| HARNESS.md | Must | this file |
| Swappable worker interface | Should | `Worker` Protocol |
| Checkpoint persistence / replay | Should | `store.py` journal |
| Human-in-the-loop escalation | Should | `escalation.py` |
| Second worker swapped in at demo | Bonus | `workers/echo_worker.py` |
