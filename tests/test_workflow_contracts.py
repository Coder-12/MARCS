import copy
import json

import pytest
from pydantic import ValidationError

from orchestration.contracts import (
    AttemptBudget,
    CreateFileChange,
    FailureCategory,
    ModifyFileChange,
    PatchOperation,
    PatchApplyResult,
    PatchApplyStatus,
    PatchProposal,
    Plan,
    RepoProfile,
    RepositoryEntryKind,
    RepositoryEntry,
    RepositoryInventory,
    RepositoryText,
    RepositoryFactEvidence,
    RepositoryAnalysisResult,
    SearchBackend,
    CodeSearchMatch,
    CodeSearchResult,
    RetrievedEvidence,
    ReviewDecision,
    ReviewFinding,
    ReviewResult,
    TaskSpec,
    TaskType,
    ValidationCommand,
    ValidationResult,
    WorkflowState,
    WorkflowStatus,
)


TASK = {"raw_input": "Fix parsing", "task_type": "bug_fix", "goal": "Reject bad input"}
MODIFY = {
    "path": "src/parser.py", "operation": "modify", "original_sha256": "a" * 64,
    "original_snippet": "return None", "replacement_snippet": "raise ValueError()",
    "justification": "Report malformed input",
}
CREATE = {
    "path": "tests/test_parser.py", "operation": "create", "content": "",
    "justification": "Add regression tests",
}


def patch_payload():
    return {"patch_id": "patch-1", "run_id": "run-1", "iteration": 1,
            "files": [copy.deepcopy(MODIFY), copy.deepcopy(CREATE)]}


def model_examples():
    return [
        (TaskSpec, copy.deepcopy(TASK)),
        (RepoProfile, {}),
        (RepositoryEntry, {"path": "a.py", "kind": "file", "size_bytes": 0}),
        (RepositoryInventory, {}),
        (RepositoryText, {"path": "a.py", "content": "line", "start_line": 1, "end_line": 1}),
        (RepositoryFactEvidence, {"field": "language", "value": "python", "path": "a.py", "reason": "Observed source"}),
        (RepositoryAnalysisResult, {"profile": {}, "inventory": {}}),
        (CodeSearchMatch, {"path": "a.py", "line_number": 1, "line": "line"}),
        (CodeSearchResult, {"query": "line", "case_sensitive": False, "backend": "python"}),
        (RetrievedEvidence, {"path": "src/parser.py", "content": "", "reason": "Task target"}),
        (ValidationCommand, {"argv": ["python", "-m", "pytest"]}),
        (Plan, {}),
        (ModifyFileChange, copy.deepcopy(MODIFY)),
        (CreateFileChange, copy.deepcopy(CREATE)),
        (PatchProposal, patch_payload()),
        (PatchApplyResult, {"patch_id": "patch-1", "status": "success", "unified_diff": "--- a/a.py\n+++ b/a.py\n",
                            "files_modified": ["a.py"]}),
        (ValidationResult, {"patch_id": "patch-1", "argv": ["python"], "exit_code": 0, "duration_ms": 0}),
        (ReviewFinding, {"message": "Missing coverage"}),
        (ReviewResult, {"patch_id": "patch-1", "decision": "approve", "summary": "Candidate accepted"}),
        (AttemptBudget, {}),
        (WorkflowState, {"run_id": "run-1", "task": copy.deepcopy(TASK)}),
    ]


@pytest.mark.parametrize("model,payload", model_examples())
def test_minimal_contracts_are_deterministic_json_data(model, payload):
    first, second = model.model_validate(payload), model.model_validate(payload)
    assert first.model_dump_json() == second.model_dump_json()
    data = first.model_dump(mode="json")
    assert model.model_validate(json.loads(json.dumps(data))).model_dump(mode="json") == data


@pytest.mark.parametrize("model,payload", model_examples())
def test_unknown_fields_are_rejected(model, payload):
    with pytest.raises(ValidationError):
        model.model_validate({**payload, "unexpected": "value"})


@pytest.mark.parametrize("field", ["raw_input", "goal"])
@pytest.mark.parametrize("value", ["", " \n\t", 123, None])
def test_task_requires_real_nonblank_text(field, value):
    with pytest.raises(ValidationError):
        TaskSpec(**{**TASK, field: value})


