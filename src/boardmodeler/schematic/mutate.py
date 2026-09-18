"""Copy-on-write circuit mutations (Phase 3 step 6).

A mutation never touches the project it was derived from: the project directory is
copied into a run variant directory, the edits are applied there, and
``modifications.json`` records every ``(file, path, old, new)`` triple so the
report can name exactly what was changed and the original can be proven
byte-identical afterwards.

Edits are addressed by a small locator language, so a mutation is data that can be
reviewed rather than a script that edits files:

```
connections.csv:U1.PG.net           # the net attached to a component pin
components.csv:U1.value             # a component field
project.json:configuration.ilim_mode
```

A locator that matches nothing, or matches more than one row, is an error: a
mutation that silently does nothing would make a fault-injection suite look like it
passed.
"""

from __future__ import annotations

import json
import shutil
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path

__all__ = [
    "MUTATORS",
    "EditSpec",
    "Mutation",
    "MutationError",
    "apply_edits",
    "fault_ids",
    "mutate_project",
]

CONNECTIONS = "circuit/connections.csv"
COMPONENTS = "circuit/components.csv"
PROJECT = "circuit/project.json"


class MutationError(RuntimeError):
    """Raised when an edit cannot be applied unambiguously."""


@dataclass(frozen=True)
class EditSpec:
    """One textual edit with its locator and the exact bytes it replaces."""

    file: str
    path: str
    old: str
    new: str
    note: str = ""

    def as_dict(self) -> dict[str, str]:
        return {
            "file": self.file,
            "path": self.path,
            "old": self.old,
            "new": self.new,
            "note": self.note,
        }


@dataclass(frozen=True)
class Mutation:
    """A named fault and the edits that inject it."""

    fault_id: str
    description: str
    edits: tuple[EditSpec, ...] = ()
    expected_detection: str = ""
    expected_status: str = "FAIL"
    metadata: Mapping[str, str] = field(default_factory=dict)

    def as_dict(self) -> dict[str, object]:
        return {
            "fault_id": self.fault_id,
            "description": self.description,
            "edits": [edit.as_dict() for edit in self.edits],
            "expected_detection": self.expected_detection,
            "expected_status": self.expected_status,
            "metadata": dict(self.metadata),
        }


# --------------------------------------------------------------------------- #
# locating


def _rows(text: str) -> tuple[list[str], list[list[str]]]:
    lines = [line for line in text.splitlines() if line.strip()]
    if not lines:
        raise MutationError("file is empty")
    header = [cell.strip() for cell in lines[0].split(",")]
    body = [[cell.strip() for cell in line.split(",")] for line in lines[1:]]
    return header, body


def _connection_edit(project_dir: Path, path: str, new: str) -> EditSpec:
    refdes, pin, field_name = path.split(".", 2)
    if field_name != "net":
        raise MutationError(f"connections locator supports '.net', got {field_name!r}")
    file = project_dir / CONNECTIONS
    header, body = _rows(file.read_text(encoding="utf-8"))
    matches = [row for row in body if row[0] == refdes and row[1] == pin]
    if len(matches) != 1:
        raise MutationError(f"{path}: expected exactly one row, found {len(matches)}")
    old = matches[0][header.index("net_name")]
    return EditSpec(
        file=CONNECTIONS,
        path=path,
        old=f"{refdes},{pin},{old}",
        new=f"{refdes},{pin},{new}",
        note=f"pin {refdes}.{pin} moved to net {new}",
    )


def _component_edit(project_dir: Path, path: str, new: str) -> EditSpec:
    refdes, field_name = path.split(".", 1)
    file = project_dir / COMPONENTS
    header, body = _rows(file.read_text(encoding="utf-8"))
    matches = [row for row in body if row[0] == refdes]
    if len(matches) != 1:
        raise MutationError(f"{path}: expected exactly one row, found {len(matches)}")
    row = matches[0]
    original = ",".join(row)
    row[header.index(field_name)] = new
    return EditSpec(
        file=COMPONENTS,
        path=path,
        old=original,
        new=",".join(row),
        note=f"{refdes}.{field_name} changed from {original.split(',')[-1]!r} to {new!r}",
    )


