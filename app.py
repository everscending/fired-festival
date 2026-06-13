"""Gradio UI — a thin client of ``harness.engine.run()``.

The UI renders ``OutboundMaterial`` and exposes the harness's observable
surface: the declared guardrails/checkpoints, per-run alarms, reliability
metrics, the worker switch (portability), and replay-by-run_id. All four
pillars run inside the harness regardless of this UI.
"""

from __future__ import annotations

import json

import gradio as gr

from harness import config, store
from harness.guardrails import list_guardrails
from harness.checkpoints import list_checkpoints
from harness import engine
from workers import AVAILABLE_WORKERS, get_worker

# Friendly labels for the swappable "alter ego" workers (label, registry key).
_WORKER_LABELS = {
    "linkedin": "LinkedIn Agent",
    "echo": "Echo (model-free portability worker)",
    "rogue": "Rogue (hallucination-bait demo)",
}
WORKER_CHOICES = [(_WORKER_LABELS.get(k, k), k) for k in AVAILABLE_WORKERS]


# --- Fail-fast startup ----------------------------------------------------
def _startup() -> None:
    missing = config.require("OPENAI_API_KEY")
    if missing:
        print(f"[startup] WARNING: missing env vars: {missing} "
              "(linkedin worker will be unavailable; echo/rogue still work)")
    store.init_store()
    # Pre-warm the configured worker so any document-loading failure (a worker
    # concern, not a harness concern) surfaces at startup rather than mid-chat.
    try:
        get_worker(config.ACTIVE_WORKER)
        print(f"[startup] active worker '{config.ACTIVE_WORKER}' ready")
    except Exception as exc:  # noqa: BLE001
        print(f"[startup] WARNING: worker '{config.ACTIVE_WORKER}' failed to init: {exc}")


_startup()
_LAST_RUN_ID = {"id": None}


# --- Core handler ---------------------------------------------------------
def respond(message: str, history: list[dict], worker_name: str):
    hist = history or []
    try:
        worker = get_worker(worker_name)
    except Exception as exc:  # noqa: BLE001
        reply = f"Worker '{worker_name}' unavailable: {exc}"
        new_hist = hist + [
            {"role": "user", "content": message},
            {"role": "assistant", "content": reply},
        ]
        return new_hist, "", "{}", "{}", ""

    out = engine.run(message, worker, history=hist)
    _LAST_RUN_ID["id"] = out.run_id

    new_hist = hist + [
        {"role": "user", "content": message},
        {"role": "assistant", "content": out.text},
    ]

    checkpoints_md = _render_checkpoints(out.checkpoints, out.escalated)
    alarms_md = _render_alarms(out.alarms)
    metrics_md = _render_metrics(out.metrics, out.run_id)

    return new_hist, "", checkpoints_md, alarms_md, metrics_md


def _render_checkpoints(checkpoints: list[dict], escalated: bool) -> str:
    if not checkpoints:
        rows = "_(no checkpoints — request was deflected at input)_"
    else:
        lines = ["| Checkpoint | Pass | Action | Evidence |", "|---|---|---|---|"]
        for c in checkpoints:
            mark = "✅" if c["passed"] else "❌"
            ev = json.dumps(c.get("evidence", {}))[:80]
            lines.append(f"| `{c['id']}` | {mark} | {c['recommended_action']} | {ev} |")
        rows = "\n".join(lines)
    banner = "\n\n> 🚨 **Escalated to human** — a safe fallback was returned." if escalated else ""
    return f"### Checkpoints (Pillar C){banner}\n\n{rows}"


def _render_alarms(alarms: list[dict]) -> str:
    if not alarms:
        return "### Alarms (Pillar D)\n\n_(none)_"
    lines = ["| Type | Severity | Action | Context |", "|---|---|---|---|"]
    for a in alarms:
        ctx = json.dumps(a.get("context", {}))[:90]
        lines.append(f"| `{a['type']}` | {a['severity']} | {a['recommended_action']} | {ctx} |")
    return "### Alarms (Pillar D)\n\n" + "\n".join(lines)


