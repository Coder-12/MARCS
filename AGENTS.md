# MARCS agent working guide

## Source of truth

- Current behavior is established by committed code, tests, relevant runtime/evaluation
  artifacts, and `docs/baseline_integrity.md`; README claims alone are not execution evidence.
- `docs/design.md` is the proposed task-to-patch architecture. It does not prove that
  a feature, package layout, abstraction, or workflow already exists.
- Today MARCS is a multi-agent code review system with webhook, worker, review,
  unified-diff patch application, journal/recovery, and reporting paths.
- For each task, read the relevant current callers, tests, and contracts before
  comparing them with the target design. Preserve working behavior unless the task
  or demonstrated defect justifies a change; explain intentional contract changes.
- Distinguish claims when needed:
  - **VERIFIED:** directly established by code, tests, execution, traces, or committed artifacts.
  - **INFERRED:** a conclusion from verified evidence that has not been independently demonstrated.
  - **PROPOSED:** desired design or future work; never present it as current functionality.

## Repository map

| Area | Current responsibility |
| --- | --- |
| `api/` | FastAPI app, routes, webhook handling, and request schemas. |
| `agents/` | Review agents, routing/pipeline logic, patch generation, and result models. |
| `core/` | Settings, storage/DB clients, logging, tracing, reports, and LLM adapters. |
| `orchestration/`, `worker/` | Review coordination, in-process queue, and async event processing. |
| `services/` | Review/patch processing, selection, verification, application, recovery, and evaluation. |
| `tools/` | Tool interfaces/registry, golden-data utilities, and analysis runners. |
| `eval/`, `data/golden/` | Evaluation scripts, comparisons, schemas, and golden fixtures. |
| `journals/` | Tracked journal source; generated JSONL histories remain ignored. |
| `tests/` | Regression and integration tests. |
| `scripts/` | Demo, inspection, initialization, migration, and maintenance entry points. |
| `docs/` | Target design and baseline evidence. |

## Engineering rules

1. Preserve -> understand -> measure -> extend: locate behavior, callers, tests,
   and contracts; reproduce the relevant behavior; make the smallest coherent change.
2. Refactor only when the task or evidence justifies it. Do not rewrite working
   code merely to resemble the target architecture.
3. Use the smallest topology that solves the measured problem. Ask in order:
   can deterministic code do it; can an existing tool; can the orchestrator own it;
   can an existing reasoning component handle it; is independent model reasoning necessary?
   Create a new agent only when that final need is established.
4. Prefer bounded execution, explicit state transitions and failure paths,
   inspectable artifacts, deterministic tools where practical, explicit budgets/
   attempt limits, and recoverable failures. Avoid unbounded autonomous loops.
5. Preserve existing API/artifact contracts unless deliberately changing them.
   Update relevant documentation when behavior changes; do not weaken tests to pass.

## Slice discipline
- Historical verified baseline: Slice 0 — Reproducible Baseline & Integrity Gate,
  commit `e61c71807688ddb6aa595e915d989d2032e8c3b2`. Always inspect current HEAD
  and repository state before beginning new work.
- Work only on the explicitly assigned current task or slice. Determine the current
  slice from the task context and committed repository state; do not infer it from
  this guide. Stop at that slice's acceptance gate.
- Record discovered out-of-scope defects instead of opportunistically fixing them.
  Expand scope only when the requested change cannot otherwise be correct, and explain why.
  Do not silently introduce the next slice or desirable future architecture.

## Development and validation

Python **3.12.5** is the Slice 0 verified environment, not a universal minimum-version
or cross-platform guarantee. `requirements.txt` declares runtime dependencies;
`requirements-dev.txt` includes them and adds current development/test dependencies.
Only intentionally reviewed, committed tooling defines repository workflow.
Dependency versions are recorded in baseline evidence; resolution is not locked.

```bash
python -m venv venv
source venv/bin/activate
python -m pip install -r requirements-dev.txt
python -m pytest -q
```

- Start with `python -m pytest -q <relevant tests>`; broaden validation proportionally.
- State/recovery, patch application, model boundaries, orchestration, shared schemas,
  dependency manifests, and cross-cutting changes need stronger checks, including the full suite.
- For appropriate Python changes, also consider:
  `python -m compileall -q agents api core orchestration services worker tools utils journals`.
- Trivial documentation edits do not require the full suite. Report commands,
  exit codes, exact measured results, and checks that could not run with their reasons.
- For offline baseline validation, follow the isolated-source/environment recipe in
  `docs/baseline_integrity.md`; exclude local `.env`, live credentials, and production services.

## Safety and repository hygiene

- Inspect `git status` before editing; preserve unrelated changes and local runtime data.
  Never run destructive reset/clean commands without explicit authorization.
  Do not force-push or rewrite published history during ordinary work.
- Keep credentials, `.env`, private runtime payloads, journals, backups, and local
  artifacts out of commits unless explicitly required. Never expose secrets in logs/reports.
  Temporary local artifacts are supplemental evidence, not reproducible repository fixtures.
- Use disposable repositories/workspaces for destructive patch experiments.
  `scripts/demo_orchestrator.py --yes` without `--dry-run` can mutate `--repo-root`;
  `--preview-only` avoids patch application. Preserve ignored runtime data before cleanup.
- Preserve the journal contract: UTF-8 JSONL, canonical `action`, read-only legacy
  normalization, and explicit malformed-history errors before recovery. It is single-writer;
  append writes, flushes, and fsyncs the file, without comprehensive crash-durability guarantees.
- Respect these verified deferred boundaries; address them only in the appropriate task/slice:
  - Patch-path confinement is not fully hardened; verifier uses `git apply --unsafe-paths`.
    Source-repository mutation and workspace isolation need later work.
  - Recovery has basename-matching, backup-validity, crash-window, and multi-file-atomicity limits.
  - Deployment/queue topology is not production-ready; the current queue is an in-process list.
  - Pylint/mypy/bandit runners are stubs; multiple LLM abstractions remain.
    Live LLM determinism is not established.

## Completion gate

Mark complete only when the requested behavior works, current contracts are preserved or
intentionally changed, and appropriate checks pass;
evidence is accurate, limitations are named, unrelated changes are absent, and no
next-slice work was introduced. If blocked, report the blocker without claiming completion.
For meaningful engineering changes, report changed
files, behavior changed, validation performed, measured results, and remaining risk/
deferred work. Stop when the task's acceptance gate is satisfied.
