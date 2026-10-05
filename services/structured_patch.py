"""Exact structured mutations inside owned candidate workspaces only.

All proposal validation precedes directory/file writes. No legacy patch, journal,
Git, shell, or repository code execution is used. An apply_failed workspace may
be partially changed and must be discarded. This is patch-write confinement,
not read/process isolation or protection from hostile concurrent filesystem edits.
"""
from __future__ import annotations

from dataclasses import dataclass
import difflib
from fnmatch import fnmatchcase
import hashlib
import os
from pathlib import Path
import re
import stat
import tempfile

from pydantic import ValidationError

from orchestration.contracts import (
    ModifyFileChange, PatchApplyResult, PatchApplyStatus, PatchProposal,
)
from services.workspace import Workspace, WorkspaceError


class _PatchValidationError(Exception):
    pass


@dataclass(frozen=True)
class _PreparedChange:
    path: str
    target: Path
    before: bytes | None
    before_text: str
    after: bytes
    mode: int | None


def _target(root: Path, path: str) -> Path:
    # Independent lexical defense, including portable filesystem ambiguities.
    if (
        not isinstance(path, str) or not path or path != path.strip()
        or path.startswith("/") or "\\" in path or re.match(r"^[A-Za-z]:", path)
        or any(ord(char) < 32 or ord(char) == 127 for char in path)
        or any(part in ("", ".", "..") or ":" in part or part.endswith((" ", "."))
               for part in path.split("/"))
    ):
        raise _PatchValidationError("invalid repository-relative target path")
    parts = path.split("/")
    folded = [part.casefold() for part in parts]
    if ".git" in folded or folded[-1] == ".env" or any(
        fnmatchcase(folded[-1], pattern) for pattern in ("secrets.*", "*.key", "*.pem")
    ):
        raise _PatchValidationError(f"blocked target: {path}")
    target = root
    for index, part in enumerate(parts):
        target = target / part
        try:
            mode = target.lstat().st_mode
        except FileNotFoundError:
            continue
        if stat.S_ISLNK(mode):
            raise _PatchValidationError(f"symlink target or parent: {path}")
        if index < len(parts) - 1 and not stat.S_ISDIR(mode):
            raise _PatchValidationError(f"parent is not a directory: {path}")
    if not target.resolve(strict=False).is_relative_to(root):
        raise _PatchValidationError(f"target escapes workspace: {path}")
    return target


def _prepare(root: Path, proposal: PatchProposal) -> list[_PreparedChange]:
    # Reject aliases/ancestor files before visiting targets, on every host.
    paths = {change.path.casefold() for change in proposal.files}
    if len(paths) != len(proposal.files):
        raise _PatchValidationError("duplicate or case-ambiguous target paths")
    for path in paths:
        parts = path.split("/")
        if any("/".join(parts[:index]) in paths for index in range(1, len(parts))):
            raise _PatchValidationError("a proposed file is an ancestor of another target")
    prepared = []
    for change in sorted(proposal.files, key=lambda change: change.path):
        target = _target(root, change.path)
        try:
            existing = target.lstat()
        except FileNotFoundError:
            existing = None
        if isinstance(change, ModifyFileChange):
            if existing is None or not stat.S_ISREG(existing.st_mode):
                raise _PatchValidationError(f"modify requires an existing regular file: {change.path}")
            before = target.read_bytes()
            if hashlib.sha256(before).hexdigest() != change.original_sha256:
                raise _PatchValidationError(f"raw-byte SHA-256 mismatch: {change.path}")
            try:
                text = before.decode("utf-8", errors="strict")
            except UnicodeDecodeError as exc:
                raise _PatchValidationError(f"modify target is not UTF-8: {change.path}") from exc
            index = text.find(change.original_snippet)
            if index < 0 or text.find(change.original_snippet, index + 1) >= 0:
                raise _PatchValidationError(f"snippet must have exactly one start position: {change.path}")
            after = (text[:index] + change.replacement_snippet
                     + text[index + len(change.original_snippet):]).encode("utf-8")
            if after == before:
                raise _PatchValidationError(f"no-op modify: {change.path}")
            prepared.append(_PreparedChange(change.path, target, before, text, after,
                                             stat.S_IMODE(existing.st_mode)))
        else:
            if existing is not None:
                raise _PatchValidationError(f"create target already exists: {change.path}")
            prepared.append(_PreparedChange(change.path, target, None, "",
                                             change.content.encode("utf-8"), None))
    return prepared


