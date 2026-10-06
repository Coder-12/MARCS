"""Canonical data contracts for the future task-to-patch workflow.

These do not replace v1 webhook/review contracts or execute workflow stages.
Paths are canonical slash-separated logical names; lexical checks are not a
filesystem security boundary. Models support validated field assignment, but
in-place collection edits must be revalidated before crossing a boundary.
"""
from __future__ import annotations

import re
from enum import Enum
from typing import Annotated, Literal

from pydantic import (
    AfterValidator,
    BaseModel,
    BeforeValidator,
    ConfigDict,
    Field,
    field_validator,
    model_validator,
)


def _utf8_text(value: str) -> str:
    value.encode("utf-8")
    return value


def _enum_text(value: object) -> object:
    if not isinstance(value, str):
        raise ValueError("enum value must be a string")
    return value


def _nonblank(value: str) -> str:
    if not value.strip():
        raise ValueError("must be nonblank")
    return value


def _relative_path(value: str) -> str:
    if (
        not value.strip()
        or value != value.strip()
        or value.startswith("/")
        or "\\" in value
        or re.match(r"^[A-Za-z]:", value)
        or any(ord(char) < 32 or ord(char) == 127 for char in value)
        or any(part in ("", ".", "..") for part in value.split("/"))
    ):
        raise ValueError("must be a canonical repository-relative path")
    return value


_Text = Annotated[str, AfterValidator(_utf8_text)]
_NonBlank = Annotated[_Text, AfterValidator(_nonblank)]
_RepoPath = Annotated[_Text, AfterValidator(_relative_path)]
_PositiveInt = Annotated[int, Field(ge=1)]


class _Contract(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        strict=True,
        allow_inf_nan=False,
        validate_assignment=True,
        revalidate_instances="always",
    )


class TaskType(str, Enum):
    BUG_FIX = "bug_fix"
    FEATURE = "feature"
    REFACTOR = "refactor"
    TEST = "test"
    DOCS = "docs"
    UNKNOWN = "unknown"


class PatchOperation(str, Enum):
    MODIFY = "modify"
    CREATE = "create"


class PatchApplyStatus(str, Enum):
    SUCCESS = "success"
    VALIDATION_FAILED = "validation_failed"
    APPLY_FAILED = "apply_failed"


class WorkflowStatus(str, Enum):
    RUNNING = "running"
    SUCCESS = "success"
    FAILED = "failed"
    BLOCKED = "blocked"
    PARTIAL = "partial"


class FailureCategory(str, Enum):
    INVALID_REPO = "invalid_repo"
    UNSUPPORTED_REPO = "unsupported_repo"
    INSUFFICIENT_CONTEXT = "insufficient_context"
    PATCH_APPLY_FAILED = "patch_apply_failed"
    VALIDATION_FAILED = "validation_failed"
    REVIEW_BLOCKED = "review_blocked"
    POLICY_BLOCKED = "policy_blocked"
    ITERATION_LIMIT_REACHED = "iteration_limit_reached"
    TOOL_ERROR = "tool_error"
    TOOL_TIMEOUT = "tool_timeout"
    SCHEMA_ERROR = "schema_error"


class ReviewDecision(str, Enum):
    APPROVE = "approve"
    REVISE = "revise"
    BLOCK = "block"


class TaskSpec(_Contract):
    """Normalized user intent, without execution state or generated content."""

    raw_input: _NonBlank
    task_type: Annotated[TaskType, Field(strict=False), BeforeValidator(_enum_text)] = TaskType.UNKNOWN
    goal: _NonBlank
    constraints: list[_NonBlank] = Field(default_factory=list)
    success_criteria: list[_NonBlank] = Field(default_factory=list)


class RepoProfile(_Contract):
    """Repository characterization; unavailable facts remain absent."""

    language: _NonBlank | None = None
    framework: _NonBlank | None = None
    test_framework: _NonBlank | None = None
    important_dirs: list[_RepoPath] = Field(default_factory=list)


class RepositoryEntryKind(str, Enum):
    FILE = "file"
    DIRECTORY = "directory"
    SYMLINK = "symlink"


class RepositoryEntry(_Contract):
    """Neutral structural entry; no symlink target or absolute location."""

    path: _RepoPath
    kind: Annotated[RepositoryEntryKind, Field(strict=False), BeforeValidator(_enum_text)]
    size_bytes: Annotated[int, Field(ge=0)] | None = None

    @model_validator(mode="before")
    @classmethod
    def coherent_size(cls, data: object) -> object:
        if isinstance(data, dict):
            kind, size = data.get("kind"), data.get("size_bytes")
            if kind == RepositoryEntryKind.FILE and size is None:
                raise ValueError("regular file requires size_bytes")
            if kind in (RepositoryEntryKind.DIRECTORY, RepositoryEntryKind.SYMLINK) and size is not None:
                raise ValueError("size_bytes describes regular files only")
        return data


