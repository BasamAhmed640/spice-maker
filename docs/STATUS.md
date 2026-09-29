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

- The runtime lock exports eight pinned packages with SHA-256 hashes. `uv.lock` lists Windows x64 and ARM64 wheels for numpy, pydantic-core, and pypdfium2.
- On Windows 11 x64, a committed source ZIP extracted under a path with spaces, `é`, and `Ω` installed the eight runtime wheels, passed a real LTspice smoke test and `doctor`, and launched the text menu. The menu and flag command both blocked an LM358 build before any provider request while Internet access was off.
- The focused suite passed 171 tests; Ruff lint and format checks passed. The broader suite still includes baseline failures and missing ignored test data, so it is not described as green.
- Stubbed no-Python, installer-hash, and unsupported-architecture tests passed. Archive checks confirmed plain failures for a tampered package hash, dead proxy, too-long path, and wrong LTspice path.
- Both Python 3.14.7 installers were downloaded and hash/signature verified, but the real install step has not been run locally. Fresh GitHub Actions x64/ARM64 installer checks are pending after push.

Exact commands, output, and untested cases are in [`docs/evidence/2026-09-28-terminal/REPORT.md`](evidence/2026-09-28-terminal/REPORT.md). No live AI authoring call has been made for this change.