@pytest.mark.parametrize("task_type", list(TaskType))
def test_task_type_serialized_values(task_type):
    task = TaskSpec(**{**TASK, "task_type": task_type.value})
    assert task.task_type == task_type
    assert task.model_dump(mode="json")["task_type"] == task_type.value


@pytest.mark.parametrize("model,payload,field", [
    (TaskSpec, TASK, "task_type"),
    (ModifyFileChange, MODIFY, "operation"),
    (PatchApplyResult, {"patch_id": "patch-1", "error": "Invalid proposal"}, "status"),
    (WorkflowState, {"run_id": "run-1", "task": TASK}, "status"),
    (WorkflowState, {"run_id": "run-1", "task": TASK}, "failure_category"),
    (ReviewResult, {"patch_id": "patch-1", "decision": "approve", "summary": "OK"}, "decision"),
])
def test_unknown_enum_values_are_rejected(model, payload, field):
    with pytest.raises(ValidationError):
        model.model_validate({**payload, field: "not-a-value"})


@pytest.mark.parametrize("model,payload,field,value", [
    (TaskSpec, TASK, "task_type", b"feature"),
    (PatchApplyResult, {"patch_id": "patch-1", "error": "Invalid proposal"}, "status", b"validation_failed"),
    (WorkflowState, {"run_id": "run-1", "task": TASK}, "status", b"running"),
    (WorkflowState, {"run_id": "run-1", "task": TASK}, "failure_category", b"tool_error"),
    (ReviewResult, {"patch_id": "patch-1", "summary": "OK"}, "decision", b"approve"),
])
def test_enum_values_are_strings_without_bytes_coercion(model, payload, field, value):
    with pytest.raises(ValidationError):
        model.model_validate({**payload, field: value})


@pytest.mark.parametrize("model,payload,field,value", [
    (TaskSpec, TASK, "raw_input", "\ud800"),
    (RetrievedEvidence, {"path": "a.py", "reason": "Relevant"}, "content", "\ud800"),
    (CreateFileChange, CREATE, "content", "\ud800"),
    (ModifyFileChange, MODIFY, "replacement_snippet", "\ud800"),
    (ValidationCommand, {}, "argv", ["python", "\ud800"]),
    (ValidationResult, {"patch_id": "patch-1", "argv": ["python"], "exit_code": 0, "duration_ms": 0}, "stdout", "\ud800"),
    (WorkflowState, {"run_id": "run-1", "task": TASK}, "final_report", "\ud800"),
])
def test_non_utf8_text_rejected_before_json_serialization(model, payload, field, value):
    with pytest.raises(ValidationError):
        model.model_validate({**payload, field: value})


def test_collection_and_nested_defaults_are_independent():
    for model, payload in model_examples():
        first, second = model.model_validate(payload), model.model_validate(payload)
        before = second.model_dump(mode="json")
        # In-place edits deliberately require revalidation; no other instance may change.
        for name, value in first:
            if isinstance(value, list):
                value.clear()
                value.append("local edit")
        assert second.model_dump(mode="json") == before
    first = WorkflowState(run_id="run-1", task=TASK)
    second = WorkflowState(run_id="run-2", task=TASK)
    first.attempt_budget.patch_attempts = 1
    assert second.attempt_budget.patch_attempts == 0
    assert RepoProfile().language is None
    assert TaskSpec(raw_input="task", goal="goal").task_type == TaskType.UNKNOWN


@pytest.mark.parametrize("path", [
    "src/parser.py", "tests/test parser.py", ".github/workflows/check.yml", "café/file.py",
])
def test_paths_are_logical_repository_relative_names(path):
    assert RetrievedEvidence(path=path, content="", reason="Relevant").path == path