class RepositoryInventory(_Contract):
    """Deterministic partial tree. Counts cover visited entries, not guessed totals."""

    entries: list[RepositoryEntry] = Field(default_factory=list)
    truncated: bool = False
    excluded_policy_count: int = Field(default=0, ge=0)
    unsupported_entry_count: int = Field(default=0, ge=0)
    unreadable_entry_count: int = Field(default=0, ge=0)
    warnings: list[_NonBlank] = Field(default_factory=list)

    @property
    def entries_returned(self) -> int:
        return len(self.entries)


class RepositoryText(_Contract):
    """Exact bounded source text, with inclusive LF-based source line numbers.

    Empty output represents no lines (both bounds null). A truncated end_line
    may be a partial final line; no marker is inserted into content.
    """

    path: _RepoPath
    content: _Text
    start_line: _PositiveInt | None = None
    end_line: _PositiveInt | None = None
    truncated: bool = False

    @model_validator(mode="before")
    @classmethod
    def coherent_lines(cls, data: object) -> object:
        if isinstance(data, dict):
            content, start, end = data.get("content"), data.get("start_line"), data.get("end_line")
            if (start is None) != (end is None) or (content == "" and start is not None):
                raise ValueError("empty text has no lines; otherwise line bounds are paired")
            if isinstance(content, str) and content and start is None:
                raise ValueError("represented text requires line attribution")
            if isinstance(start, int) and isinstance(end, int) and start > end:
                raise ValueError("start_line must not exceed end_line")
        return data


class RepositoryFactEvidence(_Contract):
    """Deterministic profile attribution, separate from task retrieval evidence."""

    field: Literal["language", "framework", "test_framework", "important_dirs"]
    value: _NonBlank
    path: _RepoPath | None = None
    reason: _NonBlank


class RepositoryAnalysisResult(_Contract):
    """Observed Python-MVP facts and bounded discoveries, without task ranking."""

    profile: RepoProfile
    inventory: RepositoryInventory
    test_files: list[_RepoPath] = Field(default_factory=list)
    config_files: list[_RepoPath] = Field(default_factory=list)
    readme_excerpt: RepositoryText | None = None
    evidence: list[RepositoryFactEvidence] = Field(default_factory=list)
    warnings: list[_NonBlank] = Field(default_factory=list)
    truncated: bool = False

    @property
    def files_considered(self) -> int:
        """Regular files in the returned policy inventory, not files executed/read."""
        return sum(entry.kind == RepositoryEntryKind.FILE for entry in self.inventory.entries)


class SearchBackend(str, Enum):
    RIPGREP = "ripgrep"
    PYTHON = "python"


class CodeSearchMatch(_Contract):
    path: _RepoPath
    line_number: _PositiveInt
    line: _Text
    line_truncated: bool = False


class CodeSearchResult(_Contract):
    """Neutral literal matches. Backend determines native versus explicit ignores.

    files_considered counts known prefiltered candidates, not files rg opened.
    files_skipped counts prefilter regular-file size/read/UTF-8 failures.
    """

    query: _NonBlank
    case_sensitive: bool
    backend: Annotated[SearchBackend, Field(strict=False), BeforeValidator(_enum_text)]
    matches: list[CodeSearchMatch] = Field(default_factory=list)
    truncated: bool = False
    files_considered: int = Field(default=0, ge=0)
    files_skipped: int = Field(default=0, ge=0)
    warnings: list[_NonBlank] = Field(default_factory=list)
    fallback_reason: _NonBlank | None = None

    @field_validator("query")
    @classmethod
    def literal_line_query(cls, value: str) -> str:
        if any(char in value for char in ("\x00", "\n", "\r")):
            raise ValueError("query must be one literal non-NUL line")
        return value


class RetrievedEvidence(_Contract):
    """Attributed whole-file context or an inclusive, both-or-neither line range."""

    path: _RepoPath
    content: _Text
    reason: _NonBlank
    start_line: _PositiveInt | None = None
    end_line: _PositiveInt | None = None

    @model_validator(mode="before")
    @classmethod
    def coherent_range(cls, data: object) -> object:
        if isinstance(data, dict):
            start, end = data.get("start_line"), data.get("end_line")
            if (start is None) != (end is None):
                raise ValueError("line bounds must be supplied together")
            if isinstance(start, int) and isinstance(end, int) and start > end:
                raise ValueError("start_line must not exceed end_line")
        return data


