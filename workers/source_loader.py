"""Worker-side source loading (persona / knowledge material).

This is deliberately NOT in the harness. A worker's documents — the LinkedIn
export, the curated summary — are static configuration *of that worker*, used
to build its system prompt and to publish a grounding corpus. The harness is
domain-agnostic and knows nothing about PDFs or careers.

Workers that want document-backed knowledge use these helpers; workers that
don't (echo for retrieval-only, rogue for none) ignore them.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from pypdf import PdfReader


# Default cap so a large export cannot blow the model's context window. This is
# a worker concern (it shapes the worker's own prompt), not a harness limit.
DEFAULT_MAX_CHARS = 24_000


@dataclass
class PersonaSource:
    """A worker's persona/knowledge material, loaded from its own documents."""

    summary: str
    document_text: str
    char_count: int

    def as_prompt_block(self) -> str:
        """Render the material for embedding in a worker's system prompt."""
        return (
            "=== CURATED SUMMARY ===\n"
            f"{self.summary}\n\n"
            "=== SOURCE DOCUMENT (extracted) ===\n"
            f"{self.document_text}\n"
        )

    def as_grounding_corpus(self) -> str:
        """The plain corpus the harness's grounding checkpoint can check."""
        return f"{self.summary}\n{self.document_text}"


def load_persona_source(
    summary_path: str = "summary.txt",
    document_path: str = "linkedin.pdf",
    max_chars: int | None = None,
) -> PersonaSource:
    """Load + validate + size-cap a worker's persona documents (fail-fast).

    Raises ``FileNotFoundError`` / ``ValueError`` if inputs are missing or
    empty — a document-backed worker should never silently start ungrounded.
    """
    cap = max_chars if max_chars is not None else DEFAULT_MAX_CHARS

    sp = Path(summary_path)
    dp = Path(document_path)
    if not sp.exists():
        raise FileNotFoundError(f"Required source missing: {summary_path}")
    if not dp.exists():
        raise FileNotFoundError(f"Required source missing: {document_path}")

    summary = sp.read_text(encoding="utf-8").strip()
    if not summary:
        raise ValueError(f"{summary_path} is empty")

    reader = PdfReader(str(dp))
    pages = [(page.extract_text() or "") for page in reader.pages]
    document_text = "\n".join(pages).strip()
    if not document_text:
        raise ValueError(f"{document_path} produced no extractable text")

    if len(summary) + len(document_text) > cap:
        document_text = document_text[: max(0, cap - len(summary))]

    return PersonaSource(
        summary=summary,
        document_text=document_text,
        char_count=len(summary) + len(document_text),
    )