@pytest.mark.parametrize("path", [
    "", " ", "/etc/passwd", "../escape", "src/../escape", "C:/escape", "C:\\escape",
    "\\\\server\\file", "a\\b", ".", "./a", "a//b", "a/", "a\x00b", "a\nb",
])
def test_invalid_paths_rejected_at_all_path_boundaries(path):
    examples = [
        (RepoProfile, {"important_dirs": [path]}),
        (RetrievedEvidence, {"path": path, "content": "", "reason": "Relevant"}),
        (Plan, {"files_to_inspect": [path]}),
        (Plan, {"files_to_modify": [path]}),
        (ModifyFileChange, {**MODIFY, "path": path}),
        (CreateFileChange, {**CREATE, "path": path}),
        (ReviewFinding, {"message": "Concern", "path": path}),
        (PatchApplyResult, {"patch_id": "patch-1", "status": "apply_failed", "error": "Write failed", "files_modified": [path]}),
    ]
    for model, payload in examples:
        with pytest.raises(ValidationError):
            model.model_validate(payload)


@pytest.mark.parametrize("start,end", [(None, None), (1, 1), (2, 7)])
def test_evidence_whole_file_and_inclusive_snippets(start, end):
    evidence = RetrievedEvidence(path="src/a.py", content="source", reason="Task context",
                                 start_line=start, end_line=end)
    assert (evidence.start_line, evidence.end_line) == (start, end)


@pytest.mark.parametrize("start,end", [(None, 1), (1, None), (0, 1), (-1, 1), (2, 1), (1, "2"), (True, 1)])
def test_invalid_evidence_line_ranges(start, end):
    with pytest.raises(ValidationError):
        RetrievedEvidence(path="a.py", content="", reason="Relevant", start_line=start, end_line=end)


@pytest.mark.parametrize("argv", [[], [""], [" \t"], [123], ["python", 123], "python -m pytest", ("python",), ["python", "a\x00b"]])
def test_invalid_command_vectors(argv):
    with pytest.raises(ValidationError):
        ValidationCommand(argv=argv)
    with pytest.raises(ValidationError):
        ValidationResult(patch_id="patch-1", argv=argv, exit_code=0, duration_ms=0)


def test_argv_is_preserved_without_shell_interpretation():
    argv = ["python", "-m", "pytest", "a file.py", "", "$(command)", "x;y", " x "]
    assert ValidationCommand(argv=argv).argv == argv
    with pytest.raises(ValidationError):
        ValidationCommand(argv=argv, shell=True)


def test_structured_multi_file_patch_and_empty_replacement():
    proposal = PatchProposal(**patch_payload())
    assert isinstance(proposal.files[0], ModifyFileChange)
    assert isinstance(proposal.files[1], CreateFileChange)
    assert [f.operation for f in proposal.files] == [PatchOperation.MODIFY, PatchOperation.CREATE]
    assert proposal.files[1].content == ""
    assert ModifyFileChange(**{**MODIFY, "replacement_snippet": ""}).replacement_snippet == ""


@pytest.mark.parametrize("field,value", [
    ("patch_id", ""), ("run_id", " \n"), ("iteration", 0), ("iteration", -1),
    ("iteration", "1"), ("iteration", True), ("files", []),
    ("files", [MODIFY, MODIFY]),
    ("files", [{**CREATE, "path": MODIFY["path"]}, MODIFY]),
    ("files", [{**CREATE, "operation": "delete"}]),
])
def test_invalid_patch_proposals(field, value):
    with pytest.raises(ValidationError):
        PatchProposal(**{**patch_payload(), field: value})


@pytest.mark.parametrize("field", ["original_sha256", "original_snippet", "replacement_snippet"])
def test_modify_requires_operation_specific_fields(field):
    change = copy.deepcopy(MODIFY)
    change.pop(field)
    with pytest.raises(ValidationError):
        PatchProposal(**{**patch_payload(), "files": [change]})
    with pytest.raises(ValidationError):
        CreateFileChange(**CREATE, **{field: MODIFY[field]})


@pytest.mark.parametrize("digest", ["", "a" * 63, "g" * 64])
def test_modify_hash_is_supplied_sha256_data(digest):
    with pytest.raises(ValidationError):
        ModifyFileChange(**{**MODIFY, "original_sha256": digest})


def test_create_requires_content_and_modify_rejects_create_content():
    missing_content = {k: v for k, v in CREATE.items() if k != "content"}
    with pytest.raises(ValidationError):
        CreateFileChange(**missing_content)
    with pytest.raises(ValidationError):
        ModifyFileChange(**MODIFY, content="new")
    with pytest.raises(ValidationError):
        ModifyFileChange(**{**MODIFY, "original_snippet": ""})


