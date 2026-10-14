"""Four injected async reasoning operations for the future host/orchestrator.

No transitions, stage retries, execution, application, logging, or persistence.
The reviewer is a separate invocation supplied all candidate evidence explicitly.
Approved command/application records are host attestations, not authenticated
execution proof. Task relevance and review quality remain model judgments.
"""
from __future__ import annotations

from pydantic import BaseModel, ValidationError

from core.reasoning_contracts import AgentOutcome, EvidenceBundle, PatchDraft, ReasoningError, ReasoningLimits, TargetScope, bounded
from agents.reasoning_prompts import PLAN_PROMPT, REVIEW_PROMPT, TASK_PROMPT, WRITER_PROMPT
from core.model_gateway import ModelGateway
from core.model_providers import ModelError, canonical_json
from core.prompt_spec import PromptSpec
from orchestration.contracts import (
    PatchApplyResult, PatchProposal, Plan, RepoProfile, ReviewResult, TaskSpec,
    ValidationCommand, ValidationResult,
)
from services.patch_conversion import convert_patch_draft
from services.reasoning_context import verify_bundle
from services.repository import RepositoryView


def _data(model: BaseModel):
    return model.model_dump(mode="json")


def _context(bundle: EvidenceBundle):
    # Complete preimages are host-only. The writer sees exactly attributed excerpts.
    return bundle.model_dump(mode="json", exclude={"snapshots"})


def _task(task: TaskSpec, limits: ReasoningLimits):
    task = TaskSpec.model_validate(task)
    bounded(task.raw_input, limits.task_bytes, nonblank=True)
    bounded(canonical_json(_data(task)), limits.prompt_bytes)
    return task


def _command_proposal(command: ValidationCommand) -> None:
    argv = command.argv
    family = argv[:3] == ["python", "-m", "pytest"] or argv[0] in ("pytest", "mypy") or argv[:2] == ["ruff", "check"]
    if not family or any(arg in ("--fix", "--fix-only", "--unsafe-fixes", "-p", "--pyargs")
                         or arg.startswith(("--fix=", "--unsafe-fixes=", "-p")) for arg in argv):
        raise ReasoningError("policy_blocked")
    if len(argv) > 64:
        raise ReasoningError("input_budget")


def _plan(plan: Plan, limits: ReasoningLimits):
    plan = Plan.model_validate(plan)
    if not plan.steps or not plan.files_to_modify:
        raise ReasoningError("invalid_plan")
    if (len(plan.files_to_modify) > limits.targets or len(plan.files_to_inspect) > limits.files
            or len(plan.steps) > 32 or len(plan.assumptions) > 32 or len(plan.validation_commands) > limits.commands):
        raise ReasoningError("input_budget")
    if len(set(plan.files_to_modify)) != len(plan.files_to_modify):
        raise ReasoningError("invalid_plan")
    for command in plan.validation_commands:
        # Only a family-level proposal filter. Slice 4 remains the authorization
        # authority for flags, paths, liveness and logical argv canonicalization.
        try:
            _command_proposal(command)
        except ReasoningError:
            raise ReasoningError("invalid_plan") from None
    bounded(canonical_json(_data(plan)), limits.prompt_bytes)
    return plan


async def _generate(gateway: ModelGateway, prompt: PromptSpec, payload: dict, schema, limits):
    if type(gateway) is not ModelGateway or type(prompt) is not PromptSpec:
        raise ReasoningError("invalid_input")
    serialized = bounded(canonical_json(payload), limits.prompt_bytes)
    rendered = prompt.render({"input": serialized})
    bounded(rendered.system_prompt + rendered.user_message, limits.prompt_bytes)
    return await gateway.generate_structured(system_prompt=rendered.system_prompt, user_message=rendered.user_message,
                                             output_schema=schema, agent_name=prompt.name, prompt_version=prompt.version)


async def _outcome(schema, operation):
    try:
        output, refs, assumptions = await operation()
        output = schema.model_validate(output)
        return AgentOutcome[schema](status="success", output=output,
                                    assumptions=tuple(assumptions), evidence_refs=tuple(sorted(set(refs))))
    except ModelError as exc:
        code = {"schema_validation": "model_schema_error", "malformed_json": "model_schema_error",
                "refusal": "model_refusal"}.get(exc.code, exc.code)
    except ReasoningError as exc:
        code = exc.code
    except (ValidationError, UnicodeError, ValueError, TypeError):
        code = "invalid_input"
    except Exception:
        code = "provider_failure"
    # CancelledError inherits BaseException and deliberately escapes.
    return AgentOutcome[schema](status="blocked" if code == "policy_blocked" else "failed",
                                error_code=code, message="Reasoning boundary rejected the operation.")


