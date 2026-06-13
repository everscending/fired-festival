"""Human-in-the-Loop Escalation (Should #10).

The harness stops and asks rather than guesses when: an ``action.approval``
guardrail trips on a risky tool; a checkpoint fails repeatedly; a question is
unanswerable from source; or a lead is captured. It emits an ``ESCALATION``
alarm and routes full context (run_id, failing gate, offending input) to the
human via Mailtrap, deflecting instead of shipping a guess.

Mailtrap is best-effort: if it is not configured or fails, the escalation
still happens (alarm + deflection) and the email failure is itself surfaced.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from harness import config
from harness.alarms import AlarmBus, AlarmType, Severity


@dataclass
class EscalationResult:
    escalated: bool
    reason: str
    emailed: bool
    detail: str = ""


def _send_mailtrap(subject: str, body: str) -> tuple[bool, str]:
    if not (config.MAILTRAP_API_KEY and config.EMAIL_FROM and config.EMAIL_TO):
        return False, "mailtrap not configured (missing key/from/to)"
    try:
        import mailtrap as mt

        mail = mt.Mail(
            sender=mt.Address(email=config.EMAIL_FROM, name="Alter Ego Harness"),
            to=[mt.Address(email=config.EMAIL_TO)],
            subject=subject,
            text=body,
            category="harness-escalation",
        )
        client = mt.MailtrapClient(token=config.MAILTRAP_API_KEY)
        client.send(mail)
        return True, "sent"
    except Exception as exc:  # noqa: BLE001 — email is best-effort
        return False, f"{type(exc).__name__}: {exc}"


def escalate(
    *,
    run_id: str,
    reason: str,
    failing_gate: str,
    offending_input: str,
    bus: AlarmBus,
    extra: dict[str, Any] | None = None,
) -> EscalationResult:
    """Raise an ESCALATION alarm and route full context to the human."""
    context = {
        "run_id": run_id,
        "failing_gate": failing_gate,
        "offending_input": offending_input[:1000],
    }
    if extra:
        context.update(extra)

    bus.raise_alarm(
        type=AlarmType.ESCALATION,
        severity=Severity.WARNING,
        context=context,
        recommended_action="escalate to owner",
    )

    subject = f"[Alter Ego Harness] Escalation: {failing_gate} (run {run_id})"
    body = (
        f"A run was escalated to a human.\n\n"
        f"Reason: {reason}\n"
        f"Failing gate: {failing_gate}\n"
        f"Run ID: {run_id}\n"
        f"Offending input: {offending_input[:1000]}\n"
    )
    if extra:
        body += "\nExtra context:\n" + "\n".join(f"  {k}: {v}" for k, v in extra.items())

    emailed, detail = _send_mailtrap(subject, body)
    return EscalationResult(escalated=True, reason=reason, emailed=emailed, detail=detail)