@pytest.mark.parametrize("exit_code,passed", [(0, True), (1, False), (-9, False)])
def test_validation_outcome_is_derived(exit_code, passed):
    result = ValidationResult(patch_id="patch-1", argv=["python"], exit_code=exit_code, duration_ms=1.25)
    assert result.passed is passed
    result.exit_code = 1 if passed else 0
    assert result.passed is (not passed)
    assert "passed" not in result.model_dump(mode="json")
    with pytest.raises(ValidationError):
        ValidationResult(patch_id="patch-1", argv=["python"], exit_code=exit_code, duration_ms=0, passed=not passed)


@pytest.mark.parametrize("field,value", [("duration_ms", -1), ("duration_ms", float("nan")),
                                        ("duration_ms", float("inf")), ("duration_ms", "0"),
                                        ("exit_code", "0"), ("exit_code", True)])
def test_invalid_validation_result_numbers(field, value):
    with pytest.raises(ValidationError):
        ValidationResult(**{**{"patch_id": "patch-1", "argv": ["python"], "exit_code": 0, "duration_ms": 0}, field: value})


@pytest.mark.parametrize("decision", list(ReviewDecision))
def test_review_dispositions(decision):
    review = ReviewResult(patch_id="patch-1", decision=decision.value, summary="Review explanation",
                          findings=[{"message": "Candidate concern", "path": "a.py"}])
    assert review.decision == decision
    assert isinstance(review.findings[0], ReviewFinding)
    with pytest.raises(ValidationError):
        ReviewResult(patch_id="patch-1", decision=decision, summary=" ")


def test_budget_defaults_and_total_attempts():
    budget = AttemptBudget()
    assert (budget.patch_attempts, budget.retrieval_attempts, budget.validation_attempts) == (0, 0, 0)
    assert (budget.max_patch_attempts, budget.max_retrieval_attempts, budget.max_validation_attempts) == (2, 2, 3)
    exhausted = AttemptBudget(patch_attempts=2, retrieval_attempts=2, validation_attempts=3)
    assert exhausted.patch_attempts == exhausted.max_patch_attempts
    # Two total attempts are valid; a third is not an extra allowed retry.
    with pytest.raises(ValidationError):
        AttemptBudget(patch_attempts=3)


@pytest.mark.parametrize("stage", ["patch", "retrieval", "validation"])
@pytest.mark.parametrize("current,maximum", [(-1, 2), (0, 0), (0, -1), (3, 2), (True, 2), ("1", 2)])
def test_attempt_budget_bounds(stage, current, maximum):
    with pytest.raises(ValidationError):
        AttemptBudget(**{f"{stage}_attempts": current, f"max_{stage}_attempts": maximum})


@pytest.mark.parametrize("status", list(WorkflowStatus))
def test_workflow_outcomes_without_future_stage_requirements(status):
    reason = {} if status in (WorkflowStatus.RUNNING, WorkflowStatus.SUCCESS) else {
        "failure_category": "insufficient_context"
    }
    state = WorkflowState(run_id="run-1", task=TASK, status=status.value, **reason)
    assert state.status == status
    assert state.is_terminal == (status != WorkflowStatus.RUNNING)
    assert state.final_report is None and state.plan is None and state.repo_profile is None


@pytest.mark.parametrize("status", ["failed", "blocked", "partial"])
def test_unsuccessful_outcomes_require_machine_readable_reason(status):
    with pytest.raises(ValidationError):
        WorkflowState(run_id="run-1", task=TASK, status=status, failure_message="Human detail alone")


@pytest.mark.parametrize("failure", [{"failure_category": "tool_error"}, {"failure_message": "Broken"}])
def test_success_rejects_failure_information(failure):
    with pytest.raises(ValidationError):
        WorkflowState(run_id="run-1", task=TASK, status="success", **failure)


@pytest.mark.parametrize("category", list(FailureCategory))
def test_failure_categories_are_bounded_reason_data(category):
    state = WorkflowState(run_id="run-1", task=TASK, status="partial", failure_category=category.value)
    assert state.model_dump(mode="json")["failure_category"] == category.value