class ValidationCommand(_Contract):
    """Declarative argv only; neither command policy nor command execution."""

    argv: list[_Text] = Field(min_length=1)

    @field_validator("argv")
    @classmethod
    def valid_executable(cls, value: list[str]) -> list[str]:
        _nonblank(value[0])
        if any("\x00" in argument for argument in value):
            raise ValueError("argv arguments must not contain NUL")
        return value


class Plan(_Contract):
    """Implementation intent, independent of execution and attempt counts."""

    steps: list[_NonBlank] = Field(default_factory=list)
    files_to_inspect: list[_RepoPath] = Field(default_factory=list)
    files_to_modify: list[_RepoPath] = Field(default_factory=list)
    validation_commands: list[ValidationCommand] = Field(default_factory=list)
    assumptions: list[_NonBlank] = Field(default_factory=list)


class ModifyFileChange(_Contract):
    """Exact snippet replacement; hash/snippet verification belongs to the workspace."""

    path: _RepoPath
    operation: Literal[PatchOperation.MODIFY] = PatchOperation.MODIFY
    original_sha256: _Text = Field(pattern=r"^[0-9a-f]{64}$")
    original_snippet: _Text = Field(min_length=1)
    replacement_snippet: _Text
    justification: _NonBlank


class CreateFileChange(_Contract):
    """New UTF-8 text content; existence is not checked by this contract."""

    path: _RepoPath
    operation: Literal[PatchOperation.CREATE] = PatchOperation.CREATE
    content: _Text
    justification: _NonBlank


PatchFileChange = Annotated[
    ModifyFileChange | CreateFileChange, Field(discriminator="operation")
]


class PatchProposal(_Contract):
    """One structured proposal for an iteration; never a legacy raw unified diff."""

    patch_id: _NonBlank
    run_id: _NonBlank
    iteration: _PositiveInt
    files: list[PatchFileChange] = Field(min_length=1)
    assumptions: list[_NonBlank] = Field(default_factory=list)
    risks: list[_NonBlank] = Field(default_factory=list)

    @field_validator("files")
    @classmethod
    def unique_paths(cls, value: list[PatchFileChange]) -> list[PatchFileChange]:
        paths = [change.path for change in value]
        if len(paths) != len(set(paths)):
            raise ValueError("duplicate file paths in patch proposal")
        return value


class PatchApplyResult(_Contract):
    """Workspace-only outcome. Discard an apply_failed candidate; no rollback.

    patch_id is null only when invalid input cannot be attributed. Failure diffs
    are empty; files_modified on apply failure lists completed writes only.
    Completeness of successful evidence is established by the patch service.
    """

    patch_id: _NonBlank | None
    status: Annotated[PatchApplyStatus, Field(strict=False), BeforeValidator(_enum_text)]
    unified_diff: _Text = ""
    files_modified: list[_RepoPath] = Field(default_factory=list)
    error: _NonBlank | None = None

    @field_validator("files_modified")
    @classmethod
    def ordered_unique_paths(cls, value: list[str]) -> list[str]:
        if len(value) != len(set(value)):
            raise ValueError("duplicate modified paths")
        return sorted(value)

    @model_validator(mode="before")
    @classmethod
    def coherent_result(cls, data: object) -> object:
        if isinstance(data, dict):
            status = data.get("status")
            diff, files, error = data.get("unified_diff", ""), data.get("files_modified", []), data.get("error")
            if status == PatchApplyStatus.SUCCESS:
                if data.get("patch_id") is None or error is not None or not diff or not files:
                    raise ValueError("success requires identity, diff, changed files, and no error")
            elif status in (PatchApplyStatus.VALIDATION_FAILED, PatchApplyStatus.APPLY_FAILED):
                if error is None or diff:
                    raise ValueError("failure requires an error and no canonical diff")
                if status == PatchApplyStatus.APPLY_FAILED and data.get("patch_id") is None:
                    raise ValueError("application failure requires candidate identity")
                if status == PatchApplyStatus.VALIDATION_FAILED and files:
                    raise ValueError("validation failure must not report writes")
        return data


class ValidationResult(ValidationCommand):
    """One candidate's command result; passed is derived, not stored separately."""

    patch_id: _NonBlank
    exit_code: int
    stdout: _Text = ""
    stderr: _Text = ""
    duration_ms: float = Field(ge=0)

    @property
    def passed(self) -> bool:
        return self.exit_code == 0


class ReviewFinding(_Contract):
    """Candidate-level concern, optionally attributable to a repository path."""

    message: _NonBlank
    path: _RepoPath | None = None


class ReviewResult(_Contract):
    """Independent candidate disposition, not a v1 synthesized code review."""

    patch_id: _NonBlank
    decision: Annotated[ReviewDecision, Field(strict=False), BeforeValidator(_enum_text)]
    summary: _NonBlank
    findings: list[ReviewFinding] = Field(default_factory=list)


