"""Domain types shared across extraction, sandbox, labeling, repair, storage and the API.

Field names here are the contract between modules. Change them only together with
docs/ARCHITECTURE.md.
"""

from datetime import date
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, Field, computed_field


class StepKind(StrEnum):
    PYTHON = "python"
    REPL = "repl"
    JAVASCRIPT = "javascript"
    TYPESCRIPT = "typescript"
    C = "c"
    CPP = "cpp"
    SHELL = "shell"
    OUTPUT = "output"
    OTHER = "other"


RUNNABLE_KINDS: frozenset[StepKind] = frozenset(
    {
        StepKind.PYTHON,
        StepKind.REPL,
        StepKind.JAVASCRIPT,
        StepKind.TYPESCRIPT,
        StepKind.C,
        StepKind.CPP,
    }
)
"""Kinds whose code goes to the sandbox. Shell, output and other never run."""


def is_runnable(kind: StepKind) -> bool:
    return kind in RUNNABLE_KINDS


class Label(StrEnum):
    WORKS = "works"
    DEPRECATED = "deprecated"
    BROKEN = "broken"
    CANT_VERIFY = "cant_verify"


class PatchVerdict(StrEnum):
    """What a sandbox run of a patch proves (ADR-008). Decided by `labels.judge_patch`."""

    FIXED = "fixed"
    """The patch ran with no error."""
    FIXED_UNVERIFIED = "fixed_unverified"
    """The patch got past the broken API and stopped inside library code only because the
    sandbox lacks a key, network, data file or GPU."""
    FAILED = "failed"


class SourceKind(StrEnum):
    URL = "url"
    NOTEBOOK = "notebook"
    PASTE = "paste"


class SourceFormat(StrEnum):
    HTML = "html"
    NOTEBOOK = "notebook"
    GITHUB_README = "github_readme"
    STACKOVERFLOW = "stackoverflow"
    VIDEO = "video"
    PASTE = "paste"


class DateSource(StrEnum):
    PAGE = "page"
    ESTIMATE = "estimate"
    UNKNOWN = "unknown"


class CheckStatus(StrEnum):
    RUNNING = "running"
    DONE = "done"
    FAILED = "failed"


# Extraction


class Block(BaseModel):
    """One code block from the tutorial, in page order."""

    idx: int
    kind: StepKind
    code: str
    """The block's text as published, after HTML unescaping."""
    runnable_code: str | None = None
    """What gets sent to the sandbox: REPL prompts and output lines stripped, notebook
    `!` and `%` lines removed. None for kinds that never run (shell, output, other)."""
    shown_output: str | None = None
    """Output shown by the tutorial for this block, used only for comparison and never run."""
    context: str = ""
    """Prose or Markdown just before the block, used as context for explanations."""
    video_timestamp: int | None = None
    """Whole seconds from the start of the video, when recovered from video."""
    low_confidence: bool = False
    """Whether the block was transcribed from video and may have been misread."""
    read_agreement: int | None = None
    """How many independent video reads saw this block. None for other sources."""


class Extraction(BaseModel):
    source_kind: SourceKind
    source_format: SourceFormat
    source: str
    """The URL, the uploaded notebook's file name, or "Pasted code"."""
    title: str | None = None
    published: date | None = None
    date_source: DateSource = DateSource.UNKNOWN
    blocks: list[Block]
    packages: list[str]
    """Sorted PyPI names, standard library excluded, version pins dropped."""
    npm_packages: list[str] = Field(default_factory=list)
    """Sorted npm names used by JavaScript and TypeScript blocks and npm install lines."""
    c_system_headers: list[str] = Field(default_factory=list)
    """Sorted `<header>` names in C and C++ blocks that are not in the standard library."""
    fetched_via: str | None = None
    """Name of the page reader that supplied the content, or None for a direct fetch."""


# Sandbox runner replies (see ARCHITECTURE.md, "Runner protocol")


class WarningInfo(BaseModel):
    category: str
    message: str
    filename: str = ""
    lineno: int | None = None
    module: str = ""
    """Top-level package the warning came from, or "__step__" for step code."""


class ErrorInfo(BaseModel):
    type: str
    message: str
    traceback: str = ""


class ExecResult(BaseModel):
    id: str
    status: Literal["ok", "error"]
    stdout: str = ""
    stderr: str = ""
    result: str | None = None
    warnings: list[WarningInfo] = Field(default_factory=list)
    error: ErrorInfo | None = None
    duration_ms: int = 0


class ClosestMatch(BaseModel):
    name: str
    signature: str | None = None
    doc: str | None = None


