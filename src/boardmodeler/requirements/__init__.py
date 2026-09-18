"""Requirement validation and source-consistency review.

This package is the gate that decides whether an extracted requirement may be
used as device data:

* :mod:`boardmodeler.requirements.model` — deterministic, machine-readable
  validation: limit sanity, the unit vocabulary, absolute-maximum rejection,
  typical-vs-limit inference, condition capture, duplicate ids.
* :mod:`boardmodeler.requirements.review` — source consistency: citation
  verification against the cited page of the real document, conflict detection
  between sources, and the review questions a human has to answer.

Nothing here repairs a requirement. Every problem is reported with a stable
code and an honest severity, and citation verification never returns ``True``
because a document or a page merely exists.
"""

from __future__ import annotations

__all__ = ["model", "review"]