class AttemptBudget(_Contract):
    """Total attempts including first execution; no increments or retry behavior."""

    patch_attempts: int = Field(default=0, ge=0)
    retrieval_attempts: int = Field(default=0, ge=0)
    validation_attempts: int = Field(default=0, ge=0)
    max_patch_attempts: _PositiveInt = 2
    max_retrieval_attempts: _PositiveInt = 2
    max_validation_attempts: _PositiveInt = 3

    @model_validator(mode="before")
    @classmethod
    def within_budget(cls, data: object) -> object:
        if isinstance(data, dict):
            for stage in ("patch", "retrieval", "validation"):
                current = data.get(f"{stage}_attempts", 0)
                maximum_key = f"max_{stage}_attempts"
                maximum = data.get(maximum_key, cls.model_fields[maximum_key].default)
                if isinstance(current, int) and isinstance(maximum, int) and current > maximum:
                    raise ValueError(f"{stage}_attempts exceeds {maximum_key}")
        return data


class WorkflowState(_Contract):
    """Accumulated run data, with local outcome consistency but no transition graph.

    FAILED is a hard/system failure, BLOCKED an intentional stop, and PARTIAL
    progress without convergence. Each requires a machine-readable reason.
    """

    run_id: _NonBlank
    task: TaskSpec
    repo_profile: RepoProfile | None = None
    retrieved_evidence: list[RetrievedEvidence] = Field(default_factory=list)
    plan: Plan | None = None
    patch_proposals: list[PatchProposal] = Field(default_factory=list)
    validation_results: list[ValidationResult] = Field(default_factory=list)
    review_result: ReviewResult | None = None
    attempt_budget: AttemptBudget = Field(default_factory=AttemptBudget)
    status: Annotated[WorkflowStatus, Field(strict=False), BeforeValidator(_enum_text)] = WorkflowStatus.RUNNING
    failure_category: Annotated[
        FailureCategory, Field(strict=False), BeforeValidator(_enum_text)
    ] | None = None
    failure_message: _Text | None = None
    final_report: _Text | None = None

    @model_validator(mode="before")
    @classmethod
    def coherent_outcome(cls, data: object) -> object:
        if isinstance(data, dict):
            def field(value: object, name: str) -> object:
                if isinstance(value, _Contract):
                    return getattr(value, name, None)
                return value.get(name) if isinstance(value, dict) else None

            status = data.get("status", WorkflowStatus.RUNNING)
            category, message = data.get("failure_category"), data.get("failure_message")
            if status == WorkflowStatus.SUCCESS:
                if category is not None or (isinstance(message, str) and message.strip()):
                    raise ValueError("success must not carry failure information")
            elif status in (WorkflowStatus.FAILED, WorkflowStatus.BLOCKED, WorkflowStatus.PARTIAL):
                if category is None:
                    raise ValueError("unsuccessful terminal status requires failure_category")
            run_id = data.get("run_id")
            proposals = data.get("patch_proposals", [])
            patch_ids: set[str] = set()
            iterations: set[int] = set()
            if isinstance(proposals, list):
                for proposal in proposals:
                    proposal_run = field(proposal, "run_id")
                    if isinstance(run_id, str) and proposal_run is not None and proposal_run != run_id:
                        raise ValueError("patch proposal run_id must match workflow run_id")
                    patch_id, iteration = field(proposal, "patch_id"), field(proposal, "iteration")
                    if isinstance(patch_id, str):
                        if patch_id in patch_ids:
                            raise ValueError("duplicate patch_id in workflow proposals")
                        patch_ids.add(patch_id)
                    if isinstance(iteration, int):
                        if iteration in iterations:
                            raise ValueError("duplicate iteration in workflow proposals")
                        iterations.add(iteration)
            results = data.get("validation_results", [])
            if isinstance(results, list):
                for result in results:
                    patch_id = field(result, "patch_id")
                    if isinstance(patch_id, str) and patch_id not in patch_ids:
                        raise ValueError("validation result patch_id must reference a workflow proposal")
            review = data.get("review_result")
            review_patch_id = field(review, "patch_id")
            if isinstance(review_patch_id, str) and review_patch_id not in patch_ids:
                raise ValueError("review result patch_id must reference a workflow proposal")
            if status == WorkflowStatus.SUCCESS and review is not None:
                if field(review, "decision") != ReviewDecision.APPROVE:
                    raise ValueError("success requires approve when a review result is present")
        return data

    @property
    def is_terminal(self) -> bool:
        return self.status != WorkflowStatus.RUNNING
