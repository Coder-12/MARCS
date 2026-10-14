"""Slice-6 data boundaries, not workflow state or execution attestations.

Canonical mutable contracts are revalidated and copied at operation entry.
Frozen source records carry host evidence, not cryptographic authentication.
"""
from __future__ import annotations

from typing import Annotated, Generic, Literal, TypeVar

from pydantic import BaseModel, ConfigDict, Field, model_validator

from orchestration.contracts import _NonBlank, _RepoPath, _Text


class ReasoningError(Exception):
    def __init__(self, code: str):
        self.code = code
        super().__init__(code)  # Never include source, provider text, or filesystem errors.


class FrozenRecord(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid", frozen=True,
                              revalidate_instances="always", allow_inf_nan=False)


class ReasoningLimits(FrozenRecord):
    task_bytes: int = Field(default=8192, ge=1, le=32768)
    queries: int = Field(default=4, ge=1, le=16)
    query_bytes: int = Field(default=256, ge=1, le=1024)
    files: int = Field(default=8, ge=1, le=32)
    evidence_bytes: int = Field(default=4096, ge=1, le=32768)
    context_bytes: int = Field(default=16384, ge=1, le=65536)
    snapshot_bytes: int = Field(default=65536, ge=1, le=1048576)
    snapshot_total_bytes: int = Field(default=262144, ge=1, le=4194304)
    lines: int = Field(default=2000, ge=1, le=65536)
    inventory_entries: int = Field(default=2000, ge=1, le=8000)
    targets: int = Field(default=8, ge=1, le=32)
    draft_bytes: int = Field(default=16384, ge=1, le=262144)
    diff_bytes: int = Field(default=16384, ge=1, le=65536)
    validation_bytes: int = Field(default=8192, ge=1, le=32768)
    commands: int = Field(default=8, ge=1, le=32)
    prompt_bytes: int = Field(default=65536, ge=1, le=131072)


class SourceSnapshot(FrozenRecord):
    path: _RepoPath
    content: _Text = Field(repr=False)
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


class SourceEvidence(FrozenRecord):
    path: _RepoPath
    content: _Text = Field(repr=False)
    start_line: int | None = Field(default=None, ge=1)
    end_line: int | None = Field(default=None, ge=1)
    complete: bool
    truncated: bool
    sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    origin: Literal["repository_read"] = "repository_read"
    reason: _NonBlank
    warnings: tuple[_NonBlank, ...] = ()

    @model_validator(mode="after")
    def coherent(self):
        if (self.start_line is None) != (self.end_line is None):
            raise ValueError("paired source lines required")
        if self.content and self.start_line is None:
            raise ValueError("nonempty source requires line attribution")
        if not self.content and self.start_line is not None:
            raise ValueError("empty source has no represented lines")
        if self.start_line is not None and self.start_line > self.end_line:
            raise ValueError("invalid source interval")
        if self.content:
            count = self.content.count("\n") + (0 if self.content.endswith("\n") else 1)
            if count != self.end_line - self.start_line + 1:
                raise ValueError("line interval must describe represented LF-based lines")
        if self.complete and (self.truncated or self.start_line not in (None, 1) or self.sha256 is None):
            raise ValueError("complete evidence requires whole-source identity")
        return self


class EvidenceBundle(FrozenRecord):
    sources: tuple[SourceEvidence, ...]
    snapshots: tuple[SourceSnapshot, ...] = Field(default=(), repr=False)
    observed_files: tuple[_RepoPath, ...]
    inventory_complete: bool
    warnings: tuple[_NonBlank, ...] = ()

    @model_validator(mode="after")
    def unique(self):
        for paths in ([s.path for s in self.sources], [s.path for s in self.snapshots], self.observed_files):
            if len(set(paths)) != len(paths):
                raise ValueError("duplicate context paths")
        return self


class TargetScope(FrozenRecord):
    modify: tuple[_RepoPath, ...] = ()
    create: tuple[_RepoPath, ...] = ()


class DraftChange(FrozenRecord):
    path: _RepoPath
    operation: Literal["modify", "create"]
    original_snippet: _Text | None
    replacement_snippet: _Text | None
    content: _Text | None
    justification: _NonBlank

    @model_validator(mode="after")
    def operation_fields(self):
        if self.operation == "modify":
            if not self.original_snippet or self.replacement_snippet is None or self.content is not None:
                raise ValueError("modify requires snippets and no content")
        elif self.content is None or self.original_snippet is not None or self.replacement_snippet is not None:
            raise ValueError("create requires content and no snippets")
        return self


class PatchDraft(FrozenRecord):
    changes: list[DraftChange] = Field(min_length=1, max_length=32)
    assumptions: list[_NonBlank] = Field(default_factory=list, max_length=32)
    risks: list[_NonBlank] = Field(default_factory=list, max_length=32)


Output = TypeVar("Output", bound=BaseModel)


class AgentOutcome(FrozenRecord, Generic[Output]):
    status: Literal["success", "failed", "blocked"]
    output: Output | None = None
    error_code: str | None = Field(default=None, max_length=64)
    message: str = Field(default="", max_length=256)
    assumptions: tuple[_NonBlank, ...] = ()
    evidence_refs: tuple[_RepoPath, ...] = ()

    @model_validator(mode="after")
    def disposition(self):
        if self.status == "success":
            if self.output is None or self.error_code is not None:
                raise ValueError("success requires output and no error")
        elif self.output is not None or not self.error_code or not self.error_code.strip():
            raise ValueError("failure requires error and no output")
        return self


def bounded(text: str, limit: int, *, nonblank: bool = False) -> str:
    if not isinstance(text, str) or (nonblank and not text.strip()):
        raise ReasoningError("invalid_input")
    try:
        size = len(text.encode("utf-8"))
    except UnicodeError:
        raise ReasoningError("invalid_input") from None
    if size > limit:
        raise ReasoningError("input_budget")
    return text
