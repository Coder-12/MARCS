# Slice 0 — Reproducible baseline and integrity gate

Initial evidence recorded on 2026-09-29; independent final gate verified on 2026-10-01.
Current implementation plus execution establishes
current-state truth; `docs/design.md` remains the proposed target architecture.

## Starting state

| Item | Observed value |
| --- | --- |
| Branch | `main` |
| Starting HEAD | `e6d871edef720f230ed59930f025cf13ad30294a` |
| Commit | `e6d871e Merge v1 into main` |
| Initial working tree | `?? AGENTS.md`; preserved unchanged |
| Platform | macOS 26.1, Darwin 25.1.0, arm64 |
| Python | 3.12.5, conda-forge build; used to create isolated stdlib venvs |
| Starting pip | 24.0 in the developer environment; not used to install test dependencies |
| Venv pip | 24.2 in the initial environments and the independent gate environment |

The actual HEAD matched the prepared prompt and remained unchanged through the
independent gate. At the start of that gate, 12 intended files were modified, six
intended files were untracked, and the index was empty; unrelated `AGENTS.md` was
also untracked. The final gate stages the explicit 18-file Slice 0 candidate for
human review. No commit, push, merge, tag, or PR is performed. `AGENTS.md` remains
unchanged and unstaged.

## Pre-fix evidence

**VERIFIED:** A `git archive HEAD` copy contained only `journals/.gitkeep` under
`journals/`. `git log --all -- journals` showed only release commit `6129483`;
`git log --all -- journals/journal.py` returned nothing. There was no journal
implementation to recover from the locally available history. Remote/deleted
history was not searched.

The following is the historical initial-pass execution record. The independent
gate rechecked tracked source/history and reproduced the final candidate separately;
it did not repeat the original dependency-install diagnosis.

| Command in clean copy / fresh venv | Exit code | Result |
| --- | --- | --- |
| `python -m pip install -r requirements.txt` (sandbox) | 1 | Package-index DNS/network access denied; environmental limitation |
| Same installation with network permission | 0 | Declared runtime packages installed |
| `python -m pytest --collect-only -q` | 1 | `No module named pytest` |
| `python -m pytest -q` | 1 | `No module named pytest` |
| `python -m pip install pytest pytest-asyncio` | 0 | Explicit diagnostic additions, absent from original manifest |
| Collection after diagnostic additions | 2 | 54 tests collected, 54 collection errors |
| Test execution after diagnostic additions | 2 | Interrupted by 54 collection errors; no tests executed |

Collection established missing `journals.journal`, `aiohttp`, `httpx`, and
`structlog`. The Starlette TestClient error suggested `httpx2`, but inspection of
its installed source showed a supported `httpx` fallback; declaring the runtime
`httpx` dependency was sufficient. Static inspection additionally confirmed
unconditional `asyncpg` and `async_timeout` imports in `core/db.py`.

The inspector read flat `action` records while two patch tests asserted `op`.
Unconditional debug prints exposed parsed patches, original repository text, and
entire recovery records. Recovery tests assigned module resolver functions
directly and leaked those overrides between tests.

## Changes and contract

