# Alter Ego Harness — Planning Document

> **Challenge framing.** This is a **harness** project, not an agent project. The thing
> being evaluated is the *framework the agent lives inside* — the guardrails, checkpoints,
> material handling, and alarms it provides — **demonstrably separate from the worker**.
> The career-conversation chatbot is just *a* worker we drop into the harness to prove it
> works. A second worker can be dropped in to prove portability.

## Project Summary

**Alter Ego Harness** is a domain-agnostic AI harness: a runtime that wraps any
chat/tool-using agent ("worker") and gives it, **for free**, the four things every
production agent needs:

1. **Guardrails** — declared checks that constrain inputs, tool actions, and outputs.
2. **Checkpoints** — named evaluation gates with explicit pass/fail criteria, persisted
   so a run can be replayed from any checkpoint forward.
3. **Material handling** — clean, typed interfaces for passing material into and out of
   the worker (context in, structured results out), so the harness and worker never leak
   into each other.
4. **Alarms** — structured, named alarm events (type + severity + context + recommended
   action) emitted when something goes wrong, plus the observability/traces around them.

The **demo worker** is the *Alter Ego* career chatbot: it role-plays as a specific person
and answers questions about their career using their LinkedIn export and a curated summary
as its source of truth. When a visitor shares an email, or asks something the worker
can't answer, the harness routes it to a human (owner email) — a **human-in-the-loop
escalation path**, not just a side effect.

The harness governs the worker, and **the worker's behavior changes meaningfully based on
guardrail and checkpoint feedback** (e.g. a failed grounding checkpoint forces the worker
to retry or escalate rather than ship a hallucinated answer).

> **Built on:** Python + the **OpenAI Agents SDK** for the *worker*. The **harness** is our
> own code wrapped around it. The worker is reached only through a narrow `Worker`
> interface (see *Swappable Worker Interface*), so the SDK is an implementation detail of
> one worker, not a dependency of the harness.

## The Two Vocabularies (and why this doc uses the rubric's)

The challenge ships two different "four pillars" lists. They are **not** the same and this
matters for grading:

| Presentation deck ("anatomy of a harness") | Challenge rubric ("what you're scored on") |
|---|---|
| Chat / Loop | **Guardrails** — constrain behavior |
| Tools | **Checkpoints** — evaluate outputs (pass/fail) |
| Guardrails | **Material handling** — clean in/out interfaces |
| Observability | **Alarms** — structured, named, severity |

The deck describes *what a harness is*; the rubric defines *what is scored*. **This
document is organized around the rubric pillars.** The deck's concepts (the loop, tools,
observability) still appear — they live *inside* the rubric pillars (the loop and tools are
material handling + the run engine; observability is the substrate alarms ride on).

## Requirements Traceability

Every Must/Should/Bonus from the challenge brief, mapped to where it is satisfied.

| # | Req | Level | Where satisfied |
|---|---|---|---|
| 1 | Four pillars implemented, **separate from the worker** | Must | `harness/` package: `guardrails.py`, `checkpoints.py`, `material.py`, `alarms.py` — worker lives in `workers/` |
| 2 | Harness governs agent; **behavior changes** on guardrail/checkpoint feedback | Must | Checkpoint failure → forced retry / escalate (see *Checkpoints*, *Control Flow*) |
| 3 | Guardrails **declared, not implicit** | Must | Declarative `GUARDRAILS` registry (see *Guardrails*) |
| 4 | Checkpoints with **explicit pass/fail criteria** | Must | `Checkpoint` objects with named criteria + verdicts (see *Checkpoints*) |
| 5 | Alarms produce **structured output** (type, severity, context, action) | Must | `Alarm` schema + `AlarmType`/`Severity` enums (see *Alarms*) |
| 6 | Runs on a **real input from your own work** at demo time | Must | Engineer's real `linkedin.pdf` + `summary.txt` (see *Material Handling*) |
| 7 | **HARNESS.md** covering architecture & design | Must | `HARNESS.md` in repo root |
| 8 | **Swappable worker interface** (drop-in, no harness changes) | Should | `Worker` Protocol (see *Swappable Worker Interface*) |
| 9 | **Checkpoint results persisted**; replay from any checkpoint | Should | Run journal + checkpoint store (see *Checkpoint Persistence & Replay*) |
| 10 | **Human-in-the-loop escalation** paths | Should | Escalation policy → owner email / pause-and-ask (see *Human-in-the-Loop*) |
| 11 | **Second worker swapped in** during demo (portability) | Bonus | Second `Worker` impl (see *Swappable Worker Interface*) |

