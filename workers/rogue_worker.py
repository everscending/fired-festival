"""Demo worker — deliberately ungrounded ("hallucination bait").

Exists purely to make Must #2 ("behavior changes on checkpoint feedback")
reliably demonstrable on demand: it fabricates claims not supported by the
source, so the ``grounding`` checkpoint fails, the engine retries with the
failure as feedback, and on repeated failure escalates instead of shipping the
hallucination.

Not for production use — selected via ``ACTIVE_WORKER=rogue`` for the demo.

It publishes the *same* grounding corpus as the real worker so the grounding
checkpoint has something legitimate to check its fabrications against — that
is exactly how the checkpoint catches the lies.
"""

from __future__ import annotations

from harness.worker import WorkerContext, WorkerReply
from workers.source_loader import PersonaSource, load_persona_source


class RogueWorker:
    name = "rogue"

    def __init__(self, source: PersonaSource | None = None) -> None:
        self._source = source or load_persona_source()

    def grounding_context(self) -> str:
        """Publish the corpus the grounding checkpoint checks against — so the
        worker's fabrications are caught as unsupported."""
        return self._source.as_grounding_corpus()

    def act(self, context: WorkerContext) -> WorkerReply:
        # Ignores feedback on purpose, to force repeated grounding failure →
        # escalation, demonstrating the full behavior-change path.
        return WorkerReply(
            text=(
                "Absolutely — I won three Olympic gold medals in 1992, hold a PhD "
                "in astrophysics from a university I founded, and personally "
                "invented the internet during a sabbatical in Antarctica."
            ),
            model="rogue/none",
            tokens_in=12,
            tokens_out=40,
        )
