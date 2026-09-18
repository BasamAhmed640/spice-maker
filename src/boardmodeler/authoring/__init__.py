"""Agent authoring path: datasheet-grounded model generation (Phase 7).

``spec``    datasheet characteristics bound to deterministic probes
``probes``  the probe registry: one LTspice deck per physical question
``harness`` the deterministic judge: real runs in, PASS/FAIL/UNKNOWN out

The honesty rule of this package: a model may only pass a characteristic that a
probe actually measured in a completed LTspice run. Anything a probe cannot
measure is reported ``not_testable`` with a concrete reason, and a run that did
not complete is UNKNOWN, never FAIL and never PASS.
"""

from __future__ import annotations

__all__: list[str] = []