def test_local_state_consistency_and_no_automatic_progress():
    state = WorkflowState(run_id="run-1", task=TASK)
    assert state.status == WorkflowStatus.RUNNING
    assert state.retrieved_evidence == state.patch_proposals == state.validation_results == []
    with pytest.raises(ValidationError):
        WorkflowState(run_id=" ", task=TASK)
    with pytest.raises(ValidationError):
        WorkflowState(run_id="other", task=TASK, patch_proposals=[patch_payload()])
    with pytest.raises(ValidationError):
        WorkflowState(run_id="run-1", task=TASK, attempt_budget={"patch_attempts": 3})
    assert state.attempt_budget.patch_attempts == 0
    assert state.model_dump(mode="json") == WorkflowState.model_validate(state).model_dump(mode="json")


def test_invalid_assignments_preserve_previous_valid_state():
    budget = AttemptBudget(patch_attempts=1)
    before = budget.model_dump()
    for name, value in [("patch_attempts", 3), ("max_patch_attempts", 0)]:
        with pytest.raises(ValidationError):
            setattr(budget, name, value)
        assert budget.model_dump() == before
    state = WorkflowState(run_id="run-1", task=TASK, status="success")
    with pytest.raises(ValidationError):
        state.failure_category = FailureCategory.TOOL_ERROR
    assert state.failure_category is None and state.status == WorkflowStatus.SUCCESS
    evidence = RetrievedEvidence(path="a.py", content="", reason="Relevant")
    with pytest.raises(ValidationError):
        evidence.start_line = 1
    assert evidence.start_line is None


def test_in_place_collection_edits_are_revalidated_at_boundaries():
    proposal = PatchProposal(**patch_payload())
    proposal.files.append(proposal.files[0])
    with pytest.raises(ValidationError):
        PatchProposal.model_validate(proposal)
    with pytest.raises(ValidationError):
        WorkflowState(run_id="run-1", task=TASK, patch_proposals=[proposal])


def test_populated_workflow_json_round_trip():
    state = WorkflowState(
        run_id="run-1", task={**TASK, "constraints": ["No new dependencies"], "success_criteria": ["Tests pass"]},
        repo_profile={"language": "Python", "framework": None, "test_framework": "pytest", "important_dirs": ["src", "tests"]},
        retrieved_evidence=[{"path": "src/parser.py", "content": "return None", "reason": "Fix target", "start_line": 1, "end_line": 1}],
        plan={"steps": ["Reject malformed input"], "files_to_inspect": ["src/parser.py"],
              "files_to_modify": ["src/parser.py"], "validation_commands": [{"argv": ["python", "-m", "pytest", "-q"]}],
              "assumptions": ["Input contract unchanged"]},
        patch_proposals=[patch_payload()],
        validation_results=[{"patch_id": "patch-1", "argv": ["python", "-m", "pytest", "-q"], "exit_code": 1, "stdout": "failure\n", "stderr": "", "duration_ms": 5}],
        review_result={"patch_id": "patch-1", "decision": "revise", "summary": "Add coverage", "findings": [{"message": "Missing regression test", "path": "tests/test_parser.py"}]},
        attempt_budget={"patch_attempts": 2, "retrieval_attempts": 1, "validation_attempts": 2},
        status="partial", failure_category="iteration_limit_reached", failure_message="Attempts exhausted",
        final_report="Progress made; candidate not accepted.",
    )
    data = state.model_dump(mode="json")
    recovered = WorkflowState.model_validate(json.loads(json.dumps(data)))
    assert recovered == state
    assert recovered.model_dump(mode="json") == data
    assert recovered.model_dump_json() == state.model_dump_json()
    assert recovered.validation_results[0].passed is False
    assert recovered.validation_results[0].patch_id == "patch-1"
    assert recovered.review_result.patch_id == "patch-1"
    assert isinstance(recovered.patch_proposals[0].files[1], CreateFileChange)


