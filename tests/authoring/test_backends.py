"""The offline scripted author: turn order, naming, and construction validation.

The Bob Shell backend tests that used to live here are Bob-only and belong to the
Bob edition, which owns its copy of this module. This edition ships no CLI
backend, so what remains here is the offline author it does ship.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, cast

import pytest

from boardmodeler.authoring.backends import AuthorRequest, ScriptedBackend

PROMPT = "author a model for U1"


def request(tmp_path: Path, *, max_turns: int = 5) -> AuthorRequest:
    return AuthorRequest(
        prompt=PROMPT,
        workdir=tmp_path,
        model_dir=tmp_path / "model",
        max_turns=max_turns,
    )


def test_scripted_backend_plays_turns_in_order(tmp_path: Path) -> None:
    seen: list[tuple[int, Path, str]] = []

    def script(turn: int, workdir: Path, prompt: str) -> None:
        seen.append((turn, workdir, prompt))
        (workdir / "model").mkdir(parents=True, exist_ok=True)
        (workdir / "model" / "U1.lib").write_text(f"# turn {turn}", encoding="utf-8")

    backend = ScriptedBackend(script)
    assert backend.availability() == (True, "scripted backend")
    assert backend.name == "scripted"

    first = backend.author(request(tmp_path))
    second = backend.author(request(tmp_path))

    assert [turn for turn, _, _ in seen] == [1, 2]
    assert [prompt for _, _, prompt in seen] == [PROMPT, PROMPT]
    assert first.ok and second.ok
    assert first.session_id == "scripted-1"
    assert second.session_id == "scripted-2"
    assert (tmp_path / "model" / "U1.lib").read_text(encoding="utf-8") == "# turn 2"


def test_scripted_backend_rejects_a_non_callable_script() -> None:
    with pytest.raises(TypeError, match="callable"):
        ScriptedBackend(cast(Any, "not a script"))
