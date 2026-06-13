"""Alter Ego Harness — domain-agnostic AI harness.

The harness provides four pillars to any worker dropped into it:
guardrails, checkpoints, material handling, and alarms. The worker is
reached only through the narrow ``Worker`` Protocol in :mod:`harness.worker`;
the worker imports nothing from the pillar modules.
"""

from harness import config  # noqa: F401

__all__ = ["config"]
