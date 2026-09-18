"""Retro installer wizard (INTERFACES §4).

A self-contained, light, low-resolution 8-bit-styled ``QWizard``: every pixel is
drawn in code (3 px hard frames, a 16-colour palette, a dithered panel, chunky
monospace text, a blinking block cursor and a ``[3/5]`` step counter). No
images, no stylesheet files, no extra dependency.

The wizard reports **observed** facts only. LTspice detection, the version and
the smoke-test result come from the same core functions the CLI uses
(``simulation.ltspice.locate_outcome`` / ``version`` / ``smoke_test``); a failed
smoke test is displayed as a failure with the observed detail, and a step whose
check did not run says so instead of claiming success. Every step is skippable,
and all choices are persisted through ``config.save_config``.

Test API (drives the whole wizard offscreen, no event loop needed)::

    wizard = InstallerWizard(answers={...}, config_file=tmp / "config.json")
    outcome = wizard.run_to_completion()      # InstallerOutcome
    wizard.page_ids()                         # ["welcome", "ltspice", ...]
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from PySide6.QtCore import QRectF, Qt, QTimer
from PySide6.QtGui import QColor, QFont, QFontMetrics, QKeyEvent, QPainter, QPaintEvent
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
    QWizard,
    QWizardPage,
)

from boardmodeler.config import AppConfig, config_path, load_config, save_config
from boardmodeler.domain.enums import ProviderKind
from boardmodeler.providers.base import ProviderError
from boardmodeler.providers.registry import select_provider
from boardmodeler.security.credentials import describe_credential, set_credential
from boardmodeler.simulation.ltspice import locate_outcome, smoke_test
from boardmodeler.simulation.ltspice import version as ltspice_version

__all__ = [
    "CGA",
    "RETRO_STYLESHEET",
    "STEP_IDS",
    "InstallerOutcome",
    "InstallerWizard",
    "main",
    "outcome_payload",
    "retro_font",
]

STEP_IDS: tuple[str, ...] = ("welcome", "ltspice", "provider", "policy", "finish")
"""Wizard steps, in order; ``--installer``/`boardmodeler setup` walks these."""

CGA: dict[str, str] = {
    "black": "#000000",
    "blue": "#0000aa",
    "green": "#00aa00",
    "cyan": "#00aaaa",
    "red": "#aa0000",
    "magenta": "#aa00aa",
    "brown": "#aa5500",
    "grey": "#aaaaaa",
    "dark_grey": "#555555",
    "bright_blue": "#5555ff",
    "bright_green": "#55ff55",
    "bright_cyan": "#55ffff",
    "bright_red": "#ff5555",
    "bright_magenta": "#ff55ff",
    "yellow": "#ffff55",
    "white": "#ffffff",
}

RETRO_STYLESHEET = f"""
QWizard, QWizardPage {{ background: {CGA['black']}; }}
QLabel {{ color: {CGA['grey']}; font-family: Consolas; font-size: 10pt; }}
QPushButton {{
    background: {CGA['grey']}; color: {CGA['black']};
    border: 2px solid {CGA['white']}; padding: 4px 10px;
    font-family: Consolas; font-size: 10pt; font-weight: bold;
}}
QPushButton:disabled {{ background: {CGA['dark_grey']}; color: {CGA['grey']}; }}
QPushButton:pressed {{ background: {CGA['dark_grey']}; }}
QLineEdit, QComboBox, QPlainTextEdit {{
    background: {CGA['black']}; color: {CGA['bright_green']};
    border: 2px solid {CGA['bright_blue']}; padding: 3px;
    font-family: Consolas; font-size: 10pt;
}}
QCheckBox {{ color: {CGA['grey']}; font-family: Consolas; font-size: 10pt; spacing: 6px; }}
QCheckBox::indicator {{ width: 12px; height: 12px; border: 2px solid {CGA['bright_green']};
                        background: {CGA['black']}; }}
