# INTERFACES

Frozen contracts between modules that are implemented in parallel. This file is
the authority: if code disagrees with it, the code is wrong. Changes are
deliberate and must be recorded here in the same commit.

## 1. Pipeline controller (`pipeline/controller.py`)

```python
class Stage(StrEnum):
    IDENTIFY = "IDENTIFY"
    COLLECT_EVIDENCE = "COLLECT_EVIDENCE"
    BUILD_REQUIREMENTS = "BUILD_REQUIREMENTS"
    REVIEW_SOURCES = "REVIEW_SOURCES"
    FREEZE_BASELINE = "FREEZE_BASELINE"
    SELECT_MODEL = "SELECT_MODEL"
    COMPILE_SIMULATE = "COMPILE_SIMULATE"
    EVALUATE = "EVALUATE"
    REPAIR = "REPAIR"
    EXPORT = "EXPORT"

STAGE_ORDER: tuple[Stage, ...]          # exactly the order above

@dataclass
class PipelineRequest:
    project_dir: Path
    mode: Literal["component", "circuit"] = "component"
    document_paths: list[Path] = field(default_factory=list)   # inputs to ingest
    part_identity: PartIdentity | None = None
    use_profile: str = "Power and I/O sequencing"
    scope: str | None = None                # limit which test cases run
    test_ids: list[str] | None = None
    allow_remote: bool = False
    provider: str | None = None             # provider name; never auto-swapped
    max_repair_iterations: int = 3
    export_dir: Path | None = None
    deadline_s: float | None = None

@dataclass
class StageProgress:
    stage: Stage
    status: Status                # PASS (done), FAIL, BLOCKED, UNKNOWN, NOT_APPLICABLE
    detail: str
    elapsed_s: float = 0.0
    test_counts: dict[str, int] = field(default_factory=dict)
    artifacts: list[str] = field(default_factory=list)

@dataclass
class PipelineResult:
    status: Status                # worst status of the stage chain
    stages: list[StageProgress]
    results: list[TestResult]
    findings: list[Finding]
    review_items: list[ReviewItem]
    artifacts: list[str]          # relative paths produced
    manifest_path: Path | None
    export_dir: Path | None
    diagnostics: dict[str, str] = field(default_factory=dict)

class RepairViolation(RuntimeError):
    """Raised when a repair step would weaken the frozen baseline."""

class PipelineController:
    def __init__(self, config: AppConfig | None = None) -> None: ...
    def run(
        self,
        request: PipelineRequest,
        progress: Callable[[StageProgress], None] | None = None,
        cancel: threading.Event | None = None,
    ) -> PipelineResult: ...
```

Rules the controller must enforce (each must have a test):

* Requirements, tests and `baseline.json` are frozen (hashed) **before** any
  repair. A repair step may only write under `models/candidates/<n>/`, is capped
  at `request.max_repair_iterations`, and raises `RepairViolation` — stopping the
  loop with UNKNOWN — if a step would relax a tolerance, delete a test, alter
  evidence, narrow coverage, or edit the circuit.
* A requirement or scope change creates a visible revision: `baseline.json`
  `baseline_version` bumps and `review.json` gains a `ReviewItem`.
* Simulator error/unavailability yields BLOCKED, never PASS.
* Only `reporting/export.py` computes approval/qualification receipts — the model
  generation code never writes one.

## 2. Worker protocol (`pipeline/worker.py`)

* Invocation: `python -m boardmodeler.pipeline.worker --request <request.json>
  --project <dir>`.
* stdout carries **one JSON object per line**, nothing else. Diagnostics go to
  stderr.
* Event shapes (`event` field is mandatory):

```jsonc
{"event": "stage",    "stage": "COMPILE_SIMULATE", "status": "PASS", "detail": "...", "elapsed_s": 1.2}
{"event": "progress", "stage": "EVALUATE", "done": 3, "total": 9, "detail": "..."}
{"event": "findings", "findings": [ /* Finding dicts */ ]}
{"event": "review",   "items":    [ /* ReviewItem dicts */ ]}
{"event": "waveform", "ref": "runs/<id>/probe.raw", "signals": ["V(VOUT)"], "violations": [{"req_id": "...", "t_s": 0.0012}]}
{"event": "result",   "status": "FAIL", "summary": {"PASS": 4, "FAIL": 1}, "results": [ /* TestResult dicts */ ]}
{"event": "error",    "code": "project_not_found", "detail": "..."}
```

* The worker exits 0 for any completed run (statuses are data) and non-zero only
  when the request itself could not be served.
* Cancellation: the parent terminates the child's **process tree**; the worker
  also honours SIGINT/SIGTERM by writing `{"event":"error","code":"cancelled"}`
  and exiting 130.
