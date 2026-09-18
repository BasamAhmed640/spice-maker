# BoardModeler

Datasheet-grounded LTspice model acquisition/generation plus schematic-level bring-up verification.

Given datasheets (plus optional part identity) BoardModeler produces a grounded LTspice model —
symbol, example schematic, executable tests, evidence, limitations — and separately checks a supplied
circuit's electrical/timing behaviour, including schematic connection mistakes.

Statuses are always one of `PASS`, `FAIL`, `UNKNOWN`, `BLOCKED`, `NOT_APPLICABLE`. Nothing is ever
reported as a run, citation, or approval that was not observed.

## Setup

```powershell
uv sync --all-extras
uv run boardmodeler doctor --json
uv run pytest -q
```

## Layout

|Path|Contents|
|---|---|
|`src/boardmodeler/domain/`|Record schemas (pydantic), enums, hashing, ids, constrained expression AST|
|`src/boardmodeler/simulation/`|LTspice batch invocation, log parsing, `.raw` readers, backend selection|
|`src/boardmodeler/verification/`|Assertion evaluation, vacuous-pass guards, corners, scenarios, engine|
|`src/boardmodeler/schematic/`|`.asc` parse/generate, SPICE netlist parsing, neutral CSV model, static checks, mutations|
|`src/boardmodeler/models/`|Vendor-original store, type-B behavioral templates, capability probes, symbol generation|
|`src/boardmodeler/requirements/`|Requirement extraction, validation, source-consistency review|
|`src/boardmodeler/documents/`|PDF text/page extraction, document store, OCR interface, chunking|
|`src/boardmodeler/providers/`|Fixture / HTTP inference / Bob providers behind one protocol|
|`src/boardmodeler/pipeline/`|Stage chain, baseline freeze, bounded repair, child-process worker|
|`src/boardmodeler/reporting/`|Export, model card, HTML report|
|`src/boardmodeler/security/`|Credentials, path guards, subprocess guard, data policy|
|`src/boardmodeler/ui/`|PySide6 desktop application (thin client over the same pipeline)|
|`fixtures/`|Committed test fixtures (synthetic device contract, demo board, fault matrix)|
|`docs/`|`PLAN.md`, `STATUS.md`, `DECISIONS.md`|

## Rules

See `AGENTS.md`.