def _json_edit(project_dir: Path, path: str, new: str) -> EditSpec:
    file = project_dir / PROJECT
    data = json.loads(file.read_text(encoding="utf-8"))
    keys = path.split(".")
    node: object = data
    for key in keys[:-1]:
        if not isinstance(node, dict) or key not in node:
            raise MutationError(f"{path}: {key!r} is not present")
        node = node[key]
    if not isinstance(node, dict) or keys[-1] not in node:
        raise MutationError(f"{path}: {keys[-1]!r} is not present")
    old_value = node[keys[-1]]
    node[keys[-1]] = _coerce(new, old_value)
    return EditSpec(
        file=PROJECT,
        path=path,
        old=json.dumps({keys[-1]: old_value}),
        new=json.dumps({keys[-1]: node[keys[-1]]}),
        note=f"{path} changed from {old_value!r} to {node[keys[-1]]!r}",
    )


def _coerce(text: str, like: object) -> object:
    if isinstance(like, bool):
        return text.strip().lower() in ("1", "true", "yes")
    if isinstance(like, int):
        return int(float(text))
    if isinstance(like, float):
        return float(text)
    return text


def resolve_edit(project_dir: Path, request: str) -> EditSpec:
    """``<file>:<locator>=<value>`` → an :class:`EditSpec`.

    ``file`` is one of ``connections.csv``, ``components.csv``, ``project.json``.
    """
    if "=" not in request:
        raise MutationError(f"edit request {request!r} must be '<file>:<locator>=<value>'")
    target, _, value = request.partition("=")
    file_name, _, locator = target.partition(":")
    if file_name == "connections.csv":
        return _connection_edit(project_dir, locator, value)
    if file_name == "components.csv":
        return _component_edit(project_dir, locator, value)
    if file_name == "project.json":
        return _json_edit(project_dir, locator, value)
    raise MutationError(f"unknown file {file_name!r} in {request!r}")


# --------------------------------------------------------------------------- #
# applying


def apply_edits(
    project_dir: Path, edits: Sequence[EditSpec], out_dir: Path, *, fault_id: str = "manual"
) -> Path:
    """Copy ``project_dir`` to ``out_dir``, apply ``edits`` there, record them."""
    project_dir = Path(project_dir)
    out_dir = Path(out_dir)
    if out_dir.exists():
        shutil.rmtree(out_dir)
    shutil.copytree(project_dir, out_dir)

    per_file: dict[str, list[EditSpec]] = {}
    for edit in edits:
        per_file.setdefault(edit.file, []).append(edit)

    for relative, file_edits in per_file.items():
        target = out_dir / relative
        if not target.is_file():
            raise MutationError(f"{relative} is not present in the copied project")
        text = target.read_text(encoding="utf-8")
        for edit in file_edits:
            if edit.file.endswith(".json"):
                # JSON edits are recorded as a key/value delta; apply them through
                # the same locator so nothing is rewritten by hand.
                data = json.loads(text)
                keys = edit.path.split(".")
                node = data
                for key in keys[:-1]:
                    node = node[key]
                node[keys[-1]] = json.loads(edit.new)[keys[-1]]
                if not text.endswith("\n"):
                    text += "\n"
                text = json.dumps(data, indent=2) + "\n"
                continue
            if edit.old not in text:
                raise MutationError(
                    f"{relative}: the text to replace for {edit.path!r} is not present "
                    f"({edit.old!r})"
                )
            if text.count(edit.old) != 1:
                raise MutationError(
                    f"{relative}: {edit.path!r} matched {text.count(edit.old)} lines; "
                    "a mutation must be unambiguous"
                )
            text = text.replace(edit.old, edit.new)
        target.write_text(text, encoding="utf-8")

    payload = {
        "fault_id": fault_id,
        "edits": [edit.as_dict() for edit in edits],
        "files_originals": {
            relative: str((project_dir / relative).resolve()) for relative in per_file
        },
    }
    (out_dir / "modifications.json").write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return out_dir


# --------------------------------------------------------------------------- #
# the fault catalogue


