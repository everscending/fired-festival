"""Checkpoint / run persistence for replay (Should #9).

Every run gets a ``run_id``; each stage (material-in → guardrails → worker
turn → checkpoint → guardrails-out) writes a journal entry. Checkpoint
results and worker I/O are persisted so a run can be **replayed from any
checkpoint forward** without re-running prior stages or re-calling the model.

Storage is a local SQLite file. HF Spaces disk is ephemeral, so this is for
in-session demo + replay; set ``HARNESS_STORE_PATH`` to point at a mounted
dataset for durability.
"""

from __future__ import annotations

import json
import sqlite3
import threading
from typing import Any

from harness import config

_LOCK = threading.Lock()


def _conn() -> sqlite3.Connection:
    conn = sqlite3.connect(config.STORE_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_store() -> None:
    with _LOCK, _conn() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS journal (
                id        INTEGER PRIMARY KEY AUTOINCREMENT,
                run_id    TEXT NOT NULL,
                seq       INTEGER NOT NULL,
                stage     TEXT NOT NULL,
                ts        TEXT NOT NULL,
                payload   TEXT NOT NULL
            )
            """
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_journal_run ON journal(run_id, seq)"
        )


def append(run_id: str, stage: str, payload: dict[str, Any], ts: str) -> None:
    """Append a journal entry for a run stage."""
    with _LOCK, _conn() as conn:
        row = conn.execute(
            "SELECT COALESCE(MAX(seq), -1) + 1 AS next FROM journal WHERE run_id = ?",
            (run_id,),
        ).fetchone()
        seq = row["next"]
        conn.execute(
            "INSERT INTO journal (run_id, seq, stage, ts, payload) VALUES (?, ?, ?, ?, ?)",
            (run_id, seq, stage, ts, json.dumps(payload, default=str)),
        )


def get_journal(run_id: str) -> list[dict[str, Any]]:
    with _LOCK, _conn() as conn:
        rows = conn.execute(
            "SELECT seq, stage, ts, payload FROM journal WHERE run_id = ? ORDER BY seq",
            (run_id,),
        ).fetchall()
    return [
        {"seq": r["seq"], "stage": r["stage"], "ts": r["ts"], "payload": json.loads(r["payload"])}
        for r in rows
    ]


def list_runs(limit: int = 25) -> list[dict[str, Any]]:
    with _LOCK, _conn() as conn:
        rows = conn.execute(
            """
            SELECT run_id, MIN(ts) AS started, MAX(seq) AS stages
            FROM journal GROUP BY run_id ORDER BY MIN(id) DESC LIMIT ?
            """,
            (limit,),
        ).fetchall()
    return [
        {"run_id": r["run_id"], "started": r["started"], "stages": r["stages"] + 1}
        for r in rows
    ]


def find_stage(run_id: str, stage: str) -> dict[str, Any] | None:
    """Return the latest journal entry whose stage starts with ``stage``."""
    for entry in reversed(get_journal(run_id)):
        if entry["stage"].startswith(stage):
            return entry
    return None


def replay_from(run_id: str, checkpoint_stage: str = "checkpoints") -> dict[str, Any]:
    """Replay a run from a checkpoint forward without re-calling the model.

    Reads the persisted worker output + checkpoint results from the journal
    and returns a reconstructed view, demonstrating that downstream stages can
    be re-derived without re-running the worker.
    """
    journal = get_journal(run_id)
    if not journal:
        return {"ok": False, "error": f"no journal for run_id={run_id}"}

    worker_entry = find_stage(run_id, "worker.turn")
    cp_entry = find_stage(run_id, checkpoint_stage)
    out_entry = find_stage(run_id, "material.out")

    if cp_entry is None:
        return {"ok": False, "error": f"no '{checkpoint_stage}' stage in run {run_id}"}

    return {
        "ok": True,
        "run_id": run_id,
        "replayed_from": checkpoint_stage,
        "worker_output": (worker_entry or {}).get("payload", {}),
        "checkpoints": cp_entry["payload"],
        "final_output": (out_entry or {}).get("payload", {}),
        "note": "Reconstructed from journal; the worker/model was NOT re-invoked.",
    }