@pytest.mark.parametrize("model,payload", [
    (ValidationResult, {"argv": ["python"], "exit_code": 0, "duration_ms": 0}),
    (ReviewResult, {"decision": "approve", "summary": "Accepted"}),
])
@pytest.mark.parametrize("patch_id", ["", " \n\t", 123, None, b"patch-1"])
def test_result_candidate_identity_is_strict_nonblank_text(model, payload, patch_id):
    with pytest.raises(ValidationError):
        model.model_validate({**payload, "patch_id": patch_id})


@pytest.mark.parametrize("model,payload", [
    (ValidationResult, {"argv": ["python"], "exit_code": 0, "duration_ms": 0}),
    (ReviewResult, {"decision": "approve", "summary": "Accepted"}),
])
def test_result_candidate_identity_is_required(model, payload):
    with pytest.raises(ValidationError):
        model.model_validate(payload)
    assert model.model_validate({**payload, "patch_id": "patch-1"}).patch_id == "patch-1"


@pytest.mark.parametrize("second,reason", [
    ({"patch_id": "patch-1", "iteration": 2}, "duplicate patch_id"),
    ({"patch_id": "patch-2", "iteration": 1}, "duplicate iteration"),
])
def test_duplicate_proposal_identity_rejected(second, reason):
    with pytest.raises(ValidationError, match=reason):
        WorkflowState(run_id="run-1", task=TASK,
                      patch_proposals=[patch_payload(), {**patch_payload(), **second}])


def test_noncontiguous_proposal_iterations_are_valid():
    state = WorkflowState(run_id="run-1", task=TASK, patch_proposals=[
        patch_payload(), {**patch_payload(), "patch_id": "patch-3", "iteration": 3},
    ])
    assert [patch.iteration for patch in state.patch_proposals] == [1, 3]


@pytest.mark.parametrize("field,payload,reason", [
    ("validation_results", {"patch_id": "patch-2", "argv": ["python"],
                            "exit_code": 0, "duration_ms": 0}, "validation result patch_id"),
    ("review_result", {"patch_id": "patch-2", "decision": "approve",
                       "summary": "Accepted"}, "review result patch_id"),
])
@pytest.mark.parametrize("with_proposal", [False, True])
@pytest.mark.parametrize("as_model", [False, True])
def test_orphan_candidate_results_are_rejected(field, payload, reason, with_proposal, as_model):
    model = ValidationResult if field == "validation_results" else ReviewResult
    result = model.model_validate(payload) if as_model else payload
    value = [result] if field == "validation_results" else result
    with pytest.raises(ValidationError, match=reason):
        WorkflowState(run_id="run-1", task=TASK,
                      patch_proposals=[patch_payload()] if with_proposal else [],
                      **{field: value})


@pytest.mark.parametrize("decision", [None, "approve", "revise", "block"])
@pytest.mark.parametrize("as_model", [False, True])
def test_success_review_consistency(decision, as_model):
    review = None if decision is None else {
        "patch_id": "patch-1", "decision": decision, "summary": "Candidate disposition",
    }
    if as_model and review is not None:
        review = ReviewResult.model_validate(review)
    payload = {"run_id": "run-1", "task": TASK, "status": "success",
               "patch_proposals": [patch_payload()] if review is not None else [],
               "review_result": review}
    if decision in ("revise", "block"):
        with pytest.raises(ValidationError, match="success requires approve"):
            WorkflowState.model_validate(payload)
    else:
        assert WorkflowState.model_validate(payload).status == WorkflowStatus.SUCCESS


def test_success_preserves_attributed_historical_failed_validation():
    state = WorkflowState(
        run_id="run-1", task=TASK, status="success",
        patch_proposals=[patch_payload(), {**patch_payload(), "patch_id": "patch-2", "iteration": 2}],
        validation_results=[
            {"patch_id": "patch-1", "argv": ["python"], "exit_code": 1, "duration_ms": 1},
            {"patch_id": "patch-2", "argv": ["python"], "exit_code": 0, "duration_ms": 1},
        ],
        review_result={"patch_id": "patch-2", "decision": "approve", "summary": "Accepted"},
    )
    assert [result.passed for result in state.validation_results] == [False, True]
    assert state.status == WorkflowStatus.SUCCESS


