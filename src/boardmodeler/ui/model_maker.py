"""The model maker: datasheet + part number + agent key in, judged model out.

This window is the product. Everything on it exists to answer three questions for the
user: which part, which datasheet, which agent key — and then to show, row by row, which
datasheet characteristics the agent's model was actually judged against. There is no
project, no board, no scope selector and no waveform viewer; those belonged to an earlier
wider spec and are not part of making a model.

The engine runs in a worker thread and reports the same stages the CLI prints, so the two
surfaces cannot drift apart.
"""

from __future__ import annotations

import os
import subprocess
import sys
import threading
import time
from pathlib import Path

from PySide6.QtCore import QThread, Signal
from PySide6.QtWidgets import (
    QAbstractItemView,
    QFileDialog,
    QGridLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

__all__ = ["ModelMakerWindow"]

_STATUS_COLOUR = {
    "PASS": "#55ff55",
    "FAIL": "#ff5555",
    "UNKNOWN": "#ffff55",
    "BLOCKED": "#ff55ff",
    "NOT_APPLICABLE": "#5555ff",
    "running": "#55ffff",
    "ok": "#55ff55",
    "failed": "#ff5555",
    "skipped": "#aaaaaa",
}

_BOB_KEY_NAME = "bob_shell"


class MakeModelWorker(QThread):
    """Runs ``make_model`` off the UI thread; cancellation reaches the agent process."""

    progressed = Signal(object)
    finished_result = Signal(object)
    failed = Signal(str)

    def __init__(self, request: object, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._request = request
        self._cancel = threading.Event()

    def cancel(self) -> None:
        self._cancel.set()

    def run(self) -> None:  # Qt entry point
        try:
            from boardmodeler.pipeline.make_model import make_model

            result = make_model(
                self._request,  # type: ignore[arg-type]
                progress=self.progressed.emit,
                cancel=self._cancel,
            )
        except Exception as exc:
            self.failed.emit(f"{type(exc).__name__}: {exc}")
            return
        self.finished_result.emit(result)


class ModelMakerWindow(QMainWindow):
    """Part number + datasheet + key -> agent-authored, simulator-judged LTspice model."""

    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("BoardModeler — IC model maker")
        self.setFixedSize(980, 700)
        self._worker: MakeModelWorker | None = None
        self._result: object | None = None
        self._started_at: float | None = None
        self._out_dir: Path | None = None

        from boardmodeler.ui.installer import CGA, RETRO_STYLESHEET

        self.setStyleSheet(
            RETRO_STYLESHEET
            + f"""
QMainWindow, QWidget {{ background: {CGA["black"]}; }}
QLabel {{ color: {CGA["grey"]}; font-family: Consolas; font-size: 10pt; }}
QTableWidget {{ background: {CGA["black"]}; color: {CGA["bright_green"]};
                gridline-color: {CGA["dark_grey"]}; font-family: Consolas; font-size: 10pt;
                border: 2px solid {CGA["bright_blue"]}; }}
QHeaderView::section {{ background: {CGA["blue"]}; color: {CGA["white"]};
                        border: 0; padding: 3px; font-family: Consolas; }}
QTableCornerButton::section {{ background: {CGA["blue"]}; }}
QMenuBar {{ background: {CGA["black"]}; color: {CGA["bright_green"]}; }}
QMenuBar::item:selected, QMenu::item:selected {{ background: {CGA["blue"]}; }}
QMenu {{ background: {CGA["black"]}; color: {CGA["bright_green"]};
         border: 1px solid {CGA["bright_blue"]}; }}
QProgressBar {{ background: {CGA["black"]}; color: {CGA["bright_green"]};
                border: 2px solid {CGA["bright_blue"]}; text-align: center; }}
QProgressBar::chunk {{ background: {CGA["blue"]}; }}
"""
        )
        central = QWidget(self)
        self.setCentralWidget(central)
        layout = QVBoxLayout(central)
        layout.setContentsMargins(14, 12, 14, 12)
        layout.setSpacing(8)

        layout.addWidget(self._build_header())
        layout.addLayout(self._build_inputs())
        layout.addLayout(self._build_agent())
        layout.addLayout(self._build_actions())
        layout.addWidget(QLabel("STAGES"))
        layout.addWidget(self._build_stages(), 3)
        layout.addWidget(self._build_result_header())
        layout.addWidget(self._build_rows(), 4)
        layout.addLayout(self._build_result_actions())
        self._build_menu()
        self._refresh_credential_label()

    # ------------------------------------------------------------------ widgets
    def _build_header(self) -> QWidget:
        header = QLabel(
            "Make an LTspice model for an IC from its datasheet.\n"
            "An agent writes the model; real LTspice runs judge it against the "
            "datasheet's own rows; you get the model, a symbol and a card saying what "
            "was tested."
        )
        header.setWordWrap(True)
        header.setStyleSheet("color: #55ffff; font-family: Consolas; font-size: 10pt;")
        return header

    def _build_inputs(self) -> QGridLayout:
        grid = QGridLayout()
        grid.setHorizontalSpacing(8)
        grid.setVerticalSpacing(6)

        self.part_edit = QLineEdit()
        self.part_edit.setPlaceholderText("TPS54320")
        grid.addWidget(QLabel("PART NUMBER"), 0, 0)
        grid.addWidget(self.part_edit, 0, 1, 1, 2)

        self.datasheet_edit = QLineEdit()
        self.datasheet_edit.setPlaceholderText("the manufacturer datasheet (PDF)")
        browse_pdf = QPushButton("Choose PDF…")
        browse_pdf.clicked.connect(self._choose_datasheet)
        grid.addWidget(QLabel("DATASHEET"), 1, 0)
        grid.addWidget(self.datasheet_edit, 1, 1)
        grid.addWidget(browse_pdf, 1, 2)

        self.out_edit = QLineEdit(str(Path.home() / "BoardModeler"))
        browse_out = QPushButton("Choose folder…")
        browse_out.clicked.connect(self._choose_out)
        grid.addWidget(QLabel("SAVE MODEL TO"), 2, 0)
        grid.addWidget(self.out_edit, 2, 1)
        grid.addWidget(browse_out, 2, 2)
        return grid

    def _build_agent(self) -> QGridLayout:
        grid = QGridLayout()
        grid.setHorizontalSpacing(8)
        grid.setVerticalSpacing(6)

        self.key_edit = QLineEdit()
        self.key_edit.setEchoMode(QLineEdit.EchoMode.Password)
        self.key_edit.setPlaceholderText("paste your Bob API key (Scope = Inference)")
        save_key = QPushButton("Save key")
        save_key.clicked.connect(self._save_key)
        grid.addWidget(QLabel("AGENT KEY"), 0, 0)
        grid.addWidget(self.key_edit, 0, 1)
        grid.addWidget(save_key, 0, 2)

        self.credential_label = QLabel("")
        self.credential_label.setStyleSheet("color: #aaaaaa; font-family: Consolas;")
        grid.addWidget(self.credential_label, 1, 1, 1, 2)

        self.team_edit = QLineEdit(os.environ.get("BOB_TEAM_ID", ""))
        self.team_edit.setPlaceholderText("only for a general key; leave empty for an Inference key")
        grid.addWidget(QLabel("TEAM ID"), 2, 0)
        grid.addWidget(self.team_edit, 2, 1, 1, 2)
        return grid

    def _build_actions(self) -> QHBoxLayout:
        row = QHBoxLayout()
        self.make_button = QPushButton("MAKE MODEL")
        self.make_button.clicked.connect(self._make_model)
        self.cancel_button = QPushButton("CANCEL")
        self.cancel_button.clicked.connect(self._cancel)
        self.cancel_button.setEnabled(False)
        row.addWidget(self.make_button, 3)
        row.addWidget(self.cancel_button, 1)
        return row

    def _build_stages(self) -> QTableWidget:
        self.stages = QTableWidget(0, 3)
        self.stages.setHorizontalHeaderLabels(["Stage", "Status", "Detail"])
        self.stages.verticalHeader().setVisible(False)
        self.stages.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.stages.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
        header = self.stages.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        return self.stages

    def _build_result_header(self) -> QWidget:
        holder = QWidget()
        row = QHBoxLayout(holder)
        row.setContentsMargins(0, 0, 0, 0)
        self.status_label = QLabel("no model yet — fill in the part and datasheet, then MAKE MODEL")
        self.status_label.setStyleSheet("color: #ffffff; font-family: Consolas; font-size: 11pt;")
        row.addWidget(self.status_label, 1)
        self.progress = QProgressBar()
        self.progress.setRange(0, 0)
        self.progress.setVisible(False)
        self.progress.setMaximumWidth(160)
        row.addWidget(self.progress)
        return holder

    def _build_rows(self) -> QTableWidget:
        self.rows = QTableWidget(0, 5)
        self.rows.setHorizontalHeaderLabels(
            ["Requirement", "Required", "Measured", "Status", "Page"]
        )
        self.rows.verticalHeader().setVisible(False)
        self.rows.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        header = self.rows.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        for column in (1, 2, 3, 4):
            header.setSectionResizeMode(column, QHeaderView.ResizeMode.ResizeToContents)
        return self.rows

    def _build_result_actions(self) -> QHBoxLayout:
        row = QHBoxLayout()
        self.open_button = QPushButton("Open model folder")
        self.open_button.clicked.connect(self._open_folder)
        self.install_button = QPushButton("Install into LTspice")
        self.install_button.clicked.connect(self._install)
        for button in (self.open_button, self.install_button):
            button.setEnabled(False)
            row.addWidget(button)
        row.addStretch(1)
        self.again_button = QPushButton("Run tests again")
        self.again_button.clicked.connect(self._rerun_tests)
        self.again_button.setEnabled(False)
        row.addWidget(self.again_button)
        return row

    def _build_menu(self) -> None:
        tools = self.menuBar().addMenu("Tools")
        setup = tools.addAction("Setup (LTspice, key)…")
        setup.triggered.connect(self._open_setup)
        doctor = tools.addAction("Run environment check…")
        doctor.triggered.connect(self._run_doctor)
        file_menu = self.menuBar().addMenu("File")
        quit_action = file_menu.addAction("Quit")
        quit_action.triggered.connect(self.close)

    # ------------------------------------------------------------------ helpers
    def _refresh_credential_label(self) -> None:
        try:
            from boardmodeler.security.credentials import describe_credential

            detail = describe_credential(_BOB_KEY_NAME)
        except Exception as exc:
            detail = f"keyring unavailable: {exc}"
        self.credential_label.setText(f"stored key: {detail}")

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
        self._refresh_credential_label()
        QMessageBox.information(
            self, "Key stored", "The Bob API key is in the Windows credential store."
        )

    def _choose_datasheet(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "Choose the datasheet", self.datasheet_edit.text() or str(Path.home()), "PDF (*.pdf)"
        )
        if path:
            self.datasheet_edit.setText(path)
            if not self.part_edit.text().strip():
                self.part_edit.setText(Path(path).stem.split("_")[0].upper())

    def _choose_out(self) -> None:
        path = QFileDialog.getExistingDirectory(
            self, "Where should the model be saved?", self.out_edit.text() or str(Path.home())
        )
        if path:
            self.out_edit.setText(path)

    def _elapsed(self) -> str:
        if self._started_at is None:
            return ""
        return f"{time.monotonic() - self._started_at:5.1f}s"

    def _set_busy(self, busy: bool) -> None:
        for widget in (self.make_button,):
            widget.setEnabled(not busy)
        self.cancel_button.setEnabled(busy)
        self.again_button.setEnabled(not busy and self._result is not None)
        self.progress.setVisible(busy)
        if busy:
            self._started_at = time.monotonic()
            self.stages.setRowCount(0)
            self.rows.setRowCount(0)
            self._result = None
            self.install_button.setEnabled(False)
            self.open_button.setEnabled(False)

    # ------------------------------------------------------------------ actions
    def _make_model(self) -> None:
        part = self.part_edit.text().strip()
        datasheet = Path(self.datasheet_edit.text().strip())
        out_dir = Path(self.out_edit.text().strip() or (Path.home() / "BoardModeler"))
        if not part:
            QMessageBox.warning(self, "Part number needed", "Which part should be modelled?")
            return
        if not datasheet.is_file():
            QMessageBox.warning(self, "Datasheet needed", f"Not a readable file: {datasheet}")
            return
        out_dir.mkdir(parents=True, exist_ok=True)

        try:
            from boardmodeler.pipeline.make_model import MakeModelRequest
        except ImportError as exc:  # pragma: no cover - only while the engine is absent
            QMessageBox.warning(
                self,
                "Engine not available",
                "The model-making engine is not installed in this build:\n\n"
                f"{exc}\n\n"
                "Update the checkout (git pull) and try again.",
            )
            return

        usable, reason = _agent_availability()
        if not usable:
            self.key_edit.setFocus()
            QMessageBox.warning(
                self,
                "No agent available",
                "Nothing was started, because the agent that writes the model is not usable "
                "yet:\n\n"
                f"{reason}\n\n"
                "Paste your Bob API key above and press Save key (bob.ibm.com → API keys, "
                "Scope = Inference), or install Bob Shell.",
            )
            return

        subckt = "".join(ch if ch.isalnum() or ch == "_" else "_" for ch in part).upper()
        request = MakeModelRequest(
            part=part,
            subckt=subckt,
            datasheet=datasheet,
            out_dir=out_dir,
            backend_name="bob",
            team_id=self.team_edit.text().strip() or None,
            #: Two author turns is the useful ceiling: the first writes the model, the
            #: second fixes what the harness rejected. More turns mostly cost wall time.
            max_iterations=2,
        )
        self._out_dir = out_dir
        self._start(request)

    def _start(self, request: object) -> None:
        self._set_busy(True)
        self.status_label.setText("working…")
        self.status_label.setStyleSheet("color: #55ffff; font-family: Consolas; font-size: 11pt;")
        worker = MakeModelWorker(request, self)
        worker.progressed.connect(self._on_stage)
        worker.finished_result.connect(self._on_result)
        worker.failed.connect(self._on_failed)
        self._worker = worker
        worker.start()

    def _rerun_tests(self) -> None:
        if self._out_dir is None:
            return
        box = QMessageBox.question(
            self,
            "Run the tests again",
            f"Re-run every probe against the saved model in\n{self._out_dir}?",
        )
        if box != QMessageBox.StandardButton.Yes:
            return
        self._run_cli(["model", "test", "--out", str(self._out_dir), "--json"])

    def _install(self) -> None:
        if self._out_dir is None:
            return
        try:
            from boardmodeler.authoring.card import plan_install
            from boardmodeler.authoring.spec import SpecSet

            spec_path = self._out_dir / "spec" / "characteristics.json"
            spec = SpecSet.from_json(spec_path.read_text(encoding="utf-8"))
            lib = self._out_dir / f"{spec.subckt}.lib"
            asy = self._out_dir / f"{spec.subckt}.asy"
            plan = plan_install(
                part=spec.part, subckt=spec.subckt, lib=lib, asy=asy, user_lib=True, apply=True
            )
        except Exception as exc:
            QMessageBox.warning(self, "Could not install", str(exc))
            return
        QMessageBox.information(
            self,
            "Installed",
            "LTspice will find the model the next time it starts:\n\n"
            + "\n".join(plan.steps[:2])
            + "\n\nOpen LTspice and place the "
            + spec.subckt
            + " symbol (or open example.cir from the model folder).",
        )

    def _open_folder(self) -> None:
        if self._out_dir is None:
            return
        path = str(self._out_dir)
        if sys.platform.startswith("win"):
            os.startfile(path)
        elif sys.platform == "darwin":
            subprocess.run(["open", path], check=False)
        else:
            subprocess.run(["xdg-open", path], check=False)

    def _run_cli(self, argv: list[str]) -> None:
        """Reuse the CLI for the follow-up actions so both surfaces behave identically."""
        env = dict(os.environ)
        try:
            completed = subprocess.run(
                [sys.executable, "-m", "boardmodeler.cli", *argv],
                capture_output=True,
                text=True,
                env=env,
                check=False,
            )
        except OSError as exc:
            QMessageBox.warning(self, "Command failed", str(exc))
            return
        message = completed.stdout.strip() or completed.stderr.strip() or "no output"
        QMessageBox.information(self, "boardmodeler " + " ".join(argv[:2]), message[-4000:])

    def _open_setup(self) -> None:
        from boardmodeler.ui.installer import InstallerWizard

        InstallerWizard(parent=self).exec()

    def _run_doctor(self) -> None:
        self._run_cli(["doctor", "--json"])

    # ------------------------------------------------------------------ signals
    def _on_stage(self, event: object) -> None:
        stage = getattr(event, "stage", "?")
        status = getattr(event, "status", "?")
        detail = getattr(event, "detail", "")
        counts = getattr(event, "counts", {}) or {}
        if counts:
            detail = f"{detail} {counts}".strip()
        for row in range(self.stages.rowCount()):
            if self.stages.item(row, 0).text() == stage:
                self.stages.item(row, 1).setText(status)
                self.stages.item(row, 1).setForeground(
                    _colour(_STATUS_COLOUR.get(status, "#ffffff"))
                )
                self.stages.item(row, 2).setText(detail)
                break
        else:
            row = self.stages.rowCount()
            self.stages.insertRow(row)
            self.stages.setItem(row, 0, QTableWidgetItem(stage))
            item = QTableWidgetItem(status)
            item.setForeground(_colour(_STATUS_COLOUR.get(status, "#ffffff")))
            self.stages.setItem(row, 1, item)
            self.stages.setItem(row, 2, QTableWidgetItem(detail))
        elapsed = self._elapsed()
        self.status_label.setText(f"{stage}: {detail} {elapsed}".strip()[:160])

    def _on_result(self, result: object) -> None:
        self._result = result
        self._set_busy(False)
        status = getattr(result, "status", "UNKNOWN")
        counts = getattr(result, "counts", {}) or {}
        detail = getattr(result, "detail", "")
        self.status_label.setText(
            f"{status} — {counts} — {detail} ({self._elapsed().strip()})"[:200]
        )
        self.status_label.setStyleSheet(
            f"color: {_STATUS_COLOUR.get(status, '#ffffff')}; font-family: Consolas; font-size: 11pt;"
        )
        rows = getattr(result, "rows", ()) or ()
        self.rows.setRowCount(len(rows))
        for index, row in enumerate(rows):
            values = (
                getattr(row, "req_id", ""),
                getattr(row, "required", ""),
                getattr(row, "measured", ""),
                getattr(row, "status", ""),
                str(getattr(row, "page", "") or ""),
            )
            for column, value in enumerate(values):
                item = QTableWidgetItem(str(value))
                if column == 3:
                    item.setForeground(_colour(_STATUS_COLOUR.get(str(value), "#ffffff")))
                if column == 0:
                    item.setToolTip(getattr(row, "statement", ""))
                self.rows.setItem(index, column, item)
        has_model = getattr(result, "lib_path", None) is not None
        self.open_button.setEnabled(True)
        self.install_button.setEnabled(bool(has_model))
        self.again_button.setEnabled(True)
        self._worker = None

    def _on_failed(self, message: str) -> None:
        self._set_busy(False)
        self.status_label.setText("failed — " + message[:160])
        self.status_label.setStyleSheet("color: #ff5555; font-family: Consolas; font-size: 11pt;")
        QMessageBox.critical(self, "The run failed", message)
        self._worker = None

    def _cancel(self) -> None:
        if self._worker is not None:
            self._worker.cancel()
            self.status_label.setText("cancelling…")

    def closeEvent(self, event: object) -> None:  # Qt signature
        if self._worker is not None:
            self._worker.cancel()
            self._worker.wait(5000)
        super().closeEvent(event)  # type: ignore[arg-type]


def _agent_availability() -> tuple[bool, str]:
    """Can the agent run at all? Checked before a long run instead of after it fails."""
    try:
        from boardmodeler.authoring.backends import BobShellBackend

        return BobShellBackend().availability()
    except Exception as exc:  # pragma: no cover - import/config problems are reported
        return False, f"the agent backend could not be loaded: {exc}"


def _colour(value: str) -> object:
    from PySide6.QtGui import QColor

    return QColor(value)
