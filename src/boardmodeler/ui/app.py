"""QApplication entry point (INTERFACES §4).

``boardmodeler ui [--project DIR] [--installer]``. Importing this module
requires Qt; importing :mod:`boardmodeler.ui` does not (the package facade
imports this lazily).
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence
from pathlib import Path

from PySide6.QtWidgets import QApplication

from boardmodeler import __version__

__all__ = ["build_application", "main"]


def build_application(argv: Sequence[str] | None = None) -> QApplication:
    """Return the running ``QApplication`` or create one (never two)."""
    existing = QApplication.instance()
    app = existing if existing is not None else QApplication(list(argv) if argv is not None else [])
    app.setApplicationName("BoardModeler")
    app.setApplicationVersion(__version__)
    app.setOrganizationName("BoardModeler")
    return app


def main(argv: Sequence[str] | None = None, *, exec_app: bool = True) -> int:
    """Launch the desktop application (or the installer with ``--installer``)."""
    parser = argparse.ArgumentParser(
        prog="boardmodeler ui", description="BoardModeler desktop application"
    )
    parser.add_argument("--project", type=Path, default=None, help="project directory to open")
    parser.add_argument(
        "--installer", action="store_true", help="launch the setup wizard instead of the app"
    )
    args = parser.parse_args(list(argv) if argv is not None else None)

    app = build_application([sys.argv[0]])
    if args.installer:
        from boardmodeler.ui.installer import InstallerWizard

        wizard = InstallerWizard()
        if args.project:
            wizard.set_answers({"project_dir": str(args.project)})
        if not exec_app:
            return 0
        return int(wizard.exec())

    from boardmodeler.ui.main_window import MainWindow

    window = MainWindow()
    if args.project is not None:
        window.load_project(args.project)
    window.show()
    if not exec_app:
        return 0
    return int(app.exec())


if __name__ == "__main__":  # pragma: no cover - manual launch
    raise SystemExit(main())
