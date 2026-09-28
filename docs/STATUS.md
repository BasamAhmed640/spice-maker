# Current status — 2026-09-28 terminal transition

The active product is being changed from a desktop window and compiled installer to a source ZIP with `Setup.cmd`, one `.venv`, and a text menu. This file records current observations. Earlier release history is preserved verbatim in [`docs/evidence/2026-09-28-terminal/history/STATUS-before-terminal.md`](evidence/2026-09-28-terminal/history/STATUS-before-terminal.md).

## Baseline from fresh clones

| Edition | Command | Observed result |
| --- | --- | --- |
| General, `main` b1ced1c | `uv run pytest -q` | 1,610 passed, 110 failed, 148 skipped, 8 errors in 125.77 s. |
| General, `main` b1ced1c | `ruff check .` | 2 errors in historical evidence files. |
| General, `main` b1ced1c | `ruff format --check .` | 7 existing files needed formatting. |
| Bob, `main` cf6b430 | `.venv\Scripts\python.exe -m pytest -q` | 1,368 passed, 55 failed, 156 skipped, 8 errors in 80.53 s without `LTSPICE_EXE`. |
| Bob, `main` cf6b430 | Same command with `LTSPICE_EXE` set to the local LTspice executable | 1,369 passed, 55 failed, 155 skipped, 8 errors in 78.35 s. |
| Bob, `main` cf6b430 | `.venv\Scripts\ruff.exe check .` | Passed. |

These are pre-refactor results. Both fresh clones lack the ignored `models/T1-tps54332/spec/requirements.json`, which accounts for the eight errors in each edition. Several existing tests assume configured Internet access or an already configured LTspice path; a fresh clone has neither, so they fail before reaching their mocked provider path. These failures must not be reported as regressions from the terminal change.

## Implementation observations

- The general edition's runtime lock now exports eight pinned packages with SHA-256 hashes. `uv.lock` lists Windows x64 and ARM64 wheels for numpy, pydantic-core, and pypdfium2.
- On the Windows 11 x64 developer machine, the new local bootstrap installed those eight wheels with hashes, then an LTspice smoke test and doctor completed successfully. The clean Windows machine path and Python installer path have not been run yet.
- Post-change full test, format, source ZIP, shortcut, no-Python, offline, long-path, and ARM64 results are pending. Their exact commands and output belong in [`docs/evidence/2026-09-28-terminal/REPORT.md`](evidence/2026-09-28-terminal/REPORT.md) once observed.

No live AI authoring call has been made for this change.