| Path | Reason and behavior |
| --- | --- |
| `.gitignore` | Allow exactly the two journal source files; keep runtime JSONL and caches ignored |
| `journals/__init__.py` | Add the local source package |
| `journals/journal.py` | Implement existing append/read/clear/path API with one canonical record contract |
| `services/journal_inspector.py` | Share journal reader/writer, reject malformed histories before recovery, report errors, leave failed rollback retryable, report failed commit-marker writes, correct affected stdlib logging calls, remove record dump |
| `services/patch_applier.py` | Remove two unconditional patch/content prints; preserve apply behavior |
| `requirements.txt` | Declare actual runtime imports; remove unused `psycopg2-binary` and `rq` |
| `requirements-dev.txt` | Include runtime requirements plus pytest and pytest-asyncio |
| `tests/test_journal.py` | Test round trip, UTF-8, repeatable serialization, clear, dynamic configuration, resolved confinement with relative/symlink directories, path rejection, malformed/duplicate fields, I/O errors, legacy normalization |
| `tests/test_journal_inspector.py` | Test malformed-history refusal, directory boundary, shared recovery writes/errors, failed-operation retry, and legacy rollback |
| `tests/test_patch_applier.py` | Assert canonical `action` without weakening commit checks |
| `tests/test_patch_applier_integration.py` | Assert canonical `action` without weakening new-file checks |
| `tests/test_reviewer_crash_resume.py` | Remove leaking resolver overrides; exercise the existing environment configuration |
| `services/golden_runner.py` | Use `asyncio.run()` in the synchronous CLI; create and close its own event loop |
| `tests/test_golden_runner.py` | Await `run_all` without weakening assertions; cover CLI with no current loop and preserve success/failure exit policy |
| `services/startup.py` | Isolate dry-run journal failures; log/store an explicit error and continue inspecting valid journals |
| `tests/test_startup.py` | Verify corrupt-history refusal, continued inspection, explicit errors, and unchanged journal/target bytes in dry-run and recovery modes |
| `README.md` | Replace unsupported readiness/determinism/coverage/test-count claims, document isolated installs and evidence, correct initialization script path |
| `docs/baseline_integrity.md` | Record this evidence and its limits |

**Journal contract:** UTF-8 JSONL; one object per nonblank line, with a nonempty
`action` string and flat action-specific fields. Writers reject reserved
`action`/`op` payload keys and non-finite JSON numbers, use sorted keys, and add no
clock/random values. Each append performs **write + flush + file fsync** before
closing. Parent directories are not fsynced, records are not framed atomically
against partial writes, and concurrent writers are unsupported. Comprehensive
crash durability has not been established. Missing journals read as `[]`; clear is
idempotent. Invalid records, including duplicate JSON keys at any object level, raise a
contextual `ValueError`; I/O errors propagate.
`MACRS_JOURNAL_DIR` is resolved on every call. Event IDs start with an ASCII letter
or digit and contain only ASCII letters, digits, `.`, `_`, or `-`; file symlinks
are rejected, including at open using POSIX `O_NOFOLLOW`.

**Local supplemental compatibility evidence:** The working checkout also contained 16 ignored local
journal files, inspected without displaying payload values and left unchanged.
Seven records had `op` without `action`; 49 had both fields with nested `payload`;
four were flat inspector records. These local artifacts justify compatibility
but were not used as clean-checkout source or test fixtures. At the read boundary,
known `op`/`payload` envelopes become flat `action` records; matching canonical
`action` and legacy `op` fields are collapsed. Conflicting fields and malformed envelopes are
rejected. New writes never emit `op`; old files are not rewritten. Tests use
synthetic versions of the observed shapes. A final read-only check also normalized
all 60 records in the 16 existing local files; before/after SHA-256 checks confirmed
their bytes were unchanged. No recovery was run against those local files.
These private files are not required to reproduce the baseline: the repository's
synthetic tests cover both observed envelope shapes, conflicting fields,
non-mutating reads, canonical new writes, and legacy rollback.

Recovery decision rules remain unchanged. A malformed history cannot drive
partial recovery; scan results now include `action: error`. Rollback marker write
failure raises instead of being swallowed. Failed restoration/removal does not
write a terminal rollback marker. Resume marker failure retains its helper-level
`ok: false` result, but inspection raises and scanning reports `action: error`.
These error paths and successful retries are covered by focused tests.

## Final corrective verification

**VERIFIED — Golden runner:** The synchronous CLI was reproduced failing after a
prior `asyncio.run()` closed its loop: `RuntimeError: There is no current event
loop in thread 'MainThread'.` This was a production entry-point defect as well as
a test defect. The CLI now uses `asyncio.run(run_all(...))`. The original test
awaits `run_all()` with its assertions intact; a new parametrized CLI test verifies
execution without a current loop and both existing return-code outcomes.

