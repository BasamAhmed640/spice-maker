"""Application entry point tests: ``boardmodeler ui`` / ``--installer``."""

from __future__ import annotations

from pathlib import Path

import pytest

from boardmodeler.ui.app import build_application, main

pytestmark = pytest.mark.gui


def test_build_application_is_a_singleton(qapp) -> None:
    app = build_application(["boardmodeler"])
    assert app is build_application(["boardmodeler"])
    assert app.applicationName() == "Spice Maker"


def test_main_opens_the_window_for_a_project(qapp, tmp_path: Path) -> None:
    from boardmodeler.pipeline.project import create_project

    project = create_project(tmp_path / "proj", project_id="P1", name="Entry point")
    assert main(["--project", str(project.root)], exec_app=False) == 0


def test_main_with_installer_flag_builds_the_wizard(qapp) -> None:
    assert main(["--installer"], exec_app=False) == 0
