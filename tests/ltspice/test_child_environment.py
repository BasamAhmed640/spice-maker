"""What the simulator process is allowed to know about this machine.

The owner's rule is that this application must never hand a Windows secret — or its
own agent key — to anything it launches. LTspice is the one third-party program that
runs on every build, so the boundary is enforced where the process is created:
:func:`boardmodeler.simulation.ltspice.child_environment` is an allowlist, and these
tests fail if it becomes a copy of the parent environment, if a credential-shaped
name slips through, or if the spawn stops using it.

The last test runs the real simulator with a poisoned parent environment, which is the
only way to show the containment did not also break the product.
"""

from __future__ import annotations

import ast
import os
from pathlib import Path

import pytest

from boardmodeler.simulation.ltspice import child_environment, run_batch
from boardmodeler.storage import app_root

_MODULE = Path(__file__).resolve().parents[2] / "src" / "boardmodeler" / "simulation" / "ltspice.py"


def test_the_allowlist_is_what_the_child_gets_and_nothing_else() -> None:
    parent = {
        "SystemRoot": r"C:\Windows",
        "PATH": r"C:\Windows\System32",
        "APPDATA": r"C:\Users\someone\AppData\Roaming",
        # Everything below is a variable this program's own environment really holds.
        "DEEPSEEK_API_KEY": "sk-do-not-leak",
        "OPENAI_API_KEY": "sk-do-not-leak",
        "BOARDMODELER_OPENCODE_API_KEY": "sk-do-not-leak",
        "LTSPICE_EXE": r"C:\somewhere\LTspice.exe",
        "PYTHONPATH": r"C:\somewhere\else",
        "SOME_UNRELATED_VAR": "1",
    }
    kept = child_environment(parent)
    assert kept["SystemRoot"] == r"C:\Windows"
    assert kept["PATH"] == r"C:\Windows\System32"
    assert "DEEPSEEK_API_KEY" not in kept
    assert "OPENAI_API_KEY" not in kept
    assert "BOARDMODELER_OPENCODE_API_KEY" not in kept
    assert "LTSPICE_EXE" not in kept
    assert "PYTHONPATH" not in kept
    assert "SOME_UNRELATED_VAR" not in kept


def test_no_kept_name_can_look_like_a_credential() -> None:
    """An allowlist edit is not enough to leak a secret: the name is checked too."""
    parent = {
        "SystemRoot": r"C:\Windows",
        "SOMETHING_KEY": "leak",
        "A_TOKEN": "leak",
        "MY_SECRET": "leak",
        "DB_PASSWORD": "leak",
        "AZURE_CREDENTIAL": "leak",
    }
    kept = child_environment(parent)
    # Only the OS variable and the private scratch names may survive: every
    # credential-shaped name is dropped even though the parent really holds it.
    assert set(kept) == {"SystemRoot", "TEMP", "TMP", "TMPDIR"}, kept
    assert not any("leak" in value for value in kept.values())


def test_scratch_space_is_inside_this_copy() -> None:
    """Simulator scratch files belong with the copy, not in the user's profile."""
    kept = child_environment({})
    for name in ("TEMP", "TMP", "TMPDIR"):
        assert Path(kept[name]).is_relative_to(app_root()), kept[name]


def test_the_case_of_an_os_variable_does_not_change_the_decision() -> None:
    """Windows environment names are case-insensitive; the allowlist must agree.

    ``os.environ`` on Windows folds case, so a hand-written mapping is the only way to
    ask whether ``SYSTEMROOT`` in the allowlist also admits ``SystemRoot`` — and whether
    the credential check still refuses ``DeepSeek_API_KEY``.
    """
    kept = child_environment({"SystemRoot": r"C:\Windows", "DeepSeek_API_KEY": "leak"})
    assert kept["SystemRoot"] == r"C:\Windows"
    assert not any("leak" in value for value in kept.values())
    assert not any("key" in name.lower() for name in kept)


def test_the_simulator_spawn_passes_the_scrubbed_environment() -> None:
    """A source guard: the one Popen in the simulator module must keep ``env=``.

    Asserted on the tree rather than by faking a process, because the claim is about
    the call site: a refactor that drops ``env=child_environment()`` would silently
    hand the agent key to LTspice while every behaviour test still passed.
    """
    tree = ast.parse(_MODULE.read_text(encoding="utf-8"))
    spawns = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and isinstance(node.func.value, ast.Name)
        and node.func.value.id == "subprocess"
        and node.func.attr in {"Popen", "run", "call", "check_output", "check_call"}
    ]
    assert spawns, "no subprocess call found; this guard would pass vacuously"
    envless = [node for node in spawns if "env" not in {keyword.arg for keyword in node.keywords}]
    assert not envless, [f"{_MODULE.name}:{node.lineno}" for node in envless]


@pytest.mark.ltspice
def test_a_real_run_succeeds_with_a_poisoned_parent_environment(
    ltspice_exe: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The product still works when the parent environment holds an agent key.

    The key is set for real (so it *would* be inherited by a naive spawn) and the deck
    is the smoke circuit: if the environment scrub broke a dependency LTspice needs,
    this run fails rather than passing quietly.
    """
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-poisoned-parent-environment")
    deck = tmp_path / "poisoned.cir"
    deck.write_text(
        "* poisoned environment\nV1 1 0 PULSE(0 1 0 1n 1n 1 2)\nR1 1 2 1k\nC1 2 0 1u\n.tran 5m\n.end\n",
        encoding="utf-8",
    )
    result = run_batch(ltspice_exe, deck, tmp_path, timeout_s=90.0)
    assert not result.timed_out, result.observed()
    assert result.exit_code == 0, (result.exit_code, result.observed())
    assert result.raw_path is not None and result.raw_path.is_file()
    combined = result.stdout + result.stderr
    assert "sk-poisoned-parent-environment" not in combined
    assert os.environ["DEEPSEEK_API_KEY"] == "sk-poisoned-parent-environment"
