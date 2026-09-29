# Terminal transition verification — general edition

Date: 2026-09-28. Branch: `terminal-app`. Base: `main` at `b1ced1c`.

## Scope and integrity

The Qt window, frozen installer, their tests, and installer-only tools were removed. `Setup.cmd`, `Start.cmd`, `Boardmodeler.cmd`, a standard-library bootstrap, and a text setup wizard/menu were added. The existing model engine was not edited: all 64 protected/general engine files, including `pipeline/make_model.py`, compare byte-for-byte with the base. `uv run python tools/shared_core.py --check` returned `shared core intact: 44 files`; `--compare ..\spice-maker-bob-terminal` returned `identical: 44 files`.

The original source checkout with uncommitted buck work was not modified. This work used a fresh clone. No live AI call or real Python installer was run on the developer PC.

## Pin evidence

`Get-FileHash requirements.txt -Algorithm SHA256`:

```text
4AAC02951F71F13E1A637EE51F54AB41F5D3D67C3EB53B8D68F0454616A28DA2
```

`Get-FileHash tools\python-install-pins.txt -Algorithm SHA256`:

```text
DA0DA1A9670AE49E219C83647D8D84D6EA8CFD9457B5A2D1BE0F627F3775E8E2
```

Both Python 3.14.7 installers were downloaded, not run locally, and their SHA-256 and Authenticode signatures matched the pins. The x64 installer is 33,258,168 bytes and SHA-256 `9d9eb2709ef81bf5cd30db3c2096bdbc4ea10087c22e62f27d356b36f6ae9649`; ARM64 is 32,570,072 bytes and SHA-256 `9a3fe120cc81bc2cb099550f794d8356811f96a86c7f438519243c3485db928d`. The pinned hashes match `winget show Python.Python.3.14` for 3.14.7. The GitHub Actions workflow runs each installer on a fresh runner after push; results must be checked separately.

## Test commands and observed output

Baseline fresh clone: `uv run pytest -q` returned `1610 passed, 110 failed, 148 skipped, 8 errors`; the eight errors were caused by missing ignored `models/T1-tps54332/spec/requirements.json`. Baseline `ruff check .` had two historical evidence errors, and `ruff format --check .` flagged seven existing files.

Current focused suite:

```text
uv run pytest -q tests/setup tests/test_terminal_menu.py tests/test_cli_model_only_scope.py tests/test_portable_storage.py tests/test_edition_bob_absent.py tests/e2e/test_reopen_model.py tests/security/test_execution.py
171 passed in 25.99s
uv run ruff check .
All checks passed!
uv run ruff format --check .
265 files already formatted
```

The wider network-disabled run, `BOARDMODELER_NO_NETWORK=1; uv run pytest -q -m "not network and not slow"`, returned `1629 passed, 107 failed, 11 skipped, 1 deselected, 8 errors`. It remains non-green for the pre-existing missing ignored fixture and tests that assume configured network/LTspice state; it is not reported as a clean full-suite pass.

`git archive --format=zip HEAD` was extracted without `.git` under `Spice Maker café Ω general`. On Windows 11 x64 with registered CPython 3.14.2, this command passed without installing Python:

```text
Setup.cmd --no-install-python --yes --ltspice <existing LTspice.exe> --model-dir models --provider openai --internet off --key-env SPICE_MAKER_TEST_KEY --shortcut yes --shortcut-dir <temporary shortcut folder>
Successfully installed annotated-types-0.8.0 numpy-2.5.3 pydantic-2.13.5 pydantic-core-2.46.5 pypdf-6.19.0 pypdfium2-5.13.0 typing-extensions-4.16.0 typing-inspection-0.4.4
LTspice smoke test passed.
Settings saved in this extracted copy.
```

The archive `.venv` held exactly those eight runtime packages plus pip; no Qt package was present. `Boardmodeler.cmd doctor --json` returned `ok: true` and LTspice smoke `pass`. Piped `4`, `5` into `Start.cmd` printed `Setup check: ready`. A second Setup run refreshed the existing environment. The final Unicode-safe shortcut creation printed `Shortcut ready` with no warning; its `.lnk` contained the exact UTF-16 path to `Start.cmd`, including `Ω`. `Setup.cmd --remove --shortcut-dir <temporary shortcut folder>` removed the shortcut and marker, both confirmed absent.

With Internet disabled, piping `1`, `LM358`, its real datasheet path, and `5` into `Start.cmd` reached the unchanged authoring handler and returned `BLOCKED` with `internet_access_off`. The matching flag command returned `status: BLOCKED` and zero requirement rows. There is no bundled or cached LM358 author, so a completed LM358 build and full menu/flag row parity would require a paid live provider call and were not run.

Hostile-path archive checks: a tampered requirements hash exited 1 before any runtime package was installed (`pip list` showed only pip); a dead proxy exited 1 before package installation with a plain setup explanation; a 206-character path exited 1 before Python discovery; a wrong LTspice path exited 1, left `data/config.json` absent, and did not search for LTspice. The Windows launcher test matrix passed 14 tests, including absent Python, declined or empty input, wrong installer hash, stub re-detection, unsupported architecture, and the Store/MSYS2 decoys.

`doctor --json` and `setup --json` key sets match the base. Safe blocked build/test/open JSON key sets and exits match the base. The requested setup flags were added; `ui` and `--installer` were removed. Source `.cmd` files in the archive have CRLF endings.

## Remaining verification

- The real Python install step has not run on a clean PC in this local test. The x64 and ARM64 GitHub Actions runner results must be recorded after push.
- Windows 10 and ARM64 setup, and a machine without Python, LTspice, and developer tools, have not been tested end to end locally.
- A live provider key check and a completed LM358 model build were not run; they would make external requests.
- The repository still declares `Proprietary` in `pyproject.toml`. Publication license terms are a question for the owner; no license was chosen here.

## Change size

The initial code/documentation transition commit reported `126 files changed, 5511 insertions(+), 19934 deletions(-)` from `git show --shortstat`. Final branch diff statistics are recorded in the closing verification update after all fixes.