* The GUI never computes a verdict; it renders what the worker emits.

## 3. Schematic layer (`schematic/`)

```python
# schematic/netlist.py
@dataclass(frozen=True)
class Device:
    refdes: str; kind: str; nodes: tuple[str, ...]; value: str; params: dict[str, str]
    subckt: str | None = None; extra: tuple[str, ...] = ()

@dataclass(frozen=True)
class SubcktDef:
    name: str; ports: tuple[str, ...]; params: dict[str, str]

@dataclass
class Circuit:
    devices: dict[str, Device]; subckts: dict[str, SubcktDef]
    includes: list[str]; directives: list[str]; source_path: Path | None
    def nodes(self) -> list[str]
    def refdes(self) -> list[str]

class NetMap:
    """Connectivity built from a Circuit (flattened through X instances)."""
    def node_of(self, refdes: str, pin: str) -> str | None
    def pins_on(self, net: str) -> list[tuple[str, str]]
    def has_refdes(self, refdes: str) -> bool
    def is_ground(self, net: str) -> bool

def parse_netlist(text: str, *, source_path: Path | None = None) -> Circuit
def parse_netlist_file(path: Path) -> Circuit
def build_netmap(circuit: Circuit, symbols: Mapping[str, Sequence[str]] | None = None) -> NetMap
```

`NetMap` satisfies `boardmodeler.verification.assertions.Connectivity`
(`node_of`, `pins_on`, `has_refdes`) so the `net_equals` / `pin_connected` /
`pullup_domain` ops work against a real netlist.

```python
# schematic/neutral.py
@dataclass(frozen=True)
class ComponentRow: refdes; manufacturer; part_number; package; value
@dataclass(frozen=True)
class ConnectionRow: refdes; physical_pin; net_name
@dataclass
class NeutralProject:
    components: list[ComponentRow]; connections: list[ConnectionRow]
    supply_domains: dict[str, str]      # net -> domain name
    model_assignments: dict[str, str]   # refdes -> model id
    loads: dict[str, float]             # refdes -> current A
    timing: dict[str, float]            # name -> seconds
    abstractions: list[AbstractionBoundary]
    configuration: dict[str, str]
def read_components(path) -> list[ComponentRow]
def read_connections(path) -> list[ConnectionRow]
def write_components(path, rows) -> Path
def write_connections(path, rows) -> Path
def read_neutral_project(dir: Path) -> NeutralProject
def to_circuit(project: NeutralProject) -> Circuit
def validate_neutral(project, *, pins: Mapping[str, Sequence[str]]) -> list[Finding]
```

`validate_neutral` reports duplicate refdes, duplicate `(refdes, physical_pin)`,
pins absent from the device's `PinDefinition` list, components with no model
assignment, and illegal names.

```python
# schematic/asc.py
def parse_asc(text: str) -> AscSchematic        # SYMBOL/SYMATTR/WIRE/FLAG/TEXT/WINDOW
def read_asc(path) -> AscSchematic
def pin_offsets(asy_text: str) -> dict[str, tuple[int, int]]   # pin name -> local (x, y)

# schematic/ascgen.py
def generate_asc(spec: CircuitSpec, *, symbol_dir: Path) -> str
def write_asc(spec: CircuitSpec, path: Path, *, symbol_dir: Path) -> Path
@dataclass
class CircuitSpec:
    components: list[PlacedComponent]      # refdes, symbol, value, anchor (x, y), rotation
    wires: list[tuple[int, int, int, int]]
    flags: list[tuple[int, int, str]]      # x, y, net name
    directives: list[str]
    texts: list[tuple[int, int, str]]
```

Layout rules (verified empirically — see D-006/D-008): grid 16 units; a symbol's
pin is at `anchor + rotate(local_pin, rotation)` with `R0` identity,
`R90: (x,y) -> (-y,x)`, `R180: (-x,-y)`, `R270: (y,-x)`; `FLAG` names a net at a
coordinate; `TEXT x y Left 2 !.tran ...` carries directives.

```python
# schematic/static_check.py
CHECK_CODES: tuple[str, ...] = ("SC001_syntax", "SC002_units_names", "SC003_missing_dependency",
    "SC004_part_identity", "SC005_pinmap_physical_symbol_subckt", "SC006_symbol_prefix_model",
    "SC007_duplicate_dropped_connections", "SC008_export_portability",
    "SC009_supply_domain_assignment", "SC010_abstraction_boundary")
def run_static_checks(circuit, netmap, *, project: NeutralProject | None = None,
                      pins: Mapping[str, Sequence[PinDefinition]] | None = None,
                      model_records: Mapping[str, ModelRecord] | None = None,
                      symbol_pins: Mapping[str, Sequence[str]] | None = None) -> list[Finding]
```

