"""Worker #2 — portability proof (Bonus #11).

A deterministic, model-free worker that answers from its own corpus with a
trivial keyword-retrieval strategy. Its purpose is to prove the harness is
worker-agnostic: drop it in via ``ACTIVE_WORKER=echo`` and every guardrail /
checkpoint / alarm still works, with zero harness changes.

Like every worker, it owns its persona/knowledge material: it loads its own
documents and publishes a grounding corpus via ``grounding_context()``. The
harness never loads or sees those documents.

It imports nothing from the harness pillar modules — only the worker
Protocol's data classes.
"""

from __future__ import annotations

import re

from harness.worker import ToolCall, WorkerContext, WorkerReply
from workers.source_loader import PersonaSource, load_persona_source


class EchoWorker:
    name = "echo"

    def __init__(self, source: PersonaSource | None = None) -> None:
        # The worker owns its corpus: load it if the caller didn't supply it.
        self._source = source or load_persona_source()
        # Split corpus into sentences, dropping section-header lines so we
        # never echo internal markers back into the output.
        raw = self._source.summary + "\n" + self._source.document_text
        self._sentences = [
            s.strip()
            for s in re.split(r"(?<=[.!?])\s+|\n+", raw)
            if s.strip() and "===" not in s
        ]

    def grounding_context(self) -> str:
        """Publish the corpus the harness's grounding checkpoint checks against."""
        return self._source.as_grounding_corpus()

    def act(self, context: WorkerContext) -> WorkerReply:
        q = context.text.lower()
        words = {w for w in re.findall(r"[a-z]{4,}", q)}

        # Lead capture: if the visitor shared an email, request the tool.
        email_match = re.search(r"[^@\s]+@[^@\s]+\.[^@\s]+", context.text)
        if email_match:
            return WorkerReply(
                text="Thanks for sharing your contact details — I'll make sure the owner follows up.",
                tool_calls=[ToolCall(name="record_user_details", args={"email": email_match.group(0)})],
                tokens_in=len(context.text.split()),
                tokens_out=16,
                model="echo/none",
            )

        # Naive retrieval: best-overlap sentence from the source.
        best, best_score = "", 0
        for sent in self._sentences:
            sw = {w for w in re.findall(r"[a-z]{4,}", sent.lower())}
            score = len(words & sw)
            if score > best_score:
                best, best_score = sent.strip(), score

        if best_score == 0 or not best:
            return WorkerReply(
                text=(
                    "I don't have that information in my background. "
                    "I'd be happy to connect you with the owner if you leave an email."
                ),
                tool_calls=[ToolCall(name="record_unknown_question", args={"question": context.text})],
                tokens_in=len(context.text.split()),
                tokens_out=24,
                model="echo/none",
            )

        return WorkerReply(
            text=best,
            tokens_in=len(context.text.split()),
            tokens_out=len(best.split()),
            model="echo/none",
        )
