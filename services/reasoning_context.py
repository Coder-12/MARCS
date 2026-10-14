"""Host-invoked read-only evidence preparation; no reasoning or retrieval loops.

RepositoryText uses binary reads and exact UTF-8 decoding. A line-1 read with
truncated=False therefore round-trips original bytes, including BOM/CRLF/EOF.
Snapshots are bounded, not authenticated. Reverification detects ordinary
staleness, not hostile concurrent filesystem races.
"""
from __future__ import annotations

import hashlib
import os
import stat

from core.reasoning_contracts import (
    EvidenceBundle, ReasoningError, ReasoningLimits, SourceEvidence, SourceSnapshot, bounded,
)
from orchestration.contracts import RepositoryAnalysisResult, RepositoryEntryKind, RetrievedEvidence
from services.code_search import search_code
from services.repository import CACHE_DIRS, RepositoryError, RepositoryView, _blocked, _lines, _path_parts, analyze_repository


def _require_view(view: RepositoryView):
    if type(view) is not RepositoryView:
        raise ReasoningError("unsupported_repository")


def complete_source(view: RepositoryView, path: str, *, limits: ReasoningLimits) -> SourceSnapshot:
    _require_view(view)
    try:
        read = view.read_text(path, start_line=1, max_lines=limits.lines,
                             max_bytes=limits.snapshot_bytes, max_scan_bytes=limits.snapshot_bytes)
    except RepositoryError as exc:
        code = "incomplete_source" if exc.code in ("invalid_utf8", "scan_limit", "unreadable") else "unauthorized_target"
        raise ReasoningError(code) from None
    if read.truncated:
        raise ReasoningError("incomplete_source")
    raw = read.content.encode("utf-8")
    return SourceSnapshot(path=read.path, content=read.content, sha256=hashlib.sha256(raw).hexdigest())


def verify_bundle(bundle: EvidenceBundle, limits: ReasoningLimits) -> EvidenceBundle:
    bundle = EvidenceBundle.model_validate(bundle)
    if not bundle.sources or len(bundle.sources) > limits.files:
        raise ReasoningError("insufficient_context")
    if len(bundle.observed_files) > limits.inventory_entries or len(bundle.snapshots) > limits.files:
        raise ReasoningError("input_budget")
    total = 0
    for source in bundle.sources:
        bounded(source.content, limits.evidence_bytes)
        total += len(source.content.encode("utf-8"))
        if source.end_line is not None and source.end_line - source.start_line + 1 > limits.lines:
            raise ReasoningError("input_budget")
    if total > limits.context_bytes:
        raise ReasoningError("input_budget")
    for snapshot in bundle.snapshots:
        bounded(snapshot.content, limits.snapshot_bytes)
    if sum(len(s.content.encode("utf-8")) for s in bundle.snapshots) > limits.snapshot_total_bytes:
        raise ReasoningError("input_budget")
    return bundle


def prepare_context(view: RepositoryView, *, paths: tuple[str, ...] = (), queries: tuple[str, ...] = (),
                    required_paths: tuple[str, ...] = (), limits: ReasoningLimits | None = None,
                    analysis: RepositoryAnalysisResult | None = None) -> EvidenceBundle:
    """Explicit paths first, then lexical matches; sorted deduped final evidence.

Selection caps and incomplete reads remain explicit. Required paths fail closed.
Analysis is host evidence; it is not an authenticity token. No implicit queries.
"""
    _require_view(view)
    limits = ReasoningLimits.model_validate(limits or ReasoningLimits())
    if not all(type(value) is tuple for value in (paths, queries, required_paths)):
        raise ReasoningError("invalid_input")
    if len(queries) > limits.queries or len(paths) + len(required_paths) > 2 * limits.files:
        raise ReasoningError("input_budget")
    try:
        for path in (*paths, *required_paths):
            _path_parts(path)
        analysis = RepositoryAnalysisResult.model_validate(analysis) if analysis is not None else analyze_repository(
            view, max_entries=limits.inventory_entries)
        observed = tuple(sorted(entry.path for entry in analysis.inventory.entries if entry.kind == RepositoryEntryKind.FILE))
        warnings = set(analysis.warnings) | set(analysis.inventory.warnings)
        candidates = set(paths) | set(required_paths)
        first_match = {}
        for query in sorted(set(queries)):
            bounded(query, limits.query_bytes, nonblank=True)
            result = search_code(view, query, max_matches=limits.files, max_file_size=limits.snapshot_bytes,
                                 max_line_bytes=limits.evidence_bytes, max_query_bytes=limits.query_bytes,
                                 max_entries=limits.inventory_entries, timeout=2.0)
            warnings.update(result.warnings)
            if result.truncated:
                warnings.add("search_incomplete")
            if result.fallback_reason:
                warnings.add(result.fallback_reason)
            candidates.update(match.path for match in result.matches)
            for match in result.matches:
                first_match[match.path] = min(first_match.get(match.path, match.line_number), match.line_number)
        ordered = sorted(set(required_paths)) + sorted(candidates - set(required_paths))
        if len(set(required_paths)) > limits.files:
            raise ReasoningError("insufficient_context")
        if len(ordered) > limits.files:
            warnings.add("selection_limit")
        sources, snapshots, total, snapshot_total = [], [], 0, 0
        for path in ordered[:limits.files]:
            start = 1 if path in paths or path in required_paths else max(1, first_match.get(path, 1) - 2)
            read = view.read_text(path, start_line=start, max_lines=limits.lines,
                                 max_bytes=limits.evidence_bytes, max_scan_bytes=limits.snapshot_bytes)
            full = None
            try:
                full = complete_source(view, path, limits=limits)
            except ReasoningError as exc:
                if exc.code != "incomplete_source":
                    raise
            # Validate attribution through the canonical existing evidence contract.
            evidence = RetrievedEvidence(path=read.path, content=read.content, reason="host_selected_source",
                                         start_line=read.start_line, end_line=read.end_line)
            if read.truncated:
                warnings.add("source_truncated")
                if path in required_paths:
                    raise ReasoningError("insufficient_context")
            if start > 1:
                warnings.add("source_excerpt")
            total += len(read.content.encode("utf-8"))
            if total > limits.context_bytes:
                raise ReasoningError("input_budget")
            if full:
                snapshot_total += len(full.content.encode("utf-8"))
                if snapshot_total > limits.snapshot_total_bytes:
                    raise ReasoningError("input_budget")
                snapshots.append(full)
            sources.append(SourceEvidence(**evidence.model_dump(exclude={"reason"}), reason=evidence.reason,
                                          complete=start == 1 and not read.truncated, truncated=read.truncated,
                                          sha256=full.sha256 if full else None,
                                          warnings=("source_truncated",) if read.truncated else ()))
    except RepositoryError:
        raise ReasoningError("insufficient_context") from None
    return verify_bundle(EvidenceBundle(sources=tuple(sorted(sources, key=lambda s: s.path)),
                                       snapshots=tuple(sorted(snapshots, key=lambda s: s.path)),
                                       observed_files=observed, inventory_complete=not (
                                           analysis.truncated or analysis.inventory.truncated
                                           or analysis.inventory.unreadable_entry_count
                                           or analysis.inventory.unsupported_entry_count),
                                       warnings=tuple(sorted(warnings))), limits)