**SC009 must evaluate every power pin individually.** Grouping pins into a
simulated supply domain must not hide a disconnected pin, a pin on the wrong
rail, or two domains shorted together: the check reports one `Finding` per
offending pin, naming the observed net and the expected domain.

```python
# schematic/mutate.py
@dataclass(frozen=True)
class Mutation:
    fault_id: str; description: str; edits: list[EditSpec]
@dataclass(frozen=True)
class EditSpec:
    file: str                 # path relative to the project root
    path: str                 # locator inside the file (e.g. "components.csv:U1.value")
    old: str; new: str
MUTATORS: dict[str, Callable[[NeutralProject], Mutation]]
def apply_mutation(project_dir: Path, mutation: Mutation, out_dir: Path) -> Path
    # copy-on-write: the original project directory is never modified
```

## 4. GUI (`ui/`) and the installer wizard

* `ui/app.py`: `def main(argv: Sequence[str] | None = None) -> int` — QApplication
  entry point; `--project <dir>` preloads a project.
* `ui/main_window.py`: `class MainWindow(QMainWindow)` with `load_project(dir)`,
  `start_run()`, `cancel_run()`, `export_project()`, `results_table()` (returns the
  model), `stage_list()` and `findings_list()` accessors for tests.
* `ui/worker_client.py`: `class WorkerClient(QObject)` — `start(request)` spawns
  the worker, re-emits the protocol events as Qt signals, `cancel()` kills the
  process tree, `resumed()` support by re-running with the same request.
* `ui/waveforms.py`: `class WaveformView(QWidget)` — custom `QPainter` widget
  (no QtCharts); `set_traces(list[Trace])`, `add_violation_marker(req_id, t_s)`,
  pan/zoom via mouse, `trace_names()`.
* `ui/results_panel.py`, `ui/review_panel.py`, `ui/settings.py`.
* Tests run with `QT_QPA_PLATFORM=offscreen` and are marked `gui`.

### Retro installer wizard (`ui/installer.py`) — user-requested

A self-contained, light, low-resolution "retro" wizard that walks a new user
through setup. Requirements:

* Pure Qt Widgets, no extra dependency, no images: **every pixel is drawn in
  code** — a chunky 8-bit look (fixed-size bitmap-ish font feel, 2–3 px borders,
  hard-edged panels, a limited palette, dithered/solid backgrounds, a blinking
  cursor, and a step counter like `[3/5]`).
* Steps: (1) welcome/scan, (2) LTspice detection + smoke test, (3) provider and
  credential setup (fixture / remote / Bob — never storing a secret in plain
  text), (4) data policy + privacy acknowledgement, (5) finish/summary with the
  exact commands to run.
* It must show **observed** facts only: the LTspice path and version and the smoke
  test result come from the same core functions the CLI uses
  (`simulation.ltspice.smoke_test`), and a failure is displayed as a failure with
  the observed detail. It never claims a step passed without running it.
* Every step is skippable, and the wizard writes its choices through
  `config.save_config`, so it can be run non-interactively in a test with an
  injected answer set.
* Test API: `class InstallerWizard(QWizard)` with
  `page_ids() -> list[str]`, `set_answers(dict) -> None`,
  `run_to_completion() -> InstallerOutcome` (dataclass: `completed: bool`,
  `config_path: Path`, `steps: list[tuple[str, str]]` (step id, observed detail),
  `skipped: list[str]`), so a test can drive the whole wizard offscreen and assert
  the saved config.
* A `--installer` flag on `boardmodeler ui` (or `boardmodeler setup`) launches it.

## 5. CLI surface (final)

```
boardmodeler version [--json]
boardmodeler doctor [--json] [--no-smoke]
boardmodeler setup [--json]                       # retro installer wizard
boardmodeler ui [--project DIR]
boardmodeler run tests --project DIR [--scope S] [--test ID] [--json] [--out F] [--list-tests] [--strict]
boardmodeler demo build --out DIR [--json]
boardmodeler circuit check --project DIR --circuit FILE [--fault-matrix] [--json] [--out F]
boardmodeler run mutations --project DIR --report F [--json]
boardmodeler export --project DIR --out DIR [--json]
boardmodeler extract --project DIR --doc FILE [--provider NAME] [--allow-remote] [--json]
boardmodeler --self-test [--json]
```

Exit codes: `0` success or a completed run whose results are data; `1` when the
request could not be served or `--strict` saw a non-PASS; `2` usage error.
