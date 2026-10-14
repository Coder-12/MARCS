"""Host-authorized, read-only PatchDraft -> canonical PatchProposal conversion.

No application, directories, shell, Git, retries, or acceptance decisions. A
proposal is not proof of applicability or correctness. Slice 2 checks again.
"""
from __future__ import annotations

from pydantic import ValidationError

from core.reasoning_contracts import EvidenceBundle, PatchDraft, ReasoningError, ReasoningLimits, TargetScope, bounded
from core.model_providers import canonical_json
from orchestration.contracts import CreateFileChange, ModifyFileChange, PatchProposal, Plan
from services.reasoning_context import absent_target, verify_bundle, verify_source_evidence
from services.repository import RepositoryError, RepositoryView, _path_parts


def convert_patch_draft(draft: PatchDraft, plan: Plan, context: EvidenceBundle, *, view: RepositoryView,
                        run_id: str, patch_id: str, iteration: int, scope: TargetScope | None = None,
                        limits: ReasoningLimits | None = None) -> PatchProposal:
    limits = ReasoningLimits.model_validate(limits or ReasoningLimits())
    try:
        draft = PatchDraft.model_validate(draft)
        plan = Plan.model_validate(plan)
        context = verify_bundle(context, limits)
        scope = TargetScope.model_validate(scope) if scope is not None else TargetScope(
            modify=tuple(s.path for s in context.snapshots if s.path in plan.files_to_modify))
    except ValidationError:
        raise ReasoningError("invalid_patch_draft") from None
    bounded(canonical_json(draft.model_dump(mode="json")), limits.draft_bytes)
    if len(draft.changes) > limits.targets or len(scope.modify) + len(scope.create) > 2 * limits.targets:
        raise ReasoningError("input_budget")
    paths = [change.path.casefold() for change in draft.changes]
    if len(set(paths)) != len(paths):
        raise ReasoningError("invalid_patch_draft")
    for path in paths:
        parts = path.split("/")
        if any("/".join(parts[:index]) in paths for index in range(1, len(parts))):
            raise ReasoningError("invalid_patch_draft")
    sources = {s.path: s for s in context.sources}
    changes = []
    for change in sorted(draft.changes, key=lambda c: c.path):
        try:
            _path_parts(change.path)
        except RepositoryError:
            raise ReasoningError("unauthorized_target") from None
        # Colon aliases are unsupported even on hosts that allow them as names.
        if ":" in change.path or change.path not in plan.files_to_modify:
            raise ReasoningError("unauthorized_target")
        if change.operation == "modify":
            if change.path not in scope.modify:
                raise ReasoningError("unauthorized_target")
            snapshot = verify_source_evidence(view, context, change.path, limits)
            # find(..., first + 1) detects overlapping occurrences too.
            first = snapshot.content.find(change.original_snippet)
            if (first < 0 or snapshot.content.find(change.original_snippet, first + 1) >= 0
                    or change.original_snippet not in sources[change.path].content
                    or change.original_snippet == change.replacement_snippet):
                raise ReasoningError("invalid_patch_draft")
            changes.append(ModifyFileChange(path=change.path, original_sha256=snapshot.sha256,
                                           original_snippet=change.original_snippet,
                                           replacement_snippet=change.replacement_snippet,
                                           justification=change.justification))
        else:
            if change.path not in scope.create:
                raise ReasoningError("unauthorized_target")
            absent_target(view, change.path, limits=limits)
            bounded(change.content, limits.draft_bytes)
            changes.append(CreateFileChange(path=change.path, content=change.content, justification=change.justification))
    try:
        proposal = PatchProposal(run_id=run_id, patch_id=patch_id, iteration=iteration, files=changes,
                                 assumptions=list(draft.assumptions), risks=list(draft.risks))
        return PatchProposal.model_validate(proposal)
    except ValidationError:
        raise ReasoningError("patch_conversion_failed") from None