**VERIFIED — Startup:** Before correction, an enabled dry-run inspector with one
malformed journal raised `ValueError` out of `run_startup_inspector()`, stored no
decisions, and left the journal unchanged. The API lifespan calls this function
without catching that exception. The corrected dry-run path isolates each file:
it logs `startup_journal_inspection_failed`, stores `decision: error` with the
parser error, and continues. It neither recovers nor rewrites corrupt history.
Tests cover a replacement record followed by malformed JSON alongside a valid
journal in both dry-run and recovery modes; the target and journal bytes remain
unchanged. This verifies the inspector boundary, not a full API/service startup.
A separate fresh-process check set the real enable/dry-run environment flags
before import and confirmed the same explicit error/noop reports without mutation.

**VERIFIED — Journal review (earlier correction):** No journal source change was
needed in that correction pass.
Invalid/absolute/traversal IDs are rejected. Generated paths resolve beneath the
configured directory, including relative paths and a trusted directory symlink.
The environment is read per call. Canonical writes and read-only legacy
normalization remain covered by repository-controlled tests.

## Independent adversarial gate

**VERIFIED:** Reviewed all 18 intended files, locally available journal history,
current callers, dependencies, and deployment entry points independently.
Temporary probes reproduced these defects in the incoming candidate:

- Duplicate JSON keys silently selected the last value: an `error`/`commit`
  action conflict became `commit`, and inspection returned `noop`.
- A failed backup restoration reported a rollback with `missing` files, appended
  the terminal marker, and caused the next inspection to return `noop` while the
  modified target remained unchanged.
- A failed commit-marker append returned `action: resume_commit` with `ok: false`.

The parser now rejects duplicate keys before normalization or recovery. Rollback
marks completion only when all attempted operations succeeded; otherwise scanning
reports an error and preserves retryable history. Failed commit-marker writes also
become explicit scan errors. Existing recovery decision/target policies are not
redesigned. Regression tests check failure reports, journal/target bytes, and
successful retry. Running six new regressions against the incoming candidate
produced six failures (expected negative control); all pass after correction.
Startup tests now also exercise duplicate-action corruption in both modes.

Independent Python 3.12 probes reproduced the original HEAD golden CLI failure
and original startup dry-run exception with the reconstructed strict parser.
The corrected startup reported error/noop without mutation. A file symlink
introduced between validation and open was rejected by `O_NOFOLLOW`; its target
bytes were unchanged. The configured directory itself remains trusted.

## Dependency classification

Audit: AST scan of imports in tracked Python files, followed by inspection of
call sites, guarded imports, CLI usage, and settings loading.
The independent gate repeated case-insensitive searches across tracked Python,
scripts, Docker/deployment configuration, manifests, and operational files.
Only an RQ comment and unused `broker_url` setting remained; no current executable
usage of `rq` or `psycopg2` was found. Both dependencies remain removed.

| Classification | Dependencies / evidence |
| --- | --- |
| Runtime | `fastapi`, `uvicorn` (declared server command), `pydantic>=2` (v2 APIs), `pydantic-settings`, `python-dotenv` (settings `.env` loader), `structlog`, `aiohttp`, `httpx`, `asyncpg`, `async-timeout`, `redis` |
| Development/test | `pytest`, `pytest-asyncio`; includes the test module under `services/` |
| Optional/guarded | `openai` imported inside optional legacy adapter; `aioredis` guarded in `core/storage.py`, with memory fallback when missing; neither is validated or installed |
| Standard-library/local | Remaining resolved imports, including the restored `journals` package; no external package added for them |
| Obsolete/unreachable in inspected implementation | No tracked Python imports or active commands using `psycopg2-binary` or `rq`; current worker consumes a local list, so both declarations removed |

No new packaging system or lockfile. Only Pydantic's demonstrated v2 API need is
constrained; unbounded dependencies can resolve differently in future installs.

Important versions actually installed in the successful final install:

```text
Python 3.12.5; pip 24.2
fastapi 0.142.2; starlette 1.7.0; uvicorn 0.54.0
pydantic 2.13.5; pydantic-core 2.46.5; pydantic-settings 2.15.0
python-dotenv 1.2.4; structlog 26.1.0
aiohttp 3.14.3; httpx 0.28.1; httpcore 1.0.9; anyio 4.15.1
asyncpg 0.31.0; async-timeout 5.0.1; redis 8.1.0
pytest 9.1.1; pytest-asyncio 1.4.0; pluggy 1.6.0
opentelemetry-api 1.45.0 (transitive)
```