@pytest.mark.parametrize("target,field,value,reason", [
    ("proposal", "patch_id", "patch-1", "duplicate patch_id"),
    ("proposal", "iteration", 1, "duplicate iteration"),
    ("proposal", "run_id", "other-run", "run_id must match"),
    ("validation", "patch_id", "orphan", "validation result patch_id"),
    ("review", "patch_id", "orphan", "review result patch_id"),
    ("review", "decision", ReviewDecision.REVISE, "success requires approve"),
])
def test_nested_candidate_mutation_rejected_at_aggregate_boundary(target, field, value, reason):
    state = WorkflowState(
        run_id="run-1", task=TASK, status="success",
        patch_proposals=[patch_payload(), {**patch_payload(), "patch_id": "patch-2", "iteration": 2}],
        validation_results=[{"patch_id": "patch-2", "argv": ["python"], "exit_code": 0, "duration_ms": 0}],
        review_result={"patch_id": "patch-2", "decision": "approve", "summary": "Accepted"},
    )
    nested = {"proposal": state.patch_proposals[1], "validation": state.validation_results[0],
              "review": state.review_result}[target]
    setattr(nested, field, value)
    with pytest.raises(ValidationError, match=reason):
        WorkflowState.model_validate(state)


def test_in_place_proposal_append_rejected_at_aggregate_boundary():
    state = WorkflowState(run_id="run-1", task=TASK, patch_proposals=[patch_payload()])
    state.patch_proposals.append(PatchProposal(**{**patch_payload(), "iteration": 2}))
    with pytest.raises(ValidationError, match="duplicate patch_id"):
        WorkflowState.model_validate(state)


def test_rejected_aggregate_assignment_preserves_valid_candidate_state():
    state = WorkflowState(run_id="run-1", task=TASK, status="success",
                          patch_proposals=[patch_payload()],
                          review_result={"patch_id": "patch-1", "decision": "approve", "summary": "Accepted"})
    before = state.model_dump(mode="json")
    for name, value in [
        ("patch_proposals", []),
        ("review_result", ReviewResult(patch_id="patch-1", decision="block", summary="Blocked")),
        ("validation_results", [ValidationResult(patch_id="orphan", argv=["python"], exit_code=0, duration_ms=0)]),
    ]:
        with pytest.raises(ValidationError):
            setattr(state, name, value)
        assert state.model_dump(mode="json") == before


@pytest.mark.parametrize("model", [PatchProposal, PatchApplyResult, ValidationResult, ReviewResult, WorkflowState,
                                   RepositoryEntry, RepositoryInventory, RepositoryText, RepositoryFactEvidence,
                                   RepositoryAnalysisResult, CodeSearchMatch, CodeSearchResult])
def test_workflow_json_schema_is_serializable(model):
    schema = model.model_json_schema()
    assert json.loads(json.dumps(schema)) == schema
    if model in (ValidationResult, ReviewResult):
        assert "patch_id" in schema["required"]


@pytest.mark.parametrize("status", list(PatchApplyStatus))
def test_patch_application_result_status_and_json_round_trip(status):
    payload = {"patch_id": "patch-1", "status": status.value}
    if status == PatchApplyStatus.SUCCESS:
        payload.update(unified_diff="--- a/a.py\n+++ b/a.py\n", files_modified=["z.py", "a.py"])
    else:
        payload["error"] = "Candidate rejected"
    result = PatchApplyResult.model_validate(payload)
    assert result.status == status
    assert result.files_modified == sorted(result.files_modified)
    assert PatchApplyResult.model_validate(json.loads(json.dumps(result.model_dump(mode="json")))) == result


@pytest.mark.parametrize("overrides", [
    {"patch_id": None}, {"patch_id": " "}, {"error": "Unexpected error"},
    {"files_modified": []}, {"files_modified": ["a.py", "a.py"]}, {"unified_diff": ""},
])
def test_incoherent_patch_application_success_rejected(overrides):
    with pytest.raises(ValidationError):
        PatchApplyResult.model_validate({"patch_id": "patch-1", "status": "success",
                                        "unified_diff": "diff", "files_modified": ["a.py"], **overrides})