class ProbeResult(BaseModel):
    id: str
    status: Literal["ok", "error"]
    target_found: bool = False
    exists: bool = False
    close_matches: list[str] = Field(default_factory=list)
    closest: ClosestMatch | None = None
    package: str | None = None
    version: str | None = None
    error: ErrorInfo | None = None


class Environment(BaseModel):
    """What the sandbox ended up with after installing the tutorial's packages."""

    versions: dict[str, str] = Field(default_factory=dict)
    failed_packages: list[str] = Field(default_factory=list)
    """Names pip could not install, for example an author's local module."""
    npm_versions: dict[str, str] = Field(default_factory=dict)
    failed_npm_packages: list[str] = Field(default_factory=list)


# Labeling


class LabelDecision(BaseModel):
    label: Label
    reason: str = ""
    """Why the step got this label. Required for CANT_VERIFY, for example
    "needs an API key" or "needs keyboard input"."""


# Repair


class RepairProposal(BaseModel):
    """The JSON reply the LLM must produce for one repair attempt."""

    patched_code: str
    explanation: str
    apis_changed: list[str] = Field(default_factory=list)


class RepairAttempt(BaseModel):
    attempt: int
    """1-based."""
    patched_code: str
    explanation: str
    apis_changed: list[str] = Field(default_factory=list)
    provider: str | None = None
    """The LLM provider that proposed the patch."""
    result: ExecResult | None = None
    """None when the patch timed out or was rejected before running."""
    decision: LabelDecision
    """How the patched run would be labeled."""
    verdict: PatchVerdict


# Report


class Score(BaseModel):
    works: int = 0
    deprecated: int = 0
    broken: int = 0
    cant_verify: int = 0
    fixed: int = 0
    """Broken steps with verdict FIXED. They stay counted under broken."""
    fixed_unverified: int = 0
    """Broken steps with verdict FIXED_UNVERIFIED. They stay counted under broken."""

    @computed_field
    @property
    def still_run(self) -> int:
        return self.works + self.deprecated

    @computed_field
    @property
    def checked(self) -> int:
        return self.works + self.deprecated + self.broken

    @computed_field
    @property
    def ratio(self) -> float | None:
        """still_run / checked, or None when nothing could be checked."""
        return self.still_run / self.checked if self.checked else None


class StepReport(BaseModel):
    idx: int
    kind: StepKind
    original_code: str
    """The block as published."""
    final_code: str
    """The published code with the accepted patch applied and
    notebook `!`/`%` lines kept. Equal to `original_code` when nothing was fixed."""
    label: Label | None = None
    """The label of the original run. None for blocks that never run. A fixed step keeps
    its BROKEN label; `verdict` says it was fixed."""
    reason: str = ""
    verdict: PatchVerdict | None = None
    """None when no repair was attempted."""
    repair_attempts: list[RepairAttempt] = Field(default_factory=list)
    explanation: str = ""
    """The accepted patch's explanation."""
    apis_changed: list[str] = Field(default_factory=list)
    output: str = ""
    error: ErrorInfo | None = None
    warnings: list[WarningInfo] = Field(default_factory=list)
    video_timestamp: int | None = None
    """Whole seconds from the start of the video, when recovered from video."""
    low_confidence: bool = False
    """Whether the step was transcribed from video and may have been misread."""
    read_agreement: int | None = None
    """How many independent video reads saw this step's code. None for other sources."""

    @property
    def fixed(self) -> bool:
        return self.verdict in (PatchVerdict.FIXED, PatchVerdict.FIXED_UNVERIFIED)


class InstallPin(BaseModel):
    step_idx: int
    line: str
    package: str
    pinned: str
    used: str | None
    ecosystem: Literal["pypi", "npm"] = "pypi"
    origin: Literal["install_line", "pip_output"] = "install_line"
    """Whether the version came from an install command or pip output."""


class CheckReport(BaseModel):
    id: str
    source_kind: SourceKind
    source: str
    title: str | None = None
    published: date | None = None
    date_source: DateSource = DateSource.UNKNOWN
    status: CheckStatus
    created_at: str = ""
    """When the check was created, ISO 8601 UTC. Set by storage; empty before it is stored."""
    score: Score | None = None
    versions: dict[str, str] = Field(default_factory=dict)
    """Installed version of each requested package, keyed by its PyPI name."""
    failed_packages: list[str] = Field(default_factory=list)
    install_pins: list[InstallPin] = Field(default_factory=list)
    steps: list[StepReport] = Field(default_factory=list)
    error: str | None = None
    """Why the whole check failed, when status is FAILED."""
