"""The text menu preserves the CLI's build and reopen paths."""

from __future__ import annotations

from boardmodeler import cli, terminal_menu
from boardmodeler.config import AppConfig


def _reader(*answers: str):
    values = iter(answers)
    return lambda _prompt: next(values)


def test_no_arguments_open_the_menu(monkeypatch):
    monkeypatch.setattr("boardmodeler.storage.initialize", lambda: None)
    monkeypatch.setattr("boardmodeler.storage.install_write_guard", lambda: None)
    monkeypatch.setattr(terminal_menu, "run_menu", lambda: 23)

    assert cli.main([]) == 23


def test_menu_refuses_a_build_before_setup(monkeypatch, capsys):
    monkeypatch.setattr(terminal_menu, "load_config", lambda: AppConfig(setup_complete=False))

    def forbidden(_arguments):
        raise AssertionError("the engine must not run before setup")

    assert terminal_menu.run_menu(reader=_reader("1", "5"), command=forbidden) == 1
    assert "Setup is unfinished" in capsys.readouterr().out


def test_menu_build_accepts_dragged_quoted_pdf_and_uses_existing_handler(monkeypatch, tmp_path):
    datasheet = tmp_path / "a datasheet.pdf"
    datasheet.write_bytes(b"%PDF-1.4\n")
    base = tmp_path / "finished models"
    monkeypatch.setattr(
        terminal_menu,
        "load_config",
        lambda: AppConfig(setup_complete=True, default_model_dir=str(base), internet_access=True),
    )
    commands = []

    def command(arguments):
        commands.append(arguments)
        return 0

    assert (
        terminal_menu.run_menu(reader=_reader("1", "LM358", f'"{datasheet}"', "5"), command=command)
        == 0
    )
    assert commands == [
        [
            "model",
            "build",
            "--part",
            "LM358",
            "--datasheet",
            str(datasheet),
            "--out",
            str(base / "LM358"),
            "--allow-remote",
        ]
    ]


def test_menu_does_not_allow_remote_when_internet_is_off(monkeypatch, tmp_path):
    datasheet = tmp_path / "local.pdf"
    datasheet.write_bytes(b"%PDF-1.4\n")
    monkeypatch.setattr(
        terminal_menu,
        "load_config",
        lambda: AppConfig(setup_complete=True, internet_access=False),
    )
    commands = []
    terminal_menu.run_menu(
        reader=_reader("1", "LM358", str(datasheet), "5"),
        command=lambda arguments: commands.append(arguments) or 0,
    )
    assert "--allow-remote" not in commands[0]


def test_menu_retest_uses_model_open_verification(monkeypatch, tmp_path):
    base = tmp_path / "models"
    saved = base / "LM358"
    saved.mkdir(parents=True)
    monkeypatch.setattr(
        terminal_menu,
        "load_config",
        lambda: AppConfig(default_model_dir=str(base)),
    )
    commands = []
    assert (
        terminal_menu.run_menu(
            reader=_reader("2", "1", "t", "5"),
            command=lambda arguments: commands.append(arguments) or 0,
        )
        == 0
    )
    assert commands == [["model", "open", "--out", str(saved), "--verify"]]


def test_menu_check_setup_reports_an_unready_doctor(monkeypatch, capsys):
    monkeypatch.setattr(cli, "doctor_payload", lambda: {"ok": False})
    monkeypatch.setattr(cli, "_render_doctor_human", lambda _payload: "LTspice is not configured")

    assert terminal_menu.run_menu(reader=_reader("4", "5"), command=lambda _args: 0) == 1
    assert "Setup check: needs attention" in capsys.readouterr().out


def test_menu_check_setup_requires_a_completed_wizard(monkeypatch, capsys):
    monkeypatch.setattr(cli, "doctor_payload", lambda: {"ok": True})
    monkeypatch.setattr(cli, "_render_doctor_human", lambda _payload: "LTspice smoke test passed")
    monkeypatch.setattr(terminal_menu, "load_config", lambda: AppConfig(setup_complete=False))

    assert terminal_menu.run_menu(reader=_reader("4", "5"), command=lambda _args: 0) == 1
    assert "wizard has not been completed" in capsys.readouterr().out


def test_setup_flags_forward_to_the_wizard(monkeypatch):
    from boardmodeler import setup_wizard

    monkeypatch.setattr("boardmodeler.storage.initialize", lambda: None)
    monkeypatch.setattr("boardmodeler.storage.install_write_guard", lambda: None)
    forwarded = []

    def wizard(arguments):
        forwarded.append(arguments)
        return 0

    monkeypatch.setattr(setup_wizard, "main", wizard)
    assert (
        cli.main(
            [
                "setup",
                "--ltspice",
                "C:/LTspice.exe",
                "--model-dir",
                "models",
                "--provider",
                "deepseek",
                "--internet",
                "off",
                "--key-env",
                "TEST_KEY",
                "--yes",
            ]
        )
        == 0
    )
    assert forwarded == [
        [
            "--ltspice",
            "C:/LTspice.exe",
            "--model-dir",
            "models",
            "--provider",
            "deepseek",
            "--internet",
            "off",
            "--key-env",
            "TEST_KEY",
            "--yes",
        ]
    ]