def _write_change(change: _PreparedChange) -> None:
    change.target.parent.mkdir(parents=True, exist_ok=True)
    if change.before is None:
        # Exclusive creation, normal mode/umask. Failed writes may leave a file.
        with change.target.open("xb") as stream:
            stream.write(change.after)
        return
    fd, name = tempfile.mkstemp(prefix=".marcs-write-", dir=change.target.parent)
    temporary = Path(name)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(change.after)
            stream.flush()
            os.fchmod(stream.fileno(), change.mode)
        os.replace(temporary, change.target)
    finally:
        temporary.unlink(missing_ok=True)


def _diff(change: _PreparedChange, after: bytes) -> str:
    text = after.decode("utf-8", errors="strict")
    before_label = "/dev/null" if change.before is None else f"a/{change.path}"
    after_label = f"b/{change.path}"
    if change.before is None and not after:
        # Empty creation still changes file existence; difflib emits no hunk.
        return f"--- {before_label}\n+++ {after_label}\n@@ -0,0 +0,0 @@\n"
    lines = difflib.unified_diff(change.before_text.splitlines(keepends=True),
                                 text.splitlines(keepends=True),
                                 fromfile=before_label, tofile=after_label, lineterm="\n")
    return "".join(line if line.endswith("\n") else line + "\n\\ No newline at end of file\n"
                   for line in lines)


def _identity(proposal: object) -> str | None:
    value = getattr(proposal, "patch_id", None) if isinstance(proposal, PatchProposal) else (
        proposal.get("patch_id") if isinstance(proposal, dict) else None
    )
    if isinstance(value, str) and value.strip():
        try:
            value.encode("utf-8")
            return value
        except UnicodeEncodeError:
            pass
    return None


def apply_structured_patch(workspace: Workspace, proposal: PatchProposal) -> PatchApplyResult:
    """Revalidate, prepare the whole proposal, write, then derive complete evidence.

    No writes occur on validation_failed. On apply_failed, completed writes may
    be listed, but there is no canonical diff; discard the entire candidate.
    """
    patch_id = _identity(proposal)
    try:
        proposal = PatchProposal.model_validate(proposal)
    except ValidationError as exc:
        problem = exc.errors(include_input=False, include_context=False)[0]
        location = ".".join(map(str, problem["loc"])) or "proposal"
        return PatchApplyResult(patch_id=patch_id, status=PatchApplyStatus.VALIDATION_FAILED,
                                error=f"proposal schema invalid at {location}: {problem['type']}")
    try:
        if type(workspace) is not Workspace:
            raise WorkspaceError("patch application requires an owned Workspace")
        root = workspace._require_live()
        prepared = _prepare(root, proposal)
    except (_PatchValidationError, WorkspaceError) as exc:
        return PatchApplyResult(patch_id=patch_id, status=PatchApplyStatus.VALIDATION_FAILED,
                                error=str(exc))
    except OSError as exc:
        return PatchApplyResult(patch_id=patch_id, status=PatchApplyStatus.VALIDATION_FAILED,
                                error=f"filesystem prevalidation failed: {exc.strerror or type(exc).__name__}")
    written = []
    try:
        for change in prepared:
            _write_change(change)
            written.append(change.path)
        diffs = []
        for change in prepared:
            after = change.target.read_bytes()
            if after != change.after:
                raise OSError("post-image differs from the prepared content")
            diffs.append(_diff(change, after))
        return PatchApplyResult(patch_id=proposal.patch_id, status=PatchApplyStatus.SUCCESS,
                                unified_diff="".join(diffs), files_modified=written)
    except (OSError, UnicodeError) as exc:
        return PatchApplyResult(patch_id=patch_id, status=PatchApplyStatus.APPLY_FAILED,
                                files_modified=written,
                                error=f"candidate application/evidence failed: {exc.strerror or str(exc)}"
                                if isinstance(exc, OSError) else "candidate post-image is not UTF-8")
