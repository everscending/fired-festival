"""Run the eval set against a chosen worker and report the checkpoint
pass-rate (the harness's ``eval`` reliability signal).

Usage:
    python -m evals.run_evals [worker]   # worker: echo (default) | linkedin | rogue

``echo`` and ``rogue`` need no API key, so this is the offline-safe default.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from harness import engine
from workers import get_worker


def _alarm_types(out) -> set[str]:
    return {a["type"] for a in out.alarms}


def _grounding_passed(out) -> bool:
    for c in out.checkpoints:
        if c["id"] == "grounding":
            return c["passed"]
    return True  # deflected before checkpoints → vacuously fine


def _input_deflected(out) -> bool:
    return not out.checkpoints and any(
        a["type"] == "guardrail_trip" for a in out.alarms
    )


def _tool_called(out, name: str) -> bool:
    return any(t["name"] == name and t["ok"] for t in out.tool_events)


def run(worker_name: str = "echo") -> int:
    cases = json.loads(Path("evals/eval_set.json").read_text())["cases"]
    worker = get_worker(worker_name)

    passed = 0
    print(f"\nRunning {len(cases)} eval cases against worker='{worker.name}'\n")
    for case in cases:
        out = engine.run(case["question"], worker)
        exp = case["expect"]
        checks: list[bool] = []

        if "grounding_passed" in exp:
            checks.append(_grounding_passed(out) == exp["grounding_passed"])
        if "escalated" in exp:
            checks.append(out.escalated == exp["escalated"])
        if "input_deflected" in exp:
            checks.append(_input_deflected(out) == exp["input_deflected"])
        if "tool_called" in exp:
            checks.append(_tool_called(out, exp["tool_called"]))
        if "alarm_types" in exp:
            checks.append(set(exp["alarm_types"]).issubset(_alarm_types(out)))

        ok = all(checks) if checks else True
        passed += int(ok)
        print(f"  [{'PASS' if ok else 'FAIL'}] {case['id']:24s} "
              f"grounding={_grounding_passed(out)} escalated={out.escalated} "
              f"alarms={sorted(_alarm_types(out))}")

    rate = passed / len(cases) if cases else 1.0
    print(f"\nEval pass-rate: {passed}/{len(cases)} = {rate*100:.0f}%\n")
    return 0 if passed == len(cases) else 1


if __name__ == "__main__":
    worker = sys.argv[1] if len(sys.argv) > 1 else "echo"
    raise SystemExit(run(worker))
