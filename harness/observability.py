"""Observability substrate — OTel spans + run-level metrics.

Every model/tool call is wrapped in an OpenTelemetry span carrying
``llm.model``, ``tokens_in``, ``tokens_out``, ``cost_usd`` and latency.
Emitting OTel means no backend lock-in: a console exporter is used by
default, and an OTLP/HTTP exporter is wired up automatically when
``OTEL_EXPORTER_OTLP_ENDPOINT`` is set.

The four reliability signals (p95 latency, $/run, err%, eval pass-rate) are
aggregated per run by :class:`RunMetrics`.
"""

from __future__ import annotations

import contextlib
from dataclasses import dataclass, field
from typing import Any, Iterator

from opentelemetry import trace
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import (
    BatchSpanProcessor,
    ConsoleSpanExporter,
    SimpleSpanProcessor,
)

from harness import config

_INITIALIZED = False


def init_tracing() -> None:
    """Idempotently configure the global tracer provider."""
    global _INITIALIZED
    if _INITIALIZED:
        return

    import os

    resource = Resource.create({"service.name": "alter-ego-harness"})
    provider = TracerProvider(resource=resource)

    if config.OTEL_EXPORTER_OTLP_ENDPOINT:
        try:
            from opentelemetry.exporter.otlp.proto.http.trace_exporter import (
                OTLPSpanExporter,
            )

            provider.add_span_processor(
                BatchSpanProcessor(
                    OTLPSpanExporter(endpoint=config.OTEL_EXPORTER_OTLP_ENDPOINT)
                )
            )
        except Exception:  # noqa: BLE001 — fall back to console
            provider.add_span_processor(SimpleSpanProcessor(ConsoleSpanExporter()))
    elif os.getenv("HARNESS_QUIET_OTEL") != "1":
        # Console exporter keeps the demo self-contained / no backend needed.
        provider.add_span_processor(SimpleSpanProcessor(ConsoleSpanExporter()))

    trace.set_tracer_provider(provider)
    _INITIALIZED = True


def tracer():
    init_tracing()
    return trace.get_tracer("harness")


@contextlib.contextmanager
def span(name: str, **attrs: Any) -> Iterator[Any]:
    """Start a span with attributes; never raises into the run."""
    sp = tracer().start_as_current_span(name)
    cm = sp.__enter__()
    try:
        for k, v in attrs.items():
            if v is not None:
                cm.set_attribute(k, v)
        yield cm
    finally:
        sp.__exit__(None, None, None)


def annotate_llm_span(
    span_obj: Any,
    *,
    model: str,
    tokens_in: int,
    tokens_out: int,
    cost_usd: float,
    latency_ms: float,
) -> None:
    """Stamp the canonical LLM attributes onto a span."""
    try:
        span_obj.set_attribute("llm.model", model)
        span_obj.set_attribute("tokens_in", tokens_in)
        span_obj.set_attribute("tokens_out", tokens_out)
        span_obj.set_attribute("cost_usd", cost_usd)
        span_obj.set_attribute("latency_ms", latency_ms)
    except Exception:  # noqa: BLE001
        pass


def estimate_cost(model: str, tokens_in: int, tokens_out: int) -> float:
    p = config.price_for(model)
    return (tokens_in / 1000.0) * p["in"] + (tokens_out / 1000.0) * p["out"]


@dataclass
class RunMetrics:
    """Per-run aggregation of the four reliability signals."""

    latencies_ms: list[float] = field(default_factory=list)
    cost_usd: float = 0.0
    tokens_in: int = 0
    tokens_out: int = 0
    tool_calls: int = 0
    tool_errors: int = 0
    checkpoints_total: int = 0
    checkpoints_passed: int = 0
    turns: int = 0

    def record_llm(self, tokens_in: int, tokens_out: int, cost_usd: float, latency_ms: float) -> None:
        self.tokens_in += tokens_in
        self.tokens_out += tokens_out
        self.cost_usd += cost_usd
        self.latencies_ms.append(latency_ms)

    def record_tool(self, ok: bool, latency_ms: float) -> None:
        self.tool_calls += 1
        if not ok:
            self.tool_errors += 1
        self.latencies_ms.append(latency_ms)

    def record_checkpoints(self, total: int, passed: int) -> None:
        self.checkpoints_total += total
        self.checkpoints_passed += passed

    @property
    def p95_latency_ms(self) -> float:
        if not self.latencies_ms:
            return 0.0
        ordered = sorted(self.latencies_ms)
        idx = min(len(ordered) - 1, int(round(0.95 * (len(ordered) - 1))))
        return ordered[idx]

    @property
    def err_rate(self) -> float:
        return (self.tool_errors / self.tool_calls) if self.tool_calls else 0.0

    @property
    def eval_pass_rate(self) -> float:
        return (
            self.checkpoints_passed / self.checkpoints_total
            if self.checkpoints_total
            else 1.0
        )

    def snapshot(self) -> dict[str, Any]:
        return {
            "p95_latency_ms": round(self.p95_latency_ms, 1),
            "cost_usd": round(self.cost_usd, 6),
            "tokens_in": self.tokens_in,
            "tokens_out": self.tokens_out,
            "err_rate": round(self.err_rate, 4),
            "eval_pass_rate": round(self.eval_pass_rate, 4),
            "tool_calls": self.tool_calls,
            "tool_errors": self.tool_errors,
            "turns": self.turns,
        }
