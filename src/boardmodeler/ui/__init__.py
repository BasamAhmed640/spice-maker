"""BoardModeler desktop application (PySide6) — D12.

Importing this package must not require a display or a ``QApplication``: Qt is
imported inside the modules that need it. ``boardmodeler ui`` and
``boardmodeler setup`` reach :func:`boardmodeler.ui.app.main` and
:func:`boardmodeler.ui.installer.main` through these lazy wrappers.
"""

from __future__ import annotations

from collections.abc import Sequence

__all__ = ["installer_main", "main"]


def main(argv: Sequence[str] | None = None) -> int:
    """Run the desktop application (INTERFACES §4)."""
    from boardmodeler.ui.app import main as app_main

    return app_main(argv)


def installer_main(argv: Sequence[str] | None = None) -> int:
    """Run the retro installer wizard (INTERFACES §4)."""
    from boardmodeler.ui.installer import main as wizard_main

    return wizard_main(argv)