async def understand_task(raw_input: str, *, gateway: ModelGateway, prompt: PromptSpec = TASK_PROMPT,
                          limits: ReasoningLimits | None = None) -> AgentOutcome[TaskSpec]:
    async def operation():
        budget = ReasoningLimits.model_validate(limits or ReasoningLimits())
        raw = bounded(raw_input, budget.task_bytes, nonblank=True)
        result = await _generate(gateway, prompt, {"raw_input": raw}, TaskSpec, budget)
        if result.raw_input != raw:
            raise ReasoningError("model_schema_error")
        return result, (), ()
    return await _outcome(TaskSpec, operation)


async def create_plan(task: TaskSpec, profile: RepoProfile, context: EvidenceBundle, *, gateway: ModelGateway,
                      constraints: tuple[str, ...] = (), proposed_creation_paths: tuple[str, ...] = (),
                      prompt: PromptSpec = PLAN_PROMPT, limits: ReasoningLimits | None = None) -> AgentOutcome[Plan]:
    """Host-supplied proposed creation names are suggestions, never permissions."""
    async def operation():
        budget = ReasoningLimits.model_validate(limits or ReasoningLimits())
        checked_task, checked_profile = _task(task, budget), RepoProfile.model_validate(profile)
        bundle = verify_bundle(context, budget)
        if type(constraints) is not tuple or len(constraints) > 32:
            raise ReasoningError("invalid_input")
        for value in constraints:
            bounded(value, budget.task_bytes, nonblank=True)
        suggestions = TargetScope(create=proposed_creation_paths).create
        if len(suggestions) > budget.targets:
            raise ReasoningError("input_budget")
        result = _plan(await _generate(gateway, prompt, {"task": _data(checked_task), "profile": _data(checked_profile),
            "context": _context(bundle), "constraints": constraints, "proposed_creation_paths": suggestions,
            "command_status": "proposed_not_approved"}, Plan, budget), budget)
        observed = set(bundle.observed_files) | {s.path for s in bundle.sources}
        if any(path not in observed for path in result.files_to_inspect):
            raise ReasoningError("invalid_plan")
        if any(path not in observed and path not in suggestions for path in result.files_to_modify):
            raise ReasoningError("invalid_plan")
        if any(path not in observed for path in result.files_to_modify) and not result.assumptions:
            raise ReasoningError("invalid_plan")
        return result, (s.path for s in bundle.sources), result.assumptions
    return await _outcome(Plan, operation)


async def propose_patch(task: TaskSpec, plan: Plan, context: EvidenceBundle, *, gateway: ModelGateway,
                        view: RepositoryView, run_id: str, patch_id: str, iteration: int,
                        scope: TargetScope | None = None, prompt: PromptSpec = WRITER_PROMPT,
                        limits: ReasoningLimits | None = None) -> AgentOutcome[PatchProposal]:
    async def operation():
        budget = ReasoningLimits.model_validate(limits or ReasoningLimits())
        checked_task, checked_plan, bundle = _task(task, budget), _plan(plan, budget), verify_bundle(context, budget)
        # Detach host scope before awaiting model generation. No deep reactive state.
        checked_scope = TargetScope.model_validate(scope) if scope is not None else TargetScope(
            modify=tuple(s.path for s in bundle.snapshots if s.path in checked_plan.files_to_modify))
        draft = await _generate(gateway, prompt, {"task": _data(checked_task), "plan": _data(checked_plan),
            "context": _context(bundle), "target_scope": _data(checked_scope)}, PatchDraft, budget)
        proposal = convert_patch_draft(draft, checked_plan, bundle, view=view, run_id=run_id,
                                       patch_id=patch_id, iteration=iteration, scope=checked_scope, limits=budget)
        return proposal, (s.path for s in bundle.sources), proposal.assumptions
    return await _outcome(PatchProposal, operation)


def _review_policy_preflight(policy_blocked: bool) -> None:
    if type(policy_blocked) is not bool:
        raise ReasoningError("invalid_input")
    if policy_blocked:
        raise ReasoningError("policy_blocked")


def _review_plan_consistency(plan: Plan, proposal: PatchProposal) -> tuple[Plan, PatchProposal]:
    """Plan membership is necessary consistency, not host target authorization."""
    plan = Plan.model_validate(plan)
    proposal = PatchProposal.model_validate(proposal)
    if not {change.path for change in proposal.files}.issubset(set(plan.files_to_modify)):
        raise ReasoningError("off_plan_candidate")
    return plan, proposal