@pytest.mark.parametrize("status", ["validation_failed", "apply_failed"])
@pytest.mark.parametrize("error", [None, "", " \t"])
def test_patch_application_failure_requires_diagnostic(status, error):
    with pytest.raises(ValidationError):
        PatchApplyResult(patch_id="patch-1", status=status, error=error)


def test_patch_application_failure_cannot_report_canonical_diff_or_validation_writes():
    for status in ("validation_failed", "apply_failed"):
        with pytest.raises(ValidationError):
            PatchApplyResult(patch_id="patch-1", status=status, error="Failed", unified_diff="partial diff")
    with pytest.raises(ValidationError):
        PatchApplyResult(patch_id="patch-1", status="validation_failed", error="Failed", files_modified=["a.py"])
    with pytest.raises(ValidationError):
        PatchApplyResult(patch_id=None, status="apply_failed", error="Failed")
    assert PatchApplyResult(patch_id=None, status="validation_failed", error="Invalid identity").patch_id is None
    assert PatchApplyResult(patch_id="patch-1", status="apply_failed", error="Failed", files_modified=["a.py"]).files_modified == ["a.py"]


@pytest.mark.parametrize("kind", list(RepositoryEntryKind))
def test_repository_entry_metadata_is_typed_and_json_serializable(kind):
    entry = RepositoryEntry(path="item", kind=kind.value, size_bytes=12 if kind == RepositoryEntryKind.FILE else None)
    assert entry.model_dump(mode="json")["kind"] == kind.value


@pytest.mark.parametrize("kind,size", [("file", None), ("file", -1), ("file", True), ("directory", 1), ("symlink", 1), ("unknown", None), (b"file", 1)])
def test_invalid_repository_entry_metadata_rejected(kind, size):
    with pytest.raises(ValidationError):
        RepositoryEntry(path="item", kind=kind, size_bytes=size)


@pytest.mark.parametrize("content,start,end", [("line", None, None), ("", 1, 1), ("line", 0, 1), ("line", 2, 1), ("line", 1, None)])
def test_repository_text_line_attribution_is_coherent(content, start, end):
    with pytest.raises(ValidationError):
        RepositoryText(path="a.py", content=content, start_line=start, end_line=end)


@pytest.mark.parametrize("backend", ["unknown", b"python"])
def test_search_backend_is_a_strict_stable_enum(backend):
    with pytest.raises(ValidationError):
        CodeSearchResult(query="line", case_sensitive=False, backend=backend)


def test_repository_metrics_are_derived_without_duplicate_count_fields():
    inventory = RepositoryInventory(entries=[RepositoryEntry(path="a.py", kind="file", size_bytes=1)])
    analysis = RepositoryAnalysisResult(profile=RepoProfile(), inventory=inventory)
    assert inventory.entries_returned == 1 and analysis.files_considered == 1
    with pytest.raises(ValidationError):
        RepositoryInventory(entries_returned=99)
    with pytest.raises(ValidationError):
        CodeSearchMatch(path="a.py", line_number=0, line="line")
    with pytest.raises(ValidationError):
        CodeSearchResult(query="a\nb", case_sensitive=False, backend=SearchBackend.PYTHON)


def test_existing_v1_contracts_remain_independent_and_constructible():
    from agents.results import AgentFinding, AgentResult, Evidence, SynthesizedReview
    from eval.schema import ExpectedResult, GoldenCase
    from orchestration.types import InternalEvent

    event = InternalEvent(event_type="push", delivery_id="delivery", raw_payload={"files": []})
    evidence = Evidence(source="diff_hunk", snippet="old finding evidence", rule="rule")
    finding = AgentFinding(id="finding", message="Existing finding", evidence=[evidence])
    result = AgentResult(agent_name="existing", findings=[finding])
    review = SynthesizedReview.from_results(event_id=event.delivery_id, repo="owner/repo", results=[result])
    expected = ExpectedResult(min_findings=1)
    case = GoldenCase(id="case", description="Existing evaluation", expected=expected)
    assert review.findings[0].message == finding.message
    assert case.expected.min_findings == 1 and review.suggested_patches == {}
    assert not isinstance(evidence, RetrievedEvidence)
    assert not isinstance(review, ReviewResult)
    assert not isinstance(event, TaskSpec)
