"""The setup page: persistent settings only, and none of them leak.

These tests drive the real widgets and check what the page actually persists — the
config file it writes and the credential store it talks to — plus the property the owner
asked for explicitly: the page is sized to its content, with no dead space under it.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

pytest.importorskip("PySide6")
pytestmark = pytest.mark.gui

SECRET = "sk-sentinel-0123456789"


@pytest.fixture
def isolated_config(tmp_path: Path, monkeypatch):
    """Point every config read/write at a throwaway file."""
    target = tmp_path / "config.json"
    monkeypatch.setattr("boardmodeler.config.config_path", lambda: target)
    monkeypatch.setattr("boardmodeler.ui.setup_dialog.config_path", lambda: target)
    return target


@pytest.fixture
def dialog(qtbot, isolated_config):
    from boardmodeler.ui.setup_dialog import SetupDialog

    page = SetupDialog()
    qtbot.addWidget(page)
    page.show()
    qtbot.waitExposed(page)
    return page


def test_the_page_is_sized_to_its_content(dialog) -> None:
    """A page taller than its content is the dead space the owner objected to.

    The window is resizable now, so the page lives in a scroll area; the scroll area's own
    hint is a frame constant, so the content being asserted is the page's: it must open
    showing all of it, with no scrollbar.
    """
    assert dialog.size() == dialog.content_size()
    assert not dialog.scroll_area.horizontalScrollBar().isVisible()
    assert not dialog.scroll_area.verticalScrollBar().isVisible()


def test_saving_persists_only_the_declared_settings(dialog, isolated_config: Path) -> None:
    dialog.model_dir_edit.setText(str(isolated_config.parent / "models"))
    dialog.ltspice_edit.setText(r"C:\tools\LTspice.exe")
    dialog.internet_check.setChecked(False)

    dialog._save()

    saved = json.loads(isolated_config.read_text(encoding="utf-8"))
    assert saved["default_model_dir"] == str(isolated_config.parent / "models")
    assert saved["ltspice"]["path"] == r"C:\tools\LTspice.exe"
    assert saved["internet_access"] is False


def test_the_api_key_goes_to_the_credential_store_and_never_to_the_config(
    dialog, isolated_config: Path, monkeypatch
) -> None:
    stored: list[tuple[str, str]] = []
    monkeypatch.setattr(
        "boardmodeler.security.credentials.set_credential",
        lambda name, value: stored.append((name, value)),
    )
    monkeypatch.setattr(
        "boardmodeler.security.credentials.describe_credential",
        lambda name: "sourced from this folder's plain local file (not encrypted)",
    )

    dialog.key_edit.setText(SECRET)
    dialog._save_key()
    dialog._save()

    from boardmodeler import agent_providers

    assert stored == [(agent_providers.default_provider().credential, SECRET)]
    assert dialog.key_edit.text() == "", "the field must not keep the secret on screen"
    assert SECRET not in isolated_config.read_text(encoding="utf-8")


def test_settings_round_trip_through_the_config(dialog, isolated_config: Path) -> None:
    dialog.internet_check.setChecked(False)
    dialog._save()

    from boardmodeler.config import load_config

    reloaded = load_config(isolated_config)
    assert reloaded.internet_access is False


def test_the_page_has_exactly_one_switch_for_internet_access(dialog) -> None:
    """The owner asked for one box for internet access; a second one would be the bug.

    Enumerated from the widget tree rather than from attribute names, so a checkbox added
    without a name of its own is still seen. The one switch must be the INTERNET ACCESS
    box, and the per-build full-verification choice must not be a SETUP control at all:
    it belongs beside GO in the build window, where the build that uses it is made.
    """
    from PySide6.QtWidgets import QCheckBox

    boxes = dialog.findChildren(QCheckBox)
    assert [box.text() for box in boxes] == ["INTERNET ACCESS"], (
        "SETUP must hold exactly one checkbox, and it must be the network switch"
    )
    (network,) = boxes
    assert network is dialog.internet_check
    assert network.isChecked() is True, "the page opens with the stored value"

    governing = [
        box
        for box in boxes
        if any(
            word in box.text().lower()
            for word in ("internet", "network", "remote", "web", "reinforce")
        )
    ]
    assert len(governing) == 1, [box.text() for box in governing]
    assert not hasattr(dialog, "reinforce_check")
    assert not hasattr(dialog, "full_verification_check")
    assert not any("verification" in box.text().lower() for box in boxes)

    # The page's JSON view has one network key and no per-build verification key.
    from boardmodeler.ui.setup_dialog import describe_settings

    described = describe_settings(dialog._config)
    assert "full_verification" not in described
    assert described["internet_access"] is True

    # The one line beside it names both halves the switch governs.
    hint = dialog.internet_hint.text()
    assert "agent provider's API" in hint
    assert "part vendor's site" in hint and "supporting material" in hint


def test_the_smoke_test_is_run_and_reported(dialog, monkeypatch, tmp_path: Path) -> None:
    """RUN SMOKE TEST must call the real smoke test and show the measured value."""

    class _Install:
        path = tmp_path / "LTspice.exe"

    class _Result:
        status = "pass"
        measured_v = 0.6321192595944498
        detail = "RC step measured at 1 ms"

    calls: list[Path] = []

    def fake_smoke(exe: Path, workdir: Path):
        calls.append(Path(exe))
        return _Result()

    monkeypatch.setattr("boardmodeler.simulation.ltspice.locate", lambda explicit=None: _Install())
    monkeypatch.setattr("boardmodeler.simulation.ltspice.smoke_test", fake_smoke)

    dialog._run_smoke()

    assert calls, "the smoke test must actually be invoked"
    assert "0.6321" in dialog.ltspice_status.text()
    assert "pass" in dialog.ltspice_status.text().lower()


def test_a_missing_ltspice_is_reported_not_raised(dialog, monkeypatch) -> None:
    monkeypatch.setattr("boardmodeler.simulation.ltspice.locate", lambda explicit=None: None)
    dialog._run_smoke()
    assert "not found" in dialog.ltspice_status.text().lower()


def test_json_mode_describes_the_settings_without_a_window(isolated_config: Path) -> None:
    from boardmodeler.ui.setup_dialog import main

    assert main(["--json"]) == 0


def test_the_page_carries_only_persistent_settings(dialog) -> None:
    """No project directory, no provider selection, no policy acknowledgement."""
    labels = {
        child.text().strip()
        for child in dialog.findChildren(type(dialog.key_status))
        if isinstance(child.text(), str)
    }
    for gone in ("PROJECT", "PROVIDER", "DATA POLICY", "PRIVACY"):
        assert gone not in labels, f"{gone} does not belong on the settings page"


def test_the_ltspice_library_is_shown_read_only(dialog) -> None:
    from boardmodeler.ui.setup_dialog import ltspice_user_lib

    shown = [
        child.text()
        for child in dialog.findChildren(type(dialog.key_status))
        if str(ltspice_user_lib()) in child.text()
    ]
    assert shown, "the LTspice user library path must be visible"


def test_choosing_a_provider_points_the_key_row_at_its_own_credential(
    dialog, isolated_config: Path, monkeypatch
) -> None:
    """The key row follows the provider row: label, model default and stored name."""
    from boardmodeler import agent_providers

    stored: list[tuple[str, str]] = []
    monkeypatch.setattr(
        "boardmodeler.security.credentials.set_credential",
        lambda name, value: stored.append((name, value)),
    )
    entries = list(agent_providers.CATALOG)
    combo = dialog.provider_combo
    if combo is None:
        # A one-entry build has no row to choose from: that page is the sole entry's own.
        assert dialog.restricted_note is not None, "a single-provider build must say so"
        chosen = entries[0]
        assert dialog.key_label.text() == chosen.key_label
        assert dialog.model_edit.isVisible() is chosen.model_editable
    else:
        assert dialog.restricted_note is None, "a build with a choice must not claim restriction"
        ids = [combo.itemData(index) for index in range(combo.count())]
        assert ids == [entry.id for entry in entries], "one row per provider this build accepts"
        assert dialog.key_label.text() == entries[0].key_label
        assert dialog.model_edit.isVisible() is entries[0].model_editable

        chosen = entries[1]
        combo.setCurrentIndex(ids.index(chosen.id))
        assert dialog.key_label.text() == chosen.key_label
        assert dialog.model_edit.isVisible() is chosen.model_editable
        assert dialog.model_edit.text() == (chosen.model or ""), "the provider's own default"

    dialog.key_edit.setText(SECRET)
    dialog._save_key()
    dialog._save()

    assert stored == [(chosen.credential, SECRET)], "the key is stored under the chosen provider"
    saved = json.loads(isolated_config.read_text(encoding="utf-8"))
    assert saved["agent_provider"] == chosen.id
    if chosen.model_editable:
        assert saved["agent_model"] == dialog.model_edit.text()
    assert SECRET not in isolated_config.read_text(encoding="utf-8")


def test_an_unknown_provider_in_the_config_is_reported_never_replaced(
    isolated_config: Path,
) -> None:
    """A hand-edited config naming a provider this build lacks is refused, not laundered."""
    from boardmodeler import agent_providers
    from boardmodeler.config import load_config
    from boardmodeler.ui.setup_dialog import configured_provider, describe_settings

    config = load_config(isolated_config)
    config.agent_provider = "not-a-provider"

    provider, reason = configured_provider(config)

    assert provider is None, "a provider this build lacks must never be swapped for another"
    assert reason.startswith("api_provider_unavailable:") and "not-a-provider" in reason
    assert all(f"'{name}'" in reason for name in agent_providers.ids())

    described = describe_settings(config)
    assert described["agent_provider"] == "not-a-provider", "the configured id is kept as written"
    assert described["agent_provider_accepted"] is False


def test_a_config_naming_another_provider_is_repairable_from_setup(
    qtbot, isolated_config: Path, monkeypatch
) -> None:
    """One provider, and a config that names a provider this build lacks.

    SAVE must not substitute it on its own, so the page offers the provider it has; one
    click plus SAVE is what writes it — otherwise such a config would be unrepairable
    from the application, since there is no provider row to pick from.
    """
    from boardmodeler import agent_providers
    from boardmodeler.config import load_config, save_config

    # A one-entry catalog stands in for a restricted build; the general catalog itself
    # always offers a choice, so the single-provider page needs a switched-in catalog.
    only_entry = agent_providers.default_provider()
    monkeypatch.setattr(agent_providers, "CATALOG", (only_entry,))

    config = load_config()
    config.agent_provider = "another-build-provider"
    save_config(config)
    assert load_config().agent_provider == "another-build-provider"

    from boardmodeler.ui.setup_dialog import SetupDialog

    page = SetupDialog()
    qtbot.addWidget(page)

    assert page.provider_combo is None, "one catalog entry means no provider row"
    assert page.use_note is not None, "the page offers the provider this build uses"
    only = agent_providers.only_provider()
    assert only is not None and only.id == only_entry.id

    page.use_note.click()
    page._save()

    assert load_config().agent_provider == only_entry.id


# --------------------------------------------------------------------------- #
# LTspice on this page: configured, browsed or searched — never snooped


def _page(qtbot):
    """A freshly built setup page, with no ambient simulator path inherited."""
    from boardmodeler.ui.setup_dialog import SetupDialog

    page = SetupDialog()
    qtbot.addWidget(page)
    page.show()
    qtbot.waitExposed(page)
    return page


def test_opening_the_page_searches_nothing(qtbot, isolated_config: Path, monkeypatch) -> None:
    """Opening SETUP must ignore an inherited simulator setting."""
    monkeypatch.delenv("LTSPICE_EXE", raising=False)

    page = _page(qtbot)
    page._refresh_status()
    page._fit_to_content()
    assert page.ltspice_edit.text() == ""
    assert not hasattr(page, "find_ltspice_button")


def test_nothing_configured_says_so_and_asks_the_user(
    qtbot, isolated_config: Path, monkeypatch
) -> None:
    monkeypatch.delenv("LTSPICE_EXE", raising=False)

    page = _page(qtbot)

    shown = page.ltspice_status.text().lower()
    assert page.ltspice_edit.text() == ""
    assert "not set yet" in shown
    assert "browse" in shown, "the user must be told how to set it"
    assert "find" not in shown, "the app must not offer machine-wide discovery"


def test_a_saved_path_is_reported_as_saved_configuration(
    qtbot, isolated_config: Path, monkeypatch, tmp_path: Path
) -> None:
    """The other honest state: it came from the config file, not from a search."""
    from boardmodeler.config import AppConfig, LtspiceConfig, save_config

    monkeypatch.delenv("LTSPICE_EXE", raising=False)
    exe = tmp_path / "LTspice.exe"
    exe.write_bytes(b"test fixture")
    save_config(AppConfig(ltspice=LtspiceConfig(path=str(exe))), isolated_config)

    page = _page(qtbot)

    assert page.ltspice_edit.text() == str(exe)
    shown = page.ltspice_status.text().lower()
    assert "saved configuration" in shown
    assert "discovered automatically" not in shown and "find found" not in shown


def test_browse_fills_the_field_by_hand_and_saves_nothing(
    qtbot, isolated_config: Path, monkeypatch, tmp_path: Path
) -> None:
    monkeypatch.delenv("LTSPICE_EXE", raising=False)
    exe = tmp_path / "LTspice.exe"
    exe.write_bytes(b"test fixture")
    monkeypatch.setattr(
        "boardmodeler.ui.setup_dialog.QFileDialog.getOpenFileName",
        lambda *args, **kwargs: (str(exe), "LTspice (*.exe)"),
    )

    page = _page(qtbot)
    page.browse_ltspice_button.click()

    assert page.ltspice_edit.text() == str(exe)
    assert "browse" in page.ltspice_status.text().lower()
    assert not isolated_config.exists(), "BROWSE must not save either"


def test_saving_a_browsed_path_makes_it_the_configured_one(
    qtbot, isolated_config: Path, monkeypatch, tmp_path: Path
) -> None:
    """SAVE is what turns "found/chosen" into "configured", and it must say so."""
    monkeypatch.delenv("LTSPICE_EXE", raising=False)
    exe = tmp_path / "LTspice.exe"
    exe.write_bytes(b"test fixture")
    monkeypatch.setattr(
        "boardmodeler.ui.setup_dialog.QFileDialog.getOpenFileName",
        lambda *args, **kwargs: (str(exe), "LTspice (*.exe)"),
    )

    page = _page(qtbot)
    page.browse_ltspice_button.click()
    page.model_dir_edit.setText(str(tmp_path / "models"))
    page._save()

    assert json.loads(isolated_config.read_text(encoding="utf-8"))["ltspice"]["path"] == str(exe)
    assert "saved configuration" in page.ltspice_status.text().lower()


# --------------------------------------------------------------------------- #
# What BROWSE hands the file dialog: a folder, never the file it must pick.
#
# Qt's third argument is the starting *directory*. Handed a path that is not an existing
# directory — the LTspice.exe itself, or an install that has since been removed — the
# native dialog chooses a place of its own: in practice the process's current drive root,
# "Look in: C:\\", listing C:\\ where nothing is selectable, so Open cannot succeed.
# That is the reported bug, so every case below asserts on that argument.


def _record_dir(monkeypatch, seen: list[str]) -> None:
    """Stand in for both static dialogs, recording the starting directory each is given."""

    def record_dir(parent, caption, directory, *rest):
        seen.append(directory)
        return ""

    monkeypatch.setattr("boardmodeler.ui.setup_dialog.QFileDialog.getExistingDirectory", record_dir)


def test_browse_starts_in_the_folder_that_holds_the_configured_exe(
    qtbot, isolated_config: Path, monkeypatch, tmp_path: Path
) -> None:
    """The user's case: the field holds a full path to LTspice.exe that is there."""
    monkeypatch.delenv("LTSPICE_EXE", raising=False)
    exe = tmp_path / "Programs" / "ADI" / "LTspice" / "LTspice.exe"
    exe.parent.mkdir(parents=True)
    exe.write_bytes(b"test fixture")
    seen: list[str] = []

    def record_open(parent, caption, directory, filter):
        seen.append(directory)
        return "", ""

    monkeypatch.setattr("boardmodeler.ui.setup_dialog.QFileDialog.getOpenFileName", record_open)

    page = _page(qtbot)
    page.ltspice_edit.setText(str(exe))
    page.browse_ltspice_button.click()

    assert seen == [str(exe.parent)], "the folder holding the exe, never the exe itself"
    assert Path(seen[0]).is_dir()