## Architecture

```
                         ┌──────────────────────────────────────────────┐
                         │                  HARNESS                      │
                         │  (domain-agnostic; knows nothing about        │
                         │   careers, LinkedIn, or the worker's model)   │
                         │                                              │
   ┌──────────┐  input   │  ┌───────────────┐   ┌──────────────────┐    │
   │ Visitor  │─────────▶│  │  Material IN  │──▶│  Guardrails IN   │    │
   └──────────┘          │  │  (normalize,  │   │  (declared input │    │
        ▲                │  │   size-limit) │   │   checks)        │    │
        │                │  └───────────────┘   └────────┬─────────┘    │
        │ response       │                               ▼             │
        │                │                      ┌──────────────────┐    │
        │                │   ┌──────────────────│   RUN ENGINE     │    │
        │                │   │ Action guardrails │  (bounded loop:  │    │
        │                │   │ wrap each tool    │   call worker,   │    │
        │                │   │ call (allow-list, │   run tools,     │    │
        │                │   │ approval gates)   │   append, repeat)│    │
        │                │   └──────────────────▶│   ┌──────────┐   │    │
        │                │                       │   │  WORKER  │   │    │  ← swappable
        │                │                       │   │ (Agents  │   │    │     (Protocol)
        │                │                       │   │  SDK /   │   │    │
        │                │                       │   │  other)  │   │    │
        │                │                       │   └──────────┘   │    │
        │                │                       └────────┬─────────┘    │
        │                │                                ▼             │
        │                │   ┌──────────────────┐  ┌──────────────────┐ │
        │                │   │  CHECKPOINTS     │  │  Guardrails OUT  │ │
        │                │   │ (pass/fail gates,│─▶│  (output checks, │ │
        │                │   │  persisted)      │  │   schema/fact)   │ │
        │                │   └────────┬─────────┘  └────────┬─────────┘ │
        │                │            │ fail → retry/escalate│          │
        │                │            ▼                      ▼          │
        │                │   ┌──────────────────────────────────────┐  │
        │                │   │  ALARMS + OBSERVABILITY (everywhere)  │  │
        │                │   │  structured alarms · OTel spans ·     │  │
        │                │   │  run journal · cost/latency/err/eval  │  │
        │                │   └──────────────────────────────────────┘  │
        │                │            │ escalate                       │
        └────────────────┼────────────┘  (human-in-the-loop)           │
                         │  Material OUT                                │
                         └──────────────────────────────────────────────┘

                 ┌──────────────┐        ┌──────────────┐
                 │   Mailtrap   │        │  OTel sink   │
                 │ (escalation/ │        │ (Phoenix /   │
                 │  alarm dest) │        │  OpenAI traces)│
                 └──────────────┘        └──────────────┘

   Deployed on: Hugging Face Spaces (Gradio SDK)  ·  UI is a thin client of the harness
```

### Code layout (pillars are distinct, identifiable components)

```
harness/
  __init__.py
  engine.py        # bounded run loop: build context → call worker → run tools → checkpoint → repeat
  material.py      # PILLAR: Material handling — typed in/out boundary objects
  guardrails.py    # PILLAR: Guardrails — declared input/action/output checks
  checkpoints.py   # PILLAR: Checkpoints — named pass/fail gates + verdicts
  alarms.py        # PILLAR: Alarms — Alarm schema, AlarmType, Severity, sinks
  observability.py # OTel spans + run journal (the substrate alarms ride on)
  escalation.py    # human-in-the-loop policy + routing
  worker.py        # Worker Protocol (the swappable interface)
  store.py         # checkpoint/run persistence for replay
workers/
  alter_ego.py     # demo worker #1 (OpenAI Agents SDK career chatbot)
  echo_worker.py   # demo worker #2 (portability proof — different model/impl)
app.py             # Gradio UI; thin client that calls harness.engine.run(...)
```

