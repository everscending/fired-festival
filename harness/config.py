"""Central configuration for the harness.

Reads from environment (``.env`` in dev, Spaces Secrets when deployed) and
exposes the hard limits that bound a run. Nothing here knows about careers or
LinkedIn — these are harness-level knobs only.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

from dotenv import load_dotenv

load_dotenv()


# --- Secrets / endpoints --------------------------------------------------
OPENAI_API_KEY: str | None = os.getenv("OPENAI_API_KEY")
MAILTRAP_API_KEY: str | None = os.getenv("MAILTRAP_API_KEY")
EMAIL_FROM: str | None = os.getenv("EMAIL_FROM")
EMAIL_TO: str = os.getenv("EMAIL_TO", EMAIL_FROM or "")
OTEL_EXPORTER_OTLP_ENDPOINT: str | None = os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT")

# Selects the worker implementation (portability demo). See workers/registry.
ACTIVE_WORKER: str = os.getenv("ACTIVE_WORKER", "linkedin")

# Model used by the demo worker.
MODEL: str = os.getenv("MODEL", "gpt-4o-mini")

# Where the run journal / checkpoint store lives (SQLite).
STORE_PATH: str = os.getenv("HARNESS_STORE_PATH", "harness_runs.sqlite3")


@dataclass(frozen=True)
class HardLimits:
    """Bounds that keep a confused worker from spinning forever."""

    max_turns: int = 4
    max_input_chars: int = 4_000
    token_budget: int = 40_000
    wall_clock_seconds: float = 60.0
    spend_ceiling_usd: float = 0.50
    max_tool_result_chars: int = 6_000


HARD_LIMITS = HardLimits()


# Rough per-1K-token pricing for cost estimation (USD). gpt-4o-mini defaults.
PRICING_PER_1K = {
    "gpt-4o-mini": {"in": 0.00015, "out": 0.0006},
    "gpt-4o": {"in": 0.0025, "out": 0.01},
}


def price_for(model: str) -> dict[str, float]:
    return PRICING_PER_1K.get(model, PRICING_PER_1K["gpt-4o-mini"])


def require(*names: str) -> list[str]:
    """Return the list of missing required env var names (fail-fast helper)."""
    return [n for n in names if not os.getenv(n)]
