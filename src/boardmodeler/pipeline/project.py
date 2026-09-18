"""Project workspace (D3).

A project is a directory with a declared layout. Everything the pipeline reads
is addressed relative to it, and the requirement/test baseline is a *file* — so
a run can always name the exact bytes it was judged against.

```
<project_dir>/
  project.json          ProjectConfig
  docs/                 DocumentRecord JSON + originals under docs/files/
  evidence/             requirements.json, pinmap.json, cache/
  models/               vendor/, generated/, candidates/<n>/
  circuit/              components.csv, connections.csv, project.json
  runs/<run_id>/        deck.cir, deck.raw, deck.log, results.json
  baseline.json         frozen requirement/test baseline (hashes)
  review.json           ReviewItem list
  export/               produced by `boardmodeler export`
```

`requirements()` and `tests()` prefer the frozen baseline over the working files:
a judgement made against a baseline must be reproducible from that baseline.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from boardmodeler.domain import SCHEMA_VERSION
from boardmodeler.domain.hashing import canonical_json_bytes, sha256_bytes
from boardmodeler.domain.records import (
    CircuitMapping,
    DocumentRecord,
    PartIdentity,
    Requirement,
    ReviewItem,
    TestCase,
)
from boardmodeler.pipeline.runner import RunContext
from boardmodeler.simulation.ltspice import LtspiceInstall, locate

__all__ = [
    "BASELINE_FILENAME",
    "PROJECT_FILENAME",
    "Baseline",
    "Project",
    "ProjectConfig",
    "ProjectError",
    "create_project",
]

PROJECT_FILENAME = "project.json"
BASELINE_FILENAME = "baseline.json"
REQUIREMENTS_REL = "evidence/requirements.json"
TESTS_REL = "tests/tests.json"
REVIEW_REL = "review.json"
DOCS_DIR = "docs"
EVIDENCE_DIR = "evidence"
MODELS_DIR = "models"
CIRCUIT_DIR = "circuit"
RUNS_DIR = "runs"
EXPORT_DIR = "export"

STANDARD_DIRS = (DOCS_DIR, EVIDENCE_DIR, MODELS_DIR, CIRCUIT_DIR, RUNS_DIR, "tests")


class ProjectError(RuntimeError):
    """Raised when a project directory is missing or malformed."""


class ProjectConfig(BaseModel):
    """`project.json`: what this project is and which documents it owns."""

    model_config = ConfigDict(extra="forbid")

    config_version: int = SCHEMA_VERSION
    project_id: str
    name: str
    mode: Literal["component", "circuit"] = "component"
    created_utc: str = ""
    part: PartIdentity | None = None
    documents: list[str] = Field(default_factory=list)
    requirements_file: str = REQUIREMENTS_REL
    tests_file: str = TESTS_REL
    supply_domains: dict[str, str] = Field(default_factory=dict)
    use_profile: str = "Power and I/O sequencing"
    notes: list[str] = Field(default_factory=list)


class Baseline(BaseModel):
    """`baseline.json`: the frozen requirement/test set a run was judged against."""

    model_config = ConfigDict(extra="forbid")

    baseline_version: int = 1
    frozen_utc: str = ""
    requirement_baseline_hash: str = ""
    test_hash: str = ""
    requirements: list[Requirement] = Field(default_factory=list)
    tests: list[TestCase] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)

    def compute_hash(self) -> str:
        """Hash over the frozen content (not over the metadata fields)."""
        payload = {
            "requirements": json.loads(
                json.dumps([r.model_dump(mode="json", by_alias=True) for r in self.requirements])
            ),
            "tests": json.loads(json.dumps([t.model_dump(mode="json") for t in self.tests])),
        }
        return sha256_bytes(canonical_json_bytes(payload))


def create_project(
    root: str | Path,
    *,
    project_id: str,
    name: str,
    mode: Literal["component", "circuit"] = "component",
    supply_domains: dict[str, str] | None = None,
    notes: list[str] | None = None,
) -> Project:
    """Create (or open) a project directory with the standard layout."""
    root_path = Path(root)
    root_path.mkdir(parents=True, exist_ok=True)
    for sub in STANDARD_DIRS:
        (root_path / sub).mkdir(parents=True, exist_ok=True)
    (root_path / DOCS_DIR / "files").mkdir(parents=True, exist_ok=True)

    config_file = root_path / PROJECT_FILENAME
    if config_file.is_file():
        return Project(root_path)
    config = ProjectConfig(
        project_id=project_id,
        name=name,
        mode=mode,
        created_utc=datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        supply_domains=dict(supply_domains or {}),
        notes=list(notes or []),
    )
    config_file.write_text(config.model_dump_json(indent=2), encoding="utf-8")
    return Project(root_path)


class Project:
    """Read access to a project's declared artifacts."""

    def __init__(self, root: str | Path) -> None:
        self.root = Path(root).resolve()
        config_file = self.root / PROJECT_FILENAME
        if not config_file.is_file():
            raise ProjectError(f"{config_file} does not exist; is this a BoardModeler project?")
        self.config = ProjectConfig.model_validate_json(config_file.read_text(encoding="utf-8"))

    # ----------------------------------------------------------------- paths

    def path(self, relative: str) -> Path:
        return self.root / relative

    @property
    def requirements_path(self) -> Path:
        return self.path(self.config.requirements_file)

    @property
    def tests_path(self) -> Path:
        return self.path(self.config.tests_file)

    @property
    def baseline_path(self) -> Path:
        return self.root / BASELINE_FILENAME

    @property
    def review_path(self) -> Path:
        return self.root / REVIEW_REL

    @property
    def runs_dir(self) -> Path:
        return self.root / RUNS_DIR

    # ------------------------------------------------------------ artifacts

    def baseline(self) -> Baseline | None:
        """The frozen baseline, when one exists."""
        if not self.baseline_path.is_file():
            return None
        return Baseline.model_validate_json(self.baseline_path.read_text(encoding="utf-8"))

    def requirements(self) -> dict[str, Requirement]:
        """Requirements by id, preferring the frozen baseline."""
        baseline = self.baseline()
        if baseline is not None and baseline.requirements:
            return {r.req_id: r for r in baseline.requirements}
        return {r.req_id: r for r in self.read_requirements_file()}

    def read_requirements_file(self) -> list[Requirement]:
        if not self.requirements_path.is_file():
            return []
        raw = json.loads(self.requirements_path.read_text(encoding="utf-8"))
        items = raw["requirements"] if isinstance(raw, dict) else raw
        return [Requirement.model_validate(item) for item in items]

    def tests(
        self, *, scope: str | None = None, test_ids: list[str] | None = None
    ) -> list[TestCase]:
        """Test cases, preferring the frozen baseline, optionally filtered."""
        baseline = self.baseline()
        if baseline is not None and baseline.tests:
            cases = list(baseline.tests)
        else:
            cases = self.read_tests_file()
        if scope:
            cases = [c for c in cases if c.scope == scope]
        if test_ids:
            wanted = set(test_ids)
            cases = [c for c in cases if c.test_id in wanted]
        return cases

    def read_tests_file(self) -> list[TestCase]:
        if not self.tests_path.is_file():
            return []
        raw = json.loads(self.tests_path.read_text(encoding="utf-8"))
        items = raw["tests"] if isinstance(raw, dict) else raw
        return [TestCase.model_validate(item) for item in items]

    def documents(self) -> list[DocumentRecord]:
        records: list[DocumentRecord] = []
        docs_dir = self.root / DOCS_DIR
        if not docs_dir.is_dir():
            return records
        for path in sorted(docs_dir.glob("*.json")):
            records.append(DocumentRecord.model_validate_json(path.read_text(encoding="utf-8")))
        return records

    def document(self, doc_id: str) -> DocumentRecord | None:
        for record in self.documents():
            if record.doc_id == doc_id:
                return record
        return None

    def review_items(self) -> list[ReviewItem]:
        if not self.review_path.is_file():
            return []
        raw = json.loads(self.review_path.read_text(encoding="utf-8"))
        items = raw["items"] if isinstance(raw, dict) else raw
        return [ReviewItem.model_validate(item) for item in items]

    def circuit_mapping(self) -> CircuitMapping | None:
        path = self.root / CIRCUIT_DIR / "mapping.json"
        if not path.is_file():
            return None
        return CircuitMapping.model_validate_json(path.read_text(encoding="utf-8"))

    # ---------------------------------------------------------------- runner

    def run_context(
        self,
        *,
        ltspice: LtspiceInstall | None = None,
        timeout_s: float = 120.0,
        ascii_raw: bool = False,
    ) -> RunContext:
        """A :class:`RunContext` for this project (LTspice is located if not given)."""
        install = ltspice if ltspice is not None else locate()
        return RunContext(
            project_dir=self.root,
            ltspice=install.path if install else None,
            timeout_s=timeout_s,
            ascii_raw=ascii_raw,
            ltspice_lib_dir=_default_lib(),
        )

    def write_baseline(self, baseline: Baseline) -> Path:
        """Freeze a baseline (used by the pipeline controller, never by repair)."""
        payload = baseline.model_dump(mode="json", by_alias=True)
        payload["requirement_baseline_hash"] = baseline.compute_hash()
        self.baseline_path.write_text(
            json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8"
        )
        return self.baseline_path

    def freeze_baseline(self, *, notes: list[str] | None = None) -> Baseline:
        """Freeze the current working requirements/tests into ``baseline.json``."""
        requirements = self.read_requirements_file()
        tests = self.read_tests_file()
        baseline = Baseline(
            baseline_version=1,
            frozen_utc=datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
            requirements=requirements,
            tests=tests,
            notes=list(notes or []),
        )
        baseline.requirement_baseline_hash = baseline.compute_hash()
        self.write_baseline(baseline)
        return baseline


def _default_lib() -> Path | None:
    from boardmodeler.simulation.ltspice import default_lib_dir

    return default_lib_dir()