## Final validation

The independent gate used a new `git archive HEAD` snapshot with all changed/new
Slice 0 files overlaid. Its separate, fresh venv was populated
only by `requirements-dev.txt` and its runtime include; no diagnostic additions
were made. These manifests are changes under review, not yet committed.
`sys.prefix != sys.base_prefix` was true and `site.ENABLE_USER_SITE` false.
Existing `.env`, runtime journals, backups, and developer site packages were not
copied. Tests ran with an allowlisted environment: venv/system `PATH`, temporary
`HOME`/`TMPDIR`, `LANG=en_US.UTF-8`, `PYTHONNOUSERSITE=1`,
`STORAGE_BACKEND=memory`, `MACRS_LLM_BACKEND=fake`. Credentials were absent.

To reproduce after obtaining the reviewed source files, create a clean source
copy, place the venv outside that copy, and run from the copy:

```bash
python -m venv "$BASELINE_VENV"
"$BASELINE_VENV/bin/python" -m pip install -r requirements-dev.txt
# Repeat each validation command below with this environment prefix:
env -i PATH="$BASELINE_VENV/bin:/usr/bin:/bin:/usr/sbin:/sbin" \
  HOME="$BASELINE_HOME" TMPDIR="$BASELINE_TMP" LANG=en_US.UTF-8 \
  PYTHONNOUSERSITE=1 STORAGE_BACKEND=memory MACRS_LLM_BACKEND=fake \
  python -m pytest -q
```

`BASELINE_VENV`, `BASELINE_HOME`, and `BASELINE_TMP` are absolute disposable paths;
create the latter two directories first. `BASELINE_REPORT` names a disposable
JUnit output file outside the source copy. The source copy must have no `.env`.
The initial archive was made using `git archive HEAD`; while changes remain
uncommitted, overlay every changed/new file listed above before final validation.

| Command | Exit code | Result |
| --- | --- | --- |
| `python -m pip install -r requirements-dev.txt` | 0 | Fresh installation completed |
| `python -m pip check` | 0 | No broken requirements |
| `python -m pytest --collect-only -q` | 0 | **230 tests collected**, 1 warning |
| `python -m pytest -q --junitxml="$BASELINE_REPORT"` | 0 | **229 passed, 1 skipped, 49 warnings** in 13.91s; 0 failures/errors (JUnit) |
| Focused command below | 0 | **67 passed, 7 warnings** in 1.01s; no failures/skips |
| `python -m compileall -q agents api core orchestration services worker tools utils journals` | 0 | Compilation succeeded |
| Import smoke below | 0 | API, worker, journal, patch/recovery, DB/Redis clients, tool registry imported |
| Independent Python 3.12 boundary probes | 0 | Original golden/startup defects reproduced; corrected startup error/noop without mutation; symlink-at-open rejected |
| New regressions against incoming candidate (negative control) | 1 | 6 failed, 42 deselected; expected proof that tests detect the defects |
| `python -m pip freeze` | 0 | Important resolved versions recorded above |
| `git diff --check` | 0 | No whitespace errors |
| `git status --short` | 0 | Expected Slice 0 changes and pre-existing untracked `AGENTS.md` |
| `git diff --cached --check` | 0 | No staged whitespace errors |
| `git diff --cached --stat` / `git diff --cached --name-only` | 0 | Exactly 18 intended Slice 0 files; new source, manifests, and tests included |
| `git check-ignore journals/probe.jsonl journals/__pycache__/probe.pyc` | 0 | Runtime files/caches remain ignored |
| `git check-ignore journals/__init__.py journals/journal.py` | 1 | Neither source file is ignored (expected exit 1) |
| Intended-file hygiene and preservation check | 0 | 18 Slice 0 files checked; no machine paths, credential-shaped literals/URLs, `.env`, runtime journals, or `AGENTS.md` in the intended change set; generated snapshot journals ignored |
| Initial-pass local journal normalization and SHA-256 comparison | 0 | 60 records in 16 files read; all bytes unchanged; supplemental evidence |
| Initial-pass `docker compose config --quiet` | 1 | Clean copy lacks `.env`; warnings for unset Postgres substitutions and obsolete Compose version field. Not repeated; no runtime validation |