def mutate_project(project_dir: Path, fault_id: str, out_dir: Path) -> Mutation:
    """Apply the named fault into ``out_dir`` and return what was changed."""
    if fault_id not in MUTATORS:
        raise MutationError(f"unknown fault {fault_id!r}; known: {sorted(MUTATORS)}")
    mutation = MUTATORS[fault_id](Path(project_dir))
    apply_edits(project_dir, mutation.edits, out_dir, fault_id=fault_id)
    return mutation


def fault_ids() -> tuple[str, ...]:
    """The fault catalogue, in a stable order."""
    return tuple(MUTATORS)


def _mutator(
    fault_id: str,
    description: str,
    requests: Sequence[str],
    *,
    detection: str,
    status: str = "FAIL",
    metadata: Mapping[str, str] | None = None,
) -> Callable[[Path], Mutation]:
    def _build(project_dir: Path) -> Mutation:
        edits = tuple(resolve_edit(project_dir, request) for request in requests)
        return Mutation(
            fault_id=fault_id,
            description=description,
            edits=edits,
            expected_detection=detection,
            expected_status=status,
            metadata=dict(metadata or {}),
        )

    return _build


MUTATORS: dict[str, Callable[[Path], Mutation]] = {
    "swap_straps": _mutator(
        "swap_straps",
        "Swap the CONFIG0 and CONFIG2 strap pulls so the sampled strap word changes.",
        [
            "connections.csv:U5.CONFIG0.net=CONFIG1",
            "connections.csv:U5.CONFIG1.net=CONFIG0",
        ],
        detection="strap_connection",
    ),
    "en_invert": _mutator(
        "en_invert",
        "Tie the buck enable to ground so the rail never comes up.",
        ["connections.csv:U1.EN.net=GND"],
        detection="rail_never_valid",
    ),
    "missing_pullup": _mutator(
        "missing_pullup",
        "Remove the power-good pull-up so the open-drain signal can never reach its idle level.",
        ["connections.csv:R7.A.net=NC_PG_3V3"],
        detection="open_drain_level",
    ),
    "pullup_wrong_domain": _mutator(
        "pullup_wrong_domain",
        "Move the sideband pull-up from 3V3 to 1V8: a connection error even if the link works.",
        ["connections.csv:R17.B.net=1V8"],
        detection="sideband_level",
    ),
    "early_reset_release": _mutator(
        "early_reset_release",
        "Release PERST# immediately by tying the reset pull-up to the input rail.",
        ["connections.csv:R12.B.net=12V"],
        detection="reset_pullup_domain",
    ),
    "slow_rail_u2": _mutator(
        "slow_rail_u2",
        "Overload the 1V8 rail so it cannot reach its window in time.",
        ["project.json:loads.I2=1.5"],
        detection="rail_never_valid",
        metadata={
            "note": "the declared 1V8 nominal load is what the deck applies (project.json loads)"
        },
    ),
    "missing_pg": _mutator(
        "missing_pg",
        "Leave the buck power-good net undriven so the dependent LDO never enables.",
        ["connections.csv:U2.EN.net=NC_EN_U2"],
        detection="sequencing_never_completes",
    ),
    "invalid_strap": _mutator(
        "invalid_strap",
        "Pull CONFIG1 high as well, producing the undocumented 111 strap word.",
        ["connections.csv:R14.A.net=1V8"],
        detection="strap_word_invalid",
    ),
    "break_sideband": _mutator(
        "break_sideband",
        "Remove the SMB_DAT pull-up so the sideband line cannot idle high.",
        ["connections.csv:R17.A.net=NC_SMB_DAT"],
        detection="open_drain_level",
    ),
    "release_reset_early": _mutator(
        "release_reset_early",
        "Short the reset release delay so PERST# rises with the first rail.",
        ["project.json:timing.pg_delay_s=1e-06"],
        detection="reset_release_too_early",
    ),
    "remove_rail": _mutator(
        "remove_rail",
        "Disconnect the 1V8 rail from its regulator output.",
        ["connections.csv:C3.1.net=NC_1V8"],
        detection="SC009_supply_domain_assignment",
    ),
    "delay_rail": _mutator(
        "delay_rail",
        "Delay the 1V8 enable well beyond its sequencing window.",
        ["connections.csv:U2.EN.net=PG_3V3_DELAYED"],
        detection="sequencing_never_completes",
    ),
}