QCheckBox::indicator:checked {{ background: {CGA['bright_green']}; }}
"""

TITLE_H = 26
BODY_PITCH = 15
BODY_ROWS = 9
FRAME = 3
CURSOR_MS = 420


def retro_font(size: int = 10, *, bold: bool = False) -> QFont:
    """A chunky fixed-pitch font with antialiasing disabled."""
    font = QFont("Consolas", size)
    font.setStyleHint(QFont.StyleHint.Monospace)
    font.setStyleStrategy(QFont.StyleStrategy.NoAntialias)
    font.setBold(bold)
    return font


@dataclass
class InstallerOutcome:
    """What the walk did: completed, where the config went, what was observed."""

    completed: bool
    config_path: Path
    steps: list[tuple[str, str]] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)


_KIND_BY_NAME: dict[str, ProviderKind] = {kind.name.lower(): kind for kind in ProviderKind}


def _kind_for_name(name: str) -> ProviderKind:
    """Provider kind for a wizard provider name (``fixture`` -> ``FIXTURE``)."""
    try:
        return _KIND_BY_NAME[name.lower()]
    except KeyError as exc:
        raise ValueError(
            f"unknown provider {name!r}; known providers: {sorted(_KIND_BY_NAME)}"
        ) from exc


class RetroPage(QWizardPage):
    """Base page: paints the 8-bit frame, title bar, step counter and body."""

    def __init__(self, wizard: InstallerWizard, step_id: str, heading: str) -> None:
        super().__init__(wizard)
        self.step_id = step_id
        self.heading = heading
        self._cursor_on = True
        self._timer = QTimer(self)
        self._timer.setInterval(CURSOR_MS)
        self._timer.timeout.connect(self._blink)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, TITLE_H + BODY_ROWS * BODY_PITCH + 8, 16, 14)
        layout.setSpacing(6)
        self.body_area = layout

    # ------------------------------------------------------------------ blink
    def _blink(self) -> None:
        self._cursor_on = not self._cursor_on
        self.update()

    def showEvent(self, event: Any) -> None:  # Qt signature
        super().showEvent(event)
        self._timer.start()

    def hideEvent(self, event: Any) -> None:  # Qt signature
        self._timer.stop()
        super().hideEvent(event)

    def body_lines(self) -> list[str]:
        return list(self.wizard().body_lines(self.step_id))

    # ------------------------------------------------------------------ paint
    def paintEvent(self, _event: QPaintEvent) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, False)
        rect = self.rect()
        painter.fillRect(rect, QColor(CGA["grey"]))
        inner = rect.adjusted(FRAME, FRAME, -FRAME, -FRAME)
        painter.fillRect(inner, QColor(CGA["black"]))
        self._draw_dither(painter, inner)

        title = QRectF(inner.left(), inner.top(), inner.width(), TITLE_H)
        painter.fillRect(title, QColor(CGA["blue"]))
        painter.setFont(retro_font(10, bold=True))
        painter.setPen(QColor(CGA["black"]))
        painter.drawText(title.adjusted(7, 0, 0, 0), Qt.AlignmentFlag.AlignVCenter, self.heading)
        painter.setPen(QColor(CGA["white"]))
        painter.drawText(title.adjusted(6, 0, 0, 0), Qt.AlignmentFlag.AlignVCenter, self.heading)
        counter = f"[{self.wizard().step_number(self.step_id)}/{len(STEP_IDS)}]"
        painter.drawText(
            title.adjusted(0, 0, -8, 0),
            Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignRight,
            counter,
        )

        painter.setFont(retro_font(9))
        y = inner.top() + TITLE_H + 14
        last_width = 0
        for line in self.body_lines()[:BODY_ROWS]:
            upper = line.upper()
            colour = QColor(CGA["grey"])
            if "NOT FOUND" in upper or "NOT STORED" in upper or "-> FAIL" in upper:
                colour = QColor(CGA["bright_red"])
            elif upper.startswith("SMOKE    PASS") or upper.startswith("PASS") or line.startswith("OK"):
                colour = QColor(CGA["bright_green"])
            elif line.startswith(">"):
                colour = QColor(CGA["bright_cyan"])
            painter.setPen(colour)
            painter.drawText(inner.left() + 14, y, line)
            last_width = QFontMetrics(painter.font()).horizontalAdvance(line)
            y += BODY_PITCH
        if self._cursor_on:
            painter.fillRect(
                QRectF(inner.left() + 16 + last_width, y - BODY_PITCH - 10, 8, 12),
                QColor(CGA["bright_green"]),
            )
        painter.setPen(QColor(CGA["dark_grey"]))
        painter.drawText(
            inner.left() + 14,
            inner.bottom() - 4,
            "F2=SKIP STEP  ENTER=NEXT  ESC=ABORT  (every step is skippable)",
        )
        painter.end()

    def _draw_dither(self, painter: QPainter, rect: Any) -> None:
        painter.setPen(QColor("#101018"))
        for y in range(rect.top() + TITLE_H, rect.bottom(), 3):
            painter.drawLine(rect.left() + 1, y, rect.right() - 1, y)

    # ------------------------------------------------------------------ keys
    def keyPressEvent(self, event: QKeyEvent) -> None:  # Qt signature
        if event.key() == Qt.Key.Key_F2:
            self.wizard().skip_current()
            return
        super().keyPressEvent(event)


class WelcomePage(RetroPage):
    def __init__(self, wizard: InstallerWizard) -> None:
        super().__init__(wizard, "welcome", "STEP 1/5  WELCOME")
        self.project_edit = QLineEdit(self)
        self.project_edit.setPlaceholderText("project directory (optional)")
        browse = QPushButton("BROWSE", self)
        browse.clicked.connect(self._browse)
        scan = QPushButton("SCAN AGAIN", self)
        scan.clicked.connect(lambda: self.wizard().observe("welcome", force=True))
        row = QHBoxLayout()
        row.addWidget(QLabel("PROJECT", self))
        row.addWidget(self.project_edit, 1)
        row.addWidget(browse)
        self.body_area.addLayout(row)
        self.body_area.addWidget(scan)
        self.project_edit.textChanged.connect(
            lambda text: self.wizard().set_answers({"project_dir": text.strip()})
        )

    def _browse(self) -> None:
        chosen = QFileDialog.getExistingDirectory(self, "Select the project directory")
        if chosen:
            self.project_edit.setText(chosen)


class LtspicePage(RetroPage):
    def __init__(self, wizard: InstallerWizard) -> None:
        super().__init__(wizard, "ltspice", "STEP 2/5  LTSPICE")
        self.path_edit = QLineEdit(self)
        self.path_edit.setPlaceholderText("LTspice.exe (leave empty to auto-discover)")
        browse = QPushButton("BROWSE", self)
        browse.clicked.connect(self._browse)
        self.run_button = QPushButton("RUN SMOKE TEST", self)
        self.run_button.setObjectName("run-smoke-test")
        self.run_button.clicked.connect(lambda: self.wizard().observe("ltspice", force=True))
        row = QHBoxLayout()
        row.addWidget(QLabel("EXE", self))
        row.addWidget(self.path_edit, 1)
        row.addWidget(browse)
        self.body_area.addLayout(row)
        self.body_area.addWidget(self.run_button)
        self.path_edit.textChanged.connect(
            lambda text: self.wizard().set_answers({"ltspice_exe": text.strip()})
        )

    def _browse(self) -> None:
        chosen, _ = QFileDialog.getOpenFileName(self, "Locate LTspice", "", "LTspice (LTspice.exe)")
        if chosen:
            self.path_edit.setText(chosen)


class ProviderPage(RetroPage):
    def __init__(self, wizard: InstallerWizard) -> None:
        super().__init__(wizard, "provider", "STEP 3/5  PROVIDER")
        self.provider_box = QComboBox(self)
        self.provider_box.addItems(["fixture", "http_inference", "bob_direct"])
        self.provider_box.currentTextChanged.connect(
            lambda text: self.wizard().set_answers({"provider": text})
        )
        self.endpoint_edit = QLineEdit(self)
        self.endpoint_edit.setPlaceholderText("endpoint from vendor documentation")
        self.model_edit = QLineEdit(self)
        self.model_edit.setPlaceholderText("model id from vendor documentation")
        self.secret_edit = QLineEdit(self)
        self.secret_edit.setEchoMode(QLineEdit.EchoMode.Password)
        self.secret_edit.setPlaceholderText("API key (keyring only, never written to config)")
        store = QPushButton("STORE IN KEYRING", self)
        store.setObjectName("store-credential")
        store.clicked.connect(self._store)
        check = QPushButton("CHECK PROVIDER", self)
        check.clicked.connect(lambda: self.wizard().observe("provider", force=True))

        for widget, label in (
            (self.provider_box, "PROVIDER"),
            (self.endpoint_edit, "ENDPOINT"),
            (self.model_edit, "MODEL"),
            (self.secret_edit, "SECRET"),
        ):
            row = QHBoxLayout()
            row.addWidget(QLabel(label, self))
            row.addWidget(widget, 1)
            self.body_area.addLayout(row)
        buttons = QHBoxLayout()
        buttons.addWidget(store)
        buttons.addWidget(check)
        self.body_area.addLayout(buttons)

        self.endpoint_edit.textChanged.connect(
            lambda text: self.wizard().set_answers({"endpoint": text.strip()})
        )
        self.model_edit.textChanged.connect(
            lambda text: self.wizard().set_answers({"model": text.strip()})
        )

    def _store(self) -> None:
        self.wizard().set_answers({"api_key": self.secret_edit.text()})
        self.wizard().observe("provider", force=True)
        if self.wizard().credential_stored():
            self.secret_edit.clear()
            self.wizard().set_answers({"api_key": ""})


class PolicyPage(RetroPage):
    def __init__(self, wizard: InstallerWizard) -> None:
        super().__init__(wizard, "policy", "STEP 4/5  DATA POLICY")
        self.allow_remote = QCheckBox("ALLOW REMOTE INFERENCE", self)
        self.deny_unknown = QCheckBox("REFUSE UNKNOWN CLASSIFICATION", self)
        self.allow_bob_shell = QCheckBox("ALLOW BOB SHELL (non-interactive tools)", self)
        self.cache = QCheckBox("CACHE EXTRACTION BY HASH (offline repeats)", self)
        self.acknowledge = QCheckBox("I ACKNOWLEDGE THE PRIVACY TERMS ABOVE", self)
        for box in (self.allow_remote, self.deny_unknown, self.allow_bob_shell, self.cache):
            self.body_area.addWidget(box)
        self.body_area.addWidget(self.acknowledge)
        for box, key in (
            (self.allow_remote, "allow_remote"),
            (self.deny_unknown, "deny_unknown_classification"),
            (self.allow_bob_shell, "allow_bob_shell"),
            (self.cache, "cache_extraction"),
            (self.acknowledge, "acknowledge_privacy"),
        ):
            box.toggled.connect(
                lambda checked, name=key: self.wizard().set_answers({name: bool(checked)})
            )


class FinishPage(RetroPage):
    def __init__(self, wizard: InstallerWizard) -> None:
        super().__init__(wizard, "finish", "STEP 5/5  FINISH")
        self.commands = QPlainTextEdit(self)
        self.commands.setReadOnly(True)
        self.commands.setPlainText("\n".join(wizard.commands()))
        copy = QPushButton("COPY COMMANDS", self)
        copy.clicked.connect(self._copy)
        self.body_area.addWidget(self.commands, 1)
        self.body_area.addWidget(copy)

    def _copy(self) -> None:
        QApplication.clipboard().setText(self.commands.toPlainText())


class InstallerWizard(QWizard):
    """The retro setup wizard; drive it with :meth:`run_to_completion`."""

    def __init__(
        self,
        *,
        answers: Mapping[str, Any] | None = None,
        config: AppConfig | None = None,
        config_file: Path | None = None,
        smoke_runner: Any | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("BoardModeler setup")
        self.setWizardStyle(QWizard.WizardStyle.ClassicStyle)
        self.setOption(QWizard.WizardOption.NoBackButtonOnStartPage, True)
        self.setStyleSheet(RETRO_STYLESHEET)
        self.setButtonText(QWizard.WizardButton.NextButton, "NEXT >")
        self.setButtonText(QWizard.WizardButton.BackButton, "< BACK")
        self.setButtonText(QWizard.WizardButton.FinishButton, "FINISH")
        self.setButtonText(QWizard.WizardButton.CancelButton, "ABORT")
        self.setButtonText(QWizard.WizardButton.CustomButton1, "SKIP STEP")
        self.customButtonClicked.connect(self._on_custom_button)

        self._config_file = Path(config_file) if config_file is not None else config_path()
        self._base_config = config if config is not None else load_config(self._config_file)
        self._config = self._base_config
        self._smoke_runner = smoke_runner if smoke_runner is not None else smoke_test
        self._answers: dict[str, Any] = {
            "provider": "fixture",
            "allow_remote": False,
            "deny_unknown_classification": True,
            "allow_bob_shell": False,
            "cache_extraction": True,
            "acknowledge_privacy": False,
        }
        self._skip: list[str] = []
        self._observed: dict[str, str] = {}
        self._body: dict[str, list[str]] = {}
        self._credential_stored = False
        self._last_config_path = self._config_file
        self._outcome: InstallerOutcome | None = None
        self._pages: dict[str, RetroPage] = {}

        for page in (
            WelcomePage(self),
            LtspicePage(self),
            ProviderPage(self),
            PolicyPage(self),
            FinishPage(self),
        ):
            self._pages[page.step_id] = page
            self.addPage(page)
        if answers:
            self.set_answers(answers)
        self.restart()
        self.observe("welcome", force=True)

    # ------------------------------------------------------------------- pages

    def page_ids(self) -> list[str]:
        """Step ids in order (the test/CLI surface for the wizard's steps)."""
        return list(STEP_IDS)

    def step_number(self, step_id: str) -> int:
        return STEP_IDS.index(step_id) + 1

    def page_widget(self, step_id: str) -> RetroPage:
        return self._pages[step_id]

    # ----------------------------------------------------------------- answers

    def set_answers(self, answers: Mapping[str, Any]) -> None:
        """Apply an injected answer set (used by tests and by the widgets)."""
        for key, value in answers.items():
            if key == "skip":
                self._skip = [str(step) for step in value or []]
                continue
            self._answers[key] = value
        self._apply_answers()
        self._observed.clear()

    def answers(self) -> dict[str, Any]:
        return dict(self._answers)

    def skipped_steps(self) -> list[str]:
        """Steps the user marked as skipped so far."""
        return list(self._skip)

    def _apply_answers(self) -> None:
        welcome = self._pages["welcome"]
        if isinstance(welcome, WelcomePage):
            project = str(self._answers.get("project_dir") or "")
            if welcome.project_edit.text() != project:
                welcome.project_edit.setText(project)
        ltspice = self._pages["ltspice"]
        if isinstance(ltspice, LtspicePage):
            explicit = self._answers.get("ltspice_exe")
            if explicit is None:
                explicit = self._base_config.ltspice.path or ""
            if ltspice.path_edit.text() != str(explicit or ""):
                ltspice.path_edit.setText(str(explicit or ""))
        provider = self._pages["provider"]
        if isinstance(provider, ProviderPage):
            name = str(self._answers.get("provider", "fixture"))
            if provider.provider_box.currentText() != name:
                provider.provider_box.setCurrentText(name)
            for widget, key in (
                (provider.endpoint_edit, "endpoint"),
                (provider.model_edit, "model"),
            ):
                value = str(self._answers.get(key) or "")
                if widget.text() != value:
                    widget.setText(value)
        policy = self._pages["policy"]
        if isinstance(policy, PolicyPage):
            mapping = (
                (policy.allow_remote, "allow_remote"),
                (policy.deny_unknown, "deny_unknown_classification"),
                (policy.allow_bob_shell, "allow_bob_shell"),
                (policy.cache, "cache_extraction"),
                (policy.acknowledge, "acknowledge_privacy"),
            )
            for box, key in mapping:
                wanted = bool(self._answers.get(key, box.isChecked()))
                if box.isChecked() != wanted:
                    box.setChecked(wanted)

    # -------------------------------------------------------------- observation

    def body_lines(self, step_id: str) -> list[str]:
        if step_id in self._body:
            return list(self._body[step_id])
        return self._placeholder_body(step_id)

    def _placeholder_body(self, step_id: str) -> list[str]:
        if step_id == "ltspice":
            return [
                "> LTSPICE CHECK HAS NOT RUN IN THIS SESSION",
                "> PRESS [RUN SMOKE TEST] OR SKIP THIS STEP",
                "(nothing is reported as passing until it runs)",
            ]
        return [
            f"> {step_id.upper()} NOT CHECKED YET",
            "> PRESS NEXT, OR F2 TO SKIP THIS STEP",
        ]

    def observe(self, step_id: str, *, force: bool = False) -> str:
        """Run (or reuse) the check for ``step_id``; returns the observed detail."""
        if not force and step_id in self._observed:
            summary = self._observed[step_id]
            if step_id in ("welcome", "provider", "policy"):
                self._refresh_page(step_id)
            return summary
        observer = getattr(self, f"_observe_{step_id}")
        summary, lines = observer()
        self._observed[step_id] = summary
        self._body[step_id] = lines
        self._refresh_page(step_id)
        return summary

    def _refresh_page(self, step_id: str) -> None:
        page = self._pages.get(step_id)
        if page is not None:
            page.update()

    def _observe_welcome(self) -> tuple[str, list[str]]:
        project_dir = self._answers.get("project_dir") or ""
        config_file = self._config_file
        lines = [
            "> SCANNING ENVIRONMENT",
            f"CONFIG   {config_file} ({'PRESENT' if config_file.is_file() else 'NOT WRITTEN YET'})",
            f"PYTHON   {sys.version.split()[0]}",
        ]
        if project_dir:
            candidate = Path(str(project_dir))
            project_file = candidate / "project.json"
            if project_file.is_file():
                lines.append(f"PROJECT  {project_file} FOUND")
            elif candidate.is_dir():
                lines.append(f"PROJECT  {candidate} HAS NO project.json -> FAIL")
            else:
                lines.append(f"PROJECT  {candidate} DOES NOT EXIST -> FAIL")
        else:
            lines.append("PROJECT  (none selected; you can set one in the app)")
        try:
            from boardmodeler.documents.ocr import probe_ocr

            unavailable = probe_ocr()
            lines.append(
                "OCR      available"
                if unavailable is None
                else f"OCR      UNAVAILABLE ({unavailable.reason})"
            )
        except Exception as exc:  # OCR is optional; report, never hide
            lines.append(f"OCR      UNAVAILABLE ({type(exc).__name__})")
        outline = locate_outcome(self._answers.get("ltspice_exe") or self._base_config.ltspice.path)
        lines.append(
            f"LTSPICE  {outline.install.path} ({outline.reason})"
            if outline.install
            else f"LTSPICE  NOT FOUND ({outline.reason})"
        )
        summary = (
            f"config={config_file} present={config_file.is_file()}; "
            f"project={'set' if project_dir else 'unset'}; "
            f"ltspice={'found' if outline.install else 'not found'}"
        )
        return summary, lines

    def _observe_ltspice(self) -> tuple[str, list[str]]:
        explicit = self._answers.get("ltspice_exe") or self._base_config.ltspice.path
        outcome = locate_outcome(explicit)
        install = outcome.install
        if install is None:
            lines = [
                f"LTSPICE  NOT FOUND ({outcome.reason})",
                *(f"PROBED   {path}" for path in outcome.probed_paths[:3]),
                "SMOKE    NOT RUN - no executable, so nothing is claimed",
            ]
            summary = f"fail: LTspice not found ({outcome.reason}); smoke test not run"
            return summary, lines
        reported = ltspice_version(install.path)
        timeout_s = float(self._base_config.ltspice.timeout_s)
        workdir = Path(tempfile.mkdtemp(prefix="boardmodeler-installer-smoke-"))
        result = self._smoke_runner(install.path, workdir, timeout_s=timeout_s)
        measured = (
            f"{result.measured_v:.6f} V" if getattr(result, "measured_v", None) is not None else "n/a"
        )
        expected = getattr(result, "expected_v", None)
        expected_text = f"{expected:.6f} V" if expected is not None else "n/a"
        lines = [
            f"FOUND    {install.path} ({install.source})",
            f"VERSION  {reported or 'unknown'}",
            f"SMOKE    {str(result.status).upper()}",
            f"MEASURED {measured} (expected {expected_text})",
            f"DETAIL   {result.detail}",
        ]
        summary = f"{result.status}: {result.detail}"
        return summary, lines

    def _observe_provider(self) -> tuple[str, list[str]]:
        name = str(self._answers.get("provider", "fixture"))
        try:
            kind = _kind_for_name(name)
        except ValueError as exc:
            summary = f"fail: {exc}"
            return summary, [f"> {exc}", "PROVIDER NOT CHANGED - unknown provider name"]
        candidate = self._build_config()
        try:
            selection = select_provider(
                candidate,
                requested=kind,
                allow_bob_shell=bool(self._answers.get("allow_bob_shell", False)),
            )
            eligibility = selection.detail
        except ProviderError as exc:
            eligibility = f"{exc.code}: {exc.detail}"
        except Exception as exc:  # provider construction must not kill the wizard
            eligibility = f"{type(exc).__name__}: {exc}"

        secret = str(self._answers.get("api_key") or "")
        if secret:
            try:
                set_credential(name, secret)
            except Exception as exc:
                credential = f"NOT STORED ({type(exc).__name__}: {exc})"
            else:
                credential = f"stored in the OS keyring as provider:{name}:api_key"
                self._credential_stored = True
        else:
            credential = describe_credential(name)
        lines = [
            f"> PROVIDER {name} ({kind.value})",
            f"ENDPOINT {self._answers.get('endpoint') or '(none set)'}",
            f"MODEL    {self._answers.get('model') or '(none set)'}",
            f"CRED     {credential}",
            f"CHECK    {eligibility[:96]}",
        ]
        summary = f"provider={name} kind={kind.value}; credential={credential}; {eligibility}"
        return summary, lines

    def credential_stored(self) -> bool:
        return self._credential_stored

    def _observe_policy(self) -> tuple[str, list[str]]:
        acknowledged = bool(self._answers.get("acknowledge_privacy", False))
        allow_remote = bool(self._answers.get("allow_remote", False))
        if allow_remote and not acknowledged:
            allow_remote = False
            self._answers["allow_remote"] = False
            notes = "remote inference forced OFF: the privacy acknowledgement was not given"
        elif allow_remote:
            notes = "remote inference ON for permitted classifications only"
        else:
            notes = "remote inference OFF"
        lines = [
            f"> PRIVACY ACKNOWLEDGEMENT: {'GIVEN' if acknowledged else 'NOT GIVEN'}",
            f"REMOTE   {'ON' if allow_remote else 'OFF'}",
            "PERMITTED public, synthetic_fixture (never internal/confidential/unknown)",
            f"BOB SHELL {'ON' if self._answers.get('allow_bob_shell') else 'OFF'}",
            f"CACHE    {'ON' if self._answers.get('cache_extraction', True) else 'OFF'}",
            "TELEMETRY none; extraction cache makes repeat runs offline",
        ]
        summary = (
            f"acknowledged={acknowledged}; remote={'on' if allow_remote else 'off'}; "
            f"bob_shell={'on' if self._answers.get('allow_bob_shell') else 'off'}; {notes}"
        )
        return summary, lines

    def _observe_finish(self) -> tuple[str, list[str]]:
        path = self._last_config_path
        lines = [
            f"> CONFIG WRITTEN TO {path}",
            "COMMANDS TO RUN NEXT:",
            *[f"  {command}" for command in self.commands()],
        ]
        summary = f"configuration written to {path}"
        return summary, lines

    def commands(self) -> list[str]:
        """Exact commands a new user should run after the wizard."""
        project = str(self._answers.get("project_dir") or r"<project-dir>")
        return [
            "boardmodeler doctor --json",
            f'boardmodeler ui --project "{project}"',
            f'boardmodeler run tests --project "{project}"',
            "boardmodeler setup",
        ]

    # ------------------------------------------------------------------ config

    def _build_config(self) -> AppConfig:
        config = self._base_config.model_copy(deep=True)
        name = str(self._answers.get("provider", "fixture"))
        kind = _kind_for_name(name)
        entry = config.providers.get(name)
        if entry is not None:
            entry.kind = kind
            entry.endpoint = str(self._answers.get("endpoint") or "") or None
            entry.model = str(self._answers.get("model") or "") or None
            config.provider_order = [kind, *[k for k in config.provider_order if k is not kind]]
        config.data_policy.allow_remote = bool(self._answers.get("allow_remote", False))
        config.data_policy.deny_unknown_classification = bool(
            self._answers.get("deny_unknown_classification", True)
        )
        config.data_policy.allow_bob_shell = bool(self._answers.get("allow_bob_shell", False))
        config.data_policy.cache_extraction = bool(self._answers.get("cache_extraction", True))
        explicit = self._answers.get("ltspice_exe")
        if explicit:
            config.ltspice.path = str(explicit)
        return config

    def _finalize(self) -> Path:
        config = self._build_config()
        self._config = config
        self._last_config_path = save_config(config, self._config_file)
        return self._last_config_path

    # ------------------------------------------------------------ completion

    def run_to_completion(self) -> InstallerOutcome:
        """Walk every step with the injected answers and save the config."""
        steps: list[tuple[str, str]] = []
        skipped: list[str] = []
        failed = False
        for step_id in STEP_IDS:
            if step_id in self._skip:
                skipped.append(step_id)
                continue
            if step_id == "finish":
                continue
            try:
                steps.append((step_id, self.observe(step_id, force=True)))
            except Exception as exc:  # a step failure is an observation, not a crash
                steps.append((step_id, f"fail: {type(exc).__name__}: {exc}"))
                failed = True
        try:
            path = self._finalize()
        except Exception as exc:
            path = self._last_config_path
            steps.append(("finish", f"fail: cannot write {path}: {type(exc).__name__}: {exc}"))
            self._outcome = InstallerOutcome(
                completed=False, config_path=path, steps=steps, skipped=skipped
            )
            return self._outcome
        if "finish" not in skipped:
            steps.append(("finish", self.observe("finish", force=True)))
        self._outcome = InstallerOutcome(
            completed=not failed, config_path=path, steps=steps, skipped=skipped
        )
        return self._outcome

    def outcome(self) -> InstallerOutcome | None:
        return self._outcome

    # ------------------------------------------------------------- interaction

    def skip_current(self) -> None:
        """Mark the current step skipped and move on."""
        step_id = getattr(self.currentPage(), "step_id", None)
        if step_id is None:
            index = self.currentId()
            step_id = STEP_IDS[index] if 0 <= index < len(STEP_IDS) else None
        if step_id is not None and step_id not in self._skip:
            self._skip.append(step_id)
        if step_id == "finish":
            self.accept()
        else:
            self.next()

    def _on_custom_button(self, which: int) -> None:
        if which == QWizard.WizardButton.CustomButton1:
            self.skip_current()

    def accept(self) -> None:
        """Save the configuration before closing (the real final step)."""
        if self._outcome is None:
            self._outcome = self.run_to_completion()
        super().accept()


def outcome_payload(outcome: InstallerOutcome) -> dict[str, Any]:
    """JSON-safe view of an :class:`InstallerOutcome` for ``boardmodeler setup --json``."""
    return {
        "completed": outcome.completed,
        "config_path": str(outcome.config_path),
        "steps": [{"step": step, "observed": detail} for step, detail in outcome.steps],
        "skipped": list(outcome.skipped),
    }


def main(argv: Sequence[str] | None = None) -> int:
    """``boardmodeler setup`` / ``boardmodeler ui --installer`` entry point."""
    parser = argparse.ArgumentParser(
        prog="boardmodeler setup", description="Retro setup wizard (LTspice, provider, policy)"
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="walk every step with the default answers and print the observed outcome",
    )
    parser.add_argument("--project", type=Path, default=None, help="project directory to record")
    args = parser.parse_args(list(argv) if argv is not None else None)

    if args.json and not os.environ.get("QT_QPA_PLATFORM"):
        # nothing is displayed in --json mode, so do not require a display
        os.environ["QT_QPA_PLATFORM"] = "offscreen"

    from boardmodeler.ui.app import build_application

    build_application([sys.argv[0]])
    answers: dict[str, Any] | None = (
        {"project_dir": str(args.project)} if args.project is not None else None
    )
    wizard = InstallerWizard(answers=answers)
    if args.json:
        outcome = wizard.run_to_completion()
        print(json.dumps(outcome_payload(outcome), indent=2))
        return 0 if outcome.completed else 1
    return int(wizard.exec())


if __name__ == "__main__":  # pragma: no cover - manual launch
    raise SystemExit(main())