The four pillar modules import the worker **only** through `worker.py`'s Protocol; the
worker imports **nothing** from the pillar modules. That is the "demonstrably separate"
boundary the rubric requires.

## Pillar 1 — Material Handling

*Clean, typed interfaces for passing material in and out of the worker.* The harness owns
these boundary types; the worker never sees raw HTTP/Gradio objects and the UI never sees
raw worker output.

- **`InboundMaterial`** — normalized request entering the harness: `text`, `history`,
  `source_documents`, `metadata`. Built by `material.py`, never by the worker.
- **`SourceMaterial`** — the engineer's **real input** loaded at startup: `linkedin.pdf`
  (extracted with `pypdf`) + `summary.txt`. This is the "real input from your own work"
  the rubric requires. The harness loads, validates, and size-caps it; the worker receives
  it as opaque context.
- **`OutboundMaterial`** — structured result leaving the harness: `text`, `tool_events`,
  `checkpoints`, `alarms`, `escalated`. The UI renders this; it is also the replayable
  record.
- **Tool result contract** — every tool returns parseable data, and **errors come back as
  data** (`{"ok": false, "error": ...}`), never as exceptions that crash the loop.
- **Engineering concerns** (per deck): per-tool timeouts, idempotency keys on side-effecting
  tools, and **truncation of large results** before they blow the context window — all done
  in `material.py`, not in the worker.

## Pillar 2 — Guardrails

*Declared, not implicit.* All guardrails live in a single declarative registry so they can
be listed, audited, and toggled. Each has an id, a phase, and a decision.

```python
# harness/guardrails.py  (illustrative)
GUARDRAILS = [
    Guardrail(id="input.injection",   phase="input",  fn=detect_injection,  on_trip=DEFLECT),
    Guardrail(id="input.size",        phase="input",  fn=max_input_len,     on_trip=REJECT),
    Guardrail(id="action.allowlist",  phase="action", fn=tool_allowlisted,  on_trip=BLOCK),
    Guardrail(id="action.email_fmt",  phase="action", fn=valid_email,       on_trip=ASK_AGAIN),
    Guardrail(id="action.rate_limit", phase="action", fn=not_duplicate,     on_trip=BLOCK),
    Guardrail(id="action.approval",   phase="action", fn=needs_human,       on_trip=ESCALATE),
    Guardrail(id="output.no_leak",    phase="output", fn=no_prompt_leak,    on_trip=FALLBACK),
    Guardrail(id="output.schema",     phase="output", fn=matches_schema,    on_trip=FALLBACK),
]
```

Three layers, mirroring the deck:

- **Input** — strip/flag prompt-injection, validate and **size-limit** what enters the
  prompt.
- **Action** — **allow-list** tools, validate tool args, dedup/rate-limit side effects, and
  **require human approval for risky calls** (links into *Human-in-the-Loop*).
- **Output** — schema-check, filter, and fact-gate the final response (no system-prompt
  leak, no raw source-document dump) before it ships.

Plus the **hard limits** that bound the run: turn cap, token budget, wall-clock timeout, and
a spend ceiling. A tripped guardrail is logged, may raise an **alarm**, and **changes worker
behavior** (deflect / re-ask / escalate) rather than silently passing.

