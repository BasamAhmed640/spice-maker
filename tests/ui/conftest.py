"""Qt test configuration: every GUI test runs offscreen, without a display."""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
