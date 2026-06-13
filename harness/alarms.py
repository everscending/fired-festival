"""Pillar D — Alarms.

Structured, named alarm events: ``Alarm { type, severity, context,
recommended_action, ts }``. Alarms are emitted to one or more **sinks**:
structured stdout (HF logs), an OTel span event, and — for actionable ones —
the owner's inbox via Mailtrap (through escalation).

Alarms are *data*: a tool/model error becomes a structured value the worker
can react to, while the harness simultaneously raises a structured alarm.
"""

from __future__ import annotations

import json
import sys
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Callable


class AlarmType(str, Enum):
    GUARDRAIL_TRIP = "guardrail_trip"
    CHECKPOINT_FAIL = "checkpoint_fail"
    TURN_LIMIT = "turn_limit_exceeded"
    TOOL_ERROR = "tool_error"
    BUDGET_EXCEEDED = "budget_exceeded"
    WORKER_ERROR = "worker_error"
    ESCALATION = "human_escalation_required"


class Severity(str, Enum):
    INFO = "info"
    WARNING = "warning"
    ERROR = "error"
    CRITICAL = "critical"


@dataclass
class Alarm:
    type: AlarmType
    severity: Severity
    context: dict[str, Any]
    recommended_action: str
    ts: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["type"] = self.type.value
        d["severity"] = self.severity.value
        return d


# --------------------------------------------------------------------------
# Sinks
# --------------------------------------------------------------------------
def stdout_sink(alarm: Alarm) -> None:
    """Structured stdout — picked up by HF Spaces logs."""
    payload = {"ALARM": alarm.to_dict()}
    print(json.dumps(payload, default=str), file=sys.stdout, flush=True)


def otel_sink(alarm: Alarm) -> None:
    """Record the alarm as an event on the current OTel span, if any."""
    try:
        from opentelemetry import trace

        span = trace.get_current_span()
        if span is not None:
            span.add_event(
                f"alarm.{alarm.type.value}",
                attributes={
                    "alarm.type": alarm.type.value,
                    "alarm.severity": alarm.severity.value,
                    "alarm.recommended_action": alarm.recommended_action,
                    "alarm.context": json.dumps(alarm.context, default=str)[:1000],
                },
            )
    except Exception:  # noqa: BLE001 — observability must never crash a run
        pass


# Default sink chain. Mailtrap routing is handled by escalation.py for
# actionable alarms (ESCALATION etc.), not on every alarm.
_DEFAULT_SINKS: list[Callable[[Alarm], None]] = [stdout_sink, otel_sink]


class AlarmBus:
    """Collects alarms for a single run and fans them out to sinks.

    Alarms are buffered on the run record (for OutboundMaterial) and emitted
    to live sinks as they are raised.
    """

    def __init__(self, sinks: list[Callable[[Alarm], None]] | None = None) -> None:
        self.sinks = sinks if sinks is not None else list(_DEFAULT_SINKS)
        self.alarms: list[Alarm] = []

    def raise_alarm(
        self,
        type: AlarmType,
        severity: Severity,
        context: dict[str, Any],
        recommended_action: str,
    ) -> Alarm:
        alarm = Alarm(
            type=type,
            severity=severity,
            context=context,
            recommended_action=recommended_action,
        )
        self.alarms.append(alarm)
        for sink in self.sinks:
            try:
                sink(alarm)
            except Exception:  # noqa: BLE001
                pass
        return alarm

    def flush(self) -> list[dict[str, Any]]:
        return [a.to_dict() for a in self.alarms]
