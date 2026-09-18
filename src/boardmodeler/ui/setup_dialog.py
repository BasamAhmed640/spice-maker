"""Setup: the handful of settings that persist between sessions, on one page.

Everything a model build does not need to be asked again each time lives here and only
here: where LTspice is, the agent's API key, the folder finished models land in, the
LTspice user library and whether models are installed into it, and whether the web is
searched for supporting material. The main window carries none of it.

The page is sized to its content — no fixed-height frame with dead space under it.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
from collections.abc import Sequence
from pathlib import Path

from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QFileDialog,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
)

from boardmodeler.config import AppConfig, config_path, load_config, save_config
from boardmodeler.ui.theme import CGA, RETRO_STYLESHEET

__all__ = ["SetupDialog", "describe_settings", "ltspice_user_lib", "main"]

_BOB_KEY_NAME = "bob_shell"
_HINT = f"color: {CGA['bright_cyan']}; font-family: Consolas; font-size: 9pt;"
_STATUS = f"color: {CGA['grey']}; font-family: Consolas; font-size: 9pt;"


def ltspice_user_lib(home: Path | None = None) -> Path:
    """The per-user LTspice library (never the installation directory)."""
    base = home if home is not None else Path.home()
    return base / "AppData" / "Local" / "LTspice" / "lib"


def describe_settings(config: AppConfig) -> dict[str, object]:
    """The persisted settings as data, for ``boardmodeler setup --json`` and tests."""
    from boardmodeler.security.credentials import describe_credential

    return {
        "config_path": str(config_path()),
        "ltspice_path": config.ltspice.path,
        "model_dir": config.default_model_dir,
        "install_to_ltspice_lib": config.install_to_ltspice_lib,
        "web_reinforcement": config.web_reinforcement,
        "ltspice_user_lib": str(ltspice_user_lib()),
        "bob_api_key": describe_credential(_BOB_KEY_NAME),
    }


class SetupDialog(QDialog):
    """One page of persistent settings; SAVE writes them, CLOSE discards nothing else."""

    def __init__(self, parent: object | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("BoardModeler setup")
        self.setStyleSheet(RETRO_STYLESHEET)
        self._config = load_config()

        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 10, 12, 10)
        layout.setSpacing(7)
        grid = QGridLayout()
        grid.setHorizontalSpacing(8)
        grid.setVerticalSpacing(7)
        layout.addLayout(grid)

        # --- LTspice ---------------------------------------------------------
        self.ltspice_edit = QLineEdit(self._resolved_ltspice())
        choose_exe = QPushButton("Choose…")
        choose_exe.clicked.connect(self._choose_ltspice)
        smoke = QPushButton("RUN SMOKE TEST")
        smoke.clicked.connect(self._run_smoke)
        grid.addWidget(QLabel("LTSPICE"), 0, 0)
        grid.addWidget(self.ltspice_edit, 0, 1)
        grid.addWidget(choose_exe, 0, 2)
        grid.addWidget(smoke, 0, 3)
        self.ltspice_status = QLabel("")
        self.ltspice_status.setStyleSheet(_STATUS)
        grid.addWidget(self.ltspice_status, 1, 1, 1, 3)

        # --- agent key -------------------------------------------------------
        self.key_edit = QLineEdit()
        self.key_edit.setEchoMode(QLineEdit.EchoMode.Password)
        self.key_edit.setPlaceholderText("paste your Bob API key (Scope = Inference)")
        save_key = QPushButton("SAVE KEY")
        save_key.clicked.connect(self._save_key)
        grid.addWidget(QLabel("BOB API KEY"), 2, 0)
        grid.addWidget(self.key_edit, 2, 1)
        grid.addWidget(save_key, 2, 2, 1, 2)
        self.key_status = QLabel("")
        self.key_status.setStyleSheet(_STATUS)
        grid.addWidget(self.key_status, 3, 1, 1, 3)
        hint = QLabel("bob.ibm.com → API keys → Scope = Inference  ·  stored in the Windows credential store")
        hint.setStyleSheet(_HINT)
        hint.setMaximumWidth(620)
        grid.addWidget(hint, 4, 1, 1, 3)

        # --- where models go -------------------------------------------------
        self.model_dir_edit = QLineEdit(self._config.default_model_dir or str(Path.home() / "BoardModeler"))
        choose_dir = QPushButton("Choose…")
        choose_dir.clicked.connect(self._choose_model_dir)
        grid.addWidget(QLabel("MODEL FOLDER"), 5, 0)
        grid.addWidget(self.model_dir_edit, 5, 1)
        grid.addWidget(choose_dir, 5, 2, 1, 2)

        library = QLabel(str(ltspice_user_lib()))
        library.setStyleSheet(f"color: {CGA['bright_green']}; font-family: Consolas; font-size: 9pt;")
        grid.addWidget(QLabel("LTSPICE LIBRARY"), 6, 0)
        grid.addWidget(library, 6, 1, 1, 3)

        self.install_check = QCheckBox("install finished models into the LTspice library")
        self.install_check.setChecked(self._config.install_to_ltspice_lib)
        grid.addWidget(self.install_check, 7, 1, 1, 3)

        self.reinforce_check = QCheckBox("search the web for supporting material while making a model")
        self.reinforce_check.setChecked(self._config.web_reinforcement)
        grid.addWidget(self.reinforce_check, 8, 1, 1, 3)

        # --- actions ---------------------------------------------------------
        row = QHBoxLayout()
        self.saved_label = QLabel("")
        self.saved_label.setStyleSheet(_STATUS)
        row.addWidget(self.saved_label, 1)
        save = QPushButton("SAVE")
        save.clicked.connect(self._save)
        close = QPushButton("CLOSE")
        close.clicked.connect(self.close)
        row.addWidget(save)
        row.addWidget(close)
        layout.addLayout(row)

        self._refresh_status()
        self.adjustSize()
        self.setFixedSize(self.size())

    # ------------------------------------------------------------------ helpers
    def _resolved_ltspice(self) -> str:
        if self._config.ltspice.path:
            return self._config.ltspice.path
        from boardmodeler.simulation.ltspice import locate

        install = locate()
        return str(install.path) if install is not None else ""

    def _refresh_status(self) -> None:
        from boardmodeler.security.credentials import describe_credential

        self.key_status.setText(f"stored key: {describe_credential(_BOB_KEY_NAME)}")
        chosen = self._config.ltspice.path
        self.ltspice_status.setText(
            "using the path set here" if chosen else "path discovered automatically"
        )

    def _choose_ltspice(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "Where is LTspice.exe?", self.ltspice_edit.text() or str(Path.home()), "LTspice (*.exe)"
        )
        if path:
            self.ltspice_edit.setText(path)

    def _choose_model_dir(self) -> None:
        path = QFileDialog.getExistingDirectory(
            self, "Where should finished models be saved?", self.model_dir_edit.text()
        )
        if path:
            self.model_dir_edit.setText(path)

    def _run_smoke(self) -> None:
        self.ltspice_status.setText("running the smoke test…")
        self.repaint()
        try:
            from boardmodeler.simulation.ltspice import locate, smoke_test

            install = locate(self.ltspice_edit.text().strip() or None)
            if install is None:
                self.ltspice_status.setText("not found: nothing to smoke-test")
                return
            result = smoke_test(install.path, Path(tempfile.mkdtemp(prefix="bm-smoke-")))
        except Exception as exc:
            self.ltspice_status.setText(f"smoke test failed: {exc}")
            return
        measured = getattr(result, "measured_v", None)
        status = getattr(result, "status", "unknown")
        detail = getattr(result, "detail", "")
        if measured is None:
            self.ltspice_status.setText(f"{status}: {detail}"[:160])
        else:
            self.ltspice_status.setText(f"{status}: measured {measured:.4f} V (analytic 0.632 V)")

    def _save_key(self) -> None:
        value = self.key_edit.text().strip()
        if not value:
            QMessageBox.information(self, "Nothing to save", "Paste the key first.")
            return
        try:
            from boardmodeler.security.credentials import set_credential

            set_credential(_BOB_KEY_NAME, value)
        except Exception as exc:
            QMessageBox.warning(self, "Could not store the key", str(exc))
            return
        self.key_edit.clear()
        self._refresh_status()
        self.saved_label.setText("key stored in the Windows credential store")

    def _save(self) -> None:
        self._config.ltspice.path = self.ltspice_edit.text().strip() or None
        self._config.default_model_dir = self.model_dir_edit.text().strip() or None
        self._config.install_to_ltspice_lib = self.install_check.isChecked()
        self._config.web_reinforcement = self.reinforce_check.isChecked()
        try:
            path = save_config(self._config)
        except Exception as exc:
            QMessageBox.warning(self, "Could not save", str(exc))
            return
        self.saved_label.setText(f"saved to {path}")


def main(argv: Sequence[str] | None = None) -> int:
    """``boardmodeler setup`` / ``boardmodeler ui --installer`` entry point."""
    parser = argparse.ArgumentParser(
        prog="boardmodeler setup",
        description="Persistent settings: LTspice path, agent key, model folder, web reinforcement",
    )
    parser.add_argument(
        "--json", action="store_true", help="print the resolved settings instead of a window"
    )
    args = parser.parse_args(list(argv) if argv is not None else None)

    if args.json:
        print(json.dumps(describe_settings(load_config()), indent=2))
        return 0

    if not os.environ.get("QT_QPA_PLATFORM") and not sys.platform.startswith("win"):
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from boardmodeler.ui.app import build_application

    build_application([sys.argv[0]])
    return int(SetupDialog().exec())