```bash
python -m pytest -q tests/test_journal.py tests/test_journal_inspector.py \
  tests/test_startup.py tests/test_patch_applier.py tests/test_patch_applier_integration.py \
  tests/test_reviewer_crash_resume.py tests/test_golden_runner.py \
  tests/test_orchestrator.py tests/test_patch_verifier.py
python -c 'import api.app, worker.worker, services.patch_applier, services.journal_inspector, services.patch_verifier, core.db, core.redis_client, tools.registry, journals.journal'
```

The independent final results supersede the earlier non-green Slice 0 run and
the intermediate 221-passed correction result. A JUnit report was written outside
the source copy by the full test command. Validation output
was retained in disposable logs outside the source tree; machine-specific paths
and private runtime data are intentionally excluded from this evidence artifact.
`AGENTS.md` was excluded from snapshots and the intended Slice 0 change set;
its SHA-256 was unchanged. Only the 18 intended files are staged; staged blobs
match the validated source (the evidence document was updated after execution).
`git diff --cached --check` passes. No runtime journals are staged.

## Remaining failures / risks

### Slice 0 blockers

None for the measured integrity gate. The final offline suite passes; the one
live-service smoke test is skipped explicitly.

### Deferred verified issues

- The one skipped test, `test_reviewer_smoke_live`, requires `OPENAI_API_KEY`.
  Live model behavior is outside this offline baseline.
- 49 warnings: deprecated `datetime.utcnow()` calls and Starlette's deprecated
  `httpx` TestClient fallback. These were not suppressed or pinned away.
- Patch application joins supplied file paths directly to the source repository;
  verifier code uses `git apply --unsafe-paths`. Fuzzy/basename matching, source
  mutation, and workspace/command isolation remain unchanged.
- Recovery matches backup/replacement records by basename for its decision and
  does not verify backup contents before resuming. Missing on-disk backups can
  still select target removal under the existing rollback policy. Comprehensive
  crash windows, backup validity, and multi-file atomicity are not established.
  Failed restore/remove attempts now report errors without a terminal marker;
  an I/O failure after writing a marker may still leave bytes on disk.
- `docker-compose.yml` runs nonexistent `orchestrator.worker`; actual code is
  `worker/worker.py`. API and worker use an in-process list queue, which cannot
  transport events between separate containers. No superficial command repair.
- `tools/pylint_runner.py`, `mypy_runner.py`, and `bandit_runner.py` return stub
  success results. Their tool names do not establish executed analysis.
- Both `core/llm.py` and `core/llm_client.py` remain; model abstraction
  consolidation is deferred.

### Unverified concerns

Deployment/runtime readiness, Postgres/Redis connectivity, optional OpenAI and
aioredis backend compatibility, live LLM quality/determinism, performance,
coverage, cross-platform behavior (including Windows `O_NOFOLLOW` availability),
and future dependency resolutions have not been validated. The journal directory
itself is trusted configuration; its confinement tests do not establish security
for the broader patch pipeline.

## Evidence boundary

**VERIFIED:** On this Python/macOS environment, the reviewed source can be
installed from its manifests into an isolated venv, imported at the listed entry
points, compiled, collected, and tested without live credentials or provisioned
production services. The journal contract, legacy read normalization, error
handling, startup corruption isolation, synchronous golden CLI compatibility,
duplicate-field rejection, retry after failed recovery operations, and listed
patch/recovery paths pass their focused tests. No coverage
measurement or live-service validation was performed.

**INFERRED:** Matching dependency versions and environment settings should permit
another developer to reproduce these results; this was not tested on a second
machine. Dependency resolution is recorded, not locked.

**PROPOSED:** Task-to-patch architecture and all later slices remain design work.
This baseline establishes no general security, crash-safety, deterministic LLM,
production-readiness, deployment, or performance guarantee.