> **Guardrails vs. checkpoints.** Guardrails are *boundary constraints* ("don't let this in
> / don't let this out / don't let this happen"). Checkpoints are *evaluations of work
> product* ("is this answer grounded?"). They are deliberately separate pillars.

## Pillar 3 — Checkpoints

*Named evaluation gates with explicit pass/fail criteria, persisted for replay.* After the
worker produces a candidate answer, the harness runs it through an ordered list of
checkpoints. Each yields a structured **verdict**.

```python
# harness/checkpoints.py  (illustrative)
@dataclass
class CheckpointResult:
    id: str                 # "grounding"
    passed: bool            # explicit pass/fail
    criteria: str           # human-readable pass condition
    score: float | None     # optional numeric
    evidence: dict          # what was checked, why it passed/failed
    recommended_action: str # "retry" | "escalate" | "accept"

CHECKPOINTS = [
    Checkpoint(id="grounding",   criteria="Answer is supported by SourceMaterial; no invented facts."),
    Checkpoint(id="on_persona",  criteria="Response stays in persona and on the career domain."),
    Checkpoint(id="lead_capture",criteria="If visitor gave contact info, it was recorded/escalated."),
    Checkpoint(id="answerable",  criteria="If unanswerable from docs, the unknown-question path fired."),
]
```

- **Explicit pass/fail** — each checkpoint returns `passed: bool` with the criteria string,
  not a vibe.
- **Behavior change on feedback (Must #2)** — a failed `grounding` checkpoint causes the
  engine to **re-prompt the worker with the failure as feedback** (bounded retries), and on
  repeated failure to **escalate** instead of shipping. This is the concrete "agent behavior
  changes based on checkpoint feedback" demonstration.
- Checkpoints can use cheap deterministic checks or a small LLM-judge; either way the result
  is the same structured `CheckpointResult`.

### Checkpoint Persistence & Replay (Should #9)

- Every run gets a `run_id`; each stage (material-in → guardrails → worker turn →
  checkpoint → guardrails-out) writes a **journal entry** to `store.py`.
- Checkpoint results and the worker I/O at each stage are persisted, so a run can be
  **replayed from any checkpoint forward without re-running prior stages** (e.g. replay from
  `grounding` using the already-captured worker output).
- **Storage:** HF Spaces disk is ephemeral, so the journal is written to an **off-box** sink
  (e.g. a small SQLite on a mounted dataset, or an OTel/Phoenix backend, or as structured
  rows emailed/exported). The replay tool reads the journal by `run_id`.

## Pillar 4 — Alarms (+ Observability)

*Structured output: named alarm types with context, severity, and a recommended action.*

```python
# harness/alarms.py  (illustrative)
class AlarmType(str, Enum):
    GUARDRAIL_TRIP   = "guardrail_trip"
    CHECKPOINT_FAIL  = "checkpoint_fail"
    TURN_LIMIT       = "turn_limit_exceeded"
    TOOL_ERROR       = "tool_error"
    BUDGET_EXCEEDED  = "budget_exceeded"
    WORKER_ERROR     = "worker_error"
    ESCALATION       = "human_escalation_required"

class Severity(str, Enum):
    INFO = "info"; WARNING = "warning"; ERROR = "error"; CRITICAL = "critical"

@dataclass
class Alarm:
    type: AlarmType
    severity: Severity
    context: dict            # run_id, stage, offending input/tool, etc.
    recommended_action: str  # "deflect", "retry", "escalate to owner", ...
    ts: datetime
```

- **Named types + severity + context + recommended action** — satisfies the Must directly.
  Alarms are emitted to one or more **sinks**: structured stdout (HF logs), an OTel span
  event, and — for actionable ones — the owner's inbox via Mailtrap.
- **Observability substrate (deck Pillar 4).** Every model/tool call is wrapped in an **OTel
  span** with `llm.model`, `tokens_in`, `tokens_out`, `cost_usd`, latency. Emitting OTel
  means **no backend lock-in** — ingest in Arize Phoenix (self-host/OSS), OpenAI traces, or
  any OTel sink.
- **The four signals that move reliability** (deck): **p95 latency** per span, **$/run**
  (token cost per trace), **err%** (tool error/retry rate), and **eval pass-rate** (the
  checkpoint pass-rate scored vs. a small test set). These are the demo's reliability
  dashboard.

## Control Flow — the bounded run loop

The engine is the deck's "~15 line loop," hardened. Pillars hang off this skeleton.

```
run(inbound):
  material  = material_in(inbound)            # Material handling
  guard_in(material)                          # Guardrails — input  (may DEFLECT/REJECT)
  for turn in range(MAX_TURNS):               # hard limit
     span = observe.start("worker.turn")      # Observability
     reply = worker.act(context)              # Worker (swappable)
     for call in reply.tool_calls:
         guard_action(call)                   # Guardrails — action (allow-list/approval)
         result = run_tool(call)              # Material handling — typed result, errors-as-data
     verdicts = run_checkpoints(reply)        # Checkpoints — pass/fail
     if all(v.passed): break
     elif retriable(verdicts): context += feedback(verdicts)   # behavior change
     else: escalate(verdicts); break          # Human-in-the-loop
  guard_out(reply)                            # Guardrails — output
  alarms.flush(); journal.persist(run_id)     # Alarms + persistence
  return material_out(reply)                  # Material handling
```

Stop conditions matter as much as steps: **cap turns, tokens, and wall-clock** so a confused
worker can't spin forever (each triggers a `TURN_LIMIT` / `BUDGET_EXCEEDED` alarm).

## Swappable Worker Interface (Should #8, Bonus #11)

The harness depends on a **Protocol**, never on a concrete model SDK:

```python
# harness/worker.py
class Worker(Protocol):
    name: str
    def act(self, context: WorkerContext) -> WorkerReply: ...   # text + optional tool_calls
```

- **Worker #1 — `workers/alter_ego.py`** — wraps the OpenAI Agents SDK `Agent` + `Runner`
  (gpt-4o-mini), with the persona + `SourceMaterial` in its instructions. The SDK is an
  implementation detail *of this worker*.
- **Worker #2 — `workers/echo_worker.py`** (or a different model, e.g. a raw OpenAI/Anthropic
  chat client) — **dropped in at demo time with zero harness changes** to prove portability
  (the Bonus). Selected via env var / dropdown.
- Because guardrails, checkpoints, material handling, and alarms wrap the `Worker`
  interface, **constraint-handling is invisible to the worker** — exactly the brief's
  closing principle.

## Human-in-the-Loop Escalation (Should #10)

The harness knows when to **stop and ask rather than guess**:

- **Triggers:** an `action.approval` guardrail trip on a risky tool call; repeated
  `checkpoint_fail`; an explicitly unanswerable question; or a captured lead.
- **Action:** emit an `ESCALATION` alarm and route to the human (owner) via Mailtrap, with
  full context (run_id, the failing checkpoint/guardrail, the offending input). The run
  pauses/deflects rather than shipping a guessed answer.
- This reframes the original "email the owner" behavior as a **first-class escalation path**,
  not an incidental side effect of a tool.

## Data Sources (the real demo input)

The worker's source of truth — and the rubric's "real input from your own work" — is two
files in the project root:

- `linkedin.pdf` — the engineer's LinkedIn export; text extracted at startup with `pypdf`.
- `summary.txt` — a curated background summary, read as plain text.

The **harness** loads, validates (fail-fast), and size-caps these into `SourceMaterial`;
the worker consumes them as opaque context embedded in its system prompt (no vector
store/RAG given the small size).

## Configuration & Secrets

Loaded from `.env` in dev; defined as **Spaces Secrets** when deployed.

| Variable | Purpose |
|---|---|
| `OPENAI_API_KEY` | Auth for the OpenAI Agents SDK worker + OpenAI traces. |
| `MAILTRAP_API_KEY` | Auth for outbound escalation/alarm email. |
| `EMAIL_FROM` | Verified sender for escalation/alarm notifications. |
| `OTEL_EXPORTER_OTLP_ENDPOINT` | (optional) OTel sink for spans/journal (e.g. Phoenix). |
| `ACTIVE_WORKER` | Selects the worker impl (portability demo). |

## Error & Edge-Case Handling

Two policies: **fail fast at startup**, **degrade gracefully at runtime** (never leak a
stack trace into the chat UI; every failure becomes an **alarm** + a friendly message).

- **Startup (fail fast):** required env vars present; `linkedin.pdf` + `summary.txt` readable.
- **Runtime (degrade):** OpenAI API errors (rate/timeout/connection/auth) → rely on the
  client's built-in retry, then a polite message + `WORKER_ERROR` alarm. SDK run errors
  (`MaxTurnsExceeded` → `TURN_LIMIT` alarm + rephrase ask; `ModelBehaviorError` /
  `ModelRefusalError` → fallback). Guardrail tripwires → deflection + `GUARDRAIL_TRIP` alarm.
- **Tool/email failures:** caught, returned to the model **as data**, and raised as a
  `TOOL_ERROR` alarm — the run continues.

## Testing & Success Criteria

A tiny **eval set** doubles as the checkpoint pass-rate signal (deck's `eval`). Automated
pytest is otherwise deferred; verification is the manual smoke checklist below.

### Manual smoke checklist (deployed Space)

- On-topic career question → grounded answer; `grounding` checkpoint **passes**.
- Unanswerable-from-docs question → `answerable` checkpoint routes to **escalation**; owner
  emailed; `ESCALATION` alarm emitted.
- Visitor shares email → lead recorded + escalated; owner emailed.
- Prompt-injection / off-topic → input guardrail trips; polite deflection; no prompt leak;
  `GUARDRAIL_TRIP` alarm.
- Hallucination attempt → `grounding` checkpoint **fails** → worker **retries with feedback**
  → on repeat failure, escalates (proves Must #2).
- **Portability:** swap `ACTIVE_WORKER` to worker #2 → same harness, same guardrails/
  checkpoints/alarms, no code changes (Bonus #11).
- **Replay:** re-run a stored `run_id` from the `grounding` checkpoint without re-calling the
  worker (Should #9).
- Fail-fast startup: removing a required secret/document yields a clear startup error.

### Definition of done

- All four **rubric pillars** exist as distinct modules, separate from the worker.
- Worker behavior demonstrably changes on guardrail/checkpoint feedback.
- Guardrails are declared; checkpoints have explicit pass/fail; alarms are structured.
- Harness runs on the engineer's real `linkedin.pdf` / `summary.txt` at demo time.
- `HARNESS.md` ships in the repo.
- Spans/metrics (p95, $/run, err%, eval pass-rate) visible in the OTel/traces backend.
- (Should/Bonus) replay works; second worker swaps in live.

## Deployment

Deployed to **Hugging Face Spaces** (Gradio SDK). The Gradio UI is a **thin client** that
calls `harness.engine.run(...)`; all four pillars run inside the harness regardless of UI.

- **Entrypoint:** `app.py` (`app_file` in `README.md` front-matter).
- **Gradio:** `6.18.0`; **Python:** `3.12` (pinned in README front-matter / `pyproject.toml`).
- **Hardware:** free CPU basic (all inference is via API).
- **Secrets:** as listed in *Configuration & Secrets*, set as Spaces Secrets.
- **Deliverable URLs:** repo URL + deployed Space URL (see *Deliverables*).

### Dependency management

`pyproject.toml` + `uv.lock` are canonical (`uv`); `requirements.txt` is generated
(`uv export --no-hashes --no-dev --no-emit-project -o requirements.txt`) because Spaces
installs from it. Regenerate on every dependency change.

## Deliverables & Schedule

| Deliverable | Due | Status |
|---|---|---|
| 1-page Harness Planning Document | Fri 11:30 PM | this file (condense to 1 page for submission) |
| Project repo URL | Sat 4:30 PM | _TBD_ |
| Deployed Harness URL (HF Space) | Sat 4:30 PM | _TBD_ |
| `HARNESS.md` (capabilities + architecture) | Sat 4:30 PM | drafted (`HARNESS.md`) |
| 5-minute demo video | Sat 4:30 PM | _TBD_ — script around the smoke checklist |

## Open Questions / Next Steps

**Decided:**

- Reframed from "agent" to "harness" per the rubric; domain (career chatbot) kept as the
  swappable demo worker.
- Worker #1 = OpenAI Agents SDK (gpt-4o-mini); worker #2 = a second impl for the portability
  bonus.
- Pillars implemented as four distinct modules under `harness/`, separate from `workers/`.

**Still to confirm:**

- Off-box persistence target for the run journal/replay (SQLite-on-dataset vs. Phoenix vs.
  exported rows) given Spaces' ephemeral disk.
- OTel backend for the demo (Arize Phoenix self-host vs. OpenAI traces) and which of the four
  reliability signals to surface live.
- Concrete second worker for the portability demo (different model vs. trivial echo worker).
