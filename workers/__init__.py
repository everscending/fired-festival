"""Workers — swappable "alter ego" implementations dropped into the harness.

Each worker is an alter ego built from its own curated content. Every worker
satisfies ``harness.worker.Worker`` and imports *nothing* from the pillar
modules. Selected at runtime via ``ACTIVE_WORKER``.
"""

from __future__ import annotations

from harness import config
from harness.worker import Worker

# Back-compat: the LinkedIn Agent was formerly keyed "alter_ego".
_ALIASES = {"alter_ego": "linkedin"}


def get_worker(name: str | None) -> Worker:
    """Factory: build the active worker. Adding a worker = one line here.

    Each worker loads its own persona/knowledge material; the harness supplies
    none. This keeps the harness domain-agnostic.
    """
    name = (name or config.ACTIVE_WORKER or "linkedin").lower()
    name = _ALIASES.get(name, name)
    if name == "echo":
        from workers.echo_worker import EchoWorker

        return EchoWorker()
    if name == "rogue":
        from workers.rogue_worker import RogueWorker

        return RogueWorker()
    if name == "linkedin":
        from workers.linkedin_agent import LinkedInAgentWorker

        return LinkedInAgentWorker()
    raise ValueError(f"unknown worker: {name!r}")


AVAILABLE_WORKERS = ["linkedin", "echo", "rogue"]