def verify_source_evidence(view: RepositoryView, bundle: EvidenceBundle, path: str,
                           limits: ReasoningLimits) -> SourceSnapshot:
    """Re-read preimage and bind exact writer excerpt to it before conversion."""
    snapshots = {s.path: s for s in bundle.snapshots}
    sources = {s.path: s for s in bundle.sources}
    if path not in snapshots or path not in sources:
        raise ReasoningError("incomplete_source")
    old, source = snapshots[path], sources[path]
    current = complete_source(view, path, limits=limits)
    if current.sha256 != old.sha256 or current.content != old.content:
        raise ReasoningError("stale_source_evidence")
    if source.sha256 != current.sha256:
        raise ReasoningError("stale_source_evidence")
    lines = _lines(current.content)
    if source.content:
        if source.start_line is None or source.end_line > len(lines):
            raise ReasoningError("incomplete_source")
        represented = "".join(lines[source.start_line - 1:source.end_line])
        if source.truncated:
            # A bounded read can end partway through its final attributed line.
            if not represented.startswith(source.content):
                raise ReasoningError("incomplete_source")
        elif represented != source.content:
            raise ReasoningError("incomplete_source")
    elif current.content:
        raise ReasoningError("incomplete_source")
    if source.complete and source.content != current.content:
        raise ReasoningError("incomplete_source")
    return current


def absent_target(view: RepositoryView, path: str, *, limits: ReasoningLimits) -> None:
    """Narrow non-writing creation preflight using the existing repository policy.

Private policy helpers are reused deliberately rather than changing Slice 3 or
creating a parallel filesystem layer. Case aliases fail closed on every host.
Existing directories are scanned with a finite bound. Missing parents are allowed.
"""
    _require_view(view)
    try:
        parts = _path_parts(path)
        if _blocked(path) or any(part.casefold() in CACHE_DIRS for part in parts) or any(":" in part for part in parts):
            raise ReasoningError("unauthorized_target")
        target = view._require_root()
        missing = False
        for index, part in enumerate(parts):
            if not missing:
                with os.scandir(target) as entries:
                    for count, entry in enumerate(entries, 1):
                        if count > limits.inventory_entries:
                            raise ReasoningError("insufficient_context")
                        if entry.name.casefold() == part.casefold() and entry.name != part:
                            raise ReasoningError("unauthorized_target")
            target /= part
            try:
                mode = target.lstat().st_mode
            except FileNotFoundError:
                missing = True
                continue
            if stat.S_ISLNK(mode) or (index < len(parts) - 1 and not stat.S_ISDIR(mode)):
                raise ReasoningError("unauthorized_target")
            if index == len(parts) - 1:
                raise ReasoningError("unauthorized_target")
        if not target.resolve(strict=False).is_relative_to(view.root):
            raise ReasoningError("unauthorized_target")
    except (RepositoryError, OSError, RuntimeError):
        raise ReasoningError("unauthorized_target") from None