def test_browse_starts_somewhere_usable_when_the_exe_is_gone(
    qtbot, isolated_config: Path, monkeypatch, tmp_path: Path
) -> None:
    """An uninstalled LTspice leaves a path behind: the dialog must not open at C:\\ for it."""
    monkeypatch.delenv("LTSPICE_EXE", raising=False)
    gone = tmp_path / "programs" / "ADI" / "LTspice" / "LTspice.exe"
    seen: list[str] = []

    def record_open(parent, caption, directory, filter):
        seen.append(directory)
        return "", ""

    monkeypatch.setattr("boardmodeler.ui.setup_dialog.QFileDialog.getOpenFileName", record_open)

    page = _page(qtbot)
    page.ltspice_edit.setText(str(gone))
    page.browse_ltspice_button.click()

    assert seen == [str(Path.home())], "nowhere better than home for a path that is gone"
    assert Path(seen[0]).is_dir()
    assert seen[0] != Path(seen[0]).anchor, "never the drive root the user reported"


def test_browse_always_hands_the_dialog_a_folder(
    qtbot, isolated_config: Path, monkeypatch, tmp_path: Path
) -> None:
    """Every shape the LTspice field can hold: empty, a folder, a file, a stale path."""
    monkeypatch.delenv("LTSPICE_EXE", raising=False)
    exe = tmp_path / "ADI" / "LTspice" / "LTspice.exe"
    exe.parent.mkdir(parents=True)
    exe.write_bytes(b"test fixture")
    empty_folder = tmp_path / "models"
    empty_folder.mkdir()
    stale = tmp_path / "LTspice.exe"  # the folder survives, the exe does not
    gone = tmp_path / "gone" / "LTspice.exe"  # nothing of that path is left
    passed: list[str] = []

    def record_open(parent, caption, directory, filter):
        passed.append(directory)
        return "", ""

    monkeypatch.setattr("boardmodeler.ui.setup_dialog.QFileDialog.getOpenFileName", record_open)
    page = _page(qtbot)

    cases = [
        ("", str(Path.home())),
        ("   ", str(Path.home())),
        (str(exe), str(exe.parent)),
        (f"  {exe}  ", str(exe.parent)),
        (str(exe.parent), str(exe.parent)),
        (str(exe.parent) + os.sep, str(exe.parent)),
        (str(stale), str(tmp_path)),
        (str(gone), str(Path.home())),  # nowhere on that path exists, so: home
    ]
    for value, expected in cases:
        page.ltspice_edit.setText(value)
        page.browse_ltspice_button.click()
        assert passed[-1] == expected, f"{value!r} must start in {expected!r}"
        assert Path(passed[-1]).is_dir(), f"{value!r} must start in a folder that exists"
        assert passed[-1] != Path(passed[-1]).anchor, f"{value!r} must not start at a drive root"


def test_choosing_a_model_folder_hands_the_dialog_a_folder_too(
    qtbot, isolated_config: Path, monkeypatch, tmp_path: Path
) -> None:
    """The model folder can be stale or clear as well; same rule, same argument."""
    folder = tmp_path / "models"
    folder.mkdir()
    stale = tmp_path / "models-old"
    gone = tmp_path / "deleted" / "models"
    seen: list[str] = []
    _record_dir(monkeypatch, seen)

    page = _page(qtbot)

    page.model_dir_edit.setText(str(folder))
    page._choose_model_dir()
    assert seen == [str(folder)], "a folder opens at itself"

    page.model_dir_edit.setText(str(stale))
    page._choose_model_dir()
    assert seen[-1] == str(tmp_path), "a stale folder opens at the one that survives"

    page.model_dir_edit.setText(str(gone))
    page._choose_model_dir()
    assert seen[-1] == str(Path.home()), "a path with nothing left opens at home"

    page.model_dir_edit.setText("")
    page._choose_model_dir()
    assert seen[-1] == str(Path.home()), "an empty field opens at home"
    assert all(Path(directory).is_dir() for directory in seen)