def review_evidence_gate(proposal: PatchProposal, applied: PatchApplyResult, expected: tuple[ValidationCommand, ...],
                         results: tuple[ValidationResult, ...], *, plan: Plan, policy_blocked: bool = False) -> None:
    """Pure approval consistency gate. Records must come from the trusted host.

Exact logical argv only: no alias guessing and no command-policy authorization.
No supplementary-result category is implemented; unexpected records fail closed.
"""
    _review_policy_preflight(policy_blocked)
    try:
        plan, proposal = _review_plan_consistency(plan, proposal)
        applied = PatchApplyResult.model_validate(applied)
        expected = tuple(ValidationCommand.model_validate(c) for c in expected)
        results = tuple(ValidationResult.model_validate(r) for r in results)
    except ValidationError:
        raise ReasoningError("inconsistent_validation_evidence") from None
    if (applied.patch_id != proposal.patch_id or applied.status != "success" or not applied.unified_diff.strip()
            or applied.error is not None or set(applied.files_modified) != {c.path for c in proposal.files}):
        raise ReasoningError("review_evidence_insufficient")
    if not expected:
        raise ReasoningError("review_evidence_insufficient")
    for command in expected:
        _command_proposal(command)
        argv = command.argv
        # The Slice-4 runner emits canonical prefixes, flags, then -- + targets.
        # Host callers must supply that representation; we never guess aliases.
        offset = 3 if argv[:3] == ["python", "-m", "pytest"] else 2 if argv[:2] == ["ruff", "check"] else 1
        if argv[0] == "pytest" or any(not arg.startswith("-") for arg in argv[offset:argv.index("--") if "--" in argv else len(argv)]):
            raise ReasoningError("inconsistent_validation_evidence")
        if argv[0] in ("ruff", "mypy") and ("--" not in argv or argv[-1] == "--"):
            raise ReasoningError("inconsistent_validation_evidence")
    required = [tuple(command.argv) for command in expected]
    actual = [tuple(result.argv) for result in results]
    if len(set(required)) != len(required) or len(set(actual)) != len(actual):
        raise ReasoningError("inconsistent_validation_evidence")
    if any(result.patch_id != proposal.patch_id for result in results) or set(actual) - set(required):
        raise ReasoningError("inconsistent_validation_evidence")
    if set(required) - set(actual):
        raise ReasoningError("review_evidence_insufficient")
    if any(not result.passed for result in results):
        raise ReasoningError("inconsistent_validation_evidence")


async def review_candidate(task: TaskSpec, plan: Plan, proposal: PatchProposal, applied: PatchApplyResult,
                           validation_results: tuple[ValidationResult, ...], expected_commands: tuple[ValidationCommand, ...],
                           context: EvidenceBundle, *, gateway: ModelGateway, policy_blocked: bool = False,
                           prompt: PromptSpec = REVIEW_PROMPT, limits: ReasoningLimits | None = None) -> AgentOutcome[ReviewResult]:
    async def operation():
        _review_policy_preflight(policy_blocked)
        budget = ReasoningLimits.model_validate(limits or ReasoningLimits())
        checked_plan, patch = _review_plan_consistency(plan, proposal)
        checked_task, checked_plan = _task(task, budget), _plan(checked_plan, budget)
        apply = PatchApplyResult.model_validate(applied)
        bundle = verify_bundle(context, budget)
        if type(validation_results) is not tuple or type(expected_commands) is not tuple:
            raise ReasoningError("invalid_input")
        results = tuple(ValidationResult.model_validate(value) for value in validation_results)
        expected = tuple(ValidationCommand.model_validate(value) for value in expected_commands)
        if len(patch.files) > budget.targets or len(results) > budget.commands or len(expected) > budget.commands:
            raise ReasoningError("input_budget")
        bounded(apply.unified_diff, budget.diff_bytes)
        for command in expected:
            _command_proposal(command)  # Still not Slice-4 execution authorization.
        bounded(canonical_json([_data(r) for r in results]), budget.validation_bytes)
        if apply.patch_id != patch.patch_id or any(r.patch_id != patch.patch_id for r in results):
            raise ReasoningError("inconsistent_validation_evidence")
        allowed_paths = {s.path for s in bundle.sources} | {c.path for c in patch.files}
        result = await _generate(gateway, prompt, {"task": _data(checked_task), "plan": _data(checked_plan),
            "proposal": _data(patch), "application": _data(apply), "validation_results": [_data(r) for r in results],
            "host_approved_expected_commands": [_data(c) for c in expected], "policy_blocked": policy_blocked,
            "context": _context(bundle)}, ReviewResult, budget)
        if result.patch_id != patch.patch_id or any(f.path is not None and f.path not in allowed_paths for f in result.findings):
            raise ReasoningError("model_schema_error")
        if result.decision == "approve":
            review_evidence_gate(patch, apply, expected, results, plan=checked_plan, policy_blocked=policy_blocked)
        return result, allowed_paths, ()
    return await _outcome(ReviewResult, operation)