def _render_metrics(metrics: dict, run_id: str) -> str:
    if not metrics:
        return ""
    return (
        f"### Reliability signals  ·  run `{run_id}`\n\n"
        f"- **p95 latency:** {metrics.get('p95_latency_ms', 0)} ms\n"
        f"- **$/run:** ${metrics.get('cost_usd', 0):.6f}  "
        f"({metrics.get('tokens_in', 0)} in / {metrics.get('tokens_out', 0)} out tokens)\n"
        f"- **err%:** {metrics.get('err_rate', 0) * 100:.1f}%  "
        f"({metrics.get('tool_errors', 0)}/{metrics.get('tool_calls', 0)} tool calls)\n"
        f"- **eval pass-rate:** {metrics.get('eval_pass_rate', 0) * 100:.1f}%\n"
        f"- **turns:** {metrics.get('turns', 0)}\n"
    )


def do_replay(run_id: str):
    rid = (run_id or "").strip() or _LAST_RUN_ID["id"]
    if not rid:
        return "_No run_id yet. Send a message first or paste a run_id._"
    rep = store.replay_from(rid, "checkpoints")
    return "```json\n" + json.dumps(rep, indent=2)[:4000] + "\n```"


def list_recent_runs():
    runs = store.list_runs(limit=15)
    if not runs:
        return "_(no runs yet)_"
    lines = ["| run_id | started | stages |", "|---|---|---|"]
    for r in runs:
        lines.append(f"| `{r['run_id']}` | {r['started'][:19]} | {r['stages']} |")
    return "\n".join(lines)


# --- Static pillar listings (declared, not implicit) ----------------------
def _declared_guardrails_md() -> str:
    lines = ["| id | phase | on trip | criteria |", "|---|---|---|---|"]
    for g in list_guardrails():
        lines.append(f"| `{g['id']}` | {g['phase']} | {g['on_trip']} | {g['criteria']} |")
    return "### Declared Guardrails (Pillar B)\n\n" + "\n".join(lines)


def _declared_checkpoints_md() -> str:
    lines = ["| id | retriable | criteria |", "|---|---|---|"]
    for c in list_checkpoints():
        lines.append(f"| `{c['id']}` | {c['retriable']} | {c['criteria']} |")
    return "### Declared Checkpoints (Pillar C)\n\n" + "\n".join(lines)


# --- Layout ---------------------------------------------------------------
with gr.Blocks(title="Alter Ego Harness") as demo:
    gr.Markdown(
        "# Alter Ego Harness\n"
        "A domain-agnostic AI **harness**: guardrails · checkpoints · material "
        "handling · alarms — demonstrably separate from the swappable worker."
    )

    with gr.Row():
        with gr.Column(scale=3):
            chatbot = gr.Chatbot(height=420, label="Conversation")
            with gr.Row():
                msg = gr.Textbox(
                    placeholder="Ask about this person's career…",
                    scale=5, show_label=False, container=False,
                )
                send = gr.Button("Send", variant="primary", scale=1)
            worker_dd = gr.Dropdown(
                choices=WORKER_CHOICES, value=config.ACTIVE_WORKER,
                label="Active alter ego (portability — swap with zero harness changes)",
            )
        with gr.Column(scale=2):
            checkpoints_out = gr.Markdown("### Checkpoints (Pillar C)\n\n_(run a turn)_")
            alarms_out = gr.Markdown("### Alarms (Pillar D)\n\n_(none yet)_")
            metrics_out = gr.Markdown("### Reliability signals\n\n_(run a turn)_")

    with gr.Accordion("Declared constraints (audit the harness)", open=False):
        gr.Markdown(_declared_guardrails_md())
        gr.Markdown(_declared_checkpoints_md())

    with gr.Accordion("Checkpoint replay (Should #9)", open=False):
        gr.Markdown("Replay a stored run from its `grounding`/`checkpoints` stage "
                    "**without re-calling the worker/model**.")
        with gr.Row():
            replay_id = gr.Textbox(label="run_id (blank = last run)", scale=3)
            replay_btn = gr.Button("Replay", scale=1)
            refresh_btn = gr.Button("List recent runs", scale=1)
        recent_runs = gr.Markdown()
        replay_out = gr.Markdown()

    inputs = [msg, chatbot, worker_dd]
    outputs = [chatbot, msg, checkpoints_out, alarms_out, metrics_out]
    send.click(respond, inputs, outputs)
    msg.submit(respond, inputs, outputs)

    replay_btn.click(do_replay, [replay_id], [replay_out])
    refresh_btn.click(list_recent_runs, [], [recent_runs])


if __name__ == "__main__":
    demo.launch()
